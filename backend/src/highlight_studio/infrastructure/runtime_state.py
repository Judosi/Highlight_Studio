from __future__ import annotations

import os
import platform
import time
import uuid
from pathlib import Path
from typing import Any

from ..core.settings import APP_VERSION, DATA_DIR, PROJECTS_DIR
from ..core.durable_pipeline import DurablePipelineState
from ..core.utils import read_json, write_json

SESSION_STATE_PATH = DATA_DIR / "session_state.json"
RECOVERY_REPORT_PATH = DATA_DIR / "startup_recovery.json"
ONBOARDING_PATH = DATA_DIR / "onboarding.json"
STABILITY_PATH = DATA_DIR / "stability_metrics.json"
CURRENT_PRIVACY_VERSION = "2026-07-12"
CURRENT_TERMS_VERSION = "2026-07-12"


def onboarding_status() -> dict[str, Any]:
    raw = read_json(ONBOARDING_PATH, {}) or {}
    accepted_privacy = bool(raw.get("accepted_privacy")) and raw.get("privacy_version") == CURRENT_PRIVACY_VERSION
    accepted_terms = bool(raw.get("accepted_terms")) and raw.get("terms_version") == CURRENT_TERMS_VERSION
    return {
        "ok": True,
        "completed": bool(raw.get("completed")) and accepted_privacy and accepted_terms,
        "completed_at": raw.get("completed_at"),
        "ai_mode": raw.get("ai_mode", "local"),
        "telemetry_enabled": bool(raw.get("telemetry_enabled", False)),
        "accepted_privacy": accepted_privacy,
        "accepted_terms": accepted_terms,
        "privacy_version": raw.get("privacy_version", ""),
        "terms_version": raw.get("terms_version", ""),
        "current_privacy_version": CURRENT_PRIVACY_VERSION,
        "current_terms_version": CURRENT_TERMS_VERSION,
        "app_version": APP_VERSION,
        "projects_dir": str(PROJECTS_DIR),
    }


def complete_onboarding(
    *,
    ai_mode: str = "local",
    telemetry_enabled: bool = False,
    accepted_privacy: bool = False,
    accepted_terms: bool = False,
) -> dict[str, Any]:
    if not accepted_privacy or not accepted_terms:
        raise ValueError("Для продолжения нужно принять политику конфиденциальности и условия использования.")
    normalized_ai_mode = ai_mode if ai_mode in {"local", "cloud_later"} else "local"
    payload = {
        "completed": True,
        "completed_at": time.time(),
        "ai_mode": normalized_ai_mode,
        "telemetry_enabled": bool(telemetry_enabled),
        "accepted_privacy": True,
        "accepted_terms": True,
        "privacy_version": CURRENT_PRIVACY_VERSION,
        "terms_version": CURRENT_TERMS_VERSION,
        "app_version": APP_VERSION,
    }
    write_json(ONBOARDING_PATH, payload)
    return onboarding_status()


def reset_onboarding() -> dict[str, Any]:
    if ONBOARDING_PATH.exists():
        ONBOARDING_PATH.unlink()
    return onboarding_status()


def _recover_project_status(project_dir: Path) -> dict[str, Any] | None:
    status_path = project_dir / "status.json"
    status = read_json(status_path, None)
    if not isinstance(status, dict):
        return None
    state = str(status.get("state") or "").lower()
    if state not in {"running", "queued", "cancelling", "cancel_requested"}:
        return None

    previous = dict(status)
    status.update(
        {
            "state": "interrupted",
            "message": "Предыдущая задача была прервана закрытием приложения. Можно продолжить с последнего сохранённого этапа.",
            "interrupted_at": time.time(),
            "updated_at": time.time(),
            "recoverable": True,
            "previous_state": previous.get("state"),
            "previous_stage": previous.get("stage"),
        }
    )
    write_json(status_path, status)
    pipeline_state = DurablePipelineState(project_dir).recover_interrupted()
    return {
        "project_id": project_dir.name,
        "previous_state": previous.get("state"),
        "stage": previous.get("stage"),
        "recoverable": True,
        "pipeline_state": pipeline_state,
    }


def begin_runtime_session(projects_dir: Path = PROJECTS_DIR) -> dict[str, Any]:
    previous = read_json(SESSION_STATE_PATH, {}) or {}
    previous_unclean = bool(previous and not previous.get("clean_shutdown", True))
    stability = read_json(STABILITY_PATH, {}) or {}
    stability["production_sessions"] = int(stability.get("production_sessions") or 0) + 1
    if previous_unclean:
        stability["crashed_sessions"] = int(stability.get("crashed_sessions") or 0) + 1
    stability["last_started_at"] = time.time()
    stability["app_version"] = APP_VERSION
    write_json(STABILITY_PATH, stability)
    recovered: list[dict[str, Any]] = []
    if projects_dir.exists():
        for item in projects_dir.iterdir():
            if item.is_dir() and (item / "project.json").exists():
                result = _recover_project_status(item)
                if result:
                    recovered.append(result)

    report = {
        "ok": True,
        "previous_unclean_shutdown": previous_unclean,
        "recovered_projects": recovered,
        "recovered_count": len(recovered),
        "checked_at": time.time(),
        "app_version": APP_VERSION,
    }
    write_json(RECOVERY_REPORT_PATH, report)
    write_json(
        SESSION_STATE_PATH,
        {
            "session_id": uuid.uuid4().hex,
            "started_at": time.time(),
            "clean_shutdown": False,
            "pid": os.getpid(),
            "platform": platform.platform(),
            "app_version": APP_VERSION,
        },
    )
    return report


def mark_clean_shutdown() -> None:
    state = read_json(SESSION_STATE_PATH, {}) or {}
    state.update({"clean_shutdown": True, "finished_at": time.time(), "app_version": APP_VERSION})
    write_json(SESSION_STATE_PATH, state)
    stability = read_json(STABILITY_PATH, {}) or {}
    stability["clean_shutdowns"] = int(stability.get("clean_shutdowns") or 0) + 1
    stability["last_clean_shutdown_at"] = time.time()
    stability["app_version"] = APP_VERSION
    write_json(STABILITY_PATH, stability)


def stability_metrics() -> dict[str, Any]:
    raw = read_json(STABILITY_PATH, {}) or {}
    sessions = max(0, int(raw.get("production_sessions") or 0))
    crashes = max(0, min(sessions, int(raw.get("crashed_sessions") or 0)))
    crash_free = round(((sessions - crashes) / sessions) * 100, 2) if sessions else 0.0
    return {
        "ok": True,
        "production_sessions": sessions,
        "crashed_sessions": crashes,
        "clean_shutdowns": max(0, int(raw.get("clean_shutdowns") or 0)),
        "crash_free_percent": crash_free,
        "app_version": APP_VERSION,
        "note": "Метрика локальная и не содержит содержимое проектов.",
    }


def startup_recovery_status() -> dict[str, Any]:
    return read_json(
        RECOVERY_REPORT_PATH,
        {
            "ok": True,
            "previous_unclean_shutdown": False,
            "recovered_projects": [],
            "recovered_count": 0,
            "app_version": APP_VERSION,
        },
    )
