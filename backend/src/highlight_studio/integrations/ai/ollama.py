from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Callable
import time

import requests

from ...core.settings import DEFAULT_OLLAMA_URL, DEFAULT_TEXT_MODEL, DEFAULT_VISION_MODEL
from ...core.utils import OperationCancelled
from .runtime import AIExecutionController, AIResourceManager, AITransportError


class OllamaClient:
    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        keep_alive: str = "5m",
        num_ctx: int = 4096,
        cancel_check: Callable[[], bool] | None = None,
        settings: dict[str, Any] | None = None,
        project_dir: Path | None = None,
    ):
        self.base_url = (base_url or DEFAULT_OLLAMA_URL).rstrip("/")
        self.keep_alive = str(keep_alive or "5m")
        self.cancel_check = cancel_check
        self.settings = dict(settings or {})
        self.project_dir = Path(project_dir) if project_dir else None
        self.runtime = AIExecutionController(settings=self.settings, project_dir=self.project_dir, cancel_check=cancel_check)
        try:
            self.num_ctx = max(2048, min(16384, int(num_ctx or 4096)))
        except Exception:
            self.num_ctx = 4096

    def _raise_if_cancelled(self) -> None:
        if self.cancel_check and self.cancel_check():
            raise OperationCancelled("AI request cancelled by user")

    def tags(self) -> list[str]:
        self._raise_if_cancelled()
        r = requests.get(f"{self.base_url}/api/tags", timeout=(5, 10))
        r.raise_for_status()
        self._raise_if_cancelled()
        return [m.get("name", "") for m in r.json().get("models", [])]

    def check(self, text_model: str = DEFAULT_TEXT_MODEL, vision_model: str = DEFAULT_VISION_MODEL) -> dict[str, Any]:
        try:
            names = self.tags()
            return {
                "ok": True,
                "models": names,
                "text_model_installed": any(n == text_model or n.startswith(text_model + ":") for n in names),
                "vision_model_installed": any(n == vision_model or n.startswith(vision_model + ":") for n in names),
            }
        except OperationCancelled:
            raise
        except Exception as exc:
            return {"ok": False, "error": str(exc), "models": []}

    def _post_generate(
        self,
        payload: dict[str, Any],
        timeout: int,
        *,
        activity_callback: Callable[[float, int], None] | None = None,
    ) -> str:
        """Run Ollama as a cancellable stream with stall and wall-clock guards.

        ``requests`` read timeouts only detect a socket that stops producing
        bytes. A model can otherwise keep emitting tiny chunks for many minutes
        and never time out. 10.15.14 therefore has a *hard* wall-clock deadline
        as well. A hard deadline is non-transient: callers should split/recover
        the semantic batch instead of repeating the same huge prompt.
        """
        self._raise_if_cancelled()
        request_payload = dict(payload)
        request_payload["stream"] = True
        requested_timeout = max(30, int(timeout or 240))
        configured_hard = max(90, int(self.settings.get("ai_request_hard_timeout", 360) or 360))
        hard_timeout = min(requested_timeout, configured_hard)
        # Slow local GPUs can keep producing a healthy stream for >6 minutes.
        # The base hard deadline protects requests that never become useful,
        # while an actively progressing stream may continue to a larger bounded
        # deadline (never beyond the caller's timeout).
        configured_active_hard = max(
            configured_hard,
            int(self.settings.get("ai_request_active_hard_timeout", 900) or 900),
        )
        active_hard_timeout = min(requested_timeout, configured_active_hard)
        ttft_timeout = max(30, min(300, int(self.settings.get("ai_ttft_timeout", 180) or 180)))
        stall_timeout = max(10, min(180, int(self.settings.get("ai_stream_stall_timeout", 60) or 60)))
        chunks: list[str] = []
        received_chars = 0
        started = time.monotonic()
        last_callback = 0.0
        useful_output_started = False
        stream_completed = False
        try:
            with requests.post(
                f"{self.base_url}/api/generate",
                json=request_payload,
                stream=True,
                timeout=(10, max(ttft_timeout, stall_timeout)),
            ) as r:
                r.raise_for_status()
                for raw_line in r.iter_lines(decode_unicode=True):
                    self._raise_if_cancelled()
                    elapsed = time.monotonic() - started
                    # A line arriving now proves the stream is still alive.
                    # If useful output has already started, allow it to continue
                    # up to the active deadline; otherwise keep the shorter hard
                    # cutoff. requests' read timeout still catches true stalls.
                    effective_hard = active_hard_timeout if received_chars > 0 else hard_timeout
                    if elapsed >= effective_hard:
                        raise AITransportError(
                            f"Ollama request exceeded hard deadline {effective_hard}s",
                            transient=False,
                        )
                    if not raw_line:
                        continue
                    try:
                        item = json.loads(raw_line)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError("Ollama returned malformed streaming JSON") from exc
                    if item.get("error"):
                        raise AITransportError(str(item.get("error")), transient=False)
                    piece = str(item.get("response") or "")
                    if piece and not useful_output_started:
                        useful_output_started = True
                        self._set_response_read_timeout(r, stall_timeout)
                    chunks.append(piece)
                    received_chars += len(piece)
                    if activity_callback and elapsed - last_callback >= 12.0:
                        last_callback = elapsed
                        try:
                            activity_callback(elapsed, received_chars)
                        except Exception:
                            pass
                    if item.get("done") is True:
                        stream_completed = True
                        if item.get("done_reason") == "length":
                            raise AITransportError("Ollama response truncated at output token limit", transient=False)
                        break
        except OperationCancelled:
            raise
        except AITransportError:
            raise
        except requests.Timeout as exc:
            self._raise_if_cancelled()
            raise AITransportError(
                (
                    f"Ollama stream stalled for {stall_timeout}s after output started"
                    if useful_output_started
                    else f"Ollama produced no first token within {ttft_timeout}s"
                ),
                transient=True,
            ) from exc
        self._raise_if_cancelled()
        if not stream_completed:
            raise AITransportError("Ollama stream incomplete: missing completion marker", transient=True)
        return "".join(chunks)

    @staticmethod
    def _set_response_read_timeout(response: requests.Response, seconds: int) -> None:
        """Best-effort switch from model-load TTFT budget to stream stall budget."""
        try:
            raw = response.raw
            sock = getattr(getattr(getattr(raw, "_fp", None), "fp", None), "raw", None)
            sock = getattr(sock, "_sock", sock)
            if sock and hasattr(sock, "settimeout"):
                sock.settimeout(float(seconds))
        except Exception:
            pass

    def warmup(self, model: str = DEFAULT_TEXT_MODEL, timeout: int = 120) -> dict[str, Any]:
        """Load a model before the first real batch without changing its output.

        Ollama accepts an empty prompt as a load/keep-alive request.  This moves
        model loading out of the first scored batch (which in 10.15.6 frequently
        appeared as attempt 2/5 or 3/5 on low-VRAM machines).
        """
        self._raise_if_cancelled()
        started = __import__("time").time()
        payload = {
            "model": model or DEFAULT_TEXT_MODEL,
            "prompt": "",
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx},
        }
        try:
            with AIResourceManager.lease("gpu_heavy", capacity=self.runtime.gpu_job_limit, cancel_check=self.cancel_check):
                r = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=(10, max(15, int(timeout))))
                r.raise_for_status()
                self._raise_if_cancelled()
            self.runtime.record_runtime_health(model, True)
            return {"ok": True, "model": model, "elapsed_seconds": round(__import__("time").time() - started, 3)}
        except OperationCancelled:
            raise
        except Exception as exc:
            self.runtime.record_runtime_health(model, False)
            return {"ok": False, "model": model, "elapsed_seconds": round(__import__("time").time() - started, 3), "error": str(exc)}

    def unload(self, model: str = DEFAULT_TEXT_MODEL, timeout: int = 30) -> dict[str, Any]:
        """Ask Ollama to release a model after a long AI stage.

        This is a memory-pressure optimization only; no analysis result changes.
        Later AI stages transparently load the same model again when required.
        """
        self._raise_if_cancelled()
        started = __import__("time").time()
        payload = {"model": model or DEFAULT_TEXT_MODEL, "prompt": "", "stream": False, "keep_alive": 0}
        try:
            with AIResourceManager.lease("gpu_heavy", capacity=self.runtime.gpu_job_limit, cancel_check=self.cancel_check):
                r = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=(10, max(10, int(timeout))))
                r.raise_for_status()
                self._raise_if_cancelled()
            return {"ok": True, "model": model, "elapsed_seconds": round(__import__("time").time() - started, 3)}
        except OperationCancelled:
            raise
        except Exception as exc:
            return {"ok": False, "model": model, "elapsed_seconds": round(__import__("time").time() - started, 3), "error": str(exc)}

    def _json_payload(self, prompt: str, model: str, *, temperature: float = 0.0) -> dict[str, Any]:
        return {
            "model": model or DEFAULT_TEXT_MODEL,
            "prompt": prompt + "\n\nОтветь СТРОГО валидным JSON без markdown, без комментариев, без текста вокруг JSON.",
            "format": "json",
            "stream": True,
            "keep_alive": self.keep_alive,
            # Qwen3 and other reasoning-capable Ollama models can spend most of
            # a low-end GPU/CPU request budget in hidden thinking.  Structured
            # scoring/JSON operations do not need that mode.  Disable it by
            # default for predictable latency; users may explicitly opt in.
            "think": bool(self.settings.get("ollama_think", False)),
            "options": {
                "temperature": temperature,
                "num_ctx": self.num_ctx,
                "repeat_penalty": 1.05,
            },
        }

    def generate_json(
        self,
        prompt: str,
        model: str = DEFAULT_TEXT_MODEL,
        timeout: int = 240,
        *,
        operation: str = "text_json",
        trace_context: dict[str, Any] | None = None,
        collection_key: str | None = None,
        expected_ids: set[int] | None = None,
        required_item_fields: set[str] | None = None,
        activity_callback: Callable[[float, int], None] | None = None,
    ) -> dict[str, Any]:
        """Generate structured JSON through the central AI runtime.

        JSON repair is local/deterministic first.  A malformed response is not
        silently sent back to Qwen for a second full inference; the caller may
        decide whether the *semantic* operation should be retried.
        """
        model = model or DEFAULT_TEXT_MODEL
        payload = self._json_payload(prompt, model, temperature=0.0)
        return self.runtime.execute_json(
            lambda: self._post_generate(payload, timeout, activity_callback=activity_callback),
            operation=operation,
            model=model,
            request_meta={
                "prompt_chars": len(prompt),
                "hard_timeout": min(max(30, int(timeout or 240)), max(90, int(self.settings.get("ai_request_hard_timeout", 360) or 360))),
                "active_hard_timeout": min(
                    max(30, int(timeout or 240)),
                    max(
                        max(90, int(self.settings.get("ai_request_hard_timeout", 360) or 360)),
                        int(self.settings.get("ai_request_active_hard_timeout", 900) or 900),
                    ),
                ),
                "think": bool(self.settings.get("ollama_think", False)),
                **(trace_context or {}),
            },
            collection_key=collection_key,
            expected_ids=set(expected_ids) if expected_ids is not None else None,
            required_item_fields=set(required_item_fields or set()),
        )

    def generate_json_with_images(
        self, prompt: str, image_paths: list[Path], model: str = DEFAULT_VISION_MODEL, timeout: int = 300
    ) -> dict[str, Any]:
        images = []
        for p in image_paths[:6]:
            self._raise_if_cancelled()
            try:
                images.append(base64.b64encode(p.read_bytes()).decode("ascii"))
            except Exception:
                pass
        payload = {
            "model": model or DEFAULT_VISION_MODEL,
            "prompt": prompt + "\n\nОтветь строго валидным JSON без markdown.",
            "images": images,
            "format": "json",
            "stream": True,
            "keep_alive": self.keep_alive,
            "think": bool(self.settings.get("ollama_think", False)),
            "options": {"temperature": 0.0, "num_ctx": self.num_ctx, "repeat_penalty": 1.05},
        }
        return self.runtime.execute_json(
            lambda: self._post_generate(payload, timeout),
            operation="vision_json",
            model=model or DEFAULT_VISION_MODEL,
            request_meta={"prompt_chars": len(prompt), "images": len(images)},
        )
