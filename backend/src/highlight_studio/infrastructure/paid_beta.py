from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from ..core.settings import APP_VERSION, DATA_DIR, PROJECTS_DIR
from ..core.utils import read_json, write_json
from .runtime_state import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION, onboarding_status

TELEMETRY_QUEUE_PATH = DATA_DIR / "telemetry_queue.jsonl"
BETA_FEEDBACK_PATH = DATA_DIR / "beta_feedback.jsonl"
PRIVACY_PATH = DATA_DIR / "privacy_preferences.json"
IDENTITY_PATH = DATA_DIR / "anonymous_installation.json"
_LOCK = threading.Lock()
ALLOWED_EVENTS = {
    "app_started",
    "job_started",
    "job_completed",
    "job_failed",
    "render_completed",
    "analysis_completed",
    "license_activated",
    "support_bundle_created",
}
ALLOWED_PROPERTY_KEYS = {"kind", "state", "duration_bucket", "error_code", "version", "platform", "result"}


def _safe_https_url(value: str) -> str:
    candidate = str(value or "").strip().rstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return ""
    return candidate


def _installation_id() -> str:
    raw = read_json(IDENTITY_PATH, {}) or {}
    value = str(raw.get("installation_id") or "") if isinstance(raw, dict) else ""
    if not value:
        value = uuid.uuid4().hex
        write_json(IDENTITY_PATH, {"installation_id": value, "created_at": time.time()})
    return value


def privacy_status() -> dict[str, Any]:
    raw = read_json(PRIVACY_PATH, {}) or {}
    onboarding = onboarding_status()
    return {
        "ok": True,
        "telemetry_enabled": bool(raw.get("telemetry_enabled", onboarding.get("telemetry_enabled", False))),
        "crash_reports_enabled": bool(raw.get("crash_reports_enabled", False)),
        "privacy_version": str(raw.get("privacy_version") or onboarding.get("privacy_version") or ""),
        "terms_version": str(raw.get("terms_version") or onboarding.get("terms_version") or ""),
        "accepted_privacy": bool(raw.get("accepted_privacy", onboarding.get("accepted_privacy", False))),
        "accepted_terms": bool(raw.get("accepted_terms", onboarding.get("accepted_terms", False))),
        "current_privacy_version": CURRENT_PRIVACY_VERSION,
        "current_terms_version": CURRENT_TERMS_VERSION,
        "network_upload_configured": bool(_safe_https_url(os.environ.get("HIGHLIGHT_STUDIO_TELEMETRY_URL", ""))),
    }


def save_privacy_preferences(
    *,
    telemetry_enabled: bool,
    crash_reports_enabled: bool,
    accepted_privacy: bool,
    accepted_terms: bool,
) -> dict[str, Any]:
    if (telemetry_enabled or crash_reports_enabled) and not (accepted_privacy and accepted_terms):
        raise ValueError("Для сетевой диагностики нужно принять политику конфиденциальности и условия использования.")
    payload = {
        "telemetry_enabled": bool(telemetry_enabled),
        "crash_reports_enabled": bool(crash_reports_enabled),
        "accepted_privacy": bool(accepted_privacy),
        "accepted_terms": bool(accepted_terms),
        "privacy_version": CURRENT_PRIVACY_VERSION if accepted_privacy else "",
        "terms_version": CURRENT_TERMS_VERSION if accepted_terms else "",
        "updated_at": time.time(),
    }
    write_json(PRIVACY_PATH, payload)
    if not telemetry_enabled:
        TELEMETRY_QUEUE_PATH.unlink(missing_ok=True)
    return privacy_status()


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n")


