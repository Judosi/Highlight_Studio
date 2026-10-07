from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

from ...core.utils import OperationCancelled, parse_json_loose
from ...infrastructure.resource_manager import ResourceManager


_TRANSIENT_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}


class AITransportError(RuntimeError):
    """A provider transport/runtime error with explicit retry semantics."""

    def __init__(self, message: str, *, transient: bool, status_code: int | None = None):
        super().__init__(message)
        self.transient = bool(transient)
        self.status_code = status_code


class AIResponseFormatError(RuntimeError):
    """The provider responded, but the payload could not be parsed locally."""

    def __init__(self, message: str, *, raw_preview: str = ""):
        super().__init__(message)
        self.raw_preview = raw_preview[:2000]


class AIResponseContractError(RuntimeError):
    """The model returned JSON, but not the contract required by this operation.

    This is deliberately different from a transport failure. Repeating the same
    large prompt several times is usually wasteful; callers can split the batch
    or run a targeted repair/rescue request instead.
    """

    def __init__(self, message: str, *, parsed_preview: Any = None):
        super().__init__(message)
        self.parsed_preview = parsed_preview


def normalize_response_contract(
    parsed: Any,
    *,
    collection_key: str | None = None,
    expected_ids: set[int] | None = None,
    required_item_fields: set[str] | None = None,
) -> dict[str, Any]:
    """Normalize common local-LLM JSON shapes without another inference.

    Qwen can occasionally return ``{"1": {...}, "2": {...}}`` instead of
    ``{"blocks": [...]}``. That shape is deterministic to repair locally and
    must not trigger a second expensive model call. An explicit ``error`` object,
    on the other hand, is never a successful result.
    """
    if not isinstance(parsed, dict):
        raise AIResponseContractError(
            "AI JSON root must be an object", parsed_preview=parsed
        )

    if parsed.get("error") not in (None, "", False):
        err = parsed.get("error")
        if isinstance(err, dict):
            try:
                err = json.dumps(err, ensure_ascii=False, default=str)
            except Exception:
                err = str(err)
        raise AIResponseContractError(
            f"AI returned an error payload: {str(err)[:1200]}", parsed_preview=parsed
        )

    if not collection_key:
        return parsed

    items: list[dict[str, Any]] | None = None
    source_shape = collection_key
    if isinstance(parsed.get(collection_key), list):
        items = [x for x in parsed.get(collection_key, []) if isinstance(x, dict)]
    else:
        for alias in ("items", "results", "segments"):
            if isinstance(parsed.get(alias), list):
                items = [x for x in parsed.get(alias, []) if isinstance(x, dict)]
                source_shape = alias
                break

    # Local deterministic repair for {"1": {...}, "2": {...}}.
    if items is None and parsed:
        numeric_items: list[dict[str, Any]] = []
        numeric_shape = True
        for raw_key, value in parsed.items():
            try:
                item_id = int(str(raw_key).strip())
            except Exception:
                numeric_shape = False
                break
            if isinstance(value, dict):
                item = dict(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                item = {"score": float(value)}
            else:
                numeric_shape = False
                break
            item.setdefault("id", item_id)
            numeric_items.append(item)
        if numeric_shape and numeric_items:
            items = numeric_items
            source_shape = "numeric_object"

    if items is None:
        raise AIResponseContractError(
            f"AI JSON is missing required collection '{collection_key}'",
            parsed_preview=parsed,
        )

    required = set(required_item_fields or set())
    if required:
        invalid: list[str] = []
        for idx, item in enumerate(items):
            missing_fields = [field for field in sorted(required) if item.get(field) in (None, "")]
            if missing_fields:
                invalid.append(f"item#{idx + 1}: missing {','.join(missing_fields)}")
        if invalid:
            raise AIResponseContractError(
                f"AI collection '{collection_key}' has invalid items: {'; '.join(invalid[:8])}",
                parsed_preview=parsed,
            )

    normalized = dict(parsed)
    normalized[collection_key] = items
    if source_shape != collection_key:
        normalized["_hs_normalized_from"] = source_shape

    if expected_ids is not None:
        actual_ids: set[int] = set()
        for item in items:
            raw_id = item.get("id")
            if isinstance(raw_id, str):
                raw_id = raw_id.strip().replace("ID=", "").replace("id=", "")
            try:
                item_id = int(raw_id)
            except Exception:
                continue
            if item_id in expected_ids:
                actual_ids.add(item_id)
        normalized["_hs_expected_ids"] = sorted(expected_ids)
        normalized["_hs_actual_ids"] = sorted(actual_ids)
        normalized["_hs_missing_ids"] = sorted(expected_ids - actual_ids)

    return normalized


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    base_delay_seconds: float = 1.5
    max_delay_seconds: float = 8.0

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None) -> "RetryPolicy":
        settings = settings or {}
        # This budget is intentionally only for transient transport failures.
        # Semantic completeness retries remain owned by the pipeline.
        attempts = max(1, min(3, int(settings.get("ai_transport_retry_count", 2) or 2)))
        return cls(max_attempts=attempts)

    def delay_for(self, attempt: int) -> float:
        return min(self.max_delay_seconds, self.base_delay_seconds * (2 ** max(0, attempt - 1)))


