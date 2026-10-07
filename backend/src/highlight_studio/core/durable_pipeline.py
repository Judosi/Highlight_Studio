from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from .utils import read_json, write_json


PIPELINE_STATE_SCHEMA_VERSION = 2
STAGE_STATES = {"pending", "running", "partial", "degraded", "completed", "cancelled", "failed"}
_TERMINAL_STAGE_STATES = {"degraded", "completed", "cancelled", "failed"}


class DurablePipelineState:
    """Atomic project-wide index for resumable long-running stages.

    Schema v2 fixes a subtle lifecycle bug from 11.2.0: a stage could reach
    100% and remain ``running`` forever after the logger moved to the next
    stage.  Stage transitions now close the previous running stage
    transactionally, while interrupted jobs still recover as ``partial``.
    """

    _locks_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / "pipeline_state.json"
        key = str(self.path.resolve(strict=False))
        with self._locks_guard:
            self._lock = self._locks.setdefault(key, threading.RLock())

    def _default(self) -> dict[str, Any]:
        now = time.time()
        return {
            "schema_version": PIPELINE_STATE_SCHEMA_VERSION,
            "state": "pending",
            "stages": {},
            "events": [],
            "created_at": now,
            "updated_at": now,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            raw = read_json(self.path, None)
            if not isinstance(raw, dict):
                return self._default()
            raw.setdefault("schema_version", PIPELINE_STATE_SCHEMA_VERSION)
            raw.setdefault("state", "pending")
            raw.setdefault("stages", {})
            raw.setdefault("events", [])
            return raw

    @staticmethod
    def _normalize_state(state: str) -> str:
        aliases = {
            "queued": "pending",
            "ready": "pending",
            "done": "completed",
            "complete": "completed",
            "error": "failed",
            "interrupted": "partial",
            "cancel_requested": "cancelled",
            "cancelling": "cancelled",
        }
        normalized = aliases.get(str(state or "").strip().lower(), str(state or "").strip().lower())
        return normalized if normalized in STAGE_STATES else "pending"

    @staticmethod
    def _finish_running_row(row: dict[str, Any], now: float, *, state: str = "completed") -> None:
        row["state"] = state
        row["updated_at"] = now
        row["finished_at"] = now
        if state == "completed" and row.get("total"):
            row["current"] = int(row["total"])
            row["progress_percent"] = 100.0

    def update_stage(
        self,
        stage: str,
        *,
        state: str,
        fingerprint: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
        artifacts: list[str] | None = None,
        error_code: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = str(stage or "unknown").strip()[:120] or "unknown"
        canonical = self._normalize_state(state)
        now = time.time()
        with self._lock:
            payload = self.snapshot()
            stages = payload.setdefault("stages", {})

            # A move to another running stage means the previous linear stage
            # finished successfully.  11.2.0 left it as ``running`` forever.
            active = str(payload.get("active_stage") or "")
            if active and active != name:
                active_row = stages.get(active)
                if isinstance(active_row, dict) and active_row.get("state") == "running":
                    if canonical == "running":
                        self._finish_running_row(active_row, now, state="completed")
                    elif canonical == "failed":
                        self._finish_running_row(active_row, now, state="failed")
                    elif canonical == "cancelled":
                        self._finish_running_row(active_row, now, state="cancelled")

            previous = stages.get(name) if isinstance(stages.get(name), dict) else {}
            previous_fp = str(previous.get("fingerprint") or "")
            next_fp = str(fingerprint or previous_fp)
            if previous_fp and next_fp and previous_fp != next_fp:
                previous = {
                    "state": "pending",
                    "invalidated_at": now,
                    "invalidated_fingerprint": previous_fp,
                }

            row = dict(previous)
            row.update({"state": canonical, "updated_at": now})
            if next_fp:
                row["fingerprint"] = next_fp
            if current is not None:
                row["current"] = max(0, int(current))
            if total is not None:
                row["total"] = max(1, int(total))
            if row.get("total"):
                row["progress_percent"] = round(
                    100.0 * min(int(row.get("current") or 0), int(row["total"])) / max(1, int(row["total"])), 2
                )
            if message is not None:
                row["message"] = str(message)[:1000]
            if artifacts is not None:
                row["artifacts"] = [str(item) for item in artifacts[:100]]
            if error_code:
                row["error_code"] = str(error_code)[:160]
            if detail:
                row["detail"] = dict(detail)
            if canonical == "running":
                row.setdefault("started_at", now)
                row.pop("finished_at", None)
            elif canonical in _TERMINAL_STAGE_STATES:
                row["finished_at"] = now
                if canonical == "completed" and row.get("total"):
                    row["current"] = int(row["total"])
                    row["progress_percent"] = 100.0
            stages[name] = row

            if canonical == "running":
                payload["state"] = "running"
                payload["active_stage"] = name
            elif canonical in {"partial", "degraded", "cancelled", "failed"}:
                payload["state"] = canonical
                payload["active_stage"] = name
            elif stages and all(
                isinstance(item, dict) and item.get("state") in {"completed", "degraded"}
                for item in stages.values()
            ):
                payload["state"] = "degraded" if any(
                    item.get("state") == "degraded" for item in stages.values() if isinstance(item, dict)
                ) else "completed"
                payload.pop("active_stage", None)
            elif canonical == "completed" and payload.get("active_stage") == name:
                payload.pop("active_stage", None)

            event = {
                "at": round(now, 3),
                "stage": name,
                "state": canonical,
                "current": row.get("current"),
                "total": row.get("total"),
                "error_code": row.get("error_code"),
            }
            events = payload.setdefault("events", [])
            if not events or any(
                events[-1].get(key) != event.get(key)
                for key in ("stage", "state", "current", "total", "error_code")
            ):
                events.append(event)
                payload["events"] = events[-300:]
            payload.update({"schema_version": PIPELINE_STATE_SCHEMA_VERSION, "updated_at": now})
            write_json(self.path, payload)
            return payload

    def finalize(self, state: str, *, message: str | None = None, error_code: str | None = None) -> dict[str, Any]:
        """Finalize job-level state without leaving stale running stages behind."""
        canonical = self._normalize_state(state)
        now = time.time()
        with self._lock:
            payload = self.snapshot()
            stages = payload.setdefault("stages", {})
            active = str(payload.get("active_stage") or "")
            for stage_name, row in stages.items():
                if not isinstance(row, dict) or row.get("state") != "running":
                    continue
                if canonical == "completed":
                    self._finish_running_row(row, now, state="completed")
                elif stage_name == active:
                    target = "cancelled" if canonical == "cancelled" else ("failed" if canonical == "failed" else "partial")
                    self._finish_running_row(row, now, state=target)
                    if target == "partial":
                        row["recoverable"] = True
            payload["state"] = canonical
            payload["updated_at"] = now
            payload["schema_version"] = PIPELINE_STATE_SCHEMA_VERSION
            if canonical in _TERMINAL_STAGE_STATES:
                payload["finished_at"] = now
            if canonical != "running":
                payload.pop("active_stage", None)
            if message:
                payload["message"] = str(message)[:1000]
            if error_code:
                payload["error_code"] = str(error_code)[:160]
            write_json(self.path, payload)
            return payload

    def recover_interrupted(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            payload = self.snapshot()
            changed = False
            for row in payload.get("stages", {}).values():
                if not isinstance(row, dict) or row.get("state") != "running":
                    continue
                row["state"] = "partial" if int(row.get("current") or 0) > 0 else "pending"
                row.update({"recoverable": True, "interrupted_at": now, "updated_at": now})
                changed = True
            if changed:
                payload.update(
                    {
                        "state": "partial",
                        "recovered_at": now,
                        "updated_at": now,
                        "schema_version": PIPELINE_STATE_SCHEMA_VERSION,
                    }
                )
                payload.pop("active_stage", None)
                write_json(self.path, payload)
            return payload