def record_event(name: str, properties: dict[str, Any] | None = None) -> bool:
    if name not in ALLOWED_EVENTS or not privacy_status().get("telemetry_enabled"):
        return False
    clean: dict[str, Any] = {}
    for key, value in (properties or {}).items():
        if key not in ALLOWED_PROPERTY_KEYS:
            continue
        if isinstance(value, (bool, int, float)) or value is None:
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = value[:120].replace("\\", "_").replace("/", "_")
    _append_jsonl(
        TELEMETRY_QUEUE_PATH,
        {
            "event": name,
            "properties": clean,
            "installation_id": hashlib.sha256(_installation_id().encode()).hexdigest()[:24],
            "app_version": APP_VERSION,
            "created_at": time.time(),
        },
    )
    return True


def _read_jsonl(path: Path, limit: int = 500) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with _LOCK:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        except json.JSONDecodeError:
            continue
    return rows


def _telemetry_batch(limit: int = 500) -> tuple[list[str], list[dict[str, Any]]]:
    if not TELEMETRY_QUEUE_PATH.exists():
        return [], []
    with _LOCK:
        raw_lines = TELEMETRY_QUEUE_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    selected_lines: list[str] = []
    events: list[dict[str, Any]] = []
    for line in raw_lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        selected_lines.append(line)
        events.append(item)
        if len(events) >= limit:
            break
    return selected_lines, events


def _remove_uploaded_telemetry_lines(uploaded_lines: list[str]) -> None:
    if not uploaded_lines or not TELEMETRY_QUEUE_PATH.exists():
        return
    with _LOCK:
        current = TELEMETRY_QUEUE_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
        remaining = list(current)
        for expected in uploaded_lines:
            try:
                index = remaining.index(expected)
            except ValueError:
                continue
            remaining.pop(index)
        if remaining:
            temporary = TELEMETRY_QUEUE_PATH.with_suffix(TELEMETRY_QUEUE_PATH.suffix + ".tmp")
            temporary.write_text("\n".join(remaining) + "\n", encoding="utf-8")
            temporary.replace(TELEMETRY_QUEUE_PATH)
        else:
            TELEMETRY_QUEUE_PATH.unlink(missing_ok=True)


def telemetry_status() -> dict[str, Any]:
    return {
        **privacy_status(),
        "queued_events": len(_read_jsonl(TELEMETRY_QUEUE_PATH, 100_000)),
    }


def flush_telemetry(timeout: int = 15) -> dict[str, Any]:
    status = telemetry_status()
    if not status.get("telemetry_enabled"):
        return {**status, "uploaded": 0, "message": "Телеметрия отключена."}
    endpoint = _safe_https_url(os.environ.get("HIGHLIGHT_STUDIO_TELEMETRY_URL", ""))
    batch_lines, events = _telemetry_batch(500)
    if not endpoint:
        return {**status, "uploaded": 0, "message": "Сервер телеметрии не настроен; события остаются только локально."}
    if not events:
        return {**status, "uploaded": 0, "message": "Нет событий для отправки."}
    try:
        response = requests.post(endpoint, json={"events": events}, timeout=timeout)
        response.raise_for_status()
    except Exception as exc:
        return {**status, "uploaded": 0, "message": f"Отправка не удалась: {str(exc)[:240]}"}
    _remove_uploaded_telemetry_lines(batch_lines)
    return {**telemetry_status(), "uploaded": len(events), "message": "Анонимные технические события отправлены."}


def commerce_config() -> dict[str, Any]:
    fields = {
        "checkout_url": os.environ.get("HIGHLIGHT_STUDIO_CHECKOUT_URL", ""),
        "account_url": os.environ.get("HIGHLIGHT_STUDIO_ACCOUNT_URL", ""),
        "support_url": os.environ.get("HIGHLIGHT_STUDIO_SUPPORT_URL", ""),
        "privacy_url": os.environ.get("HIGHLIGHT_STUDIO_PRIVACY_URL", ""),
        "terms_url": os.environ.get("HIGHLIGHT_STUDIO_TERMS_URL", ""),
    }
    clean = {key: _safe_https_url(value) for key, value in fields.items()}
    return {"ok": True, "configured": bool(clean["checkout_url"]), **clean}