AIResourceManager = ResourceManager


class AICircuitBreaker:
    """Small process-local circuit breaker keyed by provider and model."""

    _lock = threading.Lock()
    _states: dict[str, dict[str, float | int]] = {}

    def __init__(self, key: str, settings: dict[str, Any] | None = None):
        settings = settings or {}
        self.key = str(key)
        self.threshold = max(1, int(settings.get("ai_circuit_failure_threshold", 2) or 2))
        self.cooldown = max(1.0, float(settings.get("ai_circuit_cooldown_seconds", 60) or 60))

    def before_request(self) -> bool:
        with self._lock:
            state = self._states.get(self.key, {})
            opened_at = float(state.get("opened_at", 0) or 0)
            if not opened_at:
                return True
            if time.monotonic() - opened_at >= self.cooldown:
                state["opened_at"] = 0.0
                state["failures"] = max(0, self.threshold - 1)
                self._states[self.key] = state
                return True
            return False

    def success(self) -> None:
        with self._lock:
            self._states[self.key] = {"failures": 0, "opened_at": 0.0}

    def failure(self) -> None:
        with self._lock:
            state = self._states.setdefault(self.key, {"failures": 0, "opened_at": 0.0})
            failures = int(state.get("failures", 0) or 0) + 1
            state["failures"] = failures
            if failures >= self.threshold:
                state["opened_at"] = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._states.get(self.key, {"failures": 0, "opened_at": 0.0}))
        opened_at = float(state.get("opened_at", 0) or 0)
        state["open"] = bool(opened_at and time.monotonic() - opened_at < self.cooldown)
        return state


class AITraceWriter:
    """Append-only, bounded-detail trace for each logical provider request."""

    _locks_guard = threading.Lock()
    _path_locks: dict[str, threading.Lock] = {}

    def __init__(self, project_dir: Path | None):
        self.project_dir = Path(project_dir) if project_dir else None
        self.path = self.project_dir / "ai_runtime_trace.jsonl" if self.project_dir else None
        self.summary_path = self.project_dir / "ai_runtime_summary.json" if self.project_dir else None
        lock_key = str(self.summary_path or self.path or "disabled")
        with self._locks_guard:
            self._lock = self._path_locks.setdefault(lock_key, threading.Lock())

    def event(self, payload: dict[str, Any]) -> None:
        if not self.path:
            return
        record = {"at": round(time.time(), 3), **payload}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                self._refresh_summary(record)
        except Exception:
            # Diagnostics must never make the analysis fail.
            pass

    def _refresh_summary(self, latest: dict[str, Any]) -> None:
        if not self.summary_path:
            return
        summary: dict[str, Any] = {}
        try:
            if self.summary_path.exists():
                loaded = json.loads(self.summary_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    summary = loaded
        except Exception:
            summary = {}
        counters = summary.setdefault("counters", {})
        event = str(latest.get("event") or "unknown")
        counters[event] = int(counters.get(event, 0) or 0) + 1
        if latest.get("operation"):
            op = summary.setdefault("operations", {}).setdefault(str(latest["operation"]), {"requests": 0, "failures": 0})
            if event == "request_started":
                op["requests"] = int(op.get("requests", 0) or 0) + 1
            if event in {"request_failed", "response_format_failed", "response_contract_failed"}:
                op["failures"] = int(op.get("failures", 0) or 0) + 1
        summary["last_event"] = latest
        summary["updated_at"] = time.time()
        tmp = self.summary_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.summary_path)


