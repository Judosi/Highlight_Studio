from __future__ import annotations

import json
import os
import sys
from pathlib import Path


# Repository/application resource root. In source mode this is the repository
# directory. The packaged Electron shell passes HIGHLIGHT_STUDIO_APP_ROOT so
# the standalone engine can find frontend/dist and bundled third-party tools
# under the installer's resources/app directory.
def _application_root() -> Path:
    override = os.environ.get("HIGHLIGHT_STUDIO_APP_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        bundled = getattr(sys, "_MEIPASS", "")
        if bundled:
            return Path(bundled).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[4]


APP_ROOT = _application_root()


def load_release_identity(root: Path) -> dict[str, str]:
    try:
        identity = json.loads((root / "release_identity.json").read_text(encoding="utf-8"))
        if not isinstance(identity, dict) or not all(isinstance(identity.get(k), str) and identity[k].strip()
                                                   for k in ("version", "app_version", "design_id")):
            raise ValueError("missing identity fields")
        if not identity["app_version"].startswith(f"v{identity['version']}-"):
            raise ValueError("inconsistent app version")
        return identity
    except (OSError, ValueError) as exc:
        raise RuntimeError("Повреждён release_identity.json. Распакуйте полный архив Highlight Studio в новую папку.") from exc


_release_identity = load_release_identity(APP_ROOT)
APP_SEMVER = _release_identity["version"]
APP_VERSION = _release_identity["app_version"]
DESIGN_ID = _release_identity["design_id"]

# Third-party binaries shipped with the desktop archive. Keeping them under
# vendor/ makes the project tree explicit: application code never lives beside
# external executables.
VENDOR_DIR = APP_ROOT / "vendor"
BUNDLED_TWITCHDOWNLOADERCLI_DIR = VENDOR_DIR / "twitchdownloadercli"
BUNDLED_ARIA2_DIR = VENDOR_DIR / "aria2"
BUNDLED_FFMPEG_DIR = VENDOR_DIR / "ffmpeg" / "bin"
BUNDLED_TOOL_DIRS = [BUNDLED_FFMPEG_DIR, BUNDLED_TWITCHDOWNLOADERCLI_DIR, BUNDLED_ARIA2_DIR]

_existing_path = os.environ.get("PATH", "")
_prepend = [str(directory) for directory in BUNDLED_TOOL_DIRS if os.name == "nt" and directory.exists()]
if _prepend:
    os.environ["PATH"] = os.pathsep.join(_prepend + [_existing_path])


# Runtime locations are configurable so a future installer can write program
# files to Program Files while keeping mutable state in user-writable folders.
# Portable/source mode remains the default outside Windows and can be forced on
# any platform with HIGHLIGHT_STUDIO_PORTABLE=1.
def _runtime_paths() -> tuple[Path, Path]:
    data_override = os.environ.get("HIGHLIGHT_STUDIO_DATA_DIR", "").strip()
    projects_override = os.environ.get("HIGHLIGHT_STUDIO_PROJECTS_DIR", "").strip()
    portable = os.environ.get("HIGHLIGHT_STUDIO_PORTABLE", "0") == "1"

    if data_override:
        data_dir = Path(data_override).expanduser()
    elif os.name == "nt" and not portable:
        local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        data_dir = Path(local_app_data) / "HighlightStudio"
    else:
        data_dir = APP_ROOT / ".highlight_studio"

    if projects_override:
        projects_dir = Path(projects_override).expanduser()
    elif os.name == "nt" and not portable:
        user_profile = Path(os.environ.get("USERPROFILE") or Path.home())
        projects_dir = user_profile / "Videos" / "Highlight Studio"
    else:
        projects_dir = APP_ROOT / "projects"

    return data_dir.resolve(), projects_dir.resolve()


DATA_DIR, PROJECTS_DIR = _runtime_paths()
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DB = DATA_DIR / "jobs.sqlite3"
LOCAL_TOKEN_PATH = DATA_DIR / "local_auth_token.txt"
LOGS_DIR = DATA_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Deployment profiles:
# - desktop: single-user local application, local token + SQLite job cache.
# - web: multi-user server, PostgreSQL accounts/RBAC/sessions/jobs.
DEPLOYMENT_MODE = os.environ.get("HIGHLIGHT_STUDIO_DEPLOYMENT_MODE", "desktop").strip().lower()
if DEPLOYMENT_MODE not in {"desktop", "web"}:
    raise RuntimeError("HIGHLIGHT_STUDIO_DEPLOYMENT_MODE must be desktop or web")
WEB_ACCOUNTS_ENABLED = DEPLOYMENT_MODE == "web"
DATABASE_URL = os.environ.get("HIGHLIGHT_STUDIO_DATABASE_URL", "").strip()
ALLOW_SQLITE_WEB_TESTS = os.environ.get("HIGHLIGHT_STUDIO_ALLOW_SQLITE_WEB_TESTS", "0") == "1"
if WEB_ACCOUNTS_ENABLED and not DATABASE_URL:
    raise RuntimeError("HIGHLIGHT_STUDIO_DATABASE_URL is required in web mode")
if WEB_ACCOUNTS_ENABLED and not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://")) and not ALLOW_SQLITE_WEB_TESTS:
    raise RuntimeError("Web mode requires PostgreSQL. Set HIGHLIGHT_STUDIO_DATABASE_URL=postgresql+psycopg://...")
if not DATABASE_URL:
    DATABASE_URL = f"sqlite+pysqlite:///{(DATA_DIR / 'highlight_studio.db').as_posix()}"
# SQLite development/desktop databases may bootstrap automatically. PostgreSQL
# production deployments must run Alembic first so schema changes are tracked.
_default_auto_create = "1" if DATABASE_URL.startswith("sqlite") else "0"
AUTO_CREATE_DATABASE = os.environ.get("HIGHLIGHT_STUDIO_AUTO_CREATE_DATABASE", _default_auto_create) == "1"
COOKIE_SECURE = os.environ.get("HIGHLIGHT_STUDIO_COOKIE_SECURE", "1" if WEB_ACCOUNTS_ENABLED else "0") == "1"
ALLOW_PUBLIC_REGISTRATION = os.environ.get("HIGHLIGHT_STUDIO_ALLOW_REGISTRATION", "1") == "1"
SESSION_DAYS = max(1, min(90, int(os.environ.get("HIGHLIGHT_STUDIO_SESSION_DAYS", "30"))))
REQUIRE_EMAIL_VERIFICATION = os.environ.get("HIGHLIGHT_STUDIO_REQUIRE_EMAIL_VERIFICATION", "0") == "1"
PUBLIC_BASE_URL = os.environ.get("HIGHLIGHT_STUDIO_PUBLIC_BASE_URL", "").strip().rstrip("/")
SMTP_HOST = os.environ.get("HIGHLIGHT_STUDIO_SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("HIGHLIGHT_STUDIO_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("HIGHLIGHT_STUDIO_SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("HIGHLIGHT_STUDIO_SMTP_PASSWORD", "").strip()
SMTP_FROM = os.environ.get("HIGHLIGHT_STUDIO_SMTP_FROM", SMTP_USER or "noreply@highlightstudio.local").strip()
SMTP_USE_TLS = os.environ.get("HIGHLIGHT_STUDIO_SMTP_USE_TLS", "1") == "1"
DEV_AUTH_TOKENS = os.environ.get("HIGHLIGHT_STUDIO_DEV_AUTH_TOKENS", "0") == "1"
_raw_origins = os.environ.get("HIGHLIGHT_STUDIO_ALLOWED_ORIGINS", "").strip()
if _raw_origins:
    ALLOWED_ORIGINS = [item.strip().rstrip("/") for item in _raw_origins.split(",") if item.strip()]
elif WEB_ACCOUNTS_ENABLED:
    ALLOWED_ORIGINS = []  # same-origin production deployment
else:
    ALLOWED_ORIGINS = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ]

DEFAULT_OLLAMA_URL = "http://localhost:11434"
SERVER_OLLAMA_URL = os.environ.get("HIGHLIGHT_STUDIO_OLLAMA_URL", DEFAULT_OLLAMA_URL).strip() or DEFAULT_OLLAMA_URL
DEFAULT_TEXT_MODEL = "qwen3:8b"
DEFAULT_VISION_MODEL = "qwen3-vl:8b"