def submit_beta_feedback(
    *,
    category: str,
    rating: int,
    message: str,
    allow_contact: bool = False,
    email: str = "",
) -> dict[str, Any]:
    category = category if category in {"quality", "bug", "speed", "usability", "idea", "other"} else "other"
    message = str(message or "").strip()[:2000]
    email = str(email or "").strip()[:320] if allow_contact else ""
    if not message:
        raise ValueError("Напиши сообщение перед отправкой.")
    if allow_contact and email and ("@" not in email or email.startswith("@") or email.endswith("@")):
        raise ValueError("Проверь email для обратной связи.")
    payload = {
        "feedback_id": uuid.uuid4().hex,
        "category": category,
        "rating": max(1, min(5, int(rating))),
        "message": message,
        "allow_contact": bool(allow_contact),
        "email": email,
        "app_version": APP_VERSION,
        "created_at": time.time(),
        "uploaded": False,
    }
    endpoint = _safe_https_url(os.environ.get("HIGHLIGHT_STUDIO_FEEDBACK_URL", ""))
    if endpoint:
        try:
            response = requests.post(endpoint, json=payload, timeout=15)
            response.raise_for_status()
            payload["uploaded"] = True
        except Exception:
            payload["uploaded"] = False
    _append_jsonl(BETA_FEEDBACK_PATH, payload)
    return {
        "ok": True,
        "feedback_id": payload["feedback_id"],
        "uploaded": payload["uploaded"],
        "message": "Спасибо. Отзыв сохранён локально." + (" Он также отправлен команде beta." if payload["uploaded"] else ""),
    }


def beta_metrics(projects_dir: Path = PROJECTS_DIR) -> dict[str, Any]:
    projects = completed = failed = interrupted = candidates = segments = feedback_total = kept = removed = 0
    render_outputs = 0
    if projects_dir.exists():
        for directory in projects_dir.iterdir():
            if not directory.is_dir() or not (directory / "project.json").exists():
                continue
            projects += 1
            status = read_json(directory / "status.json", {}) or {}
            state = str(status.get("state") or "").lower()
            completed += int(state == "done")
            failed += int(state == "error")
            interrupted += int(state == "interrupted")
            candidate_data = read_json(directory / "candidates.json", []) or []
            segment_data = read_json(directory / "segments.json", []) or []
            candidates += len(candidate_data) if isinstance(candidate_data, list) else 0
            segments += len(segment_data) if isinstance(segment_data, list) else 0
            prefs = read_json(directory / "user_preferences.json", {}) or {}
            items = prefs.get("feedback", []) if isinstance(prefs, dict) else []
            if isinstance(items, list):
                feedback_total += len(items)
                for item in items:
                    label = str((item or {}).get("label") or "").lower() if isinstance(item, dict) else ""
                    kept += int(label in {"keep", "kept", "good", "accepted"})
                    removed += int(label in {"remove", "removed", "bad", "rejected"})
            render_outputs += int((directory / "outputs" / "highlight_final.mp4").exists())
    retention_rate = round((segments / candidates) * 100, 1) if candidates else 0.0
    feedback_acceptance = round((kept / (kept + removed)) * 100, 1) if kept + removed else None
    return {
        "ok": True,
        "projects": projects,
        "completed_jobs": completed,
        "failed_jobs": failed,
        "interrupted_jobs": interrupted,
        "render_outputs": render_outputs,
        "candidates": candidates,
        "final_segments": segments,
        "candidate_to_final_percent": retention_rate,
        "feedback_items": feedback_total,
        "feedback_acceptance_percent": feedback_acceptance,
        "local_beta_feedback": len(_read_jsonl(BETA_FEEDBACK_PATH, 10000)),
        "note": "Метрики агрегированы локально и не содержат видео, транскрипты или названия проектов.",
    }
