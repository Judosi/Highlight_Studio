from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from ..core.settings import APP_ROOT, APP_VERSION, DATA_DIR, LOGS_DIR, PROJECTS_DIR
from .migrations import migration_status
from .runtime_state import onboarding_status, startup_recovery_status

SUPPORT_DIR = DATA_DIR / "support"
SENSITIVE_RE = re.compile(r"(?i)(token|secret|password|authorization|cookie|api[_-]?key)\s*[:=]\s*[^\s,;]+")
WINDOWS_PATH_RE = re.compile(r'(?i)(?<![A-Za-z0-9])(?:[A-Z]:\\|\\\\)[^\r\n\t"<>|]+')
POSIX_PATH_RE = re.compile(r"(?<![:A-Za-z0-9])/(?:home|Users|mnt|media|tmp|var|opt|srv|Volumes)/[^\s,;)}\]]+")
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
SENSITIVE_KEYS = {"token", "secret", "password", "authorization", "cookie", "cookies", "api_key", "openai_api_key"}
# Headers may contain spaces (Bearer/Basic) and JSON keys are quoted. The old
# single-token expression left the actual bearer credential in support logs.
AUTH_HEADER_RE = re.compile(r'''(?im)(["']?(?:authorization|proxy-authorization|cookie|set-cookie)["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n]+)''')
SECRET_VALUE_RE = re.compile(r'''(?i)(["']?[\w-]*(?:token|secret|password|api[_-]?key)["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;&}]+)''')


def _redact_text(value: str) -> str:
    text = value.replace(str(Path.home()), "<USER_HOME>")
    text = text.replace(str(DATA_DIR), "<DATA_DIR>")
    text = text.replace(str(PROJECTS_DIR), "<PROJECTS_DIR>")
    text = text.replace(str(APP_ROOT), "<APP_ROOT>")
    text = AUTH_HEADER_RE.sub(lambda match: match.group(1) + "<REDACTED>", text)
    text = SECRET_VALUE_RE.sub(lambda match: match.group(1) + "<REDACTED>", text)
    text = SENSITIVE_RE.sub(lambda match: f"{match.group(1)}=<REDACTED>", text)
    text = WINDOWS_PATH_RE.sub("<LOCAL_PATH>", text)
    text = POSIX_PATH_RE.sub("<LOCAL_PATH>", text)
    return EMAIL_RE.sub("<EMAIL_REDACTED>", text)


def redact_text(value: str) -> str:
    """Redact secrets and local identifiers before text leaves the server."""
    return _redact_text(value)


def _tail(path: Path, max_bytes: int = 256_000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        data = handle.read(max_bytes)
    return _redact_text(data.decode("utf-8", errors="replace"))


def _sanitize_value(value: Any, key: str = "") -> Any:
    normalized_key = key.lower().replace("-", "_")
    if normalized_key in SENSITIVE_KEYS or normalized_key.replace("_", "").endswith(("token", "secret", "password", "apikey")):
        return "<REDACTED>"
    if isinstance(value, dict):
        return {str(item_key): _sanitize_value(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, Path):
        return _redact_text(str(value))
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _safe_json(value: Any) -> str:
    return json.dumps(_sanitize_value(value), ensure_ascii=False, indent=2, default=str)


def build_support_bundle(system_check: dict[str, Any] | None = None) -> Path:
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(SUPPORT_DIR.glob("highlight-studio-support-*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in existing[9:]:
        stale.unlink(missing_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    output = SUPPORT_DIR / f"highlight-studio-support-{timestamp}.zip"

    summary = {
        "app_version": APP_VERSION,
        "created_at": time.time(),
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "projects_count": sum(1 for item in PROJECTS_DIR.iterdir() if item.is_dir()) if PROJECTS_DIR.exists() else 0,
        "paths": {
            "app_root": "<APP_ROOT>",
            "data_dir": "<DATA_DIR>",
            "projects_dir": "<PROJECTS_DIR>",
        },
        "privacy": "Video, audio, transcripts, cookies, tokens and API keys are not included.",
    }

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("summary.json", _safe_json(summary))
        archive.writestr("migration_status.json", _safe_json(migration_status()))
        archive.writestr("startup_recovery.json", _safe_json(startup_recovery_status()))
        archive.writestr("onboarding.json", _safe_json(onboarding_status()))
        if system_check is not None:
            archive.writestr("system_check.json", _safe_json(system_check))
        desktop_log_dir = (
            Path(os.environ.get("HIGHLIGHT_STUDIO_DESKTOP_LOG_DIR", "")).expanduser()
            if os.environ.get("HIGHLIGHT_STUDIO_DESKTOP_LOG_DIR")
            else None
        )
        for name in ("backend.log", "desktop.log"):
            candidates = [LOGS_DIR / name, DATA_DIR / "logs" / name]
            if desktop_log_dir is not None:
                candidates.append(desktop_log_dir / name)
            content = next((_tail(path) for path in candidates if path.exists()), "")
            if content:
                archive.writestr(f"logs/{name}", content)
        archive.writestr(
            "README.txt", "Support bundle generated locally. It excludes project media, transcripts, tokens, cookies and API keys.\n"
        )
    return output
