from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from ..core.settings import APP_VERSION, DATA_DIR
from ..core.utils import read_json
from .paid_beta import privacy_status

CRASH_QUEUE_PATH = DATA_DIR / "crash_reports.jsonl"
_LOCK = threading.Lock()
_MAX_LOCAL_REPORTS = 200
_MAX_MESSAGE = 1200
_MAX_CONTEXT_VALUE = 240
_ALLOWED_SOURCES = {"backend", "worker", "desktop", "frontend"}
_ALLOWED_CONTEXT_KEYS = {
    "method",
    "route",
    "job_kind",
    "stage",
    "error_code",
    "platform",
    "arch",
    "app_version",
    "result",
}
_SECRET_RE = re.compile(r"(?i)(token|secret|password|cookie|authorization|api[_-]?key)\s*[:=]\s*[^\s,;]+")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\r\n\t<>|\"?*]+)")
_POSIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:home|Users|mnt|media|Volumes|private|tmp)/[^\s\r\n]+")


def _safe_https_url(value: str) -> str:
    candidate = str(value or "").strip().rstrip("/")
    try:
        parsed = urlparse(candidate)
    except Exception:
        return ""
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return ""
    return candidate


def _redact(value: str) -> str:
    text = str(value or "")[:_MAX_MESSAGE]
    text = _SECRET_RE.sub(r"\1=<REDACTED>", text)
    text = _EMAIL_RE.sub("<EMAIL_REDACTED>", text)
    text = _WINDOWS_PATH_RE.sub("<LOCAL_PATH>", text)
    text = _POSIX_PATH_RE.sub("<LOCAL_PATH>", text)
    home = str(Path.home())
    if home:
        text = text.replace(home, "<USER_HOME>")
    return text


def _append(payload: dict[str, Any]) -> None:
    CRASH_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        rows: list[str] = []
        if CRASH_QUEUE_PATH.exists():
            rows = CRASH_QUEUE_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-(_MAX_LOCAL_REPORTS - 1) :]
        rows.append(json.dumps(payload, ensure_ascii=False, allow_nan=False))
        temp = CRASH_QUEUE_PATH.with_suffix(".jsonl.tmp")
        temp.write_text("\n".join(rows) + "\n", encoding="utf-8")
        temp.replace(CRASH_QUEUE_PATH)


def _rows() -> list[dict[str, Any]]:
    if not CRASH_QUEUE_PATH.exists():
        return []
    with _LOCK:
        lines = CRASH_QUEUE_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    result: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def record_crash(
    *,
    source: str,
    error_type: str,
    message: str,
    diagnostic_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_source = source if source in _ALLOWED_SOURCES else "backend"
    safe_context: dict[str, Any] = {}
    for key, value in (context or {}).items():
        if key not in _ALLOWED_CONTEXT_KEYS:
            continue
        if isinstance(value, (bool, int, float)) or value is None:
            safe_context[key] = value
        else:
            safe_context[key] = _redact(str(value))[:_MAX_CONTEXT_VALUE]
    crash_id = diagnostic_id or uuid.uuid4().hex[:12]
    payload = {
        "crash_id": crash_id,
        "source": safe_source,
        "error_type": _redact(error_type)[:160],
        "message": _redact(message),
        "context": safe_context,
        "app_version": APP_VERSION,
        "created_at": time.time(),
        "uploaded": False,
    }
    _append(payload)
    return payload


def crash_status() -> dict[str, Any]:
    rows = _rows()
    prefs = privacy_status()
    endpoint = _safe_https_url(os.environ.get("HIGHLIGHT_STUDIO_CRASH_REPORT_URL", ""))
    return {
        "ok": True,
        "queued_reports": len(rows),
        "crash_reports_enabled": bool(prefs.get("crash_reports_enabled")),
        "network_upload_configured": bool(endpoint),
        "latest_crash_id": str(rows[-1].get("crash_id") or "") if rows else "",
        "note": "Локальные отчёты не содержат видео, транскрипты, токены или исходные пути.",
    }


def clear_crash_reports() -> dict[str, Any]:
    with _LOCK:
        CRASH_QUEUE_PATH.unlink(missing_ok=True)
    return {**crash_status(), "cleared": True}


def flush_crash_reports(timeout: int = 15) -> dict[str, Any]:
    status = crash_status()
    if not status.get("crash_reports_enabled"):
        return {**status, "uploaded": 0, "message": "Отправка crash reports отключена пользователем."}
    endpoint = _safe_https_url(os.environ.get("HIGHLIGHT_STUDIO_CRASH_REPORT_URL", ""))
    if not endpoint:
        return {**status, "uploaded": 0, "message": "HTTPS-сервер crash reports не настроен; отчёты остаются локально."}
    reports = _rows()[:100]
    if not reports:
        return {**status, "uploaded": 0, "message": "Нет crash reports для отправки."}
    install = read_json(DATA_DIR / "anonymous_installation.json", {}) or {}
    installation_id = str(install.get("installation_id") or "")
    installation_hash = hashlib.sha256(installation_id.encode()).hexdigest()[:24] if installation_id else ""
    try:
        response = requests.post(
            endpoint,
            json={"installation_id": installation_hash, "app_version": APP_VERSION, "reports": reports},
            timeout=timeout,
        )
        response.raise_for_status()
    except Exception as exc:
        return {**status, "uploaded": 0, "message": f"Отправка не удалась: {_redact(str(exc))[:240]}"}
    uploaded_ids = {str(item.get("crash_id") or "") for item in reports}
    remaining = [item for item in _rows() if str(item.get("crash_id") or "") not in uploaded_ids]
    with _LOCK:
        if remaining:
            temp = CRASH_QUEUE_PATH.with_suffix(".jsonl.tmp")
            temp.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in remaining) + "\n", encoding="utf-8")
            temp.replace(CRASH_QUEUE_PATH)
        else:
            CRASH_QUEUE_PATH.unlink(missing_ok=True)
    return {**crash_status(), "uploaded": len(reports), "message": "Анонимные crash reports отправлены."}