class AIExecutionController:
    """Central execution policy around a local AI provider.

    It owns transient transport retries, local JSON repair, tracing and heavy-AI
    resource leases.  It intentionally does *not* own semantic retries such as
    "the model skipped ID=4"; those remain a pipeline concern so retry budgets do
    not multiply invisibly.
    """

    def __init__(
        self,
        *,
        settings: dict[str, Any] | None = None,
        project_dir: Path | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ):
        self.settings = dict(settings or {})
        self.cancel_check = cancel_check
        self.retry_policy = RetryPolicy.from_settings(self.settings)
        self.trace = AITraceWriter(project_dir)
        self.gpu_job_limit = max(1, min(4, int(self.settings.get("gpu_job_limit", 1) or 1)))
        prefix = str(Path(project_dir).resolve()) if project_dir else "global"
        provider = str(self.settings.get("ollama_url") or "ollama")
        self._circuit_prefix = f"{prefix}|{provider}"

    def _circuit(self, model: str) -> AICircuitBreaker:
        return AICircuitBreaker(f"{self._circuit_prefix}|{model}", self.settings)

    def record_runtime_health(self, model: str, ok: bool) -> None:
        circuit = self._circuit(model)
        circuit.success() if ok else circuit.failure()

    def _cancelled(self) -> bool:
        return bool(self.cancel_check and self.cancel_check())

    def _raise_if_cancelled(self) -> None:
        if self._cancelled():
            raise OperationCancelled("AI request cancelled by user")

    @staticmethod
    def classify_exception(exc: Exception) -> AITransportError | None:
        if isinstance(exc, AITransportError):
            return exc
        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return AITransportError(str(exc), transient=True)
        if isinstance(exc, requests.HTTPError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            return AITransportError(str(exc), transient=status in _TRANSIENT_HTTP_STATUS, status_code=status)
        text = str(exc).lower()
        transient_markers = (
            "timed out",
            "timeout",
            "connection reset",
            "connection aborted",
            "connection refused",
            "temporarily unavailable",
            "server disconnected",
            "broken pipe",
            "ollama returned malformed streaming json",
        )
        if any(marker in text for marker in transient_markers):
            return AITransportError(str(exc), transient=True)
        return None

    def execute_json(
        self,
        request_raw: Callable[[], str],
        *,
        operation: str,
        model: str,
        request_meta: dict[str, Any] | None = None,
        collection_key: str | None = None,
        expected_ids: set[int] | None = None,
        required_item_fields: set[str] | None = None,
    ) -> dict[str, Any]:
        request_id = uuid.uuid4().hex[:16]
        meta = dict(request_meta or {})
        self.trace.event(
            {
                "event": "request_started",
                "request_id": request_id,
                "operation": operation,
                "model": model,
                "meta": meta,
                "transport_budget": self.retry_policy.max_attempts,
            }
        )
        started = time.time()
        circuit = self._circuit(model)
        if not circuit.before_request():
            self.trace.event({"event": "circuit_open", "request_id": request_id, "operation": operation, "model": model})
            raise AITransportError("Ollama circuit is open after repeated runtime failures", transient=True)
        with AIResourceManager.lease(
            "gpu_heavy",
            capacity=self.gpu_job_limit,
            cancel_check=self.cancel_check,
        ):
            raw = ""
            for attempt in range(1, self.retry_policy.max_attempts + 1):
                self._raise_if_cancelled()
                try:
                    raw = request_raw()
                    self.trace.event(
                        {
                            "event": "transport_ok",
                            "request_id": request_id,
                            "operation": operation,
                            "model": model,
                            "attempt": attempt,
                            "raw_chars": len(raw),
                        }
                    )
                    break
                except OperationCancelled:
                    raise
                except Exception as exc:
                    classified = self.classify_exception(exc)
                    transient = bool(classified and classified.transient)
                    self.trace.event(
                        {
                            "event": "transport_failed",
                            "request_id": request_id,
                            "operation": operation,
                            "model": model,
                            "attempt": attempt,
                            "transient": transient,
                            "error": f"{type(exc).__name__}: {exc}"[:1600],
                        }
                    )
                    if not transient or attempt >= self.retry_policy.max_attempts:
                        if transient:
                            circuit.failure()
                        self.trace.event(
                            {
                                "event": "request_failed",
                                "request_id": request_id,
                                "operation": operation,
                                "model": model,
                                "elapsed_seconds": round(time.time() - started, 3),
                                "error": f"{type(exc).__name__}: {exc}"[:1600],
                            }
                        )
                        # The pipeline owns recovery policy (warm-up, smaller
                        # batches and targeted single-item rescue). Preserve
                        # the normalized transport classification at this
                        # boundary so callers can actually enter that recovery
                        # path. Raising the original requests.ReadTimeout here
                        # used to bypass Micro AI recovery entirely.
                        if classified is not None:
                            raise classified from exc
                        raise
                    delay = self.retry_policy.delay_for(attempt)
                    end = time.time() + delay
                    while time.time() < end:
                        self._raise_if_cancelled()
                        time.sleep(min(0.2, max(0.0, end - time.time())))

            try:
                parsed = parse_json_loose(raw)
            except Exception as exc:
                self.trace.event(
                    {
                        "event": "response_format_failed",
                        "request_id": request_id,
                        "operation": operation,
                        "model": model,
                        "elapsed_seconds": round(time.time() - started, 3),
                        "error": str(exc)[:1200],
                        "raw_preview": raw[:1200],
                    }
                )
                raise AIResponseFormatError("AI response is not valid JSON after local repair", raw_preview=raw) from exc

            try:
                normalized = normalize_response_contract(
                    parsed, collection_key=collection_key, expected_ids=expected_ids, required_item_fields=required_item_fields
                )
            except AIResponseContractError as exc:
                self.trace.event(
                    {
                        "event": "response_contract_failed",
                        "request_id": request_id,
                        "operation": operation,
                        "model": model,
                        "elapsed_seconds": round(time.time() - started, 3),
                        "error": str(exc)[:1600],
                        "top_level_keys": list(parsed.keys())[:20] if isinstance(parsed, dict) else [],
                    }
                )
                raise

        normalized_from = normalized.get("_hs_normalized_from") if isinstance(normalized, dict) else None
        if normalized_from:
            self.trace.event(
                {
                    "event": "response_normalized",
                    "request_id": request_id,
                    "operation": operation,
                    "model": model,
                    "from_shape": normalized_from,
                    "collection_key": collection_key,
                }
            )
        missing_ids = normalized.get("_hs_missing_ids") if isinstance(normalized, dict) else None
        if missing_ids:
            self.trace.event(
                {
                    "event": "response_partial",
                    "request_id": request_id,
                    "operation": operation,
                    "model": model,
                    "missing_ids": list(missing_ids)[:100],
                }
            )

        self.trace.event(
            {
                "event": "request_succeeded",
                "request_id": request_id,
                "operation": operation,
                "model": model,
                "elapsed_seconds": round(time.time() - started, 3),
                "top_level_keys": list(normalized.keys())[:20] if isinstance(normalized, dict) else [],
            }
        )
        circuit.success()
        return normalized
