from __future__ import annotations

import os
import shutil
import threading
import time
import zipfile
import uuid
import secrets
import subprocess
import sys
import importlib.util
import json
import math
import re
import logging
import traceback
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
import hashlib
import html
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import delete as sa_delete

# Validation contracts live in api.schemas; stability-sensitive fields include ai_request_hard_timeout.
from .schemas import (
    AppSettings,
    BetaFeedbackRequest,
    ClipPreviewRequest,
    FrontendCrashRequest,
    ImportProjectRequest,
    LicenseActivateRequest,
    OnboardingCompleteRequest,
    PrivacyPreferencesRequest,
    ReleaseEvidenceRequest,
    ShortRenderRequest,
    TwitchProjectRequest,
    YouTubeUploadRequest,
)

from ..services.hardware import detect_hardware_capabilities, recommend_settings as recommend_hardware_settings
from ..core.disk_budget import estimate_analysis_disk_budget
from ..infrastructure.project_locks import project_metadata_lock, project_lifecycle_lock

from ..services.pipeline import (
    CancelledError,
    JobLogger,
    adaptive_auto_settings,
    analyze,
    apply_adaptive_settings,
    build_quality_core_report,
    build_specific_metadata_fallback,
    build_stream_context,
    clear_cancel,
    content_factory,
    create_preview_video,
    generate_srt,
    generate_youtube_metadata,
    create_hls_proxy_preview,
    export_edit_timelines,
    generate_thumbnail_ideas,
    build_compare_mode,
    cached_source_duration,
    _shorts_edit_identity,
    list_output_files,
    model_benchmark,
    metadata_specificity_audit,
    pre_render_check,
    preflight,
    preview_status,
    project_paths,
    quality_report,
    render,
    render_factory_versions,
    render_rough_cut_preview,
    render_shorts_candidates,
    validate_short_editor_bounds,
    shorts_quality_components,
    request_cancel,
    result_check,
    save_feedback,
    simple_log_summary,
    source_video_path,
    update_segments,
    visual_quality_report,
    visual_scan_video,
    ocr_scan_video,
    detect_audio_events,
)
from ..core.settings import (
    APP_ROOT,
    APP_SEMVER,
    APP_VERSION,
    PROJECTS_DIR,
    LOCAL_TOKEN_PATH,
    DATA_DIR,
    LOGS_DIR,
    DEFAULT_OLLAMA_URL,
    DEFAULT_TEXT_MODEL,
    DEFAULT_VISION_MODEL,
    WEB_ACCOUNTS_ENABLED,
    SERVER_OLLAMA_URL,
    DESIGN_ID,
    ALLOWED_ORIGINS,
    PUBLIC_BASE_URL,
)
from ..core.utils import (
    read_json,
    safe_name,
    write_json,
    which,
    video_info,
    video_duration,
    audio_streams,
    tc,
    json_file_health,
    json_safe_value,
    repair_json_from_backup,
    cancel_running_processes,
    OperationCancelled,
    reconcile_orphaned_processes,
)
from ..core.artifacts import (
    ARTIFACTS,
    RECOMPUTABLE_CACHE_PATHS,
    source_file_signature,
    source_readiness,
    validate_media_file,
    portable_source_fields,
)
from ..core.durable_pipeline import DurablePipelineState
from ..core.revisions import (
    ANALYSIS_SETTING_KEYS,
    analysis_revision,
    source_revision,
    freshness_report,
    mark_source_changed,
    mark_segments_updated,
    output_is_publishable,
    read_revision_state,
    segments_revision,
    segments_revision_from_items,
)
from ..services.gates import authoritative_source_gate, authoritative_render_gate
from ..infrastructure.job_store import create_job, update_job, get_latest_job, list_jobs, recover_interrupted_jobs
from ..infrastructure.database.engine import init_database, session_scope
from ..infrastructure.database.models import JobRecord, ProjectMembership, ProjectRecord, User
from ..infrastructure.auth.router import router as auth_router
from ..infrastructure.auth.service import (
    SESSION_COOKIE,
    authenticate_token,
    validate_csrf,
    has_project_permission,
    accessible_project_ids,
    ensure_project,
    project_role,
    get_user_preference,
    set_user_preference,
)
from ..infrastructure.migrations import CURRENT_PROJECT_SCHEMA_VERSION, migrate_all_projects, migration_status
from ..infrastructure.runtime_state import (
    begin_runtime_session,
    complete_onboarding,
    mark_clean_shutdown,
    onboarding_status,
    reset_onboarding,
    stability_metrics,
    startup_recovery_status,
)
from ..infrastructure.support_bundle import build_support_bundle, redact_text
from ..infrastructure.crash_reporting import clear_crash_reports, crash_status, flush_crash_reports, record_crash
from ..infrastructure.release_gate import release_readiness, save_release_evidence
from ..infrastructure.licensing import (
    activate_license,
    deactivate_license,
    entitlement_allowed,
    license_status,
    refresh_license,
)
from ..infrastructure.paid_beta import (
    beta_metrics,
    commerce_config,
    flush_telemetry,
    privacy_status,
    record_event,
    save_privacy_preferences,
    submit_beta_feedback,
    telemetry_status,
)
from ..integrations.youtube.publisher import (
    YouTubeIntegrationError,
    clear_youtube_connection,
    complete_oauth,
    create_authorization_url,
    get_youtube_status,
    save_client_secrets,
    upload_project_videos,
)
from ..integrations.twitch.source import (
    classify_twitch_url,
    parse_time_to_seconds,
    prepare_twitch_source,
    twitch_tool_status,
    twitch_download_plan,
    run_twitch_speed_test,
)


def get_local_auth_token() -> str:
    if LOCAL_TOKEN_PATH.exists():
        token = LOCAL_TOKEN_PATH.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    LOCAL_TOKEN_PATH.write_text(token, encoding="utf-8")
    return token


LOCAL_AUTH_TOKEN = get_local_auth_token()


def _build_app_logger() -> logging.Logger:
    logger = logging.getLogger("highlight_studio")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOGS_DIR / "backend.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


APP_LOGGER = _build_app_logger()


@asynccontextmanager
async def lifespan(app_: FastAPI):
    init_database()
    migration_report = migrate_all_projects()
    if not migration_report.get("ok"):
        APP_LOGGER.error("Project migration failures: %s", migration_report.get("results"))
    recovery_report = begin_runtime_session()
    if recovery_report.get("recovered_count"):
        APP_LOGGER.warning("Recovered %s interrupted project status file(s)", recovery_report.get("recovered_count"))
    recover_interrupted_jobs()
    orphan_reports = []
    for _project_dir in PROJECTS_DIR.iterdir() if PROJECTS_DIR.exists() else []:
        if _project_dir.is_dir():
            try:
                terminated = reconcile_orphaned_processes(_project_dir)
                if terminated:
                    orphan_reports.append({"project_id": _project_dir.name, "terminated": int(terminated)})
            except Exception:
                APP_LOGGER.exception("Failed orphan-process reconciliation for project=%s", _project_dir.name)
    if orphan_reports:
        APP_LOGGER.warning("Reconciled orphan child-process metadata: %s", orphan_reports)
    record_event("app_started", {"version": APP_VERSION, "platform": sys.platform})
    try:
        yield
    finally:
        # A desktop window close should not leave FFmpeg/Twitch child processes
        # behind. Ask workers to stop, wait briefly for checkpoints, then
        # terminate registered subprocesses for the remaining projects.
        with jobs_lock:
            active = list(jobs.items())
        for project_id, _thread in active:
            try:
                request_cancel(PROJECTS_DIR / project_id)
            except Exception:
                APP_LOGGER.exception("Failed to request shutdown cancellation for project=%s", project_id)
        deadline = time.time() + 5.0
        for project_id, thread in active:
            remaining = max(0.0, deadline - time.time())
            if remaining:
                thread.join(timeout=remaining)
            if thread.is_alive():
                cancel_running_processes(PROJECTS_DIR / project_id)
                APP_LOGGER.warning("Forced child-process cleanup for project=%s during shutdown", project_id)
        mark_clean_shutdown()


class SafeJSONResponse(JSONResponse):
    """JSON response that never crashes on an accidental NaN/Infinity value."""

    def render(self, content: Any) -> bytes:
        return super().render(json_safe_value(content))


app = FastAPI(
    title=f"Highlight Studio {APP_VERSION}",
    lifespan=lifespan,
    default_response_class=SafeJSONResponse,
)

app.include_router(auth_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return SafeJSONResponse(
        status_code=422,
        content={
            "ok": False,
            "message": "Некорректные данные запроса.",
            # Model validators include a ValueError in ctx; that object cannot
            # be encoded as JSON. The human-readable msg already contains it.
            "detail": [{key: value for key, value in error.items() if key != "ctx"} for error in exc.errors()],
            "path": request.url.path,
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Keep sensitive paths/commands out of the browser response. The full
    # traceback is retained locally with a diagnostic id for support.
    diagnostic_id = uuid.uuid4().hex[:12]
    APP_LOGGER.error(
        "Unhandled request error id=%s method=%s path=%s\n%s",
        diagnostic_id,
        request.method,
        request.url.path,
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    )
    record_crash(
        source="backend",
        error_type=exc.__class__.__name__,
        message=str(exc),
        diagnostic_id=diagnostic_id,
        context={"method": request.method, "route": request.url.path, "platform": sys.platform},
    )
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "message": "Внутренняя ошибка приложения. Подробности сохранены в локальном журнале.",
            "error_type": exc.__class__.__name__,
            "diagnostic_id": diagnostic_id,
            "path": request.url.path,
        },
    )


PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/request-password-reset",
    "/api/auth/reset-password",
    "/api/auth/verify-email",
    "/api/auth/request-email-verification",
    "/api/youtube/oauth/callback",
}
if not WEB_ACCOUNTS_ENABLED:
    PUBLIC_API_PATHS.update({"/api/system-check", "/api/desktop/shutdown"})
WEB_ADMIN_PATHS = {
    "/api/system-check",
    "/api/runtime-components",
    "/api/app-audit",
    "/api/packaging-audit",
    "/api/release-readiness",
    "/api/release-readiness/evidence",
    "/api/migrations/status",
    "/api/startup-recovery",
    "/api/stability-metrics",
    "/api/support-bundle",
    "/api/ollama/stop-models",
    "/api/ollama/monitor",
    "/api/onboarding",
    "/api/onboarding/complete",
    "/api/onboarding/reset",
    "/api/privacy/status",
    "/api/privacy/preferences",
    "/api/telemetry/status",
    "/api/telemetry/flush",
    "/api/crash-reports/status",
    "/api/crash-reports/flush",
    "/api/crash-reports",
    "/api/license/status",
    "/api/license/activate",
    "/api/license/refresh",
    "/api/license/deactivate",
    "/api/commerce/config",
    "/api/beta/metrics",
}
AUTH_COOKIE_NAME = "highlight_studio_local_token"
ALLOW_QUERY_TOKEN = os.environ.get("HIGHLIGHT_STUDIO_ALLOW_QUERY_TOKEN", "0") == "1"


def request_local_tokens(request: Request) -> list[str]:
    """Read candidate local auth tokens from safe transports first.

    Header auth is kept for automated tests and old dev flows.  The browser UI now
    gets an HttpOnly cookie from /api/health, so video/download URLs no longer need
    to leak the token in query strings. Query-token auth can be re-enabled only for
    legacy troubleshooting with HIGHLIGHT_STUDIO_ALLOW_QUERY_TOKEN=1.
    """
    tokens = [
        request.cookies.get(AUTH_COOKIE_NAME) or "",
        request.headers.get("x-local-token") or "",
    ]
    if ALLOW_QUERY_TOKEN:
        tokens.extend([request.query_params.get("local_token") or "", request.query_params.get("token") or ""])
    return [t for t in tokens if t]


@app.middleware("http")
async def unified_auth_middleware(request: Request, call_next):
    path = request.url.path
    if path == "/api/auth/status" and WEB_ACCOUNTS_ENABLED:
        token = request.cookies.get(SESSION_COOKIE, "")
        if token:
            with session_scope() as db:
                auth = authenticate_token(db, token)
                if auth:
                    request.state.user, request.state.auth_session = auth
        return await call_next(request)
    if not path.startswith("/api") or request.method == "OPTIONS" or path in PUBLIC_API_PATHS:
        return await call_next(request)
    if not WEB_ACCOUNTS_ENABLED:
        if LOCAL_AUTH_TOKEN not in request_local_tokens(request):
            return JSONResponse(status_code=401, content={"detail": "Local auth token required. Перезапусти приложение."})
        request.state.user = type(
            "LocalUser",
            (),
            {"id": "local", "email": "local@device", "display_name": "Локальный пользователь", "global_role": "admin", "is_active": True},
        )()
        return await call_next(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    with session_scope() as db:
        auth = authenticate_token(db, token)
        if not auth:
            return JSONResponse(status_code=401, content={"detail": "Требуется вход в аккаунт", "code": "AUTH_REQUIRED"})
        user, auth_session = auth
        request.state.user = user
        request.state.auth_session = auth_session
        if path in WEB_ADMIN_PATHS and user.global_role != "admin":
            return JSONResponse(status_code=403, content={"detail": "Требуются права администратора"})
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not validate_csrf(auth_session, request.headers.get("x-csrf-token", "")):
                return JSONResponse(status_code=403, content={"detail": "CSRF token invalid", "code": "CSRF_FAILED"})
        if path.startswith("/api/projects/"):
            # Project IDs have a deliberately tiny ASCII alphabet. Percent-encoded
            # aliases create ambiguous identities between auth middleware and the
            # router, so reject them before permission lookup.
            raw_segment = path[len("/api/projects/"):].split("/", 1)[0]
            if "%" in raw_segment or not PROJECT_ID_RE.fullmatch(raw_segment):
                return JSONResponse(status_code=404, content={"detail": "Project not found"})
        match = re.match(rf"^/api/projects/({PROJECT_ID_PATTERN})(?:/|$)", path)
        if match:
            project_id = match.group(1)
            owner_only = "/members" in path or request.method == "DELETE"
            sensitive_read = request.method in {"GET", "HEAD"} and path.endswith(("/logs", "/simple-log"))
            required = "owner" if owner_only else ("editor" if sensitive_read or request.method not in {"GET", "HEAD"} else "viewer")
            if not has_project_permission(db, user, project_id, required):
                return JSONResponse(status_code=403, content={"detail": "Недостаточно прав для этого проекта", "required_role": required})
    return await call_next(request)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' http://127.0.0.1:* http://localhost:*",
    )
    return response


# Local desktop app: do not expose a write-capable local API to every website.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

MAX_CONCURRENT_JOBS = max(1, min(32, int(os.environ.get("HIGHLIGHT_STUDIO_MAX_CONCURRENT_JOBS", "4" if WEB_ACCOUNTS_ENABLED else "1"))))
_WEB_SECRET_KEYS = {
    "openai_api_key",
    "api_key",
    "token",
    "password",
    "secret",
}
_WEB_PATH_KEYS = {
    "twitch_downloader_cli_path",
    "source_video_path",
    "project_folder",
    "storage_path",
    "tool_path",
}


def web_safe_payload(value: Any) -> Any:
    """Remove secrets from every client response and server paths in web mode."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _WEB_SECRET_KEYS or lowered.endswith(("_api_key", "_token", "_password", "_secret")):
                cleaned[key] = ""
                continue
            if WEB_ACCOUNTS_ENABLED and lowered in _WEB_PATH_KEYS:
                cleaned[key] = ""
                continue
            cleaned[key] = web_safe_payload(item)
        return cleaned
    if isinstance(value, list):
        return [web_safe_payload(item) for item in value]
    if WEB_ACCOUNTS_ENABLED and isinstance(value, str):
        safe = redact_text(value)
        if re.match(r"^[A-Za-z]:[\\/]", safe):
            return ""
        if safe.startswith("/") and safe != "/" and not safe.startswith(("/api/", "/youtube-publisher")):
            return ""
        return safe
    return value


ROOT_DIR = APP_ROOT
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
jobs: dict[str, threading.Thread] = {}
jobs_lock = threading.Lock()
clip_preview_locks: dict[str, threading.Lock] = {}
clip_preview_locks_guard = threading.Lock()
segment_mutation_locks: dict[str, threading.Lock] = {}
segment_mutation_locks_guard = threading.Lock()





VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".ts"}


def project_video_meta(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
        return {
            "source_video_path": str(path.resolve()),
            "source_video_size_bytes": st.st_size,
            "source_video_size_gb": round(st.st_size / 1024 / 1024 / 1024, 3),
            "source_video_mtime": st.st_mtime,
        }
    except Exception:
        return {"source_video_path": str(path)}


def normalize_user_path(raw_path: str) -> Path:
    """Normalize a path copied from Explorer/Finder/terminal.

    Handles common Fast Import mistakes:
    - quotes from Windows "Copy as path";
    - file:// URLs;
    - URL-encoded spaces/Cyrillic;
    - accidental surrounding whitespace.
    """
    value = (raw_path or "").strip().strip('"').strip("'").strip()
    if value.lower().startswith("file:"):
        parsed = urlparse(value)
        value = unquote(parsed.path or "")
        # file:///C:/video.mp4 -> /C:/video.mp4 in urlparse; Windows Path needs C:/...
        if os.name == "nt" and value.startswith("/") and len(value) > 3 and value[2] == ":":
            value = value[1:]
    else:
        value = unquote(value)
    return Path(value).expanduser().resolve()


def ensure_local_video_path(raw_path: str) -> Path:
    src = normalize_user_path(raw_path)
    if not src.exists() or not src.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Видео по этому пути не найдено",
                "checked_path": str(src),
                "hint": "Вставь полный путь к файлу или выбери видео через кнопку «Обзор диска». Не используй C:\\fakepath\\... из браузера.",
            },
        )
    if src.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(400, f"Неподдерживаемый формат: {src.suffix or 'без расширения'}")
    return src


def _video_item(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
        size_gb = round(st.st_size / 1024 / 1024 / 1024, 3)
        size_mb = round(st.st_size / 1024 / 1024, 1)
    except Exception:
        size_gb = 0
        size_mb = 0
    return {"name": path.name, "path": str(path), "size_gb": size_gb, "size_mb": size_mb}


def local_roots_list() -> list[dict[str, str]]:
    roots: list[Path] = []
    if os.name == "nt":
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:\\")
            if drive.exists():
                roots.append(drive)
    else:
        roots.append(Path("/"))
    for extra in (Path.home(), Path.cwd()):
        try:
            if extra.exists() and extra not in roots:
                roots.append(extra)
        except Exception:
            pass
    return [{"name": str(p), "path": str(p)} for p in roots]


def create_project_meta(pid: str, name: str, settings: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    meta = {
        "id": pid,
        "name": safe_name(name, "video"),
        "app_version": APP_VERSION,
        "settings_version": APP_VERSION,
        "project_schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "created_at": now,
        "updated_at": now,
        "settings": settings,
    }
    meta.update(extra)
    return meta


def explain_openai_check(result: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Turn a raw OpenAI/OpenAI-compatible check into a UI-friendly diagnostic."""
    error = str(result.get("error") or "")
    low = error.lower()
    text_model = settings.get("openai_text_model") or "gpt-4o-mini"
    vision_model = settings.get("openai_vision_model") or text_model
    base_url = settings.get("openai_base_url") or "https://api.openai.com/v1"
    if result.get("ok"):
        text_ok = bool(result.get("text_model_installed", True))
        vision_ok = bool(result.get("vision_model_installed", True))
        if text_ok and vision_ok:
            return {
                **result,
                "ok": True,
                "status": "ok",
                "code": "valid",
                "message": "API key работает. Base URL доступен, авторизация прошла, выбранные модели выглядят доступными.",
                "recommendation": "Можно запускать Проверку или AI-анализ. Для длинных стримов начинай с Hybrid Auto + Transcript only.",
                "safe_key_hint": "Ключ не возвращается из backend и не отображается в ответе.",
            }
        missing = []
        if not text_ok:
            missing.append(f"text model: {text_model}")
        if not vision_ok:
            missing.append(f"vision model: {vision_model}")
        return {
            **result,
            "ok": False,
            "status": "warning",
            "code": "model_not_found",
            "message": "API key принят, но выбранная модель не найдена в списке доступных моделей: " + ", ".join(missing),
            "recommendation": "Проверь название модели в настройках или выбери модель, которая есть у твоего API-провайдера.",
        }
    if not str(settings.get("openai_api_key") or os.getenv("OPENAI_API_KEY") or "").strip():
        code = "missing_key"
        msg = "OpenAI API key не задан."
        rec = "Вставь ключ в поле OpenAI API key или задай переменную среды OPENAI_API_KEY, потом перезапусти приложение."
    elif "401" in low or "unauthorized" in low or "incorrect api key" in low or "invalid api key" in low:
        code = "invalid_key"
        msg = "Ключ отклонён API: неверный, удалённый или не от этого провайдера."
        rec = "Проверь, что ключ скопирован полностью. Для официального OpenAI обычно нужен Base URL https://api.openai.com/v1. Для ключей формата другого сервиса включи OpenAI-compatible и укажи его Base URL."
    elif "403" in low or "forbidden" in low or "permission" in low:
        code = "forbidden"
        msg = "Ключ найден, но у него нет доступа к этому API или модели."
        rec = "Проверь права проекта, billing, организацию и выбранную модель."
    elif "429" in low or "quota" in low or "rate limit" in low or "insufficient_quota" in low:
        code = "quota_or_rate_limit"
        msg = "API отвечает, но лимит или баланс недоступен."
        rec = "Проверь billing/лимиты. Для теста уменьши batch size или используй Hybrid Auto с fallback."
    elif "404" in low or "not found" in low:
        code = "bad_base_url_or_model"
        msg = "Не найден endpoint или модель."
        rec = f"Проверь Base URL ({base_url}) и название модели. Для OpenAI-compatible URL должен оканчиваться на /v1."
    elif "connection" in low or "max retries" in low or "name resolution" in low or "nodename" in low:
        code = "connection_error"
        msg = "Не удалось подключиться к API endpoint."
        rec = "Проверь интернет, Base URL, VPN/прокси и firewall. Для локального compatible-сервера убедись, что он запущен."
    elif "timeout" in low or "timed out" in low:
        code = "timeout"
        msg = "API не ответил за отведённое время."
        rec = "Увеличь OpenAI timeout или проверь, не перегружен ли провайдер/локальный сервер."
    else:
        code = "unknown_error"
        msg = "Проверка API не прошла."
        rec = "Проверь ключ, Base URL, модель и режим OpenAI/OpenAI-compatible."
    return {
        **result,
        "ok": False,
        "status": "error",
        "code": code,
        "message": msg,
        "recommendation": rec,
        "raw_error": error[:800],
        "base_url": base_url,
        "text_model": text_model,
        "vision_model": vision_model,
    }


def strip_cloud_ai_settings(data: dict[str, Any]) -> dict[str, Any]:
    clean = dict(data or {})
    for key in list(clean.keys()):
        if str(key).startswith(("gemini_", "openai_")) or str(key) == "hybrid_disable_fallback":
            clean.pop(key, None)
    clean["ai_engine"] = "ollama"
    return clean


def default_settings() -> dict[str, Any]:
    return strip_cloud_ai_settings(AppSettings().model_dump())


def validate_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    try:
        incoming = dict(raw or {})
        # 10.15.14 Auto hardware policy: projects created by 10.15.6-10.15.13
        # could persist CPU/libx264 even after capability detection proved CUDA
        # and NVENC were available. In Auto mode those persisted values are
        # implementation residue, not a manual choice. Manual/CPU_Stable modes
        # remain untouched.
        if int(incoming.get("hardware_runtime_policy_version", 1) or 1) < 2:
            if (
                bool(incoming.get("hardware_auto_optimize", True))
                and str(incoming.get("hardware_profile") or "Auto") == "Auto"
                and not bool(incoming.get("hardware_manual_override_enabled", False))
            ):
                incoming["whisper_device"] = "auto"
                incoming["whisper_compute"] = "auto"
                incoming["video_encoder"] = "auto"
            incoming["hardware_runtime_policy_version"] = 2
        # 10.15.6 hardware migration: old releases hard-coded CPU Whisper and
        # libx264 in the built-in "Auto"/GTX1050Ti presets.  Preserve explicit
        # expert choices, but migrate legacy automatic profiles to runtime auto.
        if "hardware_auto_optimize" not in incoming:
            incoming["hardware_auto_optimize"] = True
            legacy_profile = str(incoming.get("hardware_profile") or "Auto").lower()
            if legacy_profile == "auto" or legacy_profile.startswith("gtx1050ti"):
                if str(incoming.get("whisper_device") or "cpu").lower() == "cpu":
                    incoming["whisper_device"] = "auto"
                    incoming["whisper_compute"] = "auto"
                if str(incoming.get("video_encoder") or "libx264").lower() == "libx264":
                    incoming["video_encoder"] = "auto"
                incoming["hardware_profile"] = "Auto"
        # 10.15.7 quality-guard migration. 10.15.6 treated logical AI
        # prompt batching as GPU concurrency and could persist batch=1 on low-VRAM
        # Auto profiles. That does not protect VRAM; it only multiplies sequential
        # Ollama calls. Migrate only projects that predate the new quality guard.
        if "hardware_quality_guard_enabled" not in incoming:
            incoming["hardware_quality_guard_enabled"] = True
            if bool(incoming.get("hardware_auto_optimize", True)) and str(incoming.get("hardware_profile") or "Auto") == "Auto":
                if int(incoming.get("ai_batch_size") or 1) <= 1:
                    incoming["ai_batch_size"] = 3
                if int(incoming.get("micro_batch_size") or 1) <= 2:
                    incoming["micro_batch_size"] = 8
                if str(incoming.get("hardware_decode") or "off").lower() == "off":
                    incoming["hardware_decode"] = "auto"
        # One-time migration for projects created before the Shorts v2 layout.
        # Once the new caption keys are saved, an explicitly chosen safe layout
        # remains untouched.
        if "shorts_dynamic_captions" not in incoming:
            if incoming.get("shorts_reframe_mode") == "blur_background":
                incoming["shorts_reframe_mode"] = "smart_zoom"
            try:
                old_shorts_max = float(incoming.get("shorts_max_seconds", 60) or 60)
            except (TypeError, ValueError):
                old_shorts_max = None
            if old_shorts_max == 60:
                incoming["shorts_max_seconds"] = 45
        validated = AppSettings.model_validate({**AppSettings().model_dump(), **incoming}).model_dump()
        # In multi-user web mode Ollama is server-managed. A project editor must
        # not be able to redirect backend HTTP requests to arbitrary hosts.
        if WEB_ACCOUNTS_ENABLED:
            validated["ollama_url"] = SERVER_OLLAMA_URL
        # Keep saved project settings local-only: remove old Gemini/OpenAI cloud keys.
        return strip_cloud_ai_settings(validated)
    except ValidationError as exc:
        # Return compact JSON-safe validation errors to the UI.
        errors = []
        for err in exc.errors():
            err = dict(err)
            err.pop("ctx", None)
            errors.append(err)
        raise HTTPException(status_code=422, detail=errors) from exc


def effective_project_settings(project: dict[str, Any] | None) -> dict[str, Any]:
    """Merge saved project settings with current validated defaults."""
    return validate_settings((project or {}).get("settings", {}))


def safe_ollama_settings(current: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return conservative defaults for local Ollama on long streams.

    These values favor reliability over speed: small batches, no strict mode by
    default, metadata limited to a small number of clips, and visual/OCR kept on
    only at a safe sampling level.  The UI can apply this in one click so users
    do not accidentally run Metadata clips=80 or AI batch=8 on a weak PC.
    """
    base = dict(current or {})
    base.update(
        {
            "ai_engine": "ollama",
            "ai_batch_size": 1,
            "micro_batch_size": 1,
            "ai_retry_count": 2,
            "ai_transport_retry_count": 2,
            "ollama_timeout": 900,
            "ai_request_active_hard_timeout": 900,
            "ai_ttft_timeout": 180,
            "ai_stream_stall_timeout": 60,
            "ai_circuit_failure_threshold": 2,
            "ai_circuit_cooldown_seconds": 60,
            "ollama_think": False,
            "ollama_keep_alive": "5m",
            "ollama_num_ctx": 4096,
            "hardware_profile": "CPU_Stable",
            "analysis_profile": "balanced",
            "ai_strict_mode": False,
            "full_ai_coverage": False,
            "metadata_max_segments": 12,
            "metadata_timeout": 300,
            "metadata_ai_retries": 3,
            "generate_metadata": False,
            "metadata_ai_enabled": False,
            "require_ai_metadata": False,
            "visual_scan_enabled": True,
            "visual_scan_interval_seconds": 5,
            "visual_scan_max_samples": 1200,
            "ocr_enabled": True,
            "ocr_every_n_visual_samples": 2,
        }
    )
    return validate_settings(base)


def metadata_preset_settings(current: dict[str, Any] | None = None, preset: str = "normal") -> dict[str, Any]:
    presets = {
        "fast": {"metadata_max_segments": 8, "metadata_timeout": 180, "label": "Быстро"},
        "normal": {"metadata_max_segments": 12, "metadata_timeout": 300, "label": "Нормально"},
        "detailed": {"metadata_max_segments": 20, "metadata_timeout": 600, "label": "Подробно"},
        "max": {"metadata_max_segments": 25, "metadata_timeout": 900, "label": "Макс для Ollama"},
    }
    chosen = presets.get(str(preset or "normal"), presets["normal"])
    base = dict(current or {})
    base.update(
        {
            "metadata_max_segments": chosen["metadata_max_segments"],
            "metadata_timeout": chosen["metadata_timeout"],
            "metadata_ai_retries": 3,
        }
    )
    validated = validate_settings(base)
    validated["_preset_label"] = chosen["label"]
    return validated


def hardware_preset_settings(current: dict[str, Any] | None = None, preset: str = "auto_balanced") -> dict[str, Any]:
    """Build a capability-based hardware preset for the current machine.

    v10.15.7 separates *hardware* tuning from *analysis quality*.  The previous
    release could silently reduce Whisper/visual/OCR coverage on low-VRAM PCs.
    With the hardware quality guard enabled, these presets may choose devices,
    workers and encoders, but they must not weaken the content-analysis policy.
    """
    original = validate_settings(dict(current or {}))
    base = dict(original)
    requested = str(preset or "auto_balanced").lower()
    if requested in {"gtx1050ti_fast", "fast", "auto_fast"}:
        mode = "fast"
    elif requested in {"gtx1050ti_quality", "quality", "auto_quality"}:
        mode = "quality"
    else:
        mode = "balanced"

    capabilities = detect_hardware_capabilities()
    base.update(recommend_hardware_settings(capabilities, base))
    base.update(
        {
            "ai_engine": "ollama",
            "analysis_profile": mode,
            "ai_retry_count": 2,
            "ai_strict_mode": False,
            "full_ai_coverage": False,
            "micro_cut_enabled": True,
            "dedup_enabled": True,
            "storyline_enabled": True,
            "audio_dynamics_enabled": True,
            "refill_after_dedup_enabled": True,
            "target_fill_ratio": 0.92,
            "strict_quality_mode": False,
            "generate_metadata": False,
            "metadata_ai_enabled": False,
            "require_ai_metadata": False,
            "metadata_ai_retries": 3,
            "make_srt": False,
            "ollama_keep_alive": "5m",
        }
    )

    nvidia = capabilities.get("nvidia") or {}
    gpu = (nvidia.get("gpus") or [{}])[0] if nvidia.get("gpus") else {}
    vram_mb = int(gpu.get("memory_total_mb") or 0)
    ram_gb = float((capabilities.get("memory") or {}).get("total_gb") or 0.0)

    if mode == "quality":
        base.update(
            {
                "whisper_model": "small" if vram_mb >= 5500 or (not vram_mb and ram_gb >= 24) else "base",
                "ollama_timeout": 1500,
                "ollama_num_ctx": min(6144, int(base.get("ollama_num_ctx") or 4096)),
                "block_seconds": 210,
                "chunk_seconds": 600,
                "top_blocks_for_micro": 28,
                "micro_window_seconds": 45,
                "micro_min_seconds": 18,
                "micro_max_seconds": 75,
                "visual_mode": "Средний",
                "visual_scan_enabled": True,
                "visual_scan_interval_seconds": 5,
                "visual_scan_max_samples": min(1200, int(base.get("visual_scan_max_samples") or 1200)),
                "ocr_enabled": True,
                "ocr_every_n_visual_samples": max(2, int(base.get("ocr_every_n_visual_samples") or 2)),
                "ocr_upscale": 2,
                "metadata_max_segments": 15,
                "metadata_timeout": 450,
            }
        )
    elif mode == "fast":
        base.update(
            {
                "whisper_model": "base",
                "ollama_timeout": 900,
                "ollama_num_ctx": min(4096, int(base.get("ollama_num_ctx") or 4096)),
                "block_seconds": 300,
                "chunk_seconds": 900,
                "top_blocks_for_micro": 16,
                "micro_window_seconds": 40,
                "micro_min_seconds": 18,
                "micro_max_seconds": 65,
                "visual_mode": "Лёгкий",
                "visual_scan_enabled": True,
                "visual_scan_interval_seconds": 10,
                "visual_scan_max_samples": min(600, int(base.get("visual_scan_max_samples") or 600)),
                "ocr_enabled": True,
                "ocr_every_n_visual_samples": max(5, int(base.get("ocr_every_n_visual_samples") or 5)),
                "ocr_upscale": 1,
                "metadata_max_segments": 8,
                "metadata_timeout": 240,
            }
        )
    else:
        base.update(
            {
                "whisper_model": "base" if vram_mb and vram_mb <= 4500 else str(base.get("whisper_model") or "base"),
                "ollama_timeout": 1200,
                "ollama_num_ctx": min(4096 if vram_mb and vram_mb <= 4500 else 6144, int(base.get("ollama_num_ctx") or 4096)),
                "block_seconds": 240,
                "chunk_seconds": 600,
                "top_blocks_for_micro": 22,
                "micro_window_seconds": 42,
                "micro_min_seconds": 18,
                "micro_max_seconds": 70,
                "visual_mode": "Лёгкий",
                "visual_scan_enabled": True,
                "visual_scan_interval_seconds": 8,
                "visual_scan_max_samples": min(800, int(base.get("visual_scan_max_samples") or 800)),
                "ocr_enabled": True,
                "ocr_every_n_visual_samples": max(4, int(base.get("ocr_every_n_visual_samples") or 4)),
                "ocr_upscale": 2,
                "metadata_max_segments": 12,
                "metadata_timeout": 300,
            }
        )
    if bool(original.get("hardware_quality_guard_enabled", True)):
        # These settings directly affect what information reaches the selector or
        # how much reasoning it receives. Hardware tuning must never silently
        # downgrade them. An explicit task/analysis preset can still change them
        # elsewhere in the UI.
        protected = (
            "analysis_profile", "text_model", "vision_model", "whisper_model",
            "ollama_num_ctx", "block_seconds", "chunk_seconds", "top_blocks_for_micro",
            "micro_window_seconds", "micro_min_seconds", "micro_max_seconds",
            "micro_speech_gap_seconds", "micro_source_duration_multiplier",
            "visual_mode", "visual_scan_enabled", "visual_scan_interval_seconds",
            "visual_scan_max_samples", "ocr_enabled", "ocr_every_n_visual_samples",
            "ocr_upscale", "ocr_languages", "full_ai_coverage", "strict_quality_mode",
            "semantic_quality_guard_enabled", "non_primary_reject_confidence",
            "quality_first_selection_enabled", "quality_first_min_score",
            "quality_first_min_confidence", "quality_first_min_clarity",
            "quality_recovery_score_relaxation",
            "temporal_fairness_enabled", "temporal_fairness_bucket_seconds",
            "temporal_fairness_blocks_per_bucket", "temporal_fairness_min_score",
            "micro_global_score_floor",
            "max_final_segments", "min_final_segments", "target_minutes",
        )
        for key in protected:
            base[key] = original.get(key)

    return validate_settings(base)


TASK_PRESETS: dict[str, dict[str, Any]] = {
    "balanced": {
        "label": "Сбалансированный / смысл",
        "description": "Лучшие законченные моменты всего стрима без перекоса в один тип события.",
        "settings": {
            "task_preset": "balanced",
            "task_preset_label": "Сбалансированный / смысл",
            "content_type": "Auto",
            "edit_mode": "Сбалансированный",
            "analysis_profile": "balanced",
            "target_minutes": 30,
            "min_final_segments": 8,
            "max_final_segments": 80,
            "micro_cut_enabled": True,
            "micro_window_seconds": 45,
            "micro_min_seconds": 12,
            "micro_max_seconds": 80,
            "top_blocks_for_micro": 36,
            "audio_dynamics_enabled": True,
            "visual_scan_enabled": True,
            "visual_scan_interval_seconds": 5,
            "visual_scan_max_samples": 1200,
            "ocr_enabled": True,
            "ocr_every_n_visual_samples": 2,
            "hook_first_enabled": False,
            "hook_min_score": 8.6,
            "dedup_enabled": True,
            "storyline_enabled": True,
            "refill_after_dedup_enabled": True,
            "semantic_quality_guard_enabled": True,
            "quality_first_selection_enabled": True,
            "temporal_fairness_enabled": True,
            "prompt": "Выбери лучшие законченные моменты из ВСЕГО стрима. Приоритет смыслу сцены: должна быть понятна причина, событие/развитие и реакция или payoff. Сравнивай ранние и поздние моменты на равных. Не добирай длительность техническими паузами, reconnect/waiting экранами, рекламой, повторами или заранее записанными старыми хайлайтами. Если стример вживую реагирует на чужое видео, оценивай именно его текущую реакцию и комментарий, а не качество встроенного старого ролика.",
        },
    },
    "irl_funny": {
        "label": "IRL смешное",
        "description": "Угар, абсурд, смех, мемные диалоги, реакции прохожих и чата.",
        "settings": {
            "task_preset": "irl_funny",
            "task_preset_label": "IRL смешное",
            "content_type": "IRL стрим",
            "edit_mode": "Только смешное",
            "analysis_profile": "balanced",
            "target_minutes": 30,
            "micro_cut_enabled": True,
            "micro_window_seconds": 35,
            "micro_min_seconds": 10,
            "micro_max_seconds": 65,
            "top_blocks_for_micro": 36,
            "audio_dynamics_enabled": True,
            "visual_scan_enabled": True,
            "visual_scan_interval_seconds": 6,
            "visual_scan_max_samples": 1000,
            "ocr_enabled": True,
            "ocr_every_n_visual_samples": 3,
            "hook_first_enabled": False,
            "hook_min_score": 8.4,
            "dedup_enabled": True,
            "storyline_enabled": True,
            "refill_after_dedup_enabled": True,
            "prompt": "Собери IRL-нарезку с упором на смешные моменты. Высоко оценивай смех, угар, абсурдные диалоги, мемы, неожиданные реплики, донаты и чат, которые провоцируют смешную реакцию. Убирай долгую ходьбу, бытовую воду, технические паузы и обычный спокойный разговор без payoff. Каждый выбранный момент должен быть понятен зрителю без просмотра полного стрима.",
        },
    },
    "irl_conflict": {
        "label": "IRL конфликт / хаос",
        "description": "Споры, неловкость, охрана, прохожие, резкие реакции, донат-провокации.",
        "settings": {
            "task_preset": "irl_conflict",
            "task_preset_label": "IRL конфликт / хаос",
            "content_type": "IRL стрим",
            "edit_mode": "IRL конфликт/хаос",
            "analysis_profile": "quality",
            "target_minutes": 30,
            "micro_cut_enabled": True,
            "micro_window_seconds": 45,
            "micro_min_seconds": 12,
            "micro_max_seconds": 90,
            "top_blocks_for_micro": 45,
            "audio_dynamics_enabled": True,
            "visual_scan_enabled": True,
            "visual_scan_interval_seconds": 5,
            "visual_scan_max_samples": 1200,
            "ocr_enabled": True,
            "ocr_every_n_visual_samples": 2,
            "hook_first_enabled": False,
            "hook_min_score": 8.5,
            "dedup_enabled": True,
            "storyline_enabled": True,
            "refill_after_dedup_enabled": True,
            "prompt": "Собери IRL-нарезку с упором на конфликт, хаос и сильные реакции. Высоко оценивай споры, неловкие встречи, охрану, полицию, прохожих, донат-провокации, шок, крик, резкое изменение ситуации и моменты, где зрителю интересно узнать развязку. Оставляй контекст 5-15 секунд до реакции, если без него сцена непонятна. Убирай обычную ходьбу и спокойную болтовню.",
        },
    },
    "sport": {
        "label": "Спорт / теннис",
        "description": "Розыгрыши, эмоции, спорные моменты, победы, провалы и реакции зрителей.",
        "settings": {
            "task_preset": "sport",
            "task_preset_label": "Спорт / теннис",
            "content_type": "Спорт",
            "edit_mode": "Сбалансированный",
            "analysis_profile": "balanced",
            "target_minutes": 25,
            "micro_cut_enabled": True,
            "micro_window_seconds": 40,
            "micro_min_seconds": 10,
            "micro_max_seconds": 80,
            "top_blocks_for_micro": 34,
            "audio_dynamics_enabled": True,
            "visual_scan_enabled": True,
            "visual_scan_interval_seconds": 5,
            "visual_scan_max_samples": 1000,
            "ocr_enabled": True,
            "ocr_every_n_visual_samples": 3,
            "hook_first_enabled": False,
            "hook_min_score": 8.3,
            "dedup_enabled": True,
            "storyline_enabled": True,
            "prompt": "Собери спортивную нарезку. Высоко оценивай яркие розыгрыши, напряжённые концовки, победы, провалы, спорные моменты, эмоции игроков/стримера, реакции чата и смешные комментарии. Убирай паузы между розыгрышами, долгую подготовку, повторяющийся счёт и спокойные разговоры без события. Итог должен смотреться как динамичный спортивный highlight.",
        },
    },
    "podcast": {
        "label": "Подкаст / разговор",
        "description": "Сильные мысли, истории, спор мнений, инсайты и кликабельные фразы.",
        "settings": {
            "task_preset": "podcast",
            "task_preset_label": "Подкаст / разговор",
            "content_type": "Подкаст",
            "edit_mode": "С историей",
            "analysis_profile": "quality",
            "target_minutes": 35,
            "micro_cut_enabled": True,
            "micro_window_seconds": 70,
            "micro_min_seconds": 25,
            "micro_max_seconds": 150,
            "top_blocks_for_micro": 30,
            "audio_dynamics_enabled": True,
            "visual_scan_enabled": False,
            "ocr_enabled": False,
            "hook_first_enabled": False,
            "hook_min_score": 8.2,
            "dedup_enabled": True,
            "storyline_enabled": True,
            "refill_after_dedup_enabled": True,
            "prompt": "Собери разговорную нарезку для YouTube. Высоко оценивай сильные формулировки, личные истории, спор мнений, инсайты, неожиданные признания, конфликт позиции и фразы, которые можно вынести в заголовок. Убирай повторы, длинные вступления, пересказ без вывода и технические паузы. Каждый фрагмент должен быть понятен зрителю отдельно.",
        },
    },
    "shorts_only": {
        "label": "Shorts only",
        "description": "Короткие вертикальные клипы с hook в первые секунды.",
        "settings": {
            "task_preset": "shorts_only",
            "task_preset_label": "Shorts only",
            "content_type": "Shorts",
            "edit_mode": "Плотно",
            "analysis_profile": "fast",
            "target_minutes": 8,
            "micro_cut_enabled": True,
            "micro_window_seconds": 25,
            "micro_min_seconds": 8,
            "micro_max_seconds": 45,
            "min_final_segments": 10,
            "max_final_segments": 80,
            "top_blocks_for_micro": 40,
            "shorts_count": 10,
            "shorts_vertical_reframe": True,
            "shorts_reframe_mode": "auto",
            "shorts_burn_subtitles": True,
            "shorts_dynamic_captions": True,
            "shorts_hook_title_enabled": True,
            "shorts_trim_silence": True,
            "shorts_caption_max_words": 4,
            "shorts_caption_quality": "high",
            "shorts_caption_font_size": 72,
            "shorts_funny_search_enabled": True,
            "shorts_emotion_events_enabled": True,
            "shorts_sensevoice_model": "iic/SenseVoiceSmall",
            "shorts_emotion_top_n": 20,
            "shorts_face_sample_fps": 1.0,
            "shorts_normalize_audio": True,
            "shorts_min_seconds": 3,
            "shorts_max_seconds": 45,
            "shorts_crf": 22,
            "audio_dynamics_enabled": True,
            "visual_scan_enabled": True,
            "visual_scan_interval_seconds": 5,
            "visual_scan_max_samples": 900,
            "ocr_enabled": True,
            "ocr_every_n_visual_samples": 3,
            "hook_first_enabled": False,
            "hook_min_score": 8.0,
            "dedup_enabled": True,
            "storyline_enabled": False,
            "prompt": "Найди короткие моменты для Shorts. Каждый клип должен иметь сильный hook в первые 1-3 секунды: смешная реплика, шок, конфликт, резкая реакция, донат, визуальный прикол или понятный payoff. Убирай длинный контекст и спокойные разговоры. Лучше 10 коротких сильных клипов, чем один длинный слабый фрагмент.",
        },
    },
}


def task_preset_settings(current: dict[str, Any] | None = None, preset: str = "balanced") -> dict[str, Any]:
    base = dict(current or {})
    chosen = TASK_PRESETS.get(str(preset or "balanced"), None)
    if not chosen:
        base.update({"task_preset": "balanced", "task_preset_label": "Сбалансированный / смысл"})
        return validate_settings(base)
    base.update(chosen["settings"])
    return validate_settings(base)


PROJECT_ID_PATTERN = r"[A-Za-z0-9_-]{1,64}"
PROJECT_ID_RE = re.compile(rf"^{PROJECT_ID_PATTERN}$")


def _decode_path_component(value: str, rounds: int = 1) -> str:
    # Project IDs contain only ASCII letters, digits, underscore and hyphen.
    # FastAPI already decodes a route parameter once, so accepting additional
    # percent-decoding here creates an authorization/canonicalization split.
    decoded = str(value or "")
    for _ in range(max(0, min(int(rounds), 1))):
        decoded = unquote(decoded)
    return decoded


def project_dir(project_id: str) -> Path:
    raw_id = str(project_id or "").strip()
    # Encoded forms are never necessary for our project-id alphabet. Rejecting
    # them keeps routing, RBAC and filesystem identity on one canonical value.
    if "%" in raw_id:
        raise HTTPException(404, "Project not found")
    decoded_id = _decode_path_component(raw_id, rounds=0).strip()
    if not PROJECT_ID_RE.fullmatch(decoded_id):
        raise HTTPException(404, "Project not found")
    root = PROJECTS_DIR.resolve()
    d = (root / decoded_id).resolve()
    try:
        d.relative_to(root)
    except ValueError as exc:
        raise HTTPException(404, "Project not found") from exc
    if not d.exists() or not d.is_dir():
        raise HTTPException(404, "Project not found")
    return d


def load_project(project_dir_: Path) -> dict[str, Any]:
    project = read_json(project_dir_ / "project.json", {}) or {}
    project["settings"] = validate_settings(project.get("settings", {}))
    project.setdefault("app_version", APP_VERSION)
    project.setdefault("settings_version", APP_VERSION)
    return project


def save_project(project_dir_: Path, project: dict[str, Any]) -> None:
    project["app_version"] = APP_VERSION
    project["settings_version"] = APP_VERSION
    project["project_schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
    project["updated_at"] = time.time()
    write_json(project_dir_ / "project.json", project)


INTEGRITY_JSON_FILES = [
    "project.json",
    "status.json",
    "segments.json",
    "candidates.json",
    "twitch_import.json",
    "cache_manifest.json",
    "pre_render_check.json",
    "youtube_metadata.json",
]


def project_integrity_report(project_dir_: Path) -> dict[str, Any]:
    """Fast project safety report: corrupted JSON, missing source and backups."""
    files = [json_file_health(project_dir_ / name) for name in INTEGRITY_JSON_FILES]
    missing_required: list[str] = []
    issues: list[dict[str, Any]] = []
    for item in files:
        required = item["name"] == "project.json"
        if required and not item["exists"]:
            missing_required.append(item["name"])
        if item["exists"] and not item["valid"]:
            issues.append(
                {
                    "name": item["name"],
                    "type": "corrupt_json",
                    "recoverable": item["recoverable"],
                    "message": "JSON повреждён, но есть backup." if item["recoverable"] else "JSON повреждён и backup не найден.",
                }
            )
    project = read_json(project_dir_ / "project.json", {}) or {}
    source_path = project.get("source_video_path") or ""
    source_exists = bool(source_path and Path(source_path).exists())
    if source_path and not source_exists:
        issues.append(
            {
                "name": "source_video_path",
                "type": "missing_source",
                "recoverable": False,
                "message": "Исходное видео не найдено по сохранённому пути.",
            }
        )
    recoverable_count = sum(1 for item in files if item.get("recoverable"))
    critical_ok = not missing_required and not any(i for i in issues if not i.get("recoverable"))
    return {
        "ok": critical_ok and not issues,
        "critical_ok": critical_ok,
        "project_id": project_dir_.name,
        "checked_at": time.time(),
        "source_video_path": source_path,
        "source_exists": source_exists,
        "files": files,
        "issues": issues,
        "recoverable_count": recoverable_count,
        "message": "Проект в порядке." if not issues else (f"Есть {len(issues)} проблема(ы), восстановимых: {recoverable_count}."),
    }


def repair_project_json_backups(project_dir_: Path) -> dict[str, Any]:
    results = []
    for name in INTEGRITY_JSON_FILES:
        health = json_file_health(project_dir_ / name)
        if health.get("recoverable"):
            results.append(repair_json_from_backup(project_dir_ / name))
    return {
        "ok": True,
        "repaired": sum(1 for r in results if r.get("changed")),
        "results": results,
        "integrity": project_integrity_report(project_dir_),
    }


def _readiness_item(
    id: str, label: str, ok: bool, weight: int, details: str = "", fix: str = "", severity: str = "required"
) -> dict[str, Any]:
    return {
        "id": id,
        "label": label,
        "ok": bool(ok),
        "weight": int(weight),
        "details": details,
        "fix": fix,
        "severity": severity if severity in {"required", "warning", "info"} else "warning",
    }


def product_readiness_report(project_dir_: Path) -> dict[str, Any]:
    """Product-level hardening report for real daily use.

    This is intentionally read-only and fast.  It does not run FFmpeg/Ollama on
    the whole video and it does not mutate the project.  It combines the most
    important weak spots we found during the audit: source readiness, tools,
    settings safety, cache compatibility, resume/checkpoints, render readiness
    and project JSON integrity.
    """
    project = load_project(project_dir_)
    settings = project.get("settings", default_settings())
    status = read_json(project_dir_ / "status.json", {}) or {}
    candidates = read_json(project_paths(project_dir_)["candidates"], []) or []
    segments = read_json(project_paths(project_dir_)["segments"], []) or []
    outputs = list_output_files(project_dir_)
    final_ready = any(o.get("path") == "outputs/highlight_final.mp4" for o in outputs)

    integrity = project_integrity_report(project_dir_)
    checkpoints = project_checkpoint_status(project_dir_)
    cache = cache_fingerprint_report(project_dir_, settings)
    duration = duration_control_report(project_dir_, settings)
    pre_render = quality_before_render(project_dir_, settings)
    preflight = smart_preflight_report(project_dir_, settings)

    items: list[dict[str, Any]] = []
    source_path = project.get("source_video_path") or ""
    source_exists = bool(source_path and Path(source_path).exists())
    items.append(
        _readiness_item(
            "source",
            "Исходное видео готово",
            source_exists,
            16,
            source_path or "Источник ещё не подготовлен.",
            "Подготовь Twitch VOD или импортируй локальное видео.",
        )
    )
    items.append(
        _readiness_item(
            "integrity",
            "Файлы проекта не повреждены",
            bool(integrity.get("critical_ok")),
            14,
            integrity.get("message", ""),
            "Открой Отчёты → Safety Guard и восстанови JSON из backup.",
        )
    )
    items.append(
        _readiness_item(
            "preflight",
            "Preflight разрешает запуск",
            bool(preflight.get("can_start")),
            16,
            preflight.get("title", ""),
            preflight.get("recommendation", "Исправь красные пункты preflight."),
        )
    )
    items.append(
        _readiness_item(
            "cache",
            "Кэш совместим или безопасно новый",
            cache.get("status") in {"new", "compatible"},
            8,
            cache.get("recommendation", ""),
            "Если кэш несовместим — очисти AI/render cache или пересчитай анализ.",
            "warning",
        )
    )
    items.append(
        _readiness_item(
            "resume",
            "Resume/checkpoints доступны",
            bool(checkpoints.get("can_resume")),
            8,
            f"{checkpoints.get('done_count', 0)}/{checkpoints.get('total', 0)} checkpoints",
            "После подготовки источника приложение сможет продолжить после сбоя.",
            "warning",
        )
    )
    items.append(
        _readiness_item(
            "candidates",
            "AI-кандидаты есть",
            bool(candidates),
            8,
            f"{len(candidates)} кандидатов",
            "Запусти анализ или Auto Mode, затем проверь Review Studio.",
            "warning",
        )
    )
    items.append(
        _readiness_item(
            "segments",
            "Финальный список фрагментов есть",
            bool(segments),
            8,
            f"{len(segments)} фрагментов",
            "Добавь лучшие кандидаты в монтаж или запусти автоматическую сборку.",
            "warning",
        )
    )
    fill = float(duration.get("fill_ratio") or 0)
    duration_ok = not segments or 0.75 <= fill <= 1.25
    items.append(
        _readiness_item(
            "duration",
            "Итоговая длительность близка к цели",
            duration_ok,
            7,
            f"цель {duration.get('target')} · собрано {duration.get('current')}",
            "Используй Duration Control: добрать моменты, добавить контекст или снизить score.",
            "warning",
        )
    )
    items.append(
        _readiness_item(
            "render_quality",
            "Проверка перед рендером нормальная",
            bool(pre_render.get("ready", not segments)) or not segments,
            7,
            pre_render.get("summary", "Проверка ещё не запускалась."),
            "Открой Экспорт → Проверить и исправь ошибки перед рендером.",
            "warning",
        )
    )
    items.append(
        _readiness_item(
            "output",
            "Итоговый файл создан",
            bool(final_ready),
            4,
            "highlight_final.mp4 готов" if final_ready else "Рендер ещё не создан.",
            "После Review Studio нажми Рендер YouTube.",
            "info",
        )
    )
    safe_pc = (
        int(settings.get("ai_batch_size") or 1) <= 2
        and int(settings.get("micro_batch_size") or 1) <= 2
        and int(settings.get("metadata_max_segments") or 12) <= 25
    )
    items.append(
        _readiness_item(
            "weak_pc_safe",
            "Настройки безопасны для слабого ПК",
            safe_pc,
            4,
            f"AI batch={settings.get('ai_batch_size')} · micro={settings.get('micro_batch_size')} · metadata={settings.get('metadata_max_segments')}",
            "Выбери hardware preset GTX 1050 Ti — баланс/быстро.",
            "warning",
        )
    )

    total_weight = sum(x["weight"] for x in items) or 1
    earned = sum(x["weight"] for x in items if x["ok"])
    score = round(earned / total_weight * 100)
    blockers = [x for x in items if x["severity"] == "required" and not x["ok"]]
    warnings = [x for x in items if x["severity"] == "warning" and not x["ok"]]
    if blockers:
        grade = "needs_fix"
        title = "Нельзя запускать без исправлений"
    elif score >= 85:
        grade = "ready"
        title = "Проект готов к реальной работе"
    elif score >= 65:
        grade = "beta_ready"
        title = "Можно работать, но есть слабые места"
    else:
        grade = "mvp_risk"
        title = "Нужна подготовка перед долгим видео"
    next_actions = []
    for item in blockers + warnings:
        if item.get("fix"):
            next_actions.append({"id": item["id"], "label": item["label"], "action": item["fix"], "severity": item["severity"]})
    if not next_actions and not final_ready:
        next_actions.append(
            {"id": "render", "label": "Следующий шаг", "action": "Проверь Review Studio и собери итоговый ролик.", "severity": "info"}
        )
    return {
        "ok": not blockers,
        "score": score,
        "grade": grade,
        "title": title,
        "summary": f"{score}/100 · блокеров: {len(blockers)} · предупреждений: {len(warnings)}",
        "items": items,
        "blockers": blockers,
        "warnings": warnings,
        "next_actions": next_actions[:8],
        "status_state": status.get("state") or "ready",
        "checked_at": time.time(),
    }


def build_debug_bundle(project_dir_: Path) -> Path:
    """Create a support/debug ZIP without copying source videos or render files."""
    project = load_project(project_dir_)
    settings = project.get("settings", default_settings())
    out_dir = project_dir_ / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = out_dir / f"highlight_studio_debug_{project_dir_.name}_{int(time.time())}.zip"
    report = {
        "app_version": APP_VERSION,
        "project_id": project_dir_.name,
        "created_at": time.time(),
        "project": {k: v for k, v in project.items() if k != "settings"},
        "settings_important": {
            k: settings.get(k)
            for k in [
                "task_preset",
                "hardware_profile",
                "analysis_profile",
                "target_minutes",
                "ai_batch_size",
                "micro_batch_size",
                "text_model",
                "vision_model",
                "whisper_model",
                "visual_scan_enabled",
                "ocr_enabled",
                "twitch_download_engine",
                "twitch_quality",
                "twitch_download_threads",
            ]
        },
        "readiness": product_readiness_report(project_dir_),
        "integrity": project_integrity_report(project_dir_),
        "preflight": smart_preflight_report(project_dir_, settings),
        "checkpoints": project_checkpoint_status(project_dir_),
        "cache": cache_fingerprint_report(project_dir_, settings),
    }

    def add_text(zf: zipfile.ZipFile, name: str, text: str):
        zf.writestr(name, text)

    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        add_text(
            zf,
            "README.txt",
            "Highlight Studio debug bundle. Видео, рендеры и большие cache-файлы НЕ включены. Можно отправлять для диагностики.\n",
        )
        add_text(zf, "product_readiness.json", json.dumps(report, ensure_ascii=False, indent=2, default=str))
        for name in [
            "project.json",
            "status.json",
            "segments.json",
            "candidates.json",
            "pre_render_check.json",
            "cache_manifest.json",
            "twitch_import.json",
            "youtube_metadata.json",
        ]:
            f = project_dir_ / name
            if f.exists() and f.stat().st_size <= 3_000_000:
                zf.write(f, f"project_files/{name}")
        logs = project_dir_ / "logs.txt"
        if logs.exists():
            text = logs.read_text(encoding="utf-8", errors="replace")
            add_text(zf, "logs_tail.txt", text[-120000:])
    return bundle


def active_job_count() -> int:
    with jobs_lock:
        dead = [pid for pid, thread in jobs.items() if not thread.is_alive()]
        for pid in dead:
            jobs.pop(pid, None)
        return sum(1 for thread in jobs.values() if thread.is_alive())


PAID_BETA_KIND_ENTITLEMENTS = {
    "analyze": "analysis",
    "one_click": "analysis",
    "content_factory": "analysis",
    "benchmark": "analysis",
    "visual_scan": "analysis",
    "ocr_scan": "analysis",
    "audio_events": "analysis",
    "thumbnail_ideas": "analysis",
    "metadata_ai": "metadata_ai",
    "metadata_ai_strict": "metadata_ai",
    "render": "render",
    "preview": "render",
    "rough_preview": "render",
    "hls_preview": "render",
    "render_factory": "render",
    "shorts": "shorts",
    "twitch_import": "twitch",
}


def require_paid_beta_entitlement(entitlement: str) -> dict[str, Any]:
    if WEB_ACCOUNTS_ENABLED:
        return {"can_use": True, "state": "web_account", "required_entitlement": entitlement}
    status = license_status()
    if not status.get("can_use") or not entitlement_allowed(entitlement):
        raise HTTPException(
            status_code=402,
            detail={
                "message": status.get("message") or "Для этой операции нужна активная лицензия.",
                "license": status,
                "required_entitlement": entitlement,
            },
        )
    return status


def runtime_optimized_settings(settings: dict[str, Any], project_dir_: Path | None = None) -> dict[str, Any]:
    """Resolve Auto hardware fields for the exact machine running this job.

    User-facing settings keep `auto` so a project remains portable between PCs.
    The worker receives explicit effective values and records them separately.
    """
    current = validate_settings(settings)
    if not current.get("hardware_auto_optimize", True):
        return current
    capabilities = detect_hardware_capabilities()
    recommended = recommend_hardware_settings(capabilities, current)
    runtime = dict(current)
    auto_runtime = (
        bool(runtime.get("hardware_auto_optimize", True))
        and str(runtime.get("hardware_profile") or "Auto") == "Auto"
        and not bool(runtime.get("hardware_manual_override_enabled", False))
    )
    if auto_runtime or str(runtime.get("whisper_device") or "auto").lower() == "auto":
        runtime["whisper_device"] = recommended.get("whisper_device", "cpu")
    if auto_runtime or str(runtime.get("whisper_compute") or "auto").lower() == "auto":
        runtime["whisper_compute"] = recommended.get("whisper_compute", "int8")
    if auto_runtime or str(runtime.get("video_encoder") or "auto").lower() == "auto":
        runtime["video_encoder"] = recommended.get("video_encoder", "libx264")
    if int(runtime.get("cpu_worker_limit") or 0) <= 0:
        runtime["cpu_worker_limit"] = int(recommended.get("cpu_worker_limit") or 1)
    runtime["gpu_job_limit"] = min(int(runtime.get("gpu_job_limit") or 1), int(recommended.get("gpu_job_limit") or 1))
    runtime["gpu_vram_reserve_mb"] = max(int(runtime.get("gpu_vram_reserve_mb") or 0), int(recommended.get("gpu_vram_reserve_mb") or 0))
    # Logical prompt batches are sequential and are NOT GPU concurrency. Preserve
    # them; gpu_job_limit is the actual guard against simultaneous heavy GPU jobs.
    # This fixes the 10.15.6 low-VRAM regression that forced micro_batch_size=1.
    runtime["ai_batch_size"] = int(runtime.get("ai_batch_size") or recommended.get("ai_batch_size") or 3)
    runtime["micro_batch_size"] = int(runtime.get("micro_batch_size") or recommended.get("micro_batch_size") or 8)
    if str(runtime.get("hardware_decode") or "off").lower() == "auto":
        runtime["hardware_decode"] = str(recommended.get("hardware_decode") or "off")
    runtime["hardware_detected_profile"] = str(recommended.get("hardware_detected_profile") or capabilities.get("profile_id") or "")
    if project_dir_ is not None:
        write_json(
            project_dir_ / "hardware_runtime.json",
            {
                "effective_settings": {
                    key: runtime.get(key)
                    for key in (
                        "hardware_detected_profile", "hardware_quality_guard_enabled", "hardware_manual_override_enabled", "hardware_runtime_policy_version", "cpu_worker_limit", "gpu_job_limit", "gpu_vram_reserve_mb",
                        "whisper_device", "whisper_compute", "whisper_model", "video_encoder", "hardware_decode",
                        "ai_batch_size", "micro_batch_size",
                    )
                },
                "capabilities": capabilities,
                "updated_at": time.time(),
            },
        )
        if bool(runtime.get("hardware_quality_guard_enabled", True)):
            protected_keys = (
                "text_model", "vision_model", "whisper_model", "visual_scan_enabled",
                "visual_scan_interval_seconds", "visual_scan_max_samples",
                "ocr_enabled", "ocr_every_n_visual_samples", "ocr_languages",
                "micro_window_seconds", "micro_min_seconds", "micro_max_seconds",
                "micro_speech_gap_seconds", "micro_source_duration_multiplier",
            )
            write_json(
                project_dir_ / "quality_guard_runtime.json",
                {
                    "enabled": True,
                    "policy": "hardware_auto_may_optimize_devices_workers_and_encoders_but_not_reduce_analysis_coverage",
                    "protected_settings": {key: current.get(key) for key in protected_keys},
                    "effective_settings": {key: runtime.get(key) for key in protected_keys},
                    "unchanged": all(runtime.get(key) == current.get(key) for key in protected_keys),
                    "updated_at": time.time(),
                },
            )
    return runtime


def start_background_job(project_id: str, title: str, fn, kind: str = "generic", *, prepare=None) -> dict[str, Any]:
    """Atomically admit and start one project job.

    ``prepare`` runs only after admission succeeds and while the lifecycle/job
    locks are still held. Endpoints that must persist input for a job can use it
    without creating the old race where an edit was saved but a competing job
    won the slot before the matching worker started.
    """
    entitlement = PAID_BETA_KIND_ENTITLEMENTS.get(kind)
    if entitlement:
        require_paid_beta_entitlement(entitlement)
    d = project_dir(project_id)
    with project_lifecycle_lock(d):
        with jobs_lock:
            if project_id in jobs and jobs[project_id].is_alive():
                return {"started": False, "message": "Job already running for this project"}
            running = sum(1 for thread in jobs.values() if thread.is_alive())
            if running >= MAX_CONCURRENT_JOBS:
                return {"started": False, "message": f"Достигнут лимит одновременных задач: {MAX_CONCURRENT_JOBS}"}
            if prepare is not None:
                prepare(d)
            job_id = create_job(project_id, kind, title, state="queued")
            record_event("job_started", {"kind": kind, "state": "queued"})
            # v10.0.4: write visible queued status before the background thread starts.
            # This prevents the UI from reading an old/done status immediately after
            # clicking Render/Shorts/Analyze and hiding the progress bar.
            write_json(
                d / "status.json",
                {
                    "state": "queued",
                    "progress": 1,
                    "message": title,
                    "stage": f"{kind}_queued",
                    "job_id": job_id,
                    "job_kind": kind,
                    "updated_at": time.time(),
                    "started_at": time.time(),
                    "elapsed_seconds": 0,
                    "eta_seconds": None,
                    "progress_source": "queued",
                },
            )
            DurablePipelineState(d).update_stage(kind, state="pending", message=title)

            def worker():
                logger = JobLogger(d, reset_status=False)
                try:
                    update_job(job_id, state="running", progress=1, message=title, started_at=time.time(), pid=os.getpid())
                    clear_cancel(d)
                    saved_settings = load_project(d).get("settings", default_settings())
                    settings = runtime_optimized_settings(saved_settings, d)
                    logger.log(
                        "Hardware runtime: "
                        f"{settings.get('hardware_detected_profile') or settings.get('hardware_profile')} · "
                        f"Whisper {settings.get('whisper_device')}/{settings.get('whisper_compute')} · "
                        f"encoder {settings.get('video_encoder')} · CPU workers {settings.get('cpu_worker_limit')}"
                    )
                    logger.set_status("running", 1, title, stage=kind, job_id=job_id, job_kind=kind)
                    fn(d, settings, logger)
                    try:
                        save_cache_manifest(d, settings)
                    except Exception as exc:
                        logger.log(f"Cache fingerprint не сохранён: {exc}")
                    st = read_json(d / "status.json", {}) or {}
                    final_state = str(st.get("state") or "").strip().lower()
                    if final_state not in {"done", "completed", "degraded", "cancelled", "error", "failed"}:
                        logger.set_status("done", 100, f"{title}: готово", stage=kind, job_id=job_id, job_kind=kind)
                        st = read_json(d / "status.json", {}) or {}
                        final_state = str(st.get("state") or "done").strip().lower()
                    update_job(
                        job_id,
                        state=final_state,
                        progress=st.get("progress", 100),
                        message=st.get("message", "Готово"),
                        finished_at=time.time(),
                    )
                    record_event("job_completed", {"kind": kind, "state": str(final_state)})
                except (CancelledError, OperationCancelled) as exc:
                    msg = str(exc) or "Остановлено пользователем"
                    current = read_json(d / "status.json", {}) or {}
                    progress = max(0, min(99, int(float(current.get("progress") or 0))))
                    logger.set_status("cancelled", progress, msg, job_id=job_id, job_kind=kind)
                    update_job(job_id, state="cancelled", progress=progress, message=msg, error=msg, finished_at=time.time())
                except Exception as exc:
                    diagnostic_id = uuid.uuid4().hex[:12]
                    msg = f"Ошибка: {exc} (код {diagnostic_id})"
                    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                    logger.log(f"\n[ERROR {diagnostic_id}] {trace}")
                    APP_LOGGER.error("Background job error id=%s project=%s kind=%s\n%s", diagnostic_id, project_id, kind, trace)
                    current = read_json(d / "status.json", {}) or {}
                    # An error is terminal, but it is not 100% successful work. Preserve
                    # the last real progress so the task panel does not show the
                    # contradictory state "Ошибка · 100%".
                    try:
                        error_progress = max(1, min(99, int(float(current.get("progress") or 1))))
                    except Exception:
                        error_progress = 1
                    logger.set_status("error", error_progress, msg, stage=kind, error_code=diagnostic_id, job_id=job_id, job_kind=kind)
                    update_job(
                        job_id,
                        state="error",
                        progress=error_progress,
                        message=msg,
                        error=f"{exc} [diagnostic_id={diagnostic_id}]",
                        finished_at=time.time(),
                    )
                    record_event("job_failed", {"kind": kind, "state": "error", "error_code": diagnostic_id})
                    record_crash(
                        source="worker",
                        error_type=exc.__class__.__name__,
                        message=str(exc),
                        diagnostic_id=diagnostic_id,
                        context={"job_kind": kind, "stage": "background_job", "error_code": diagnostic_id, "platform": sys.platform},
                    )
                finally:
                    with jobs_lock:
                        jobs.pop(project_id, None)

            t = threading.Thread(target=worker, daemon=True)
            jobs[project_id] = t
            t.start()
    return {"started": True, "job_id": job_id}

@app.get("/api/health")
def health(response: Response):
    if not WEB_ACCOUNTS_ENABLED:
        response.set_cookie(
            AUTH_COOKIE_NAME,
            LOCAL_AUTH_TOKEN,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )
    return {
        "ok": True,
        "app_version": APP_VERSION,
        "app_semver": APP_SEMVER,
        "design_id": DESIGN_ID,
        "release_root_name": APP_ROOT.name,
        "frontend_release_exists": (FRONTEND_DIST / "release.json").exists(),
        "max_concurrent_jobs": MAX_CONCURRENT_JOBS,
        "active_jobs": active_job_count(),
        "auth": "cookie",
    }


@app.post("/api/desktop/shutdown")
def desktop_shutdown(request: Request):
    """Ask the packaged desktop engine to stop through Uvicorn's graceful path.

    This endpoint is public only at the authentication middleware layer because
    the Electron main process does not share the browser cookie jar. Access is
    still restricted to loopback and a high-entropy per-process secret passed
    to the sidecar through its environment.
    """
    expected = os.environ.get("HIGHLIGHT_STUDIO_DESKTOP_SHUTDOWN_TOKEN", "").strip()
    supplied = request.headers.get("x-desktop-shutdown-token", "").strip()
    client_host = (request.client.host if request.client else "").strip().lower()
    loopback = client_host in {"127.0.0.1", "::1", "localhost"}
    if not expected or not supplied or not loopback or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="Desktop shutdown authorization failed")
    callback = getattr(request.app.state, "desktop_shutdown_callback", None)
    if not callable(callback):
        raise HTTPException(status_code=503, detail="Graceful desktop shutdown is unavailable")

    # Let the response leave the socket before asking Uvicorn to drain requests
    # and execute the FastAPI lifespan cleanup.
    timer = threading.Timer(0.15, callback)
    timer.daemon = True
    timer.start()
    return {"ok": True, "message": "Graceful desktop shutdown requested"}


# -----------------------------
# System readiness / local tools
# -----------------------------


@app.get("/api/onboarding")
def get_onboarding_status():
    return onboarding_status()


@app.post("/api/onboarding/complete")
def post_onboarding_complete(payload: OnboardingCompleteRequest):
    try:
        result = complete_onboarding(
            ai_mode=payload.ai_mode,
            telemetry_enabled=payload.telemetry_enabled,
            accepted_privacy=payload.accepted_privacy,
            accepted_terms=payload.accepted_terms,
        )
        save_privacy_preferences(
            telemetry_enabled=payload.telemetry_enabled,
            crash_reports_enabled=False,
            accepted_privacy=payload.accepted_privacy,
            accepted_terms=payload.accepted_terms,
        )
        return web_safe_payload(result)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/license/status")
def get_license_status():
    return license_status()


@app.post("/api/license/activate")
def post_license_activate(payload: LicenseActivateRequest):
    result = activate_license(payload.key)
    if not result.get("ok"):
        raise HTTPException(400, result.get("message") or "Активация не удалась")
    record_event("license_activated", {"result": "success"})
    return result


@app.post("/api/license/refresh")
def post_license_refresh():
    return refresh_license()


@app.post("/api/license/deactivate")
def post_license_deactivate():
    return deactivate_license()


@app.get("/api/privacy/status")
def get_privacy_status():
    return privacy_status()


@app.post("/api/privacy/preferences")
def post_privacy_preferences(payload: PrivacyPreferencesRequest):
    try:
        return save_privacy_preferences(
            telemetry_enabled=payload.telemetry_enabled,
            crash_reports_enabled=payload.crash_reports_enabled,
            accepted_privacy=payload.accepted_privacy,
            accepted_terms=payload.accepted_terms,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/telemetry/status")
def get_telemetry_status():
    return telemetry_status()


@app.post("/api/telemetry/flush")
def post_telemetry_flush():
    return flush_telemetry()


@app.get("/api/crash-reports/status")
def get_crash_report_status():
    return crash_status()


@app.post("/api/crash-reports/frontend")
def post_frontend_crash(payload: FrontendCrashRequest):
    report = record_crash(
        source="frontend",
        error_type="FrontendError",
        message=f"{payload.message}\n{payload.stack}",
        context={"route": payload.route, "platform": "browser", "app_version": APP_VERSION},
    )
    return {"ok": True, "crash_id": report["crash_id"]}


@app.post("/api/crash-reports/flush")
def post_crash_reports_flush():
    return flush_crash_reports()


@app.delete("/api/crash-reports")
def delete_crash_reports():
    return clear_crash_reports()


@app.get("/api/release-readiness")
def get_release_readiness():
    return release_readiness()


@app.post("/api/release-readiness/evidence")
def post_release_readiness_evidence(payload: ReleaseEvidenceRequest):
    try:
        return save_release_evidence(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/commerce/config")
def get_commerce_config():
    return commerce_config()


@app.get("/api/beta/metrics")
def get_beta_metrics():
    return beta_metrics()


@app.post("/api/beta/feedback")
def post_beta_feedback(payload: BetaFeedbackRequest):
    try:
        return submit_beta_feedback(
            category=payload.category,
            rating=payload.rating,
            message=payload.message,
            allow_contact=payload.allow_contact,
            email=payload.email,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/onboarding/reset")
def post_onboarding_reset():
    return reset_onboarding()


@app.get("/api/migrations/status")
def get_migration_status():
    return migration_status()


@app.get("/api/startup-recovery")
def get_startup_recovery_status():
    return startup_recovery_status()


@app.get("/api/stability-metrics")
def get_stability_metrics():
    return stability_metrics()


@app.post("/api/support-bundle")
def post_support_bundle():
    bundle = build_support_bundle(system_check=system_check())
    record_event("support_bundle_created", {"result": "success"})
    return FileResponse(bundle, media_type="application/zip", filename=bundle.name)


def _cmd_probe(cmd: str, args: list[str] | None = None, timeout: int = 4) -> dict[str, Any]:
    exe = which(cmd)
    if not exe:
        return {"ok": False, "name": cmd, "path": "", "version": "", "hint": f"{cmd} не найден в PATH"}
    try:
        p = subprocess.run(
            [exe] + (args or ["--version"]), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
        out = (p.stdout or p.stderr or "").strip().splitlines()
        return {
            "ok": p.returncode == 0,
            "name": cmd,
            "path": exe,
            "version": out[0][:220] if out else "installed",
            "hint": "" if p.returncode == 0 else (p.stderr or p.stdout)[-400:],
        }
    except Exception as exc:
        return {"ok": False, "name": cmd, "path": exe, "version": "", "hint": str(exc)[:400]}


def _module_probe(module_name: str, pretty: str | None = None) -> dict[str, Any]:
    ok = importlib.util.find_spec(module_name) is not None
    return {
        "ok": ok,
        "name": pretty or module_name,
        "module": module_name,
        "hint": "" if ok else f"Python module {module_name} не установлен",
    }


def _whisper_vad_probe() -> dict[str, Any]:
    """Verify native Whisper/VAD modules out of process.

    A broken AV/ONNX wheel can terminate the interpreter with SIGBUS/SIGILL,
    which Python cannot catch. A diagnostic probe must never take down the API.
    """
    try:
        code = (
            "import importlib.util, json, pathlib, onnxruntime; "
            "required=['silero_encoder_v5.onnx','silero_decoder_v5.onnx']; "
            "spec=importlib.util.find_spec('faster_whisper'); "
            "assets=pathlib.Path(next(iter(spec.submodule_search_locations)))/'assets' if spec else pathlib.Path('.missing'); "
            "missing=[n for n in required if not (assets/n).is_file()]; "
            "session=onnxruntime.InferenceSession(str(assets/required[0]),providers=['CPUExecutionProvider']) if not missing else None; "
            "print(json.dumps({'ok':not missing,'onnxruntime_version':getattr(onnxruntime,'__version__','unknown'),"
            "'assets':required,'missing':missing}))"
        )
        frozen = bool(getattr(sys, "frozen", False))
        command = [sys.executable, "--probe-whisper-vad"] if frozen else [sys.executable, "-c", code]
        proc = subprocess.run(
            command, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=12,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"native runtime probe exited {proc.returncode}: {(proc.stderr or proc.stdout)[-300:]}")
        output_lines = (proc.stdout or "").strip().splitlines()
        if frozen:
            result_line = next((line for line in output_lines if line.startswith("HS_WHISPER_VAD_RESULT=")), "")
            if not result_line:
                raise RuntimeError("frozen Whisper/VAD probe returned no result marker")
            payload = json.loads(result_line.split("=", 1)[1])
        else:
            payload = json.loads(output_lines[-1] if output_lines else "{}")
        return {"name": "Whisper Silero VAD", **payload,
                "hint": "" if payload.get("ok") else f"Не найдены VAD assets: {', '.join(payload.get('missing') or [])}"}
    except Exception as exc:
        return {
            "ok": False,
            "name": "Whisper Silero VAD",
            "assets": [],
            "missing": [],
            "hint": str(exc)[:400],
        }


@app.get("/api/runtime-components")
def runtime_components():
    vad = _whisper_vad_probe()
    shorts = shorts_quality_components()
    return {"ok": bool(vad["ok"]), "whisper_vad": vad, "shorts_quality": shorts}


@app.get("/api/shorts/quality-components")
def shorts_quality_component_status():
    return {"ok": True, **shorts_quality_components()}


_OLLAMA_PROBE_CACHE: dict[str, Any] = {"ts": 0.0, "url": "", "data": None}


def _ollama_probe(*, force: bool = False, ttl_seconds: float = 5.0) -> dict[str, Any]:
    url = os.getenv("OLLAMA_URL") or DEFAULT_OLLAMA_URL
    now = time.time()
    cached = _OLLAMA_PROBE_CACHE.get("data")
    if (
        not force
        and cached is not None
        and _OLLAMA_PROBE_CACHE.get("url") == url
        and now - float(_OLLAMA_PROBE_CACHE.get("ts") or 0) < max(0.0, float(ttl_seconds))
    ):
        return dict(cached, cached=True)
    result: dict[str, Any] = {
        "ok": False,
        "name": "Ollama server",
        "url": url,
        "models": [],
        "text_model_ok": False,
        "vision_model_ok": False,
        "hint": "Ollama не отвечает",
    }
    try:
        import requests

        r = requests.get(url.rstrip("/") + "/api/tags", timeout=2.5)
        if r.ok:
            data = r.json()
            models = [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)]
            result.update(
                {
                    "ok": True,
                    "models": models,
                    "text_model_ok": any(m.startswith(DEFAULT_TEXT_MODEL) or m == DEFAULT_TEXT_MODEL for m in models),
                    "vision_model_ok": any(m.startswith(DEFAULT_VISION_MODEL) or m == DEFAULT_VISION_MODEL for m in models),
                    "hint": "Ollama отвечает",
                }
            )
        else:
            result["hint"] = f"Ollama HTTP {r.status_code}: {(r.text or '')[:240]}"
    except Exception as exc:
        result["hint"] = str(exc)[:400]
    _OLLAMA_PROBE_CACHE.update({"ts": now, "url": url, "data": dict(result)})
    return dict(result, cached=False)


@app.get("/api/hardware/capabilities")
def hardware_capabilities(force: bool = Query(False)):
    """Return effective CPU/GPU capabilities from the exact runtime used by Highlight Studio."""
    return web_safe_payload(detect_hardware_capabilities(force=bool(force)))


@app.get("/api/system-check")
def system_check():
    """Readiness check for the local stable build.

    This endpoint is intentionally lightweight.  It does not start analysis and
    does not download models; it tells the UI what is missing before the user
    launches a 3-hour Twitch import or long AI job.
    """
    python_supported = (3, 10) <= sys.version_info[:2] < (3, 14)
    hardware = detect_hardware_capabilities(force=True)
    nvidia = hardware.get("nvidia") or {}
    ctranslate2_gpu = hardware.get("ctranslate2") or {}
    ffmpeg_gpu = hardware.get("ffmpeg") or {}
    checks = {
        "python": {
            "ok": python_supported,
            "name": "Python",
            "version": sys.version.split()[0],
            "hint": "Backend запущен" if python_supported else "Поддерживаются Python 3.10–3.13",
        },
        "ffmpeg": _cmd_probe("ffmpeg", ["-version"]),
        "ffprobe": _cmd_probe("ffprobe", ["-version"]),
        "tesseract": _cmd_probe("tesseract", ["--version"]),
        "faster_whisper": _module_probe("faster_whisper", "Whisper"),
        "whisper_vad": _whisper_vad_probe(),
        "yt_dlp": _module_probe("yt_dlp", "yt-dlp"),
        "streamlink": _module_probe("streamlink", "streamlink"),
        "twitch_turbo": twitch_tool_status(default_settings()),
        "ollama_cli": _cmd_probe("ollama", ["--version"]),
        "ollama_server": _ollama_probe(force=True),
        "hardware": {"ok": True, "name": "Hardware auto-detect", "hint": hardware.get("summary", ""), "profile_id": hardware.get("profile_id"), "details": hardware},
        "nvidia_gpu": {"ok": bool(nvidia.get("ok")), "name": "NVIDIA GPU", "hint": nvidia.get("hint", ""), "details": nvidia},
        "ctranslate2_cuda": {"ok": bool(ctranslate2_gpu.get("cuda_ok")), "name": "CTranslate2 CUDA backend", "hint": ctranslate2_gpu.get("hint", ""), "details": ctranslate2_gpu},
        "ffmpeg_nvenc": {"ok": bool(ffmpeg_gpu.get("nvenc_runtime_ok")), "name": "FFmpeg NVENC", "hint": ffmpeg_gpu.get("hint", ""), "details": ffmpeg_gpu},
    }
    basic = (
        checks["python"]["ok"]
        and checks["faster_whisper"]["ok"]
        and checks["whisper_vad"]["ok"]
        and checks["ffmpeg"]["ok"]
        and checks["ffprobe"]["ok"]
        and checks["ollama_server"]["ok"]
        and checks["ollama_server"].get("text_model_ok")
    )
    irl = basic and checks["tesseract"]["ok"] and checks["ollama_server"].get("vision_model_ok")
    twitch_tools = checks.get("twitch_turbo", {})
    twitch = basic and bool(
        (twitch_tools.get("tools") or {}).get("twitchdownloadercli", {}).get("ok") or checks["yt_dlp"]["ok"] or checks["streamlink"]["ok"]
    )
    recommendations: list[str] = []
    if not python_supported:
        recommendations.append("Установи Python 3.10–3.13; эта версия не входит в поддерживаемый диапазон")
    if not checks["ollama_server"]["ok"]:
        recommendations.append("Запусти Ollama: ollama serve")
    if checks["ollama_server"]["ok"] and not checks["ollama_server"].get("text_model_ok"):
        recommendations.append(f"Скачай текстовую модель: ollama pull {DEFAULT_TEXT_MODEL}")
    if checks["ollama_server"]["ok"] and not checks["ollama_server"].get("vision_model_ok"):
        recommendations.append(f"Для IRL/кадров скачай vision-модель: ollama pull {DEFAULT_VISION_MODEL}")
    if not checks["ffmpeg"]["ok"] or not checks["ffprobe"]["ok"]:
        recommendations.append("Установи FFmpeg и добавь ffmpeg/ffprobe в PATH")
    if not checks["tesseract"]["ok"]:
        recommendations.append("Tesseract не найден: OCR чата/донатов будет отключён")
    if not checks["yt_dlp"]["ok"] and not ((checks.get("twitch_turbo", {}).get("tools") or {}).get("twitchdownloadercli", {}).get("ok")):
        recommendations.append(
            "Для быстрого Twitch VOD используй встроенный TwitchDownloaderCLI. Если статус “нет”, запусти проект через START_HERE.bat или проверь папку vendor."
        )
    if not checks["streamlink"]["ok"]:
        recommendations.append("streamlink не найден: Twitch Live import может не работать. Запусти setup_windows.bat")
    if nvidia.get("ok") and not ctranslate2_gpu.get("cuda_ok"):
        recommendations.append("NVIDIA GPU найдена, но CTranslate2 CUDA backend не готов. Запусти commands\\setup\\INSTALL_GPU_ACCELERATION.bat; приложение пока безопасно использует CPU.")
    if nvidia.get("ok") and not ffmpeg_gpu.get("nvenc_runtime_ok"):
        recommendations.append("NVIDIA GPU найдена, но NVENC runtime test не прошёл. Обнови драйвер NVIDIA/FFmpeg; рендер будет использовать libx264.")
    return {
        "ok": bool(basic),
        "ready_basic": bool(basic),
        "ready_irl": bool(irl),
        "ready_twitch": bool(twitch),
        "checks": checks,
        "hardware": hardware,
        "recommended_hardware_settings": hardware.get("recommended_settings") or {},
        "recommendations": recommendations,
        "summary": ("Готово к анализу · " + str(hardware.get("summary") or "hardware detected")) if basic else "Нужно исправить зависимости перед большим анализом",
    }


@app.get("/api/app-audit")
@app.get("/api/packaging-audit")
def get_app_audit():
    return app_audit_report()


@app.post("/api/ollama/stop-models")
def stop_ollama_models(payload: dict[str, Any] | None = None):
    """Unload common Ollama models after Stop/Metadata.

    Ollama is a separate local server, so app cancel cannot always interrupt a
    request already sent to it.  This helper gives the user an explicit button
    to free RAM/VRAM after long Metadata or AI batches.
    """
    ollama = which("ollama")
    if not ollama:
        return {"ok": False, "message": "ollama.exe не найден в PATH"}
    models = (payload or {}).get("models") or [DEFAULT_TEXT_MODEL, DEFAULT_VISION_MODEL]
    results = []
    for model in models:
        try:
            p = subprocess.run([ollama, "stop", str(model)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
            results.append({"model": model, "ok": p.returncode == 0, "output": ((p.stdout or p.stderr or "").strip())[:500]})
        except Exception as exc:
            results.append({"model": model, "ok": False, "output": str(exc)[:500]})
    return {"ok": any(r["ok"] for r in results), "results": results, "message": "Команда выгрузки моделей отправлена в Ollama"}


_OLLAMA_MONITOR_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}


def _ollama_monitor_uncached() -> dict[str, Any]:
    """Read-only Ollama status. Does not start or load models."""
    server = _ollama_probe()
    cli = which("ollama")
    ps_text = ""
    running_models: list[dict[str, Any]] = []
    if cli:
        try:
            p = subprocess.run([cli, "ps"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=6)
            ps_text = (p.stdout or p.stderr or "").strip()
            lines = [x for x in ps_text.splitlines() if x.strip()]
            for line in lines[1:]:
                parts = line.split()
                if parts:
                    running_models.append({"name": parts[0], "raw": line[:500]})
        except Exception as exc:
            ps_text = f"ollama ps не сработал: {exc}"
    return {
        "ok": bool(server.get("ok")),
        "server": server,
        "cli_found": bool(cli),
        "running_models": running_models,
        "ps_text": ps_text,
        "message": "Ollama работает" if server.get("ok") else "Ollama не отвечает",
        "recommendation": "Если после Stop ПК всё ещё загружен, нажми «Выгрузить модели Ollama»."
        if server.get("ok")
        else "Запусти Ollama командой: ollama serve",
    }


def _ollama_monitor_cached(ttl_seconds: float = 12.0) -> dict[str, Any]:
    now = time.time()
    cached = _OLLAMA_MONITOR_CACHE.get("data")
    if cached is not None and now - float(_OLLAMA_MONITOR_CACHE.get("ts") or 0) < ttl_seconds:
        data = dict(cached)
        data["cached"] = True
        return data
    data = _ollama_monitor_uncached()
    _OLLAMA_MONITOR_CACHE["ts"] = now
    _OLLAMA_MONITOR_CACHE["data"] = data
    return dict(data, cached=False)


@app.get("/api/ollama/monitor")
def ollama_monitor():
    """Lightweight cached Ollama status for the UI.

    This avoids hammering `ollama ps` during live project polling while still
    giving the user useful diagnostics.
    """
    return _ollama_monitor_cached(ttl_seconds=8.0)


def project_recovery_status(project_dir_: Path) -> dict[str, Any]:
    paths = project_paths(project_dir_)
    project = read_json(project_dir_ / "project.json", {}) or {}
    settings = validate_settings(project.get("settings", {}))
    ready = source_readiness(project_dir_)
    fresh = freshness_report(project_dir_, settings)
    checkpoints = {
        "source": bool(ready.get("ready") or ready.get("ok")),
        "twitch_cache": bool(project.get("source_type") != "twitch" or ready.get("ready") or ready.get("ok")),
        "transcript": bool(paths["transcript"].exists()),
        "candidates": bool(fresh.get("candidates_current")),
        "segments": bool(fresh.get("segments_current")),
        "metadata": (project_dir_ / "youtube_metadata.json").exists(),
        "final_render": bool(fresh.get("render_current")),
    }
    status = read_json(project_dir_ / "status.json", {}) or {}
    if not checkpoints["source"]:
        next_action = "Подготовить источник видео"
        recommendation = "Исходный файл проекта недоступен. Если это Twitch-проект, подготовь источник заново; если Fast Import — выбери существующий файл."
    elif not checkpoints["transcript"] or not checkpoints["candidates"]:
        next_action = "Найти лучшие моменты"
        recommendation = "Источник готов, но анализ отсутствует или устарел. Запусти AI-анализ заново."
    elif checkpoints["candidates"] and not checkpoints["segments"]:
        next_action = "Проверить кандидаты"
        recommendation = "AI-кандидаты актуальны, но текущий монтаж ещё не подтверждён. Открой Review Studio."
    elif checkpoints["segments"] and not checkpoints["final_render"]:
        next_action = "Собрать видео"
        recommendation = "Монтаж актуален, а финальный рендер отсутствует или устарел. Выполни новый рендер."
    else:
        next_action = "Открыть результат"
        recommendation = "Финальный рендер соответствует текущему монтажу."
    return {
        "project_id": project.get("id"),
        "status": status,
        "checkpoints": checkpoints,
        "next_action": next_action,
        "recommendation": recommendation,
        "can_continue": checkpoints["source"],
        "can_render": checkpoints["segments"],
        "freshness": fresh,
        "source_readiness": ready,
    }


def project_checkpoint_status(project_dir_: Path) -> dict[str, Any]:
    p = project_paths(project_dir_)
    project = read_json(project_dir_ / "project.json", {}) or {}
    settings = validate_settings(project.get("settings", {}))
    candidates = read_json(p["candidates"], []) or []
    segments = read_json(p["segments"], []) or []
    ready = source_readiness(project_dir_)
    fresh = freshness_report(project_dir_, settings)
    checkpoints = [
        {"id": "source", "label": "Видео импортировано", "done": bool(ready.get("ready")), "path": str(ready.get("path") or ""), "details": ready},
        {"id": "audio", "label": "Аудио-анализ готов", "done": (project_dir_ / ARTIFACTS["audio_dynamics"]).exists() or (project_dir_ / ARTIFACTS["audio_events"]).exists(), "path": ARTIFACTS["audio_dynamics"]},
        {"id": "whisper", "label": "Whisper готов", "done": p["transcript"].exists(), "path": ARTIFACTS["transcript"]},
        {"id": "visual", "label": "Visual Scan готов", "done": (project_dir_ / ARTIFACTS["visual"]).exists(), "path": ARTIFACTS["visual"]},
        {"id": "ocr", "label": "OCR готов", "done": (project_dir_ / ARTIFACTS["ocr"]).exists(), "path": ARTIFACTS["ocr"]},
        {"id": "ai_batches", "label": "AI batches обработаны", "done": (project_dir_ / "ai_coverage_report.json").exists() or bool(candidates), "path": "ai_coverage_report.json"},
        {"id": "candidates", "label": "Candidates актуальны", "done": bool(candidates) and bool(fresh.get("candidates_current")), "count": len(candidates), "path": ARTIFACTS["candidates"]},
        {"id": "final_segments", "label": "Final segments актуальны", "done": bool(segments) and bool(fresh.get("segments_current")), "count": len(segments), "path": ARTIFACTS["segments"]},
        {"id": "render", "label": "Render актуален", "done": bool(fresh.get("render_current")), "path": ARTIFACTS["final_render"]},
    ]
    next_item = next((x for x in checkpoints if not x["done"]), None)
    return {
        "ok": True,
        "checkpoints": checkpoints,
        "done_count": sum(1 for x in checkpoints if x["done"]),
        "total": len(checkpoints),
        "next_checkpoint": next_item,
        "can_resume": bool(ready.get("ready")),
        "resume_label": "Продолжить с последнего этапа" if ready.get("ready") else "Сначала подготовь источник",
        "freshness": fresh,
    }


def _cache_short_hash(data: Any) -> str:
    """Stable compact hash used only for cache-manifest compatibility."""
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def cache_fingerprint_report(project_dir_: Path, settings: dict[str, Any]) -> dict[str, Any]:
    p = project_paths(project_dir_)
    video_sig = source_file_signature(p["video"])
    settings_sig = {k: settings.get(k) for k in ANALYSIS_SETTING_KEYS}
    segments = read_json(p["segments"], []) or []
    segments_sig = [{"start": x.get("start"), "end": x.get("end"), "score": x.get("score"), "title": x.get("title")} for x in segments]
    fingerprint = _cache_short_hash(
        {
            "app_version": APP_VERSION,
            "video": video_sig,
            "settings": settings_sig,
            "prompt_hash": _cache_short_hash(settings.get("prompt", "")),
            "preset": settings.get("task_preset"),
            "model": settings.get("text_model"),
            "vision_model": settings.get("vision_model"),
            "segments": segments_sig,
        }
    )
    manifest_path = project_dir_ / "cache_manifest.json"
    old = read_json(manifest_path, {}) or {}
    compatible = old.get("fingerprint") == fingerprint
    caches = {
        "whisper": {
            "path": "transcript.json",
            "exists": p["transcript"].exists(),
            "safe_to_reuse": bool(p["transcript"].exists() and video_sig.get("exists") and old.get("video_hash") == _cache_short_hash(video_sig)),
        },
        "ai_batches": {"path": "ai_batches/", "exists": (project_dir_ / "ai_batches").exists(), "safe_to_reuse": compatible},
        "micro_batches": {"path": "micro_batches/", "exists": (project_dir_ / "micro_batches").exists(), "safe_to_reuse": compatible},
        "render_parts": {"path": "render_parts/", "exists": (project_dir_ / "render_parts").exists(), "safe_to_reuse": compatible},
    }
    if not old:
        status = "new"
        recommendation = "Fingerprint ещё не сохранён. После первого анализа приложение зафиксирует совместимость кэша."
    elif compatible:
        status = "compatible"
        recommendation = "Кэш совместим с текущими видео, prompt, пресетом, моделями и настройками."
    else:
        status = "incompatible"
        recommendation = "Настройки/prompt/model/video изменились. Whisper можно переиспользовать только если видео то же; AI/render кэш лучше пересчитать."
    return {
        "ok": True,
        "status": status,
        "fingerprint": fingerprint,
        "previous_fingerprint": old.get("fingerprint"),
        "compatible": compatible,
        "video_hash": _cache_short_hash(video_sig),
        "settings_hash": _cache_short_hash(settings_sig),
        "prompt_hash": _cache_short_hash(settings.get("prompt", "")),
        "preset_hash": _cache_short_hash(settings.get("task_preset", "balanced")),
        "segments_hash": _cache_short_hash(segments_sig),
        "model_name": settings.get("text_model"),
        "app_version": APP_VERSION,
        "caches": caches,
        "recommendation": recommendation,
    }


def save_cache_manifest(project_dir_: Path, settings: dict[str, Any]) -> dict[str, Any]:
    report = cache_fingerprint_report(project_dir_, settings)
    write_json(
        project_dir_ / "cache_manifest.json",
        {
            "fingerprint": report["fingerprint"],
            "video_hash": report["video_hash"],
            "settings_hash": report["settings_hash"],
            "prompt_hash": report["prompt_hash"],
            "preset_hash": report["preset_hash"],
            "segments_hash": report["segments_hash"],
            "model_name": report["model_name"],
            "app_version": APP_VERSION,
            "saved_at": time.time(),
        },
    )
    return cache_fingerprint_report(project_dir_, settings)


def infer_music_presence(audio_profile: dict[str, Any]) -> str:
    """Conservative RMS-only music hint.

    One-second RMS cannot prove music, so this helper returns ``likely`` only
    for a sustained, low-silence bed with a moderate crest range. Speech-like,
    silent and clipped/loud profiles remain ``unknown`` rather than producing a
    false positive.
    """
    if not isinstance(audio_profile, dict) or not audio_profile.get("enabled"):
        return "unknown"
    try:
        p50 = float(audio_profile.get("p50", -90) if audio_profile.get("p50") is not None else -90)
        p95 = float(audio_profile.get("p95", -90) if audio_profile.get("p95") is not None else -90)
        silence_ratio = float(audio_profile.get("silence_ratio", audio_profile.get("avg_silence_ratio", 1.0)) or 0.0)
    except (TypeError, ValueError):
        return "unknown"
    crest = p95 - p50
    if -38.0 <= p50 <= -10.0 and 3.0 <= crest <= 14.0 and silence_ratio < 0.20:
        return "likely"
    return "unknown"


def auto_video_probe(project_dir_: Path, settings: dict[str, Any]) -> dict[str, Any]:
    project = read_json(project_dir_ / "project.json", {}) or {}
    video = source_video_path(project_dir_)
    meta: dict[str, Any] = {
        "source_type": project.get("source_type") or ("twitch" if project.get("twitch") else "local"),
        "source_ready": bool(source_readiness(project_dir_).get("ready")),
        "path": str(video),
        "duration_seconds": 0,
        "duration_label": "—",
        "width": None,
        "height": None,
        "fps": None,
        "has_audio": False,
        "has_voice": "unknown",
        "has_music": "unknown",
        "silence_ratio": None,
        "looks_like_screen_recording": False,
        "has_chat_or_donates_on_screen": "unknown",
        "file_weight": "unknown",
        "size_gb": 0,
        "warnings": [],
    }
    if not video.exists():
        meta["warnings"].append("Видео ещё не подготовлено. Для Twitch сначала нажми «Подготовить Twitch источник».")
        return meta
    try:
        st = video.stat()
        meta["size_gb"] = round(st.st_size / 1024 / 1024 / 1024, 3)
    except Exception:
        pass
    try:
        info = video_info(video)
        dur = float(info.get("duration") or 0) or video_duration(video)
        meta.update(
            {
                "duration_seconds": round(dur, 2),
                "duration_label": tc(dur),
                "width": info.get("width"),
                "height": info.get("height"),
                "fps": info.get("fps"),
                "looks_like_screen_recording": bool((info.get("width") or 0) >= 1920 and (info.get("height") or 0) >= 1080),
            }
        )
    except Exception as exc:
        meta["warnings"].append(f"Не удалось прочитать ffprobe metadata: {exc}")
    try:
        streams = audio_streams(video)
        meta["has_audio"] = bool(streams)
    except Exception:
        pass
    transcript = read_json(project_paths(project_dir_)["transcript"], []) or []
    if transcript:
        text_len = sum(len(str(x.get("text", ""))) for x in transcript if isinstance(x, dict))
        meta["has_voice"] = "yes" if text_len > 80 else "weak"
    audio = read_json(project_dir_ / "audio_dynamics.json", {}) or {}
    if audio.get("enabled"):
        meta["silence_ratio"] = audio.get("silence_ratio") or audio.get("avg_silence_ratio")
        meta["has_music"] = infer_music_presence(audio)
    ocr = read_json(project_dir_ / ARTIFACTS["ocr"], {}) or {}
    if isinstance(ocr, dict) and ocr.get("items"):
        meta["has_chat_or_donates_on_screen"] = "yes"
    elif settings.get("ocr_enabled") and settings.get("content_type") in {"IRL стрим", "IRL прогулка/город", "Shorts"}:
        meta["has_chat_or_donates_on_screen"] = "likely"
    hours = (float(meta.get("duration_seconds") or 0) / 3600) if meta.get("duration_seconds") else 0
    if meta["size_gb"] >= 10 or hours >= 4 or (meta.get("width") or 0) >= 2560:
        meta["file_weight"] = "heavy"
    elif meta["size_gb"] >= 3 or hours >= 1.5:
        meta["file_weight"] = "medium"
    else:
        meta["file_weight"] = "light"
    return meta


def build_autopilot_recommendation(project_dir_: Path, settings: dict[str, Any]) -> dict[str, Any]:
    probe = auto_video_probe(project_dir_, settings)
    source_type = str(probe.get("source_type") or "local")
    duration_min = float(probe.get("duration_seconds") or 0) / 60
    current_preset = settings.get("task_preset") or "balanced"
    preset = current_preset if current_preset in TASK_PRESETS else "balanced"
    if settings.get("content_type") == "Подкаст":
        preset = "podcast"
    elif settings.get("content_type") == "Спорт":
        preset = "sport"
    elif settings.get("content_type") == "Shorts" or duration_min <= 15:
        preset = "shorts_only"
    elif source_type == "twitch" or settings.get("content_type") in {"IRL стрим", "IRL прогулка/город", "Auto"}:
        preset = current_preset if current_preset in TASK_PRESETS else "balanced"
    target = 35 if duration_min >= 180 else 30 if duration_min >= 60 else 15 if duration_min >= 25 else 8
    if preset == "shorts_only":
        target = 8
    if preset == "podcast" and target < 30:
        target = 30
    hardware = "auto_fast" if probe.get("file_weight") == "heavy" else "auto_balanced"
    if preset == "podcast":
        ocr = False
        visual = False
    else:
        ocr = True
        visual = True
    capabilities = detect_hardware_capabilities()
    base = hardware_preset_settings(settings, hardware)
    base = task_preset_settings(base, preset)
    base.update(
        {
            "target_minutes": target,
            "ocr_enabled": ocr,
            "visual_scan_enabled": visual,
            "micro_cut_enabled": True,
            "auto_target_duration": False,
        }
    )
    final_settings = validate_settings(base)
    return {
        "ok": True,
        "probe": probe,
        "recommended_task_preset": preset,
        "recommended_task_label": TASK_PRESETS.get(preset, {}).get("label", "Сбалансированный / смысл"),
        "recommended_hardware_preset": hardware,
        "recommended_hardware_label": capabilities.get("summary") or capabilities.get("profile_id") or "Автооптимизация",
        "hardware_capabilities": capabilities,
        "ocr_enabled": ocr,
        "visual_scan_enabled": visual,
        "target_minutes": target,
        "micro_cut_enabled": True,
        "settings_patch": final_settings,
        "summary": f"Рекомендую пресет: {TASK_PRESETS.get(preset, {}).get('label', preset)}; hardware: {capabilities.get('summary') or capabilities.get('profile_id')}; OCR: {'включить' if ocr else 'выключить'}; Visual Scan: {'включить' if visual else 'выключить'}; Target: {target} минут; Micro-cut: включить.",
    }


def smart_preflight_report(project_dir_: Path, settings: dict[str, Any], *, force_ollama: bool = False) -> dict[str, Any]:
    project = read_json(project_dir_ / "project.json", {}) or {}
    video = source_video_path(project_dir_)
    checks: list[dict[str, Any]] = []

    def add(id: str, label: str, ok: bool, details: str = "", fix: str = "", required: bool = True):
        checks.append(
            {
                "id": id,
                "label": label,
                "ok": bool(ok),
                "status": "ok" if ok else ("error" if required else "warning"),
                "details": details,
                "fix": fix,
                "required": required,
            }
        )

    add(
        "video_exists",
        "Видео реально существует",
        video.exists(),
        str(video),
        "Для Twitch нажми «Подготовить Twitch источник». Для локального файла проверь, что он не перемещён.",
    )
    add(
        "video_path",
        "Путь к видео не сломан",
        bool(video.exists() and video.is_file()),
        str(video),
        "Укажи правильный source_video_path или пересоздай проект.",
    )
    ffmpeg = which("ffmpeg")
    ffprobe = which("ffprobe")
    add("ffmpeg", "FFmpeg работает", bool(ffmpeg), str(ffmpeg or ""), "Установи FFmpeg и добавь ffmpeg.exe в PATH.")
    add("ffprobe", "FFprobe работает", bool(ffprobe), str(ffprobe or ""), "Установи FFmpeg и добавь ffprobe.exe в PATH.")
    ollama = _ollama_probe(force=force_ollama)
    add("ollama", "Ollama запущен", bool(ollama.get("ok")), ollama.get("hint", ""), "Запусти Ollama: ollama serve")
    model_names = [str(name) for name in (ollama.get("models") or []) if name]

    def has_ollama_model(model: str) -> bool:
        model = str(model or "").strip()
        return bool(model and any(name == model or name.startswith(model + ":") for name in model_names))

    text_model = str(settings.get("text_model") or DEFAULT_TEXT_MODEL)
    text_ok = bool(ollama.get("ok") and has_ollama_model(text_model))
    add(
        "text_model",
        "Текстовая модель установлена",
        text_ok,
        text_model,
        f"Скачай модель: ollama pull {text_model}",
    )
    # Whole-video Visual Scan only extracts frames. The vision LLM is used by
    # pipeline.analyze() only for Medium/Full visual_mode candidate analysis.
    vision_needed = settings.get("visual_mode") in {"Средний", "Полный"}
    vision_model = str(settings.get("vision_model") or DEFAULT_VISION_MODEL)
    vision_ok = bool(ollama.get("ok") and has_ollama_model(vision_model))
    add(
        "vision_model",
        "Vision-модель готова для Visual Scan",
        (not vision_needed) or vision_ok,
        vision_model,
        f"Скачай vision-модель: ollama pull {vision_model}",
        required=vision_needed,
    )
    whisper_module = _module_probe("faster_whisper", "faster-whisper")
    add(
        "whisper",
        "Whisper доступен",
        bool(whisper_module.get("ok")),
        whisper_module.get("hint", ""),
        "Запусти setup_windows.bat или установи faster-whisper.",
    )
    twitch_tools = twitch_tool_status(settings)
    yt_ok = _module_probe("yt_dlp", "yt-dlp").get("ok")
    tdcli_ok = bool((twitch_tools.get("tools") or {}).get("twitchdownloadercli", {}).get("ok"))
    source_is_twitch = project.get("source_type") == "twitch"
    twitch_downloader_needed = bool(source_is_twitch and not video.exists())
    add(
        "twitch_downloader",
        "Twitch downloader готов",
        bool(yt_ok or tdcli_ok or not twitch_downloader_needed),
        f"recommended={twitch_tools.get('recommended_engine')}",
        "Установи TwitchDownloaderCLI.exe/yt-dlp или скачай VOD через TwitchLink и импортируй mp4.",
        required=twitch_downloader_needed,
    )
    try:
        duration = video_duration(video) if video.exists() else 0.0
        if duration > 0:
            interval = max(3, int(settings.get("visual_scan_interval_seconds", 5) or 5))
            max_samples = max(20, int(settings.get("visual_scan_max_samples", 1200) or 1200))
            visual_samples = min(max_samples, math.ceil(duration / interval)) if settings.get("visual_scan_enabled", True) else 0
            disk = estimate_analysis_disk_budget(
                project_dir_,
                duration_seconds=duration,
                source_path=video,
                chunk_seconds=max(1, int(settings.get("chunk_seconds", 900) or 900)),
                visual_scan_samples=visual_samples,
                audio_dynamics_enabled=bool(settings.get("audio_dynamics_enabled", True)),
            )
            add(
                "disk",
                "Хватает места на диске",
                disk["ok"],
                disk["message"],
                "Освободи указанное место или перенеси проект на диск с достаточным запасом.",
                required=True,
            )
        else:
            add("disk", "Проверка места на диске", False, "Не удалось определить длительность source.", "Проверь исходное видео.")
    except Exception as exc:
        add("disk", "Проверка места на диске", False, str(exc), "Проверь права доступа к папке проекта.")
    try:
        test = project_dir_ / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink(missing_ok=True)
        add("write", "Есть права записи", True, str(project_dir_), "")
    except Exception as exc:
        add("write", "Есть права записи", False, str(exc), "Запусти приложение из папки, где есть права записи.")
    dur = 0.0
    if video.exists():
        try:
            dur = video_duration(video)
        except Exception:
            pass
    target = float(settings.get("target_minutes") or 0) * 60
    add(
        "target_duration",
        "Target duration не больше исходника",
        (not dur) or target <= dur * 0.98,
        f"target={tc(target)}, video={tc(dur)}",
        "Уменьши target_minutes или отключи auto_target_duration.",
        required=False,
    )
    failed_required = [c for c in checks if c["required"] and not c["ok"]]
    warnings = [c for c in checks if (not c["required"]) and not c["ok"]]
    return {
        "ok": not failed_required,
        "can_start": not failed_required,
        "title": "Можно запускать" if not failed_required else "Нельзя запускать — исправь ошибки",
        "checks": checks,
        "errors": failed_required,
        "warnings": warnings,
        "recommendation": "Запускай «Собрать нарезку»."
        if not failed_required
        else failed_required[0].get("fix", "Исправь красные пункты."),
    }


def duration_control_report(project_dir_: Path, settings: dict[str, Any]) -> dict[str, Any]:
    segs = read_json(project_paths(project_dir_)["segments"], []) or []
    candidates = read_json(project_paths(project_dir_)["candidates"], []) or []
    target = float(settings.get("target_minutes") or 30) * 60
    total = sum(max(0, float(s.get("end", 0) or 0) - float(s.get("start", 0) or 0)) for s in segs)
    missing = max(0, target - total)
    over = max(0, total - target)
    return {
        "ok": True,
        "target_seconds": round(target, 2),
        "target": tc(target),
        "current_seconds": round(total, 2),
        "current": tc(total),
        "missing_seconds": round(missing, 2),
        "missing": tc(missing),
        "over_seconds": round(over, 2),
        "over": tc(over),
        "fill_ratio": round(total / max(1, target), 3),
        "segments": len(segs),
        "candidates": len(candidates),
        "status": "underfilled" if missing > 60 else "overfilled" if over > 60 else "good",
        "actions": [
            {"id": "add_similar", "label": "Добрать похожие моменты"},
            {"id": "lower_score", "label": "Снизить минимальный score"},
            {"id": "add_context", "label": "Добавить контекст"},
            {"id": "add_weaker", "label": "Добавить более слабые, но нормальные"},
            {"id": "make_denser", "label": "Сделать плотнее монтаж"},
            {"id": "keep_as_is", "label": "Оставить как есть"},
        ],
    }


def apply_duration_action(project_dir_: Path, settings: dict[str, Any], action: str) -> dict[str, Any]:
    p = project_paths(project_dir_)
    segs = read_json(p["segments"], []) or []
    candidates = sorted(read_json(p["candidates"], []) or [], key=lambda x: float(x.get("score", 0) or 0), reverse=True)
    target = float(settings.get("target_minutes") or 30) * 60

    def overlap(a, b):
        return max(float(a.get("start", 0)), float(b.get("start", 0))) < min(float(a.get("end", 0)), float(b.get("end", 0)))

    if action in {"add_similar", "lower_score", "add_weaker"}:
        min_score = 7.0 if action == "add_similar" else 6.0 if action == "lower_score" else 5.2
        rejected_decisions = {"remove", "reject", "rejected", "delete", "drop"}
        for c in candidates:
            current = sum(max(0, float(s.get("end", 0) or 0) - float(s.get("start", 0) or 0)) for s in segs)
            if current >= target * float(settings.get("target_fill_ratio", 0.94)):
                break
            decision = str(c.get("decision") or "").strip().lower()
            if decision in rejected_decisions:
                continue
            if float(c.get("score", 0) or 0) >= min_score and not any(overlap(c, s) for s in segs):
                segs.append(dict(c))
    elif action == "add_context":
        segs = [{**s, "start": max(0, float(s.get("start", 0) or 0) - 5), "end": float(s.get("end", 0) or 0) + 5} for s in segs]
    elif action == "make_denser":
        segs = [
            {**s, "start": float(s.get("start", 0) or 0), "end": max(float(s.get("start", 0) or 0) + 5, float(s.get("end", 0) or 0) - 2)}
            for s in segs
        ]
    # keep_as_is intentionally does nothing.
    segs = sorted(segs, key=lambda x: float(x.get("start", 0) or 0))
    try:
        source_duration = video_duration(source_video_path(project_dir_))
    except Exception:
        source_duration = None
    segs = update_segments(project_dir_, segs, source_duration=source_duration)
    return {"ok": True, "action": action, "segments": segs, "segments_revision": segments_revision(project_dir_, settings), "duration_control": duration_control_report(project_dir_, settings)}


def quality_before_render(project_dir_: Path, settings: dict[str, Any]) -> dict[str, Any]:
    p = project_paths(project_dir_)
    base = pre_render_check(project_dir_)
    segs = read_json(p["segments"], []) or []
    duration = sum(max(0, float(s.get("end", 0) or 0) - float(s.get("start", 0) or 0)) for s in segs)
    scores = [float(s.get("score", 0) or 0) for s in segs]
    duplicates = 0
    for i, a in enumerate(segs):
        text_a = (str(a.get("title", "")) + str(a.get("reason", "")))[:60].lower()
        for b in segs[i + 1 :]:
            text_b = (str(b.get("title", "")) + str(b.get("reason", "")))[:60].lower()
            if text_a and text_a == text_b:
                duplicates += 1
    missing_files = []
    if not p["video"].exists():
        missing_files.append(str(p["video"]))
    try:
        free_gb = shutil.disk_usage(project_dir_).free / (1024**3)
    except Exception:
        free_gb = 0
    problems = []
    problems.extend(base.get("errors", []))
    if duplicates:
        problems.append(f"Возможные повторы: {duplicates}")
    if any((float(s.get("end", 0) or 0) - float(s.get("start", 0) or 0)) < 6 for s in segs):
        problems.append("Есть слишком короткие моменты до 6 сек")
    if missing_files:
        problems.append("Не найден исходный файл")
    if free_gb < 3:
        problems.append("Мало места на диске для рендера")
    quality_score = max(0, min(100, 100 - len(problems) * 14 - len(base.get("warnings", [])) * 5))
    return {
        "ok": not problems,
        "can_render": not base.get("errors") and not missing_files and len(segs) > 0,
        "quality_label": "хорошее" if quality_score >= 75 else "нужно проверить" if quality_score >= 50 else "есть проблемы",
        "quality_score": quality_score,
        "segments": len(segs),
        "duration": tc(duration),
        "duration_seconds": round(duration, 2),
        "avg_score": round(sum(scores) / max(1, len(scores)), 2),
        "duplicates": duplicates,
        "too_short": sum(1 for s in segs if (float(s.get("end", 0) or 0) - float(s.get("start", 0) or 0)) < 6),
        "sharp_cuts_risk": sum(1 for s in segs if not s.get("context_before_seconds") and float(s.get("score", 0) or 0) >= 8),
        "missing_files": missing_files,
        "free_disk_gb": round(free_gb, 2),
        "errors": base.get("errors", []),
        "warnings": base.get("warnings", []),
        "problems": problems,
        "summary": "Качество хорошее. Можно рендерить."
        if not problems
        else f"Есть {len(problems)} проблемы. Лучше исправить перед рендером.",
    }


def creator_pack(project_dir_: Path, settings: dict[str, Any]) -> dict[str, Any]:
    metadata = read_json(project_dir_ / "youtube_metadata.json", {}) or {}
    segs = read_json(project_paths(project_dir_)["segments"], []) or []
    top = sorted((x for x in segs if isinstance(x, dict)), key=lambda x: float(x.get("score", 0) or 0), reverse=True)[:10]
    metadata = metadata if isinstance(metadata, dict) else {}
    context = build_stream_context(project_dir_, settings, max_items=25)
    specific = build_specific_metadata_fallback(project_dir_, settings, context=context)
    audit = metadata_specificity_audit(metadata, context) if isinstance(metadata, dict) else {"passed": False}
    metadata_is_current = (
        bool(metadata.get("context_signature"))
        and metadata.get("context_signature") == context.get("signature")
        and bool(audit.get("passed"))
    )
    source = metadata if metadata_is_current else specific

    titles: list[str] = []
    for value in list(source.get("titles") or []) + list(specific.get("titles") or []):
        title = str(value or "").strip()
        if title and title not in titles:
            titles.append(title[:100])
        if len(titles) >= 10:
            break
    tags: list[str] = []
    # Put evidence terms before generic platform tags.
    for value in list(specific.get("tags") or []) + list(source.get("tags") or []):
        tag = str(value or "").strip()
        if tag and tag.lower() not in {item.lower() for item in tags}:
            tags.append(tag)
        if len(tags) >= 30:
            break
    chapters = source.get("chapters") or specific.get("chapters") or []
    shorts = [
        {
            "start": x.get("start"),
            "end": x.get("end"),
            "title": x.get("title"),
            "score": x.get("score"),
            "why": x.get("viewer_value") or x.get("reason"),
        }
        for x in top[:5]
    ]
    thumb = source.get("thumbnail_ideas") or specific.get("thumbnail_ideas") or []
    return {
        "ok": bool(source.get("ok", False)),
        "error": source.get("error", ""),
        "warning": source.get("warning", ""),
        "titles": titles[:10],
        "short_titles": source.get("short_titles") or [],
        "short_summary": source.get("short_summary") or "",
        "montage_summary": source.get("montage_summary") or "",
        "hashtags": source.get("hashtags") or [],
        "hook_options": source.get("hook_options") or [],
        "description": source.get("description") or specific.get("description") or "",
        "tags": tags[:30],
        "chapters": chapters,
        "thumbnail_ideas": thumb,
        "best_shorts": shorts,
        "vertical_export_hint": "Нажми Export → Shorts pack, чтобы собрать вертикальные клипы 9:16 из этих моментов.",
        "generation_mode": "ai_specific" if metadata_is_current and metadata.get("ai_used") else "scene_specific_local",
        "context_signature": context.get("signature"),
        "specificity_audit": audit if metadata_is_current else specific.get("specificity_audit"),
        "stream_context_summary": specific.get("stream_context_summary"),
    }


def _moment_text_blob(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(k) or "")
        for k in ["title", "reason", "text_preview", "ai_explanation", "what_happens", "why_selected", "viewer_value", "moment_type"]
    ).strip()


def _local_moment_explanation(item: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, str]:
    """Cheap deterministic fallback explanations for Review Studio.

    This does not call Ollama and does not overwrite real AI fields unless they
    are missing. It makes older candidates usable in the new review UI.
    """
    settings = settings or {}
    blob = _moment_text_blob(item).lower()
    title = str(item.get("title") or item.get("moment_type") or "момент").strip()
    score = float(item.get("score") or item.get("ai_score") or 0)
    tags: list[str] = []
    if any(w in blob for w in ["смех", "смеш", "угар", "мем", "funny", "laugh"]):
        tags.append("смешная реакция")
    if any(w in blob for w in ["конфликт", "спор", "хаос", "крик", "conflict"]):
        tags.append("конфликт/хаос")
    if any(w in blob for w in ["донат", "чат", "donation", "chat"]):
        tags.append("чат/донат")
    if any(w in blob for w in ["спорт", "теннис", "розыгрыш", "матч", "sport", "tennis"]):
        tags.append("спорт")
    if any(w in blob for w in ["истор", "разговор", "диалог", "dialog", "story"]):
        tags.append("диалог/история")
    kind = ", ".join(tags[:3]) or str(
        item.get("moment_type") or settings.get("task_preset_label") or settings.get("content_type") or "highlight"
    )
    if score >= 8.5:
        score_line = "высокий score, потенциальный hook или сильный момент"
    elif score >= 7:
        score_line = "хороший score, момент стоит проверить вручную"
    elif score >= 5.5:
        score_line = "средний score, может пригодиться для добора длительности"
    else:
        score_line = "низкий score, лучше оставить только при нехватке длительности"
    duration = max(0.0, float(item.get("end", 0) or 0) - float(item.get("start", 0) or 0))
    risk_parts = []
    if duration < 8:
        risk_parts.append("момент слишком короткий")
    if not item.get("context_before_seconds") and score >= 8:
        risk_parts.append("проверь контекст за 5–10 секунд до начала")
    if any(w in blob for w in ["тишин", "silence", "пауза"]):
        risk_parts.append("возможна тишина или слабая динамика")
    if not risk_parts:
        risk_parts.append("проверь, понятен ли момент без длинного контекста")
    return {
        "what_happens": item.get("what_happens") or f"Фрагмент «{title}»: {kind}.",
        "why_selected": item.get("why_selected")
        or item.get("reason")
        or f"Выбран потому что {score_line} и подходит под текущий task-пресет.",
        "viewer_value": item.get("viewer_value")
        or f"Зрителю может быть интересно из-за категории: {kind}. Такой момент легче превратить в highlight/Shorts.",
        "risk": item.get("risk") or "; ".join(risk_parts) + ".",
    }


def ai_quality_audit(project_dir_: Path) -> dict[str, Any]:
    """Audit whether Review Studio has enough quality signals to be useful."""
    project = read_json(project_dir_ / "project.json", {}) or {}
    settings = project.get("settings", default_settings())
    p = project_paths(project_dir_)
    candidates = read_json(p["candidates"], []) or []
    segments = read_json(p["segments"], []) or []
    all_items = list(candidates) + list(segments)

    def has_explain(x: dict[str, Any]) -> bool:
        return bool(
            (x.get("what_happens") and x.get("why_selected") and x.get("viewer_value")) or x.get("ai_explanation") or x.get("reason")
        )

    missing_explain = [x for x in all_items if not has_explain(x)]
    weak = [x for x in candidates if float(x.get("score", 0) or 0) < 6.5]
    strong = [x for x in candidates if float(x.get("score", 0) or 0) >= 8.0]
    short = [x for x in segments if (float(x.get("end", 0) or 0) - float(x.get("start", 0) or 0)) < 8]
    context_risk = [x for x in segments if float(x.get("score", 0) or 0) >= 8.0 and not x.get("context_before_seconds")]
    duplicate_pairs = []
    seen: dict[str, int] = {}
    for i, x in enumerate(all_items):
        key = _moment_text_blob(x).lower()[:80]
        if key and key in seen:
            duplicate_pairs.append([seen[key], i])
        elif key:
            seen[key] = i
    type_counts: dict[str, int] = {}
    for x in candidates:
        blob = _moment_text_blob(x).lower()
        if any(w in blob for w in ["смех", "смеш", "угар", "мем", "funny"]):
            type_counts["смешное"] = type_counts.get("смешное", 0) + 1
        if any(w in blob for w in ["конфликт", "спор", "хаос", "крик"]):
            type_counts["конфликт"] = type_counts.get("конфликт", 0) + 1
        if any(w in blob for w in ["донат", "чат"]):
            type_counts["чат/донат"] = type_counts.get("чат/донат", 0) + 1
        if any(w in blob for w in ["спорт", "теннис"]):
            type_counts["спорт"] = type_counts.get("спорт", 0) + 1
        if any(w in blob for w in ["истор", "диалог", "разговор"]):
            type_counts["диалог"] = type_counts.get("диалог", 0) + 1
    score = 100
    if candidates and len(strong) < max(3, min(10, len(candidates) // 10)):
        score -= 12
    if all_items:
        score -= min(25, round(len(missing_explain) / max(1, len(all_items)) * 35))
    score -= min(18, len(short) * 3)
    score -= min(15, len(context_risk) * 2)
    score -= min(15, len(duplicate_pairs) * 4)
    score = max(0, min(100, score))
    recommendations = []
    if missing_explain:
        recommendations.append("Запусти Backfill explanations: старые кандидаты получат понятные причины выбора без вызова Ollama.")
    if short:
        recommendations.append("Есть слишком короткие фрагменты: добавь контекст или укороти только после ручной проверки.")
    if context_risk:
        recommendations.append("У сильных моментов нет контекста до начала: добавь +5 сек до для hook-кандидатов.")
    if len(weak) > len(candidates) * 0.45 and candidates:
        recommendations.append("Много слабых кандидатов: выбери task-пресет точнее или включи Quality/AI Strict для лучшего отбора.")
    if not recommendations:
        recommendations.append("Review Studio выглядит готовым: проверь лучшие моменты и запускай pre-render check.")
    return {
        "ok": score >= 70,
        "score": score,
        "label": "хорошо" if score >= 80 else "нужно проверить" if score >= 55 else "много рисков",
        "candidates": len(candidates),
        "segments": len(segments),
        "strong_candidates": len(strong),
        "weak_candidates": len(weak),
        "missing_explanations": len(missing_explain),
        "short_segments": len(short),
        "context_risk": len(context_risk),
        "duplicate_risk": len(duplicate_pairs),
        "type_counts": type_counts,
        "recommendations": recommendations,
        "checked_at": time.time(),
        "task_preset": settings.get("task_preset_label") or settings.get("task_preset"),
    }


def backfill_moment_explanations(project_dir_: Path) -> dict[str, Any]:
    project = read_json(project_dir_ / "project.json", {}) or {}
    settings = project.get("settings", default_settings())
    p = project_paths(project_dir_)
    changed = {"candidates": 0, "segments": 0}
    for name, path in [("candidates", p["candidates"]), ("segments", p["segments"])]:
        items = read_json(path, []) or []
        out = []
        for item in items:
            x = dict(item)
            exp = _local_moment_explanation(x, settings)
            local_changed = False
            for key, value in exp.items():
                if not x.get(key):
                    x[key] = value
                    local_changed = True
            if not x.get("ai_explanation"):
                x["ai_explanation"] = (
                    f"{x.get('why_selected') or exp['why_selected']} Зрительская ценность: {x.get('viewer_value') or exp['viewer_value']}"
                )
                local_changed = True
            if local_changed:
                changed[name] += 1
            out.append(x)
        if out != items:
            write_json(path, out)
    audit = ai_quality_audit(project_dir_)
    write_json(project_dir_ / "ai_quality_audit.json", audit)
    return {"ok": True, "changed": changed, "audit": audit, "message": "Объяснения моментов обновлены локально, без тяжёлого AI-запроса."}


def normalize_project_segments(project_dir_: Path, min_seconds: float = 5.0) -> dict[str, Any]:
    p = project_paths(project_dir_)
    raw = read_json(p["segments"], []) or []
    normalized = []
    removed = []
    seen = set()

    def safe_item_snapshot(value: Any) -> Any:
        """Make corrupt timeline data safe for strict JSON responses/logs."""
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        if isinstance(value, dict):
            return {str(key): safe_item_snapshot(item_value) for key, item_value in value.items()}
        if isinstance(value, (list, tuple)):
            return [safe_item_snapshot(item_value) for item_value in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    # A short source may be shorter than the normal minimum clip length.  The
    # previous implementation extended such clips past EOF (for example a
    # 3-second source became a 0-5 second segment), which could later make
    # FFmpeg fail or produce misleading timeline data.  Probe opportunistically
    # and keep the old offline behaviour when no readable source is available.
    source_duration = 0.0
    video = p["video"]
    if video.exists():
        try:
            probed_duration = float(video_duration(video))
            source_duration = max(0.0, probed_duration) if math.isfinite(probed_duration) else 0.0
        except Exception:
            source_duration = 0.0

    for item in raw:
        start_raw = item.get("start", 0)
        end_raw = item.get("end", 0)
        # ``read_json`` converts legacy NaN/Infinity values to None. Treat that
        # marker as invalid rather than silently turning it into timestamp 0.
        if start_raw is None or end_raw is None:
            removed.append({"reason": "non_finite_time", "item": safe_item_snapshot(item)})
            continue
        try:
            start = float(start_raw)
            end = float(end_raw)
        except (TypeError, ValueError, OverflowError):
            removed.append({"reason": "bad_time", "item": safe_item_snapshot(item)})
            continue
        # Check finiteness before max/min: max(0.0, NaN) evaluates to 0.0 in
        # Python and would otherwise turn corrupted timeline data into a clip.
        if not math.isfinite(start) or not math.isfinite(end):
            removed.append({"reason": "non_finite_time", "item": safe_item_snapshot(item)})
            continue
        start = max(0.0, start)

        if source_duration > 0:
            if start >= source_duration:
                removed.append({"reason": "start_after_source_end", "item": safe_item_snapshot(item)})
                continue
            end = min(end, source_duration)

        if end <= start:
            removed.append({"reason": "end_before_start", "item": safe_item_snapshot(item)})
            continue

        if end - start < min_seconds:
            if source_duration > 0:
                target = min(float(min_seconds), source_duration)
                end = min(source_duration, max(end, start + target))
                if end - start < target:
                    start = max(0.0, end - target)
            else:
                end = start + min_seconds

        # Defend against floating-point edge cases after clamping.
        if end <= start:
            removed.append({"reason": "empty_after_clamp", "item": safe_item_snapshot(item)})
            continue

        key = (round(start, 1), round(end, 1), str(item.get("title") or "")[:80])
        if key in seen:
            removed.append({"reason": "duplicate", "item": safe_item_snapshot(item)})
            continue
        seen.add(key)
        normalized.append({**dict(item), "start": round(start, 2), "end": round(end, 2)})
    normalized.sort(key=lambda x: float(x.get("start", 0) or 0))
    for i, item in enumerate(normalized, start=1):
        item["id"] = i
    if normalized != raw:
        write_json(p["segments"], normalized)
        project = load_project(project_dir_)
        mark_segments_updated(project_dir_, project.get("settings", default_settings()))
    return {
        "ok": True,
        "before": len(raw),
        "after": len(normalized),
        "removed": len(removed),
        "removed_items": removed[:20],
        "segments": normalized,
        "source_duration": round(source_duration, 3) if source_duration > 0 else None,
    }


def project_doctor(project_dir_: Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Safe one-click maintenance. Does not render, download or call Ollama."""
    payload = payload or {}
    actions: list[dict[str, Any]] = []
    # 1) Ensure common folders exist.
    for folder in ["outputs", "preview", "debug", "cache", "factory"]:
        path = project_dir_ / folder
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            actions.append({"id": "mkdir", "message": f"Создана папка {folder}"})
    # 2) Ensure minimal JSON files exist, without overwriting user data.
    defaults = {
        "status.json": {"state": "ready", "progress": 0, "message": "Проект готов.", "updated_at": time.time()},
        "candidates.json": [],
        "segments.json": [],
        "user_preferences.json": {"feedback": []},
    }
    for name, default in defaults.items():
        path = project_dir_ / name
        if not path.exists():
            write_json(path, default)
            actions.append({"id": "json_create", "message": f"Создан {name}"})
    # 3) Repair backups when possible.
    repaired = repair_project_json_backups(project_dir_)
    if repaired.get("repaired"):
        actions.append({"id": "repair", "message": f"Восстановлено JSON из backup: {repaired.get('repaired')}"})
    # 4) Normalize segments to reduce render failures.
    norm = normalize_project_segments(project_dir_)
    if norm.get("removed") or norm.get("before") != norm.get("after"):
        actions.append(
            {
                "id": "segments",
                "message": f"Сегменты нормализованы: {norm.get('before')} → {norm.get('after')}, удалено {norm.get('removed')}",
            }
        )
    # 5) Backfill explanations when user enabled it or when many missing.
    audit_before = ai_quality_audit(project_dir_)
    if payload.get("backfill_explanations", True) and audit_before.get("missing_explanations", 0):
        backfill = backfill_moment_explanations(project_dir_)
        actions.append(
            {
                "id": "explanations",
                "message": f"Добавлены объяснения: candidates {backfill['changed']['candidates']}, segments {backfill['changed']['segments']}",
            }
        )
    # 6) Update project metadata only.
    project = read_json(project_dir_ / "project.json", {}) or {}
    if project:
        project["app_version"] = APP_VERSION
        project["settings_version"] = APP_VERSION
        project["project_schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
        project["updated_at"] = time.time()
        write_json(project_dir_ / "project.json", project)
    report = {
        "ok": True,
        "title": "Project Doctor завершён",
        "actions": actions or [{"id": "noop", "message": "Критичных проблем не найдено. Изменения не требовались."}],
        "integrity": project_integrity_report(project_dir_),
        "ai_quality": ai_quality_audit(project_dir_),
        "product_readiness": product_readiness_report(project_dir_),
        "checked_at": time.time(),
    }
    write_json(project_dir_ / "project_doctor_report.json", report)
    return report


def product_test_plan(project_dir_: Path | None = None) -> dict[str, Any]:
    """Manual acceptance plan for moving from beta/MVP to reliable product."""
    project = read_json(project_dir_ / "project.json", {}) if project_dir_ else {}
    preset = (project.get("settings", {}) or {}).get("task_preset_label") or "любой"
    scenarios = [
        {
            "id": "short_local",
            "title": "Локальный тест 5–10 минут",
            "goal": "Проверить импорт, preflight, анализ, Review и render без долгого ожидания.",
            "pass": "Есть candidates, final segments, pre-render OK, highlight_final.mp4 создан.",
        },
        {
            "id": "twitch_speed",
            "title": "Twitch VOD speed-test",
            "goal": "Проверить TDCLI/aria2/yt-dlp fallback на реальной ссылке.",
            "pass": "Auto Turbo выбирает самый быстрый движок и создаёт локальный cache.",
        },
        {
            "id": "review_quality",
            "title": "Review Studio качество",
            "goal": f"Проверить, что пресет {preset} даёт понятные моменты и объяснения.",
            "pass": "Не менее 40–60% кандидатов выглядят пригодными после ручной проверки.",
        },
        {
            "id": "resume",
            "title": "Resume после остановки",
            "goal": "Остановить анализ/рендер и продолжить без потери проекта.",
            "pass": "Checkpoints показывают последний этап, проект продолжает без перескачивания VOD.",
        },
        {
            "id": "cache",
            "title": "Cache fingerprint",
            "goal": "Поменять prompt/settings и проверить, что старый AI/render cache не используется слепо.",
            "pass": "Приложение честно просит пересчитать несовместимый слой.",
        },
        {
            "id": "long_vod",
            "title": "Длинный VOD 3–6 часов",
            "goal": "Финальный реальный тест продукта.",
            "pass": "Скачивание быстрое, анализ не зависает, итоговая длительность близка к цели, render готов.",
        },
    ]
    return {
        "ok": True,
        "title": "Product Acceptance Test Plan",
        "summary": "Минимальный набор ручных тестов перед тем, как считать проект готовым продуктом, а не beta/MVP.",
        "scenarios": scenarios,
        "acceptance_metrics": [
            "VOD download не является узким местом",
            "AI выбирает пригодные моменты",
            "Review Studio позволяет быстро исправить результат",
            "Render не стартует с явными ошибками",
            "Resume/checkpoints помогают после сбоя",
        ],
        "checked_at": time.time(),
    }


def project_history_items(limit: int = 30, allowed_ids: set[str] | None = None, expose_paths: bool = True) -> list[dict[str, Any]]:
    items = []
    for d in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        if not d.is_dir() or not (d / "project.json").exists():
            continue
        if allowed_ids is not None and d.name not in allowed_ids:
            continue
        try:
            project = load_project(d)
        except Exception:
            project = read_json(d / "project.json", {}) or {}
        p = project_paths(d)
        status = read_json(d / "status.json", {}) or {}
        candidates = read_json(p["candidates"], []) or []
        segs = read_json(p["segments"], []) or []
        outputs = list_output_files(d)
        final = next((o for o in outputs if o.get("path") == "outputs/highlight_final.mp4"), None)
        items.append(
            {
                "id": project.get("id") or d.name,
                "name": project.get("name") or d.name,
                "date": project.get("updated_at") or project.get("created_at"),
                "status": status.get("state") or "ready",
                "status_message": status.get("message") or "—",
                "duration": tc(sum(max(0, float(s.get("end", 0) or 0) - float(s.get("start", 0) or 0)) for s in segs)),
                "preset": project.get("settings", {}).get("task_preset_label") or project.get("settings", {}).get("task_preset") or "—",
                "candidates": len(candidates),
                "segments": len(segs),
                "final_file": final.get("path") if final else "",
                "project_folder": str(d) if expose_paths else "",
            }
        )
        if len(items) >= limit:
            break
    return items


@app.get("/api/project-history")
def get_project_history(request: Request, limit: int = 30):
    allowed_ids = None
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            user = db.get(User, request.state.user.id)
            allowed_ids = accessible_project_ids(db, user)
    return {
        "ok": True,
        "items": project_history_items(limit, allowed_ids=allowed_ids, expose_paths=not WEB_ACCOUNTS_ENABLED),
    }


# -----------------------------------------------------------------------------
# Release hardening audit / workflow guard
# -----------------------------------------------------------------------------


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except Exception:
        return 0


def app_audit_report() -> dict[str, Any]:
    """Small self-audit for the packaged desktop build.

    It intentionally does not run downloads, FFmpeg or Ollama.  It checks things
    that often break a release ZIP: missing frontend build, missing start scripts,
    bundled Twitch tools, oversized monolithic files, and available smoke tests.
    """
    checks: list[dict[str, Any]] = []

    def add(id_: str, label: str, ok: bool, details: str = "", severity: str = "warning"):
        checks.append({"id": id_, "label": label, "ok": bool(ok), "details": details, "severity": "ok" if ok else severity})

    dist_index = FRONTEND_DIST / "index.html"
    assets_dir = FRONTEND_DIST / "assets"
    add(
        "frontend_dist",
        "Production frontend build",
        dist_index.exists() and assets_dir.exists(),
        "frontend/dist найден — START_HERE может отдавать готовый UI без Vite."
        if dist_index.exists()
        else "frontend/dist отсутствует: запусти npm --prefix frontend run build.",
        "critical",
    )
    launcher_paths = {
        "START_HERE.bat": ROOT_DIR / "START_HERE.bat",
        "START_DESKTOP.bat": ROOT_DIR / "commands" / "launch" / "START_DESKTOP.bat",
        "START_WEB_LOCAL.bat": ROOT_DIR / "commands" / "launch" / "START_WEB_LOCAL.bat",
        "STOP_WEB_LOCAL.bat": ROOT_DIR / "commands" / "launch" / "STOP_WEB_LOCAL.bat",
        "run_windows.bat": ROOT_DIR / "scripts" / "windows" / "run_windows.bat",
        "run_web_local.bat": ROOT_DIR / "scripts" / "windows" / "run_web_local.bat",
        "start_backend.bat": ROOT_DIR / "scripts" / "windows" / "start_backend.bat",
        "start_backend_debug.bat": ROOT_DIR / "scripts" / "windows" / "start_backend_debug.bat",
    }
    for script, path in launcher_paths.items():
        relative = path.relative_to(ROOT_DIR)
        add(f"script_{script}", script, path.exists(), f"{relative} найден" if path.exists() else f"{relative} отсутствует", "critical")

    tool_status = twitch_tool_status(default_settings()).get("tools", {})
    tdcli = tool_status.get("twitchdownloadercli", {})
    aria = tool_status.get("aria2c", {})
    bundled_tdcli = ROOT_DIR / "vendor" / "twitchdownloadercli" / "TwitchDownloaderCLI.exe"
    bundled_aria = ROOT_DIR / "vendor" / "aria2" / "aria2c.exe"
    tdcli_ok = bool(tdcli.get("ok")) or bundled_tdcli.exists()
    aria_ok = bool(aria.get("ok")) or bundled_aria.exists()
    add(
        "tdcli",
        "TwitchDownloaderCLI",
        tdcli_ok,
        tdcli.get("path") or (str(bundled_tdcli.relative_to(ROOT_DIR)) if bundled_tdcli.exists() else tdcli.get("hint") or "нет"),
        "warning",
    )
    add(
        "aria2",
        "aria2c",
        aria_ok,
        aria.get("path") or (str(bundled_aria.relative_to(ROOT_DIR)) if bundled_aria.exists() else aria.get("hint") or "нет"),
        "warning",
    )

    tests_file = ROOT_DIR / "tests" / "test_smoke.py"
    test_count = tests_file.read_text(encoding="utf-8", errors="replace").count("def test_") if tests_file.exists() else 0
    add("tests", "Smoke tests", test_count >= 40, f"{test_count} pytest-тестов найдено", "warning")

    app_file = ROOT_DIR / "frontend" / "src" / "app" / "App.jsx"
    main_file = ROOT_DIR / "backend" / "src" / "highlight_studio" / "api" / "app.py"
    pipe_file = ROOT_DIR / "backend" / "src" / "highlight_studio" / "services" / "pipeline.py"
    app_lines = _line_count(app_file)
    main_lines = _line_count(main_file)
    pipe_lines = _line_count(pipe_file)
    add(
        "frontend_size",
        "Frontend app/App.jsx",
        app_lines < 3500,
        f"{app_lines} строк; API, конфигурация, UI-компоненты и helpers вынесены отдельно.",
        "info",
    )
    add(
        "backend_size",
        "Backend api/app.py",
        main_lines < 4500,
        f"{main_lines} строк; HTTP-слой отделён от services/core/integrations.",
        "info",
    )
    add("pipeline_size", "Services pipeline", pipe_lines < 4500, f"{pipe_lines} строк; тяжёлое медиаядро изолировано в services.", "info")

    architecture_dirs = [
        ROOT_DIR / "backend" / "src" / "highlight_studio" / "api",
        ROOT_DIR / "backend" / "src" / "highlight_studio" / "core",
        ROOT_DIR / "backend" / "src" / "highlight_studio" / "services",
        ROOT_DIR / "backend" / "src" / "highlight_studio" / "integrations",
        ROOT_DIR / "backend" / "src" / "highlight_studio" / "infrastructure",
        ROOT_DIR / "frontend" / "src" / "api",
        ROOT_DIR / "frontend" / "src" / "components",
        ROOT_DIR / "frontend" / "src" / "config",
        ROOT_DIR / "frontend" / "src" / "features",
    ]
    add(
        "modular_architecture",
        "Modular source tree",
        all(path.is_dir() for path in architecture_dirs),
        "backend layers и frontend feature folders присутствуют",
        "warning",
    )

    required_notes = [
        Path("README.md"),
        Path("docs/ARCHITECTURE.md"),
        Path("docs/releases/STABLE_V1_CANDIDATE.md"),
        Path("docs/guides/START_HERE_README.txt"),
    ]
    for note in required_notes:
        note_path = ROOT_DIR / note
        add(
            f"doc_{note.as_posix()}",
            note.as_posix(),
            note_path.exists(),
            "документ есть" if note_path.exists() else "документ отсутствует",
            "info",
        )

    critical_bad = [c for c in checks if not c["ok"] and c.get("severity") == "critical"]
    warnings = [c for c in checks if not c["ok"]]
    score = max(0, 100 - len(critical_bad) * 25 - max(0, len(warnings) - len(critical_bad)) * 6)
    next_actions = []
    if critical_bad:
        next_actions.extend([f"Исправь: {x['label']} — {x['details']}" for x in critical_bad[:4]])
    if not critical_bad and warnings:
        next_actions.extend([f"Проверить: {x['label']} — {x['details']}" for x in warnings[:4]])
    if not next_actions:
        next_actions.append("Критичных проблем упаковки не найдено. Следующий шаг — тест на реальном коротком видео и длинном VOD.")
    return {
        "ok": not critical_bad,
        "audit_type": "packaging",
        "score": score,
        "packaging_score": score,
        "title": "Packaging audit: упаковка готова" if not critical_bad else "Packaging audit: есть критичные проблемы",
        "summary": "Проверяет только комплектность ZIP: frontend build, стартовые scripts, bundled tools, smoke tests и структуру. Это не оценка AI-качества или полной release readiness.",
        "limitations": [
            "Не запускает реальный Twitch download",
            "Не запускает Whisper/Ollama",
            "Не проверяет GPU encoder и длинный FFmpeg render",
            "Не заменяет clean Windows и long-VOD тесты",
        ],
        "checks": checks,
        "next_actions": next_actions,
        "app_version": APP_VERSION,
    }


def render_artifact_check(project_dir_: Path) -> dict[str, Any]:
    project = load_project(project_dir_)
    settings = project.get("settings", default_settings())
    fresh = freshness_report(project_dir_, settings)
    outputs = list_output_files(project_dir_)
    final = next((o for o in outputs if o.get("path") == ARTIFACTS["final_render"]), None)
    shorts = [o for o in outputs if "short" in str(o.get("path", "")).lower()]
    final_current = bool(final and fresh.get("render_current"))
    checks = [{
        "id": "final", "label": "Финальный mp4", "ok": final_current,
        "details": (final.get("path") if final_current else ("Файл существует, но устарел относительно текущего монтажа." if final else f"{ARTIFACTS['final_render']} ещё не создан")),
    }]
    if final:
        checks.append({"id": "final_size", "label": "Размер финального файла", "ok": float(final.get("size_mb") or 0) > 0, "details": f"{final.get('size_mb')} MB"})
    checks.append({"id": "shorts", "label": "Shorts", "ok": len(shorts) > 0, "details": f"{len(shorts)} shorts-файлов найдено" if shorts else "Shorts ещё не созданы", "severity": "info"})
    return {"ok": final_current, "final_ready": final_current, "final_stale": bool(final and not final_current), "shorts_count": len(shorts), "outputs_count": len(outputs), "checks": checks, "outputs": outputs[:50], "freshness": fresh}


def workflow_guard_report(project_dir_: Path) -> dict[str, Any]:
    project = load_project(project_dir_)
    settings = project.get("settings", default_settings())
    status = read_json(project_dir_ / "status.json", {}) or {}
    p = project_paths(project_dir_)
    candidates = read_json(p["candidates"], []) or []
    segments = read_json(p["segments"], []) or []
    fresh = freshness_report(project_dir_, settings)
    source_ready = bool(fresh.get("source_ready"))
    candidates_current = bool(candidates and fresh.get("candidates_current"))
    segments_current = bool(segments and fresh.get("segments_current"))
    final_ready = bool(fresh.get("render_current"))
    busy = str(status.get("state", "")).lower() in {"running", "queued", "cancel_requested"}
    total_duration = sum(max(0, float(s.get("end", 0) or 0) - float(s.get("start", 0) or 0)) for s in segments)
    target_sec = max(1, float(settings.get("target_minutes") or 0) * 60)
    fill_percent = round(min(100, total_duration / target_sec * 100), 1) if target_sec else 0
    steps = [
        {"id":"source","label":"Источник","ok":source_ready,"details":fresh.get("source",{}).get("path") or "исходник не подготовлен"},
        {"id":"preflight","label":"Smart Preflight","ok":bool(smart_preflight_report(project_dir_, settings).get("can_start")),"details":smart_preflight_report(project_dir_, settings).get("recommendation","")},
        {"id":"candidates","label":"AI-кандидаты","ok":candidates_current,"details":f"{len(candidates)} найдено" + (" · устарели" if candidates and not candidates_current else "")},
        {"id":"segments","label":"Финальный монтаж","ok":segments_current,"details":f"{len(segments)} фрагментов · {tc(total_duration)}" + (" · устарел" if segments and not segments_current else "")},
        {"id":"duration","label":"Длительность","ok":fill_percent >= 55 or not segments,"details":f"{fill_percent}% от цели"},
        {"id":"render","label":"Рендер","ok":final_ready,"details":"highlight_final.mp4 актуален" if final_ready else ("финальный файл устарел" if fresh.get("render_stale") else "финальный файл ещё не создан")},
    ]
    if busy:
        next_action={"step":"analysis","label":"Дождаться текущей задачи","action":status.get("message") or "Задача выполняется"}
    elif not source_ready:
        next_action={"step":"import","label":"Подготовить источник","action":"Импортируй локальное видео или подготовь Twitch cache."}
    elif not candidates_current:
        next_action={"step":"analysis","label":"Запустить AI-анализ","action":"Текущий анализ отсутствует или устарел."}
    elif not segments_current:
        next_action={"step":"review","label":"Обновить монтаж","action":"Монтаж отсутствует или устарел относительно текущего анализа."}
    elif not final_ready:
        next_action={"step":"export","label":"Рендер YouTube","action":"Выполни новый рендер для текущей версии монтажа."}
    else:
        next_action={"step":"export","label":"Ролик готов","action":"Открой файл, создай Shorts и YouTube-пакет."}
    return {"ok": source_ready and (candidates_current or final_ready), "busy":busy,"source_ready":source_ready,"candidates_current":candidates_current,"segments_current":segments_current,"final_ready":final_ready,"fill_percent":fill_percent,"steps":steps,"next_action":next_action,"freshness":fresh,"artifact_check":render_artifact_check(project_dir_)}



# -----------------------------------------------------------------------------
# Stable Candidate / My Best Settings / Final Success Center
# -----------------------------------------------------------------------------

AI_QUALITY_SENSITIVE_KEYS = {
    "ai_engine",
    "ollama_url",
    "text_model",
    "vision_model",
    "ollama_timeout",
    "ollama_num_ctx",
    "ollama_keep_alive",
    "ai_batch_size",
    "ai_retry_count",
    "ai_strict_mode",
    "full_ai_coverage",
    "micro_batch_size",
    "whisper_model",
    "whisper_device",
    "whisper_compute",
    "language",
    "block_seconds",
    "chunk_seconds",
    "micro_cut_enabled",
    "micro_window_seconds",
    "micro_min_seconds",
    "micro_max_seconds",
    "micro_speech_gap_seconds",
    "micro_source_duration_multiplier",
    "top_blocks_for_micro",
    "min_final_segments",
    "max_final_segments",
    "target_minutes",
    "dedup_enabled",
    "storyline_enabled",
    "audio_dynamics_enabled",
    "refill_after_dedup_enabled",
    "target_fill_ratio",
    "quality_first_selection_enabled",
    "quality_first_min_score",
    "quality_first_min_confidence",
    "quality_first_min_clarity",
    "quality_recovery_score_relaxation",
    "hook_first_enabled",
    "hook_min_score",
    "visual_scan_enabled",
    "visual_scan_interval_seconds",
    "visual_scan_max_samples",
    "ocr_enabled",
    "ocr_languages",
    "ocr_every_n_visual_samples",
    "ocr_roi_enabled",
    "ocr_roi_x",
    "ocr_roi_y",
    "ocr_roi_w",
    "ocr_roi_h",
    "ocr_upscale",
    "task_preset",
    "task_preset_label",
    "content_type",
    "edit_mode",
    "analysis_profile",
    "prompt",
}

GLOBAL_BEST_SETTINGS_PATH = DATA_DIR / "my_best_settings.json"
STABLE_CANDIDATE_PATH = DATA_DIR / "stable_v1_candidate.json"


def _settings_fingerprint(settings: dict[str, Any]) -> str:
    sanitized = validate_settings(settings or {})
    relevant = {k: sanitized.get(k) for k in sorted(AI_QUALITY_SENSITIVE_KEYS) if k in sanitized}
    blob = json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _profile_source_project(project: dict[str, Any], d: Path) -> dict[str, Any]:
    status = read_json(d / "status.json", {}) or {}
    p = project_paths(d)
    candidates = read_json(p["candidates"], []) or []
    segments = read_json(p["segments"], []) or []
    outputs = list_output_files(d)
    final = next((o for o in outputs if o.get("path") == "outputs/highlight_final.mp4"), None)
    return {
        "project_id": project.get("id") or d.name,
        "project_name": project.get("name") or d.name,
        "status": status.get("state") or "ready",
        "candidates": len(candidates),
        "segments": len(segments),
        "final_ready": bool(final),
        "final_file": final.get("path") if final else "",
    }


def _build_best_settings_profile(project_dir_: Path | None, settings: dict[str, Any], name: str = "Мой рабочий пресет") -> dict[str, Any]:
    validated = validate_settings(settings or {})
    source = {}
    if project_dir_ is not None and (project_dir_ / "project.json").exists():
        project = load_project(project_dir_)
        source = _profile_source_project(project, project_dir_)
    return {
        "ok": True,
        "name": name or "Мой рабочий пресет",
        "label": "Мой рабочий пресет",
        "description": "Проверенные настройки, на которых приложение уже хорошо ищет моменты, режет и рендерит видео.",
        "saved_at": time.time(),
        "app_version": APP_VERSION,
        "settings_fingerprint": _settings_fingerprint(validated),
        "quality_guard_enabled": True,
        "settings": validated,
        "source": source,
    }


def _load_best_settings_profile(project_dir_: Path | None = None) -> dict[str, Any] | None:
    candidates: list[Path] = []
    if project_dir_ is not None:
        candidates.append(project_dir_ / "my_best_settings.json")
        candidates.append(project_dir_ / "stable_profile.json")
    if not WEB_ACCOUNTS_ENABLED:
        candidates.append(GLOBAL_BEST_SETTINGS_PATH)
    for path in candidates:
        data = read_json(path, None)
        if isinstance(data, dict) and data.get("settings"):
            return data
    return None


def quality_lock_report(project_dir_: Path) -> dict[str, Any]:
    project = load_project(project_dir_)
    current = validate_settings(project.get("settings", {}))
    profile = _load_best_settings_profile(project_dir_)
    if not profile:
        return {
            "ok": True,
            "exists": False,
            "active": False,
            "locked": False,
            "title": "Рабочий профиль ещё не сохранён",
            "message": "Когда текущие настройки дают хороший результат, нажми “Сохранить как Мой рабочий пресет”.",
            "diffs": [],
            "critical_diffs": [],
        }
    best = validate_settings(profile.get("settings", {}))
    diffs = []
    critical = []
    for key in sorted(AI_QUALITY_SENSITIVE_KEYS):
        if current.get(key) != best.get(key):
            item = {"key": key, "current": current.get(key), "best": best.get(key), "critical": True}
            diffs.append(item)
            critical.append(item)
    locked = bool(profile.get("quality_guard_enabled", True))
    active = len(critical) == 0
    message = (
        "Сейчас используется стабильный рабочий профиль. Изменение AI-настроек может повлиять на качество нарезки."
        if active
        else f"Текущие AI-настройки отличаются от рабочего профиля: {len(critical)} важных полей."
    )
    return {
        "ok": not locked or active,
        "exists": True,
        "active": active,
        "locked": locked,
        "title": "Мой рабочий пресет",
        "profile_name": profile.get("name") or "Мой рабочий пресет",
        "message": message,
        "saved_at": profile.get("saved_at"),
        "app_version": profile.get("app_version"),
        "fingerprint": _settings_fingerprint(current),
        "best_fingerprint": profile.get("settings_fingerprint"),
        "diffs": diffs[:80],
        "critical_diffs": critical[:40],
        "source": profile.get("source") or {},
    }


def save_best_settings_profile(project_dir_: Path | None, settings: dict[str, Any], name: str = "Мой рабочий пресет") -> dict[str, Any]:
    profile = _build_best_settings_profile(project_dir_, settings, name=name)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not WEB_ACCOUNTS_ENABLED:
        write_json(GLOBAL_BEST_SETTINGS_PATH, profile)
    if project_dir_ is not None:
        write_json(project_dir_ / "my_best_settings.json", profile)
        write_json(project_dir_ / "stable_profile.json", profile)
        project = load_project(project_dir_)
        project["stable_profile"] = {
            "name": profile["name"],
            "saved_at": profile["saved_at"],
            "settings_fingerprint": profile["settings_fingerprint"],
            "quality_guard_enabled": True,
        }
        save_project(project_dir_, project)
    return {"ok": True, "profile": profile, "message": "Мой рабочий пресет сохранён и включена защита качества."}


def apply_best_settings_profile(project_dir_: Path) -> dict[str, Any]:
    profile = _load_best_settings_profile(project_dir_)
    if not profile:
        raise HTTPException(404, "Рабочий профиль ещё не сохранён")
    project = load_project(project_dir_)
    merged = validate_settings({**project.get("settings", {}), **profile.get("settings", {})})
    project["settings"] = merged
    project["stable_profile"] = {
        "name": profile.get("name") or "Мой рабочий пресет",
        "saved_at": profile.get("saved_at"),
        "settings_fingerprint": profile.get("settings_fingerprint") or _settings_fingerprint(merged),
        "quality_guard_enabled": True,
    }
    save_project(project_dir_, project)
    return {
        "ok": True,
        "settings": merged,
        "profile": profile,
        "quality_lock": quality_lock_report(project_dir_),
        "message": "Мой рабочий пресет применён к проекту.",
    }


def stable_candidate_info() -> dict[str, Any]:
    data = read_json(STABLE_CANDIDATE_PATH, None)
    if not isinstance(data, dict):
        data = {
            "name": "Highlight Studio Stable v1.0 Candidate",
            "app_version": APP_VERSION,
            "created_at": time.time(),
            "status": "candidate",
            "message": "Эта версия зафиксирована как стабильный кандидат. Сохрани ZIP отдельно как backup для отката.",
        }
        write_json(STABLE_CANDIDATE_PATH, data)
    return data


def final_success_report(project_dir_: Path) -> dict[str, Any]:
    project = load_project(project_dir_)
    p = project_paths(project_dir_)
    outputs = list_output_files(project_dir_)
    fresh = freshness_report(project_dir_, project.get("settings", default_settings()))
    final_any = next((o for o in outputs if o.get("path") == ARTIFACTS["final_render"]), None)
    final = final_any if fresh.get("render_current") else None
    shorts = [o for o in outputs if "short" in str(o.get("path", "")).lower()]
    candidates = read_json(p["candidates"], []) or []
    segments = read_json(p["segments"], []) or []
    metadata = read_json(project_dir_ / "youtube_metadata.json", {}) or {}
    creator = creator_pack(project_dir_, project.get("settings", default_settings()))
    total_duration = sum(max(0, float(s.get("end", 0) or 0) - float(s.get("start", 0) or 0)) for s in segments)
    return {
        "ok": True,
        "final_ready": bool(final),
        "final_stale": bool(final_any and not final),
        "freshness": fresh,
        "title": "Финальный ролик готов" if final else ("Финальный ролик устарел" if final_any else "Финальный ролик ещё не собран"),
        "message": "Можно открыть видео, папку, создать Shorts, собрать YouTube-пакет или экспортировать ZIP."
        if final
        else "Запусти рендер YouTube, после готовности здесь появится финальный экран.",
        "project_id": project.get("id") or project_dir_.name,
        "project_name": project.get("name") or project_dir_.name,
        "preset": project.get("settings", {}).get("task_preset_label") or project.get("settings", {}).get("task_preset") or "—",
        "video": final or {},
        "outputs": outputs,
        "shorts": shorts[:20],
        "duration_seconds": round(total_duration, 2),
        "duration": tc(total_duration),
        "candidates": len(candidates),
        "segments": len(segments),
        "metadata_ready": bool(metadata),
        "creator_pack": creator,
        "actions": [
            {"id": "open_video", "label": "Открыть итоговое видео", "enabled": bool(final)},
            {"id": "open_folder", "label": "Открыть папку проекта", "enabled": True},
            {"id": "shorts", "label": "Создать Shorts", "enabled": bool(segments)},
            {"id": "creator_pack", "label": "Сгенерировать названия/описание", "enabled": bool(segments or candidates)},
            {"id": "save_best", "label": "Сохранить как Мой рабочий пресет", "enabled": True},
            {"id": "export_zip", "label": "Экспортировать ZIP", "enabled": True},
        ],
        "checked_at": time.time(),
    }


def success_history_items(limit: int = 30, allowed_ids: set[str] | None = None, expose_paths: bool = True) -> list[dict[str, Any]]:
    items = []
    for item in project_history_items(limit=500, allowed_ids=allowed_ids, expose_paths=expose_paths):
        if item.get("final_file"):
            items.append(item)
        if len(items) >= limit:
            break
    return items


@app.get("/api/stable-candidate")
def get_stable_candidate():
    return stable_candidate_info()


@app.get("/api/my-best-settings")
def get_my_best_settings(request: Request):
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            profile = get_user_preference(db, request.state.user.id, "best_settings")
    else:
        profile = _load_best_settings_profile(None)
    return web_safe_payload({"ok": True, "exists": bool(profile), "profile": profile})


@app.post("/api/my-best-settings/save")
def save_global_my_best_settings(payload: dict[str, Any], request: Request):
    settings = payload.get("settings") if isinstance(payload, dict) else None
    if not isinstance(settings, dict):
        raise HTTPException(422, "settings object is required")
    profile = _build_best_settings_profile(None, settings, payload.get("name") or "Мой рабочий пресет")
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            set_user_preference(db, request.state.user.id, "best_settings", profile)
        return web_safe_payload({"ok": True, "profile": profile, "message": "Личный рабочий пресет сохранён."})
    return web_safe_payload(save_best_settings_profile(None, settings, payload.get("name") or "Мой рабочий пресет"))


@app.get("/api/projects/{project_id}/quality-lock")
def get_project_quality_lock(project_id: str):
    return quality_lock_report(project_dir(project_id))


@app.post("/api/projects/{project_id}/my-best-settings/save")
def save_project_my_best_settings(project_id: str):
    d = project_dir(project_id)
    project = load_project(d)
    return web_safe_payload(save_best_settings_profile(d, project.get("settings", default_settings()), "Мой рабочий пресет"))


@app.post("/api/projects/{project_id}/my-best-settings/apply")
def apply_project_my_best_settings(project_id: str):
    return web_safe_payload(apply_best_settings_profile(project_dir(project_id)))


@app.get("/api/projects/{project_id}/final-success")
def get_project_final_success(project_id: str):
    return final_success_report(project_dir(project_id))


@app.get("/api/success-history")
def get_success_history(request: Request, limit: int = 30):
    allowed_ids = None
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            user = db.get(User, request.state.user.id)
            allowed_ids = accessible_project_ids(db, user)
    return {
        "ok": True,
        "items": success_history_items(limit, allowed_ids=allowed_ids, expose_paths=not WEB_ACCOUNTS_ENABLED),
    }


@app.get("/api/projects/{project_id}/checkpoints")
def get_project_checkpoints(project_id: str):
    return project_checkpoint_status(project_dir(project_id))


@app.get("/api/projects/{project_id}/auto-probe")
def get_auto_probe(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    return web_safe_payload(auto_video_probe(d, settings))


@app.get("/api/projects/{project_id}/auto-recommendation")
def get_auto_recommendation(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    return web_safe_payload(build_autopilot_recommendation(d, settings))


@app.post("/api/projects/{project_id}/auto-recommendation")
def apply_auto_recommendation(project_id: str):
    d = project_dir(project_id)
    project = load_project(d)
    rec = build_autopilot_recommendation(d, project.get("settings", default_settings()))
    project["settings"] = validate_settings({**project.get("settings", {}), **rec.get("settings_patch", {})})
    project["autopilot_recommendation"] = {k: v for k, v in rec.items() if k != "settings_patch"}
    save_project(d, project)
    return web_safe_payload(
        {**rec, "settings": project["settings"], "message": "Авто-рекомендация применена. Теперь можно запускать «Собрать нарезку»."}
    )


@app.get("/api/projects/{project_id}/smart-preflight")
def get_smart_preflight(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    return web_safe_payload(smart_preflight_report(d, settings))


@app.post("/api/projects/{project_id}/smart-preflight")
def run_smart_preflight(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    report = smart_preflight_report(d, settings, force_ollama=True)
    write_json(d / "smart_preflight.json", report)
    return web_safe_payload(report)


@app.get("/api/projects/{project_id}/cache-fingerprint")
def get_cache_fingerprint(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    return cache_fingerprint_report(d, settings)


@app.post("/api/projects/{project_id}/cache-fingerprint")
def post_cache_fingerprint(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    return save_cache_manifest(d, settings)


@app.post("/api/projects/{project_id}/clear-incompatible-cache")
def clear_incompatible_cache(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    report = cache_fingerprint_report(d, settings)
    removed: list[str] = []
    failed: list[dict[str, str]] = []
    if not report.get("compatible"):
        for name in RECOMPUTABLE_CACHE_PATHS:
            target = d / name
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                else:
                    continue
                if target.exists():
                    raise OSError("path still exists after deletion")
                removed.append(name)
            except OSError as exc:
                failed.append({"path": name, "error": str(exc)})
        # Do not stamp a new compatibility manifest before recomputation.
        (d / "cache_manifest.json").unlink(missing_ok=True)
    if failed:
        raise HTTPException(status_code=409, detail={"message": "Несовместимый кэш очищен не полностью.", "removed": removed, "failed": failed})
    return web_safe_payload({
        "ok": True,
        "removed": removed,
        "failed": [],
        "cache": cache_fingerprint_report(d, settings),
        "message": "Несовместимый производный кэш очищен; manifest будет создан после успешного пересчёта." if removed else "Несовместимый cache не найден.",
    })


@app.post("/api/projects/{project_id}/resume")
def resume_project(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    source = authoritative_source_gate(d, deep_media_check=False)
    if not source.get("ok"):
        return {"started": False, "stage": "source", "message": "Нельзя продолжить: исходный файл недоступен."}
    fresh = freshness_report(d, settings)
    transcript_exists = project_paths(d)["transcript"].exists()
    candidates_exist = project_paths(d)["candidates"].exists()
    segments_exist = bool(read_json(project_paths(d)["segments"], []) or [])
    if not transcript_exists or not candidates_exist or not fresh.get("analysis_revision") or not fresh.get("candidates_current"):
        result = start_analyze(project_id)
        return {**result, "resume_stage": "analysis"}
    if not segments_exist or not fresh.get("segments_current"):
        # Analysis is current, so do not waste hours re-running Whisper/Ollama.
        # Resume at the Review/selection boundary and let the user rebuild or
        # confirm the montage from the already-current candidates.
        return {
            "started": False,
            "resume_stage": "review",
            "next_step": "review",
            "message": "Анализ актуален, но монтаж отсутствует или устарел. Продолжите с Review Studio — повторный AI-анализ не требуется.",
        }
    if not fresh.get("render_current"):
        result = start_render(project_id)
        return {**result, "resume_stage": "render"}
    return {"started": False, "resume_stage": "complete", "message": "Проект уже обработан до актуального финального рендера."}


@app.get("/api/projects/{project_id}/duration-control")
def get_duration_control(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    return duration_control_report(d, settings)


@app.post("/api/projects/{project_id}/duration-control")
def post_duration_control(project_id: str, payload: dict[str, Any] | None = None):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    action = str((payload or {}).get("action", "keep_as_is"))
    return apply_duration_action(d, settings, action)


@app.get("/api/projects/{project_id}/quality-before-render")
def get_quality_before_render(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    return quality_before_render(d, settings)


@app.post("/api/projects/{project_id}/quality-before-render")
def post_quality_before_render(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    report = quality_before_render(d, settings)
    write_json(d / "quality_before_render.json", report)
    return report


@app.get("/api/projects/{project_id}/creator-pack")
def get_creator_pack(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    return creator_pack(d, settings)


@app.post("/api/projects/{project_id}/creator-pack")
def post_creator_pack(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    pack = creator_pack(d, settings)
    write_json(d / "creator_pack.json", pack)
    return pack


@app.post("/api/projects/{project_id}/review-action")
def review_action(project_id: str, payload: dict[str, Any] | None = None):
    """Legacy Review mutation path, serialized through the modern segment lock.

    Kept for compatibility, but it now mutates the latest segment document under
    the same lock as PUT /segments and POST /segments/add so it cannot overwrite
    a concurrent modern edit.
    """
    d = project_dir(project_id)
    p = project_paths(d)
    payload = payload or {}
    action = str(payload.get("action") or "")
    item = payload.get("item") or {}
    allowed_actions = {"keep", "delete", "shorten", "add_before", "add_after", "mark_best", "make_shorts"}
    if action not in allowed_actions:
        raise HTTPException(status_code=422, detail={"message": f"Unknown review action: {action}"})
    with segment_mutation_locks_guard:
        lock = segment_mutation_locks.setdefault(project_id, threading.Lock())
    with lock:
        segments = read_json(p["segments"], []) or []
        if action == "keep" and item:
            segments.append(dict(item))
        elif action == "delete":
            target_id = str(payload.get("id") or item.get("id") or "")
            segments = [s for s in segments if str(s.get("id")) != target_id]
        elif action == "shorten":
            target_id = str(payload.get("id") or item.get("id") or "")
            segments = [
                {**s, "end": max(float(s.get("start", 0) or 0) + 5, float(s.get("end", 0) or 0) - 5)} if str(s.get("id")) == target_id else s
                for s in segments
            ]
        elif action == "add_before":
            target_id = str(payload.get("id") or item.get("id") or "")
            segments = [{**s, "start": max(0, float(s.get("start", 0) or 0) - 5)} if str(s.get("id")) == target_id else s for s in segments]
        elif action == "add_after":
            target_id = str(payload.get("id") or item.get("id") or "")
            segments = [{**s, "end": float(s.get("end", 0) or 0) + 5} if str(s.get("id")) == target_id else s for s in segments]
        elif action == "mark_best":
            target_id = str(payload.get("id") or item.get("id") or "")
            segments = [{**s, "best_moment": True, "story_role": "hook"} if str(s.get("id")) == target_id else s for s in segments]
        elif action == "make_shorts":
            target_id = str(payload.get("id") or item.get("id") or "")
            segments = [{**s, "shorts_candidate": True} if str(s.get("id")) == target_id else s for s in segments]
        segments = sorted(segments, key=lambda x: float(x.get("start", 0) or 0))
        try:
            duration = video_duration(source_video_path(d))
        except Exception:
            duration = None
        validated = update_segments(d, segments, source_duration=duration)
        settings = load_project(d).get("settings", default_settings())
        return {"ok": True, "action": action, "segments": validated, "segments_revision": segments_revision(d, settings)}


@app.post("/api/twitch/probe")
def twitch_probe(payload: dict[str, Any] | None = None):
    payload = payload or {}
    url = str(payload.get("url") or "").strip()
    if not url:
        return {
            "ok": False,
            "url": "",
            "kind": "unknown",
            "message": "Вставь Twitch VOD/Live ссылку.",
            "duration_seconds": None,
            "estimated_size_gb": None,
            "fix": "Проверь ссылку: нужна ссылка Twitch VOD или канал Live.",
        }
    try:
        info = classify_twitch_url(url)
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "kind": "unknown",
            "message": str(exc),
            "duration_seconds": None,
            "estimated_size_gb": None,
            "fix": "Проверь ссылку: нужна ссылка Twitch VOD или канал Live.",
        }
    return {
        "ok": info.get("kind") in {"vod", "live"},
        "url": info.get("url") or url,
        "kind": info.get("kind") or "unknown",
        "vod_id": info.get("vod_id"),
        "channel": info.get("channel"),
        "message": "Ссылка похожа на Twitch.",
        "duration_seconds": None,
        "estimated_size_gb": None,
        "fix": "",
    }


@app.post("/api/projects/{project_id}/one-click")
def start_one_click(project_id: str):
    """Stable one-click pipeline: prepare source if needed, then analyze.

    It intentionally stops before final render so the user can review the cut.
    Render remains a separate action to avoid wasting hours on a wrong timeline.

    v10.15.6 validates a prepared local source synchronously.  This avoids the
    old UX where the API returned "started" and the background thread failed a
    moment later because the source/runtime was already known to be invalid.
    """
    d0 = project_dir(project_id)
    project0 = load_project(d0)
    settings0 = project0.get("settings", default_settings())
    source0 = source_video_path(d0)
    twitch_needs_prepare = project0.get("source_type") == "twitch" and not source0.exists()

    if not twitch_needs_prepare:
        gate = authoritative_source_gate(d0, deep_media_check=True)
        if not gate.get("ok"):
            raise HTTPException(status_code=422, detail={
                "message": gate.get("message") or "Источник не готов к анализу.",
                "recommendation": "Вернись на шаг «Источник» и подготовь видео заново.",
                "gate": gate,
            })
        report = smart_preflight_report(d0, settings0, force_ollama=True)
        if not report.get("can_start"):
            raise HTTPException(status_code=422, detail={
                "message": report.get("title") or "Анализ нельзя запустить.",
                "recommendation": report.get("recommendation") or "Исправь обязательные проверки перед анализом.",
                "errors": report.get("errors") or [],
            })

    def task(d: Path, settings: dict[str, Any], logger: JobLogger):
        project = read_json(d / "project.json", {}) or {}
        logger.heartbeat("one_click_start", 2, "One-click: проверяю источник и настройки")
        if project.get("source_type") == "twitch" and not project.get("source_video_path") and not source_video_path(d).exists():
            logger.heartbeat("twitch_prepare", 5, "One-click: подготавливаю Twitch источник")
            prepare_twitch_source(d, settings, logger)
            # project.json may have changed after Twitch cache preparation.
            settings = load_project(d).get("settings", settings)
        logger.heartbeat("ai_analyze", 18, "One-click: запускаю AI-анализ и сборку нарезки")
        result = analyze(d, settings, logger) or {}
        candidate_count = len(result.get("candidates") or [])
        segment_count = len(result.get("segments") or [])
        try:
            report = pre_render_check(d, settings)
            write_json(d / "one_click_pre_render_check.json", report)
        except Exception as exc:
            logger.log(f"One-click pre-render check не выполнен: {exc}")
        if candidate_count:
            message = f"Анализ завершён: найдено {candidate_count} моментов. Переход в монтаж выполняется вручную."
        else:
            message = "Анализ завершён, но кандидатов найдено 0. Открой монтаж вручную для проверки или диагностику перед повторным анализом."
        logger.set_status(
            "done",
            100,
            message,
            progress_source="done",
            candidate_count=candidate_count,
            segment_count=segment_count,
            analysis_complete=True,
            manual_navigation=True,
        )

    return start_background_job(project_id, "One-click: собираю нарезку", task, kind="one_click")


MAX_BROWSER_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB. Larger files should use Fast Import/reference mode.


@app.post("/api/projects")
async def create_project(request: Request, file: UploadFile = File(...)):
    """Create a project by browser upload.

    v8.9 keeps this path for small/test videos only. Large stream files should
    use Fast Import so the app does not copy 12+ GB and duplicate disk usage.
    The old code copied the upload twice (original.ext and input.mp4); this now
    streams once with a hard backend limit.
    """
    pid = uuid.uuid4().hex[:12]
    d = PROJECTS_DIR / pid
    d.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename or "video.mp4").suffix or ".mp4"
    if ext.lower() not in VIDEO_EXTENSIONS:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, f"Неподдерживаемый формат: {ext}")
    video = d / ("input" + ext.lower())

    total = 0
    try:
        with video.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BROWSER_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "message": "Файл слишком большой для браузерной загрузки. Используй Fast Import / Импорт по пути без копирования.",
                            "limit_gb": 2,
                        },
                    )
                f.write(chunk)
    except HTTPException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(500, f"Не удалось сохранить файл: {exc}") from exc

    media_check = validate_media_file(video)
    if not media_check.get("ok"):
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(status_code=422, detail={"message": media_check.get("message"), "code": media_check.get("code"), "media": media_check})

    meta = create_project_meta(
        pid,
        Path(file.filename or "video").stem,
        default_settings(),
        {
            "original_filename": file.filename,
            "storage_mode": "copy",
            **portable_source_fields(d, video),
            "source_video_size_bytes": total,
            "upload_limit_bytes": MAX_BROWSER_UPLOAD_BYTES,
        },
    )
    write_json(d / "project.json", meta)
    mark_source_changed(d, meta.get("settings", default_settings()))
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            ensure_project(db, pid, meta["name"], db.get(User, request.state.user.id), str(d))
    JobLogger(d).set_status("ready", 0, "Видео загружено")
    return web_safe_payload(meta)


@app.get("/api/local/roots")
def local_roots():
    if WEB_ACCOUNTS_ENABLED:
        raise HTTPException(404, "Локальный файловый браузер недоступен в web-режиме")
    return {"os": os.name, "roots": local_roots_list(), "home": str(Path.home())}


@app.get("/api/local/browse")
def local_browse(path: str = Query("")):
    if WEB_ACCOUNTS_ENABLED:
        raise HTTPException(404, "Локальный файловый браузер недоступен в web-режиме")
    target = normalize_user_path(path) if path else Path.home().resolve()
    if target.is_file():
        target = target.parent
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, {"message": "Папка не найдена", "checked_path": str(target)})

    dirs: list[dict[str, str]] = []
    videos: list[dict[str, Any]] = []
    try:
        for item in target.iterdir():
            try:
                if item.is_dir():
                    # Skip noisy/system dirs where possible, but do not hide normal user folders.
                    dirs.append({"name": item.name, "path": str(item)})
                elif item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS:
                    videos.append(_video_item(item))
            except (PermissionError, OSError):
                continue
    except PermissionError:
        raise HTTPException(403, {"message": "Нет доступа к папке", "checked_path": str(target)})

    dirs.sort(key=lambda x: x["name"].lower())
    videos.sort(key=lambda x: x["name"].lower())
    parent = str(target.parent) if target.parent != target else ""
    return {"path": str(target), "parent": parent, "dirs": dirs[:300], "videos": videos[:300], "roots": local_roots_list()}


@app.post("/api/projects/from-path")
def create_project_from_path(payload: ImportProjectRequest, request: Request):
    """Fast Import for huge local videos.

    reference: no copy, pipeline reads the original file directly.
    link: creates projects/<id>/input.ext as hardlink/symlink when possible.
    copy: legacy safe copy into the project folder.
    """
    if WEB_ACCOUNTS_ENABLED:
        raise HTTPException(404, "Импорт по серверному пути недоступен в web-режиме. Загрузи файл через браузер.")
    src = ensure_local_video_path(payload.source_path)
    media_check = validate_media_file(src)
    if not media_check.get("ok"):
        raise HTTPException(status_code=422, detail={"message": media_check.get("message"), "code": media_check.get("code"), "media": media_check})
    pid = uuid.uuid4().hex[:12]
    d = PROJECTS_DIR / pid
    d.mkdir(parents=True, exist_ok=True)
    name = payload.name or src.stem
    mode = payload.storage_mode
    warnings: list[str] = []

    stored_path = src
    original_filename = src.name
    if mode == "copy":
        target = d / "input.mp4"
        shutil.copy2(src, target)
        stored_path = target
    elif mode == "link":
        target = d / ("input" + (src.suffix or ".mp4"))
        try:
            os.link(src, target)
            stored_path = target
        except Exception as hard_exc:
            try:
                target.symlink_to(src)
                stored_path = target
            except Exception as sym_exc:
                # Do not silently copy 12 GB. Fall back to reference mode and tell the user.
                mode = "reference"
                stored_path = src
                warnings.append(f"Link не создан, использую reference mode: hardlink={hard_exc}; symlink={sym_exc}")

    meta_extra = {
        "original_filename": original_filename,
        "storage_mode": mode,
        "import_warning": "; ".join(warnings),
    }
    meta_extra.update(project_video_meta(stored_path))
    meta_extra.update(portable_source_fields(d, stored_path))
    meta = create_project_meta(pid, name, default_settings(), meta_extra)
    write_json(d / "project.json", meta)
    mark_source_changed(d, meta.get("settings", default_settings()))
    message = "Видео импортировано без копирования" if mode == "reference" else f"Видео импортировано: {mode}"
    if warnings:
        message += " · " + warnings[0]
    JobLogger(d).set_status("ready", 0, message)
    return meta


@app.post("/api/projects/from-twitch")
def create_project_from_twitch(payload: TwitchProjectRequest, request: Request):
    """Create a project from a Twitch VOD or Live URL.

    VOD: the user can set vod_start/vod_end so the app only caches/analyzes a
    selected stream range. Live: the app records live_record_minutes into cache.
    In both cases the user does not download the stream manually; the backend
    prepares a local cache file for Whisper/FFmpeg.
    """
    try:
        info = classify_twitch_url(payload.url, payload.source_kind)
        start_seconds = parse_time_to_seconds(payload.vod_start)
        end_seconds = parse_time_to_seconds(payload.vod_end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if info["kind"] == "vod" and start_seconds is not None and end_seconds is not None and end_seconds <= start_seconds:
        raise HTTPException(422, "Для Twitch VOD время 'до' должно быть больше времени 'от'.")

    pid = uuid.uuid4().hex[:12]
    d = PROJECTS_DIR / pid
    d.mkdir(parents=True, exist_ok=True)
    twitch = {
        **info,
        "source_kind": info["kind"],
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "live_record_minutes": payload.live_record_minutes,
        "download_threads": payload.twitch_download_threads,
        # Never read browser credential stores from the backend host in web mode.
        "cookies_browser": "none" if WEB_ACCOUNTS_ENABLED else (payload.twitch_cookies_browser or "none").strip().lower(),
        "format_selector": (payload.twitch_format or "best").strip() or "best",
        "download_engine": (payload.twitch_download_engine or "auto").strip().lower(),
        "quality": (payload.twitch_quality or "best").strip() or "best",
        "fallback_enabled": bool(payload.twitch_fallback_enabled),
        "aria2_connections": payload.twitch_aria2_connections,
        "status": "queued" if payload.auto_start else "created",
        "created_at": time.time(),
    }
    if info["kind"] == "vod":
        range_label = ""
        if start_seconds is not None or end_seconds is not None:
            range_label = f" {start_seconds or 0}-{end_seconds or 'end'}"
        default_name = f"Twitch VOD {info.get('vod_id') or ''}{range_label}".strip()
    else:
        default_name = f"Twitch Live {info.get('channel') or ''}".strip()
    meta = create_project_meta(
        pid,
        payload.name or default_name,
        default_settings(),
        {
            "source_type": "twitch",
            "source_url": info["url"],
            "storage_mode": "twitch_cache",
            "twitch": twitch,
        },
    )
    write_json(d / "project.json", meta)
    write_json(d / "twitch_import.json", twitch)
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            ensure_project(db, pid, meta["name"], db.get(User, request.state.user.id), str(d))
    JobLogger(d).set_status(
        "queued" if payload.auto_start else "ready",
        0,
        "Twitch проект создан. Подготовка источника..."
        if payload.auto_start
        else "Twitch проект создан. Нажми «Подготовить Twitch источник».",
    )
    response = dict(meta)
    if payload.auto_start:
        response["twitch_job"] = start_background_job(
            pid, "Twitch import запущен", lambda d, s, logger: prepare_twitch_source(d, s, logger), kind="twitch_import"
        )
    return web_safe_payload(response)


@app.post("/api/projects/{project_id}/twitch-import")
def start_twitch_import(project_id: str):
    return start_background_job(
        project_id, "Twitch import запущен", lambda d, s, logger: prepare_twitch_source(d, s, logger), kind="twitch_import"
    )


@app.get("/api/projects/{project_id}/twitch-import")
def get_twitch_import(project_id: str):
    d = project_dir(project_id)
    return web_safe_payload(read_json(d / "twitch_import.json", read_json(d / "project.json", {}).get("twitch", {})))


@app.get("/api/twitch/tools")
def get_twitch_tools():
    return web_safe_payload(twitch_tool_status(default_settings()))


@app.post("/api/twitch/plan")
def get_twitch_plan(payload: dict[str, Any] | None = None):
    payload = payload or {}
    url = str(payload.get("url") or "").strip()
    if not url:
        raise HTTPException(422, "Вставь Twitch URL.")
    return web_safe_payload(twitch_download_plan(url, default_settings(), str(payload.get("source_kind") or "auto")))


@app.get("/api/projects/{project_id}/twitch-download-plan")
def get_project_twitch_plan(project_id: str):
    d = project_dir(project_id)
    project = read_json(d / "project.json", {}) or {}
    settings = effective_project_settings(project)
    url = str(project.get("source_url") or (project.get("twitch") or {}).get("url") or "").strip()
    if not url:
        return web_safe_payload({"ok": False, "message": "В проекте нет Twitch URL.", "tools": twitch_tool_status(settings)})
    return web_safe_payload(twitch_download_plan(url, settings, (project.get("twitch") or {}).get("source_kind") or "auto"))


@app.post("/api/projects/{project_id}/twitch-speed-test")
def project_twitch_speed_test(project_id: str, payload: dict[str, Any] | None = None):
    d = project_dir(project_id)
    project = read_json(d / "project.json", {}) or {}
    settings = effective_project_settings(project)
    logger = JobLogger(d)
    try:
        result = run_twitch_speed_test(d, payload or {}, settings, logger)
        if result.get("best_engine"):
            # Persist the winning engine so the next full VOD import uses the faster path automatically.
            twitch = dict(project.get("twitch") or {})
            twitch["download_engine"] = result["best_engine"]
            twitch["last_speed_test"] = result
            project["twitch"] = twitch
            project["updated_at"] = time.time()
            write_json(d / "project.json", project)
            write_json(d / "twitch_import.json", twitch)
        return web_safe_payload(result)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        return web_safe_payload(
            {
                "ok": False,
                "message": str(exc),
                "results": [],
                "recommendation": "Проверь ссылку/cookies или скачай через TwitchLink и импортируй mp4 как локальное видео.",
            }
        )


@app.get("/api/projects")
def list_projects(request: Request):
    items = []
    for d in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        if d.is_dir() and (d / "project.json").exists():
            try:
                project = load_project(d)
            except HTTPException:
                project = read_json(d / "project.json", {})
            items.append(project)
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            user = db.get(User, request.state.user.id)
            # The first/admin user can claim legacy filesystem projects during migration.
            if user.global_role == "admin":
                for item in items:
                    ensure_project(db, item.get("id", ""), item.get("name", "Project"), user, str(PROJECTS_DIR / item.get("id", "")))
            allowed = accessible_project_ids(db, user)
            if allowed is not None:
                items = [item for item in items if item.get("id") in allowed]
            for item in items:
                item["access_role"] = "owner" if user.global_role == "admin" else project_role(db, user, item.get("id", ""))
    return web_safe_payload(items)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    d = project_dir(project_id)
    data = load_project(d)
    data["status"] = read_json(d / "status.json", {})
    ready = source_readiness(d)
    data["source_ready"] = bool(ready.get("ready") or ready.get("ok"))
    data["source_readiness"] = ready
    data["freshness"] = freshness_report(d, data.get("settings", default_settings()))
    return web_safe_payload(data)


@app.get("/api/projects/{project_id}/workflow-guard")
def get_project_workflow_guard(project_id: str):
    return workflow_guard_report(project_dir(project_id))


@app.get("/api/projects/{project_id}/render-artifact-check")
def get_project_render_artifact_check(project_id: str):
    return render_artifact_check(project_dir(project_id))


def _safe_dashboard_value(name: str, fn, fallback: Any, warnings: list[dict[str, Any]]) -> Any:
    try:
        return fn()
    except Exception as exc:
        warnings.append({"field": name, "message": str(exc)[:500], "type": exc.__class__.__name__})
        return fallback


@app.get("/api/projects/{project_id}/progress-state")
def get_project_progress_state(project_id: str, request: Request):
    """Lightweight polling endpoint for an active long-running job.

    v10.15.6 refreshed the complete dashboard every ~2.5 seconds, repeatedly
    allocating candidates, diagnostics, history and large log strings in the
    browser. During multi-hour analysis this creates unnecessary Firefox memory
    churn. Active polling only needs live status, a short history/log tail and
    the cached Ollama monitor; the full dashboard is refreshed once the job
    reaches a terminal state.
    """
    d = project_dir(project_id)
    warnings: list[dict[str, Any]] = []
    status = _safe_dashboard_value(
        "status",
        lambda: read_json(d / "status.json", {"state": "unknown", "progress": 0}) or {},
        {"state": "unknown", "progress": 0},
        warnings,
    )
    if isinstance(status, dict):
        status["latest_job"] = _safe_dashboard_value("latest_job", lambda: get_latest_job(project_id), None, warnings)

    logs_text = _safe_dashboard_value(
        "logs",
        lambda: (d / "logs.txt").read_text(encoding="utf-8", errors="replace") if (d / "logs.txt").exists() else "",
        "",
        warnings,
    )
    if len(logs_text) > 20000:
        logs_text = "… live logs truncated …\n" + logs_text[-20000:]
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            if not has_project_permission(db, request.state.user, project_id, "editor"):
                logs_text = ""

    state = str(status.get("state") if isinstance(status, dict) else "").lower()
    active = state in {"running", "queued", "cancel_requested"}
    stage = str(status.get("stage") if isinstance(status, dict) else "").lower()
    include_ollama = any(token in stage for token in ("ai", "ollama", "vision", "story", "dedup", "metadata"))
    return web_safe_payload(
        {
            "ok": True,
            "app_version": APP_VERSION,
            "server_time": time.time(),
            "poll_after_ms": 2500 if active else 12000,
            "active": active,
            "terminal": state in {"done", "error", "cancelled"},
            "warnings": warnings,
            "status": status,
            "status_history": _safe_dashboard_value(
                "status_history", lambda: (read_json(d / "status_history.json", []) or [])[-60:], [], warnings
            ),
            "logs": logs_text,
            "ollama_monitor": _safe_dashboard_value("ollama_monitor", lambda: _ollama_monitor_cached(ttl_seconds=12.0), None, warnings)
            if include_ollama
            else None,
        }
    )


@app.get("/api/projects/{project_id}/dashboard-state")
def get_project_dashboard_state(project_id: str, request: Request):
    """Single read-only state endpoint for the React dashboard.

    Earlier builds polled 20+ endpoints every 2 seconds. That worked, but the
    console looked noisy and weak PCs wasted time doing repeated tiny requests.
    This endpoint returns the same UI state in one JSON response and keeps heavy
    diagnostics read-only and cached where possible.
    """
    d = project_dir(project_id)
    warnings: list[dict[str, Any]] = []
    project = _safe_dashboard_value("project", lambda: load_project(d), {"id": project_id, "settings": default_settings()}, warnings)
    settings = project.get("settings", default_settings()) if isinstance(project, dict) else default_settings()
    p = project_paths(d)

    status = _safe_dashboard_value(
        "status",
        lambda: read_json(d / "status.json", {"state": "unknown", "progress": 0}) or {},
        {"state": "unknown", "progress": 0},
        warnings,
    )
    if isinstance(status, dict):
        status["latest_job"] = _safe_dashboard_value("latest_job", lambda: get_latest_job(project_id), None, warnings)
        status["diagnosis"] = _safe_dashboard_value("diagnosis", lambda: build_status_diagnosis(project_id, d, status), {}, warnings)

    logs_text = _safe_dashboard_value(
        "logs", lambda: (d / "logs.txt").read_text(encoding="utf-8", errors="replace") if (d / "logs.txt").exists() else "", "", warnings
    )
    if len(logs_text) > 70000:
        logs_text = "… logs truncated …\n" + logs_text[-70000:]
    if WEB_ACCOUNTS_ENABLED:
        with session_scope() as db:
            if not has_project_permission(db, request.state.user, project_id, "editor"):
                logs_text = ""

    fresh = _safe_dashboard_value("freshness", lambda: freshness_report(d, settings), {}, warnings)
    ready = _safe_dashboard_value("source_readiness", lambda: source_readiness(d), {"ok": False, "ready": False}, warnings)
    outputs = _safe_dashboard_value("outputs", lambda: list_output_files(d), [], warnings)
    for output in outputs:
        path_value = str(output.get("path") or "")
        current, stale_reason = output_is_publishable(d, settings, path_value, freshness=fresh)
        output["current"] = bool(current)
        output["stale_reason"] = stale_reason
    if isinstance(project, dict):
        project["source_ready"] = bool(ready.get("ready") or ready.get("ok"))
        project["source_readiness"] = ready
        project["freshness"] = fresh
    preview = _safe_dashboard_value("preview_status", lambda: preview_status(d), {"exists": False}, warnings)
    if isinstance(preview, dict):
        rough = d / "preview" / "rough_cut_preview.mp4"
        preview["rough_exists"] = rough.exists() and rough.stat().st_size > 1024
        preview["rough_path"] = "preview/rough_cut_preview.mp4"
        preview["rough_url"] = f"/api/projects/{project_id}/file/preview/rough_cut_preview.mp4"
        preview["rough_size_mb"] = round(rough.stat().st_size / 1024 / 1024, 2) if rough.exists() else 0

    state = str(status.get("state") if isinstance(status, dict) else "").lower()
    active = state in {"running", "queued"}

    return web_safe_payload(
        {
            "ok": True,
            "app_version": APP_VERSION,
            "server_time": time.time(),
            "poll_after_ms": 2500 if active else 12000,
            "active": active,
            "warnings": warnings,
            "project": project,
            "source_readiness": ready,
            "freshness": fresh,
            "status": status,
            "status_history": _safe_dashboard_value(
                "status_history", lambda: (read_json(d / "status_history.json", []) or [])[-120:], [], warnings
            ),
            "logs": logs_text,
            # Historical artifacts stay on disk for recovery, but stale analysis
            # must never be presented to the UI as the current Review/Montage.
            "candidates": _safe_dashboard_value("candidates", lambda: (read_json(p["candidates"], []) or []) if fresh.get("candidates_current") else [], [], warnings),
            "segments": _safe_dashboard_value("segments", lambda: (read_json(p["segments"], []) or []) if fresh.get("segments_current") else [], [], warnings),
            "stale_artifacts": {
                "candidates": bool(fresh.get("analysis_stale")),
                "segments": bool(fresh.get("segments_stale")),
                "render": bool(fresh.get("render_stale")),
            },
            "quality": _safe_dashboard_value(
                "quality",
                lambda: read_json(
                    p["quality"], {"quality_score": 0, "segments": 0, "candidates": 0, "recommendations": ["Отчёт ещё не создан."]}
                ),
                None,
                warnings,
            ),
            "factory": _safe_dashboard_value(
                "factory", lambda: read_json(p["factory"] / "content_factory_manifest.json", {}), {}, warnings
            ),
            "pipeline_state": _safe_dashboard_value(
                "pipeline_state", lambda: DurablePipelineState(d).snapshot(), {}, warnings
            ),
            "metadata": _safe_dashboard_value("metadata", lambda: read_json(d / "youtube_metadata.json", {}), {}, warnings),
            "youtube_upload_report": _safe_dashboard_value(
                "youtube_upload_report", lambda: read_json(d / "youtube_upload_report.json", {}), {}, warnings
            ),
            "benchmark": _safe_dashboard_value("benchmark", lambda: read_json(d / "model_benchmark_5min.json", []) or [], [], warnings),
            "preferences": _safe_dashboard_value(
                "preferences", lambda: read_json(d / "user_preferences.json", {"feedback": []}), {"feedback": []}, warnings
            ),
            "result_check": _safe_dashboard_value(
                "result_check", lambda: read_json(d / "last_result_check.json", read_json(d / "result_check.json", {})), {}, warnings
            ),
            "outputs": outputs,
            "preview_status": preview,
            "pre_render_check": _safe_dashboard_value(
                "pre_render_check",
                lambda: read_json(
                    d / "pre_render_check.json", {"ok": False, "warnings": ["Проверка ещё не запускалась."], "errors": [], "segments": 0}
                ),
                {"ok": False, "warnings": ["Проверка ещё не запускалась."], "errors": [], "segments": 0},
                warnings,
            ),
            "simple_log": _safe_dashboard_value("simple_log", lambda: simple_log_summary(d), None, warnings),
            "visual_quality": _safe_dashboard_value(
                "visual_quality",
                lambda: read_json(d / "visual_quality_report.json", {"recommendations": ["Visual Quality отчёт ещё не создан."]}),
                {},
                warnings,
            ),
            "quality_core": _safe_dashboard_value(
                "quality_core",
                lambda: read_json(d / "quality_core_report.json", {"segments": 0, "candidates": 0, "duration": "00:00:00.00"}),
                {},
                warnings,
            ),
            "adaptive_recommendation": _safe_dashboard_value(
                "adaptive_recommendation", lambda: read_json(d / "adaptive_recommendation.json", {}), {}, warnings
            ),
            "ai_coverage": _safe_dashboard_value("ai_coverage", lambda: read_json(d / "ai_coverage_report.json", {}), {}, warnings),
            "recovery": _safe_dashboard_value("recovery", lambda: project_recovery_status(d), {}, warnings),
            "ollama_monitor": _safe_dashboard_value("ollama_monitor", lambda: _ollama_monitor_cached(ttl_seconds=12.0), None, warnings),
            "auto_recommendation": _safe_dashboard_value(
                "auto_recommendation", lambda: build_autopilot_recommendation(d, settings), None, warnings
            ),
            "smart_preflight": _safe_dashboard_value("smart_preflight", lambda: smart_preflight_report(d, settings), None, warnings),
            "checkpoints": _safe_dashboard_value("checkpoints", lambda: project_checkpoint_status(d), None, warnings),
            "cache_fingerprint": _safe_dashboard_value("cache_fingerprint", lambda: cache_fingerprint_report(d, settings), None, warnings),
            "duration_control": _safe_dashboard_value("duration_control", lambda: duration_control_report(d, settings), None, warnings),
            "quality_before_render": _safe_dashboard_value(
                "quality_before_render", lambda: quality_before_render(d, settings), None, warnings
            ),
            "creator_pack": _safe_dashboard_value("creator_pack", lambda: creator_pack(d, settings), None, warnings),
            "project_history": {"items": _safe_dashboard_value("project_history", lambda: project_history_items(30), [], warnings)},
            "integrity": _safe_dashboard_value("integrity", lambda: project_integrity_report(d), None, warnings),
            "product_readiness": _safe_dashboard_value("product_readiness", lambda: product_readiness_report(d), None, warnings),
            "ai_quality_audit": _safe_dashboard_value("ai_quality_audit", lambda: ai_quality_audit(d), None, warnings),
            "project_doctor": _safe_dashboard_value(
                "project_doctor", lambda: read_json(d / "project_doctor_report.json", None), None, warnings
            ),
            "quality_lock": _safe_dashboard_value("quality_lock", lambda: quality_lock_report(d), None, warnings),
            "final_success": _safe_dashboard_value("final_success", lambda: final_success_report(d), None, warnings),
            "stable_candidate": _safe_dashboard_value("stable_candidate", lambda: stable_candidate_info(), None, warnings),
            "success_history": {"items": _safe_dashboard_value("success_history", lambda: success_history_items(30), [], warnings)},
            "product_test_plan": _safe_dashboard_value("product_test_plan", lambda: product_test_plan(d), None, warnings),
            "workflow_guard": _safe_dashboard_value("workflow_guard", lambda: workflow_guard_report(d), None, warnings),
            "render_artifact_check": _safe_dashboard_value("render_artifact_check", lambda: render_artifact_check(d), None, warnings),
            "hardware_runtime": _safe_dashboard_value("hardware_runtime", lambda: read_json(d / "hardware_runtime.json", {}), {}, warnings),
            "whisper_runtime": _safe_dashboard_value("whisper_runtime", lambda: read_json(d / "whisper_runtime.json", {}), {}, warnings),
            "ai_runtime_summary": _safe_dashboard_value("ai_runtime_summary", lambda: read_json(d / "ai_runtime_summary.json", {}), {}, warnings),
            "candidate_trace": _safe_dashboard_value(
                "candidate_trace",
                lambda: {k: v for k, v in (read_json(d / "candidate_decision_trace.json", {}) or {}).items() if k != "items"},
                {},
                warnings,
            ),
            "app_audit": _safe_dashboard_value("app_audit", lambda: app_audit_report(), None, warnings),
        }
    )


@app.get("/api/projects/{project_id}/product-readiness")
def project_product_readiness_endpoint(project_id: str):
    return product_readiness_report(project_dir(project_id))


@app.post("/api/projects/{project_id}/debug-bundle")
def project_debug_bundle_endpoint(project_id: str):
    d = project_dir(project_id)
    bundle = build_debug_bundle(d)
    return {"ok": True, "path": str(bundle.relative_to(d)).replace("\\", "/"), "size_mb": round(bundle.stat().st_size / 1024 / 1024, 2)}


@app.get("/api/projects/{project_id}/ai-quality-audit")
def project_ai_quality_audit_endpoint(project_id: str):
    return ai_quality_audit(project_dir(project_id))


@app.get("/api/projects/{project_id}/ai-runtime")
def project_ai_runtime_endpoint(project_id: str):
    d = project_dir(project_id)
    return web_safe_payload({
        "summary": read_json(d / "ai_runtime_summary.json", {}) or {},
        "hardware_runtime": read_json(d / "hardware_runtime.json", {}) or {},
        "whisper_runtime": read_json(d / "whisper_runtime.json", {}) or {},
    })


@app.get("/api/projects/{project_id}/candidate-trace")
def project_candidate_trace_endpoint(project_id: str):
    return web_safe_payload(read_json(project_dir(project_id) / "candidate_decision_trace.json", {}) or {})


@app.post("/api/projects/{project_id}/backfill-explanations")
def project_backfill_explanations_endpoint(project_id: str):
    return backfill_moment_explanations(project_dir(project_id))


@app.post("/api/projects/{project_id}/project-doctor")
def project_doctor_endpoint(project_id: str, payload: dict[str, Any] | None = None):
    return project_doctor(project_dir(project_id), payload or {})


@app.get("/api/projects/{project_id}/product-test-plan")
def project_product_test_plan_endpoint(project_id: str):
    return product_test_plan(project_dir(project_id))


@app.post("/api/projects/{project_id}/normalize-segments")
def project_normalize_segments_endpoint(project_id: str):
    d = project_dir(project_id)
    result = normalize_project_segments(d)
    return {"ok": True, **result, "duration_control": duration_control_report(d, load_project(d).get("settings", default_settings()))}


@app.get("/api/projects/{project_id}/integrity")
def project_integrity_endpoint(project_id: str):
    return project_integrity_report(project_dir(project_id))


@app.post("/api/projects/{project_id}/repair-json-backups")
def repair_project_json_backups_endpoint(project_id: str):
    return repair_project_json_backups(project_dir(project_id))


@app.post("/api/projects/{project_id}/settings")
def save_settings(project_id: str, settings: dict[str, Any]):
    d = project_dir(project_id)
    with project_metadata_lock(d):
        data = load_project(d)
        incoming = dict(settings or {})
        existing = data.get("settings", default_settings())
        merged = {**existing, **incoming}
        data["settings"] = validate_settings(merged)
        save_project(d, data)
        saved = dict(data["settings"])
    return web_safe_payload(saved)


@app.post("/api/projects/{project_id}/safe-defaults")
def apply_safe_defaults_endpoint(project_id: str):
    d = project_dir(project_id)
    project = load_project(d)
    project["settings"] = safe_ollama_settings(project.get("settings", default_settings()))
    save_project(d, project)
    return web_safe_payload({"ok": True, "settings": project["settings"], "message": "Безопасные настройки Ollama сохранены."})


@app.post("/api/projects/{project_id}/metadata-preset")
def apply_metadata_preset_endpoint(project_id: str, payload: dict[str, Any] | None = None):
    d = project_dir(project_id)
    project = load_project(d)
    preset = (payload or {}).get("preset", "normal")
    settings = metadata_preset_settings(project.get("settings", default_settings()), preset)
    label = settings.pop("_preset_label", str(preset))
    project["settings"] = settings
    save_project(d, project)
    return web_safe_payload(
        {"ok": True, "preset": preset, "label": label, "settings": settings, "message": f"Metadata preset «{label}» сохранён."}
    )


@app.post("/api/projects/{project_id}/hardware-preset")
def apply_hardware_preset_endpoint(project_id: str, payload: dict[str, Any] | None = None):
    d = project_dir(project_id)
    project = load_project(d)
    preset = str((payload or {}).get("preset", "auto_balanced") or "auto_balanced")
    # Explicit user action means re-scan the exact runtime instead of trusting
    # a stale dashboard cache.
    capabilities = detect_hardware_capabilities(force=True)
    settings = hardware_preset_settings(project.get("settings", default_settings()), preset)
    project["settings"] = settings
    project["hardware_capabilities"] = capabilities
    save_project(d, project)
    mode = "быстро" if "fast" in preset.lower() else "качество" if "quality" in preset.lower() else "баланс"
    label = f"Авто · {mode} · {capabilities.get('summary') or capabilities.get('profile_id')}"
    return web_safe_payload(
        {
            "ok": True,
            "preset": preset,
            "label": label,
            "hardware": capabilities,
            "settings": settings,
            "message": "Автонастройка сохранена. Whisper/encoder/workers выбраны по фактическим возможностям этого ПК; недоступные GPU-функции безопасно остаются на CPU.",
        }
    )


@app.post("/api/projects/{project_id}/hardware-auto")
def apply_hardware_auto_endpoint(project_id: str, payload: dict[str, Any] | None = None):
    payload = dict(payload or {})
    payload.setdefault("preset", "auto_balanced")
    return apply_hardware_preset_endpoint(project_id, payload)


@app.get("/api/task-presets")
def list_task_presets():
    return {
        "presets": [
            {"id": key, "label": val["label"], "description": val["description"], "settings": val["settings"]}
            for key, val in TASK_PRESETS.items()
        ]
    }


@app.post("/api/projects/{project_id}/task-preset")
def apply_task_preset_endpoint(project_id: str, payload: dict[str, Any] | None = None):
    d = project_dir(project_id)
    project = load_project(d)
    preset = str((payload or {}).get("preset", "balanced"))
    if preset not in TASK_PRESETS and preset != "balanced":
        raise HTTPException(422, f"Unknown task preset: {preset}")
    settings = task_preset_settings(project.get("settings", default_settings()), preset)
    project["settings"] = settings
    save_project(d, project)
    label = settings.get("task_preset_label") or TASK_PRESETS.get(preset, {}).get("label", "Сбалансированный / смысл")
    return web_safe_payload(
        {
            "ok": True,
            "preset": preset,
            "label": label,
            "settings": settings,
            "message": f"Task-пресет «{label}» сохранён. Теперь можно нажать «Собрать нарезку».",
        }
    )


@app.get("/api/projects/{project_id}/settings-check")
def settings_check(project_id: str):
    d = project_dir(project_id)
    raw = read_json(d / "project.json", {}) or {}
    settings = validate_settings(raw.get("settings", {}))
    important = {
        "ai_batch_size": settings.get("ai_batch_size"),
        "micro_batch_size": settings.get("micro_batch_size"),
        "metadata_max_segments": settings.get("metadata_max_segments"),
        "metadata_timeout": settings.get("metadata_timeout"),
        "visual_scan_enabled": settings.get("visual_scan_enabled"),
        "ocr_enabled": settings.get("ocr_enabled"),
        "ai_strict_mode": settings.get("ai_strict_mode"),
        "full_ai_coverage": settings.get("full_ai_coverage"),
        "hardware_profile": settings.get("hardware_profile"),
        "analysis_profile": settings.get("analysis_profile"),
        "whisper_model": settings.get("whisper_model"),
        "whisper_device": settings.get("whisper_device"),
        "ollama_num_ctx": settings.get("ollama_num_ctx"),
        "ollama_keep_alive": settings.get("ollama_keep_alive"),
    }
    return web_safe_payload(
        {
            "ok": True,
            "project_id": project_id,
            "settings_version": raw.get("settings_version"),
            "important": important,
            "settings": settings,
        }
    )


@app.post("/api/projects/{project_id}/preflight")
def run_preflight(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    return web_safe_payload(preflight(d, settings))


@app.post("/api/ai/check-openai")
def check_openai_api(settings: dict[str, Any]):
    # Backwards-compatible endpoint for old frontend builds.
    # v9.1.9 is Ollama-only, so cloud API key checks are intentionally disabled.
    return {
        "ok": False,
        "status": "disabled",
        "code": "ollama_only",
        "engine": "ollama",
        "message": "OpenAI API отключён в этой версии. Используется Ollama Local.",
        "recommendation": "Запусти Ollama: ollama serve, скачай qwen3:8b и qwen3-vl:8b, затем нажми Анализ → Проверка.",
    }


@app.post("/api/projects/{project_id}/analyze")
def start_analyze(project_id: str):
    d = project_dir(project_id)
    gate = authoritative_source_gate(d, deep_media_check=True)
    if not gate.get("ok"):
        raise HTTPException(status_code=422, detail={"message": gate.get("message") or "Источник не готов к анализу.", "gate": gate})
    return start_background_job(project_id, "Анализ запущен", lambda d, s, logger: analyze(d, s, logger), kind="analyze")


@app.post("/api/projects/{project_id}/render")
def start_render(project_id: str):
    d0 = project_dir(project_id)
    settings0 = load_project(d0).get("settings", default_settings())
    gate = authoritative_render_gate(d0, settings0)
    if not gate.get("ok"):
        raise HTTPException(status_code=422, detail={"message": "Рендер заблокирован: проект не прошёл обязательную проверку.", "gate": gate})
    def task(d: Path, settings: dict[str, Any], logger: JobLogger):
        logger.heartbeat("render_prepare", 2, "Рендер YouTube: подготовка сегментов и FFmpeg")
        logger.log("Render Live: старт финального YouTube-рендера. Прогресс будет обновляться по частям.")
        result = render(d, settings, logger)
        write_json(d / "last_render.json", result)
        logger.log("Готовые файлы:")
        for item in result.get("outputs", []):
            logger.log(f"- {item.get('path')} ({item.get('size_mb')} MB)")

    return start_background_job(project_id, "Рендер YouTube запущен", task, kind="render")


@app.post("/api/projects/{project_id}/content-factory")
def run_content_factory(project_id: str):
    require_paid_beta_entitlement("analysis")
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    fresh = freshness_report(d, settings)
    if not fresh.get("segments_current"):
        raise HTTPException(status_code=409, detail={"message": "Монтаж устарел относительно текущего анализа. Сначала обновите анализ/Review.", "code": "STALE_SEGMENTS", "freshness": fresh})
    return content_factory(d, settings)


def build_status_diagnosis(project_id: str, project_dir_: Path, status: dict[str, Any]) -> dict[str, Any]:
    """Human-readable hang/stall diagnosis for long local jobs."""
    now = time.time()
    state = str(status.get("state") or "unknown")
    stage = str(status.get("stage") or "idle")
    message = str(status.get("message") or "")
    updated_at = float(status.get("updated_at") or 0)
    started_at = float(status.get("started_at") or updated_at or now)
    seconds_since_update = max(0.0, now - updated_at) if updated_at else 0.0
    elapsed = max(0.0, now - started_at)
    latest_job = get_latest_job(project_id) or {}
    diag = {
        "ok": True,
        "severity": "ok",
        "title": "Работает нормально" if state in {"running", "queued"} else "Задача не запущена",
        "message": message or "Нет активной операции.",
        "action": "Если нужна свежая информация, нажми Обновить.",
        "stage": stage,
        "seconds_since_update": round(seconds_since_update, 1),
        "elapsed_seconds": round(elapsed, 1),
        "latest_job_state": latest_job.get("state"),
    }
    if state not in {"running", "queued"}:
        if state == "error":
            diag.update(
                {
                    "ok": False,
                    "severity": "error",
                    "title": "Задача завершилась ошибкой",
                    "action": "Открой лог ниже. Частые причины: Ollama не запущена, нет FFmpeg/yt-dlp, файл перемещён.",
                }
            )
        elif state == "cancelled":
            diag.update(
                {
                    "severity": "warning",
                    "title": "Задача остановлена",
                    "action": "Можно продолжить с последнего checkpoint через «Собрать нарезку» или повторить нужный этап.",
                }
            )
        else:
            diag.update({"title": "Ожидание действия", "action": "Выбери источник, пресет задачи и нажми «Собрать нарезку»."})
        return diag

    # If status.json is not updated for a while while the job is running, make it explicit.
    stale_limit = 180
    if stage in {"twitch_import", "twitch_download"}:
        stale_limit = 300
    elif stage in {"block_ai", "micro_ai", "vision_candidates", "metadata_ai"} or "ai" in stage.lower() or "ollama" in message.lower():
        stale_limit = 360
    elif stage in {"transcription", "whisper"} or "транск" in message.lower() or "whisper" in message.lower():
        stale_limit = 240

    if seconds_since_update > stale_limit:
        diag["ok"] = False
        diag["severity"] = "warning"
        if "twitch" in stage.lower() or "yt-dlp" in message.lower():
            diag.update(
                {
                    "title": "Twitch/yt-dlp давно не получает фрагменты",
                    "message": f"Twitch download не обновлялся {round(seconds_since_update)} секунд. Возможная причина: cookies, блокировка Twitch, плохой format или недоступный VOD.",
                    "action": "Что сделать: выбери cookies browser Chrome/Firefox, поменяй format на best или bestvideo+bestaudio, уменьши threads, запусти «Тест 10 минут» или скачай меньший диапазон.",
                }
            )
        elif "transcription" in stage.lower() or "whisper" in message.lower() or "транск" in message.lower():
            diag.update(
                {
                    "title": "Whisper долго не обновлялся",
                    "message": f"Whisper работает {round(elapsed / 60, 1)} мин, без обновления {round(seconds_since_update)} сек. Возможная причина: длинный аудиофайл, CPU перегружен или слишком большой chunk.",
                    "action": "Что сделать: включи chunk mode 600–900 sec, Whisper base/tiny, CPU int8, проверь RAM. Если 10+ минут нет новых строк в логе — нажми Стоп и продолжи с checkpoint.",
                }
            )
        elif "ai" in stage.lower() or "ollama" in message.lower() or "metadata" in stage.lower():
            diag.update(
                {
                    "title": "Ollama/AI долго не отвечает",
                    "message": f"AI batch {status.get('current_batch', '?')}/{status.get('total_batches', '?')} обрабатывается, без обновления {round(seconds_since_update)} сек. Возможная причина: модель слишком тяжёлая для GTX 1050 Ti или слишком большой batch/context.",
                    "action": "Что сделать: AI batch 1–2, micro batch 1, выключить full AI coverage для теста, выбрать GTX 1050 Ti — быстро, нажать «Выгрузить модели Ollama» после Stop.",
                }
            )
        else:
            diag.update(
                {
                    "title": "Этап давно не обновлялся",
                    "message": "Прогресс не менялся дольше обычного для этого этапа.",
                    "action": "Проверь лог. Если в логе нет новых строк 10+ минут — нажми Стоп и запусти этап заново с безопасными настройками.",
                }
            )
    elif elapsed > 1800 and (status.get("eta_seconds") is None or float(status.get("eta_seconds") or 0) > 1800):
        diag.update(
            {
                "severity": "info",
                "title": "Долгая задача идёт нормально",
                "message": "Для 3–6 часовых стримов Whisper, visual scan и Ollama могут работать долго. Смотри stage-счётчик и ETA.",
                "action": "Если счётчик current/total меняется — ничего не трогай. Если нет — открой диагностику выше.",
            }
        )
    return diag


@app.get("/api/projects/{project_id}/status")
def get_status(project_id: str):
    d = project_dir(project_id)
    status = read_json(d / "status.json", {"state": "unknown", "progress": 0})
    status["latest_job"] = get_latest_job(project_id)
    status["diagnosis"] = build_status_diagnosis(project_id, d, status)
    return web_safe_payload(status)


@app.get("/api/projects/{project_id}/logs")
def get_logs(project_id: str):
    d = project_dir(project_id)
    path = d / "logs.txt"
    return web_safe_payload({"logs": redact_text(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else ""})


@app.get("/api/projects/{project_id}/candidates")
def get_candidates(project_id: str, response: Response):
    d = project_dir(project_id)
    project = load_project(d)
    fresh = freshness_report(d, project.get("settings", default_settings()))
    response.headers["X-Analysis-Revision"] = str(fresh.get("analysis_revision") or "")
    response.headers["X-Candidates-Stale"] = "false" if fresh.get("candidates_current") else "true"
    return read_json(project_paths(d)["candidates"], []) if fresh.get("candidates_current") else []


@app.get("/api/projects/{project_id}/segments")
def get_segments(project_id: str, response: Response):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    fresh = freshness_report(d, settings)
    response.headers["X-Segments-Revision"] = str(fresh.get("segments_revision") or segments_revision(d, settings))
    response.headers["X-Segments-Stale"] = "false" if fresh.get("segments_current") else "true"
    return read_json(project_paths(d)["segments"], []) if fresh.get("segments_current") else []


@app.put("/api/projects/{project_id}/segments")
def put_segments(project_id: str, segments: list[dict[str, Any]], request: Request, response: Response):
    d = project_dir(project_id)
    with segment_mutation_locks_guard:
        lock = segment_mutation_locks.setdefault(project_id, threading.Lock())
    with lock:
        settings = load_project(d).get("settings", default_settings())
        source_rev = source_revision(d)
        analysis_rev = analysis_revision(d, settings, source_rev=source_rev)
        current = segments_revision(d, settings, source_rev=source_rev, analysis_rev=analysis_rev)
        expected = str(request.headers.get("x-segments-revision") or "").strip()
        existing = read_json(project_paths(d)["segments"], []) or []
        if existing and not expected:
            raise HTTPException(status_code=428, detail={"message": "Сегменты изменяются с контролем версии. Обнови монтаж и повтори сохранение.", "current_revision": current})
        if expected and expected != current:
            raise HTTPException(status_code=409, detail={"message": "Монтаж изменился в другой вкладке или запросе. Обнови данные перед сохранением.", "expected_revision": expected, "current_revision": current})
        duration = cached_source_duration(d, allow_probe=True)
        result = update_segments(d, segments, source_duration=duration, revision_context={
            "source_revision": source_rev, "analysis_revision": analysis_rev,
        })
        new_revision = segments_revision_from_items(result, settings, source_rev=source_rev, analysis_rev=analysis_rev)
        response.headers["X-Segments-Revision"] = new_revision
        return result


@app.post("/api/projects/{project_id}/segments/remove")
def remove_project_segment(project_id: str, payload: dict[str, Any]):
    """Delete one exact clip without probing video or rewriting unrelated clips."""
    d = project_dir(project_id)
    with segment_mutation_locks_guard:
        lock = segment_mutation_locks.setdefault(project_id, threading.Lock())
    with lock:
        settings = load_project(d).get("settings", default_settings())
        source_rev = source_revision(d)
        analysis_rev = analysis_revision(d, settings, source_rev=source_rev)
        existing = read_json(project_paths(d)["segments"], []) or []
        current = segments_revision_from_items(existing, settings, source_rev=source_rev, analysis_rev=analysis_rev)
        expected = str(payload.get("expected_revision") or "")
        if not expected or expected != current:
            raise HTTPException(409 if expected else 428, detail={
                "message": "Монтаж изменился. Данные обновлены — повтори удаление.", "current_revision": current,
            })
        item = payload.get("item")
        if not isinstance(item, dict) or not all(key in item for key in ("id", "start", "end")):
            raise HTTPException(422, detail="Не указан удаляемый фрагмент")
        matches = [i for i, clip in enumerate(existing)
                   if all(clip.get(key) == item[key] for key in ("id", "start", "end"))]
        if len(matches) != 1:
            raise HTTPException(409, detail={"message": "Фрагмент изменился. Обнови монтаж.", "current_revision": current})
        result = [dict(clip) for i, clip in enumerate(existing) if i != matches[0]]
        # Preserve order, source identity and all analysis metadata on the survivors.
        for i, clip in enumerate(result, 1):
            clip["id"] = i
        new_revision = segments_revision_from_items(result, settings, source_rev=source_rev, analysis_rev=analysis_rev)
        write_json(project_paths(d)["segments"], result)
        mark_segments_updated(d, settings, source_rev=source_rev, analysis_rev=analysis_rev, segments_rev=new_revision)
        try:
            quality_report(d)
        except Exception:
            logging.getLogger(__name__).exception("Quality report refresh failed after saved clip removal")
        return {"ok": True, "segments": result, "segments_revision": new_revision}


@app.post("/api/projects/{project_id}/segments/add")
def add_project_segment(project_id: str, request: Request, payload: dict[str, Any] | None = None):
    """Atomically add one candidate to the final montage.

    This endpoint exists for the Review Studio happy path.  It avoids replacing
    the whole segments array for a one-item action, which makes the operation
    resilient to a temporarily stale browser state and removes the Firefox
    minified-bundle failure path seen in 10.15.9.
    """
    mutation_started = time.perf_counter()
    d = project_dir(project_id)
    payload = payload or {}
    item = payload.get("item") or payload.get("candidate") or {}
    if not isinstance(item, dict):
        raise HTTPException(status_code=422, detail={"message": "Не удалось определить выбранный момент."})

    try:
        start = float(item.get("start"))
        end = float(item.get("end"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail={"message": "У выбранного момента некорректные временные границы."})
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise HTTPException(status_code=422, detail={"message": "У выбранного момента некорректные временные границы."})

    with segment_mutation_locks_guard:
        lock = segment_mutation_locks.setdefault(project_id, threading.Lock())
    with lock:
        settings = load_project(d).get("settings", default_settings())
        expected = str(payload.get("expected_revision") or request.headers.get("x-segments-revision") or "").strip()
        revision_state = read_revision_state(d)
        stored_revision = str(revision_state.get("segments_revision") or "").strip()
        stored_source_revision = str(revision_state.get("source_revision") or "").strip()
        stored_analysis_revision = str(
            revision_state.get("segments_analysis_revision")
            or revision_state.get("candidates_analysis_revision")
            or ""
        ).strip()
        revision_context: dict[str, str] | None = None
        if expected and stored_revision and expected == stored_revision and stored_source_revision and stored_analysis_revision:
            # The browser and the persisted revision state agree. Reuse that
            # already-verified context instead of sampling a 10-20 GB VOD four
            # more times during one atomic Review Studio click.
            current_revision = stored_revision
            revision_context = {
                "source_revision": stored_source_revision,
                "analysis_revision": stored_analysis_revision,
            }
        else:
            current_revision = segments_revision(d, settings)
        existing = read_json(project_paths(d)["segments"], []) or []

        # A missing revision is allowed for this atomic action.  If the caller
        # supplied one, preserve the same optimistic-concurrency guarantee as
        # PUT /segments.
        if expected and expected != current_revision:
            raise HTTPException(status_code=409, detail={
                "message": "Монтаж изменился в другой вкладке. Данные обновлены — повтори добавление момента.",
                "expected_revision": expected,
                "current_revision": current_revision,
            })

        source_key = str(item.get("source_candidate_key") or "").strip()
        candidate_id = str(item.get("candidate_id") or "").strip()

        def same_segment(segment: dict[str, Any]) -> bool:
            if source_key and str(segment.get("source_candidate_key") or "").strip() == source_key:
                return True
            if candidate_id and str(segment.get("candidate_id") or "").strip() == candidate_id:
                return True
            try:
                return abs(float(segment.get("start")) - start) < 0.15 and abs(float(segment.get("end")) - end) < 0.15
            except (TypeError, ValueError):
                return False

        if any(same_segment(segment) for segment in existing if isinstance(segment, dict)):
            return {
                "ok": True,
                "added": False,
                "reason": "already_present",
                "message": "Этот момент уже находится в итоговой нарезке.",
                "segments": existing,
                "segments_revision": current_revision,
            }

        for segment in existing:
            if not isinstance(segment, dict):
                continue
            try:
                other_start = float(segment.get("start"))
                other_end = float(segment.get("end"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(other_start) and math.isfinite(other_end) and max(other_start, start) < min(other_end, end):
                raise HTTPException(status_code=409, detail={
                    "message": "Похожий фрагмент уже есть в итоговой нарезке. Измени границы существующего фрагмента вместо создания дубля.",
                    "kind": "segment_overlap",
                })

        candidate = dict(item)
        if not source_key:
            raw_id = candidate.get("id", "clip")
            candidate["source_candidate_key"] = f"{raw_id}-{start:.2f}-{end:.2f}"
        duration = cached_source_duration(d, allow_probe=True)
        validated = update_segments(
            d,
            [*existing, candidate],
            source_duration=duration if duration > 0 else None,
            revision_context=revision_context,
        )
        if revision_context:
            new_revision = segments_revision_from_items(
                validated,
                settings,
                analysis_rev=revision_context["analysis_revision"],
                source_rev=revision_context["source_revision"],
            )
        else:
            new_revision = segments_revision(d, settings)
        return {
            "ok": True,
            "added": True,
            "message": "Момент добавлен в итоговую нарезку.",
            "segments": validated,
            "segments_revision": new_revision,
            "save_duration_ms": round((time.perf_counter() - mutation_started) * 1000.0, 1),
            "fast_revision_path": bool(revision_context),
        }


@app.get("/api/projects/{project_id}/quality")
def get_quality(project_id: str):
    d = project_dir(project_id)
    return read_json(
        project_paths(d)["quality"], {"quality_score": 0, "segments": 0, "candidates": 0, "recommendations": ["Отчёт ещё не создан."]}
    )


@app.post("/api/projects/{project_id}/quality")
def post_quality(project_id: str):
    d = project_dir(project_id)
    return quality_report(d)


@app.get("/api/projects/{project_id}/factory")
def get_factory(project_id: str):
    d = project_dir(project_id)
    path = project_paths(d)["factory"] / "content_factory_manifest.json"
    return read_json(path, {})


@app.post("/api/projects/{project_id}/benchmark")
def start_benchmark(project_id: str):
    return start_background_job(project_id, "Benchmark запущен", lambda d, s, logger: model_benchmark(d, s, logger), kind="benchmark")


@app.get("/api/projects/{project_id}/benchmark")
def get_benchmark(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "model_benchmark_5min.json", [])


@app.post("/api/projects/{project_id}/feedback")
def post_feedback(project_id: str, payload: dict[str, Any]):
    d = project_dir(project_id)
    item = payload.get("item", {})
    label = payload.get("label", "good")
    note = payload.get("note", "")
    return save_feedback(d, item, label, note)


@app.get("/api/projects/{project_id}/preferences")
def get_preferences(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "user_preferences.json", {"feedback": []})


@app.post("/api/projects/{project_id}/metadata")
def post_metadata(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    return generate_youtube_metadata(d, settings)


@app.post("/api/projects/{project_id}/metadata-ai")
def post_metadata_ai(project_id: str):
    def task(d: Path, settings: dict[str, Any], logger: JobLogger):
        settings = dict(settings)
        settings["metadata_ai_enabled"] = True
        result = generate_youtube_metadata(d, settings, logger)
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "Метаданные не подготовлены. Проверьте итоговый монтаж.")
        write_json(d / "metadata_job_result.json", result)
        logger.set_status("done", 100, "AI Metadata готова" if result.get("ai_used") else "Локальные метаданные готовы; AI не использован")

    return start_background_job(project_id, "AI Metadata запущена", task, kind="metadata_ai")


@app.post("/api/projects/{project_id}/metadata-ai-strict")
def post_metadata_ai_strict(project_id: str):
    def task(d: Path, settings: dict[str, Any], logger: JobLogger):
        settings = dict(settings)
        settings["metadata_ai_enabled"] = True
        settings["require_ai_metadata"] = True
        settings["ai_strict_mode"] = True
        settings["metadata_timeout"] = max(int(settings.get("metadata_timeout", 300) or 300), 300)
        result = generate_youtube_metadata(d, settings, logger)
        if not result.get("ok") or not result.get("ai_used"):
            raise RuntimeError(result.get("error") or "AI Metadata не подготовлена.")
        write_json(d / "metadata_job_result.json", result)
        logger.set_status("done", 100, "AI Metadata готова")

    return start_background_job(project_id, "AI Metadata 100% запущена", task, kind="metadata_ai_strict")


@app.get("/api/projects/{project_id}/metadata")
def get_metadata(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "youtube_metadata.json", {})


@app.get("/api/projects/{project_id}/ai-coverage")
def get_ai_coverage(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "ai_coverage_report.json", {})


@app.get("/api/projects/{project_id}/jobs")
def get_project_jobs(project_id: str):
    project_dir(project_id)
    return list_jobs(project_id, limit=100)


@app.get("/api/jobs")
def get_jobs(request: Request):
    items = list_jobs(limit=200)
    if not WEB_ACCOUNTS_ENABLED:
        return items[:100]
    with session_scope() as db:
        user = db.get(User, request.state.user.id)
        allowed = accessible_project_ids(db, user)
    if allowed is None:
        return items[:100]
    return [item for item in items if item.get("project_id") in allowed][:100]


@app.get("/api/projects/{project_id}/editing-profile")
def get_editing_profile(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "editing_profile.json", {})


@app.get("/api/projects/{project_id}/visual-scan")
def get_visual_scan(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "visual_scan_report.json", {"enabled": False, "samples": []})


@app.post("/api/projects/{project_id}/visual-scan")
def post_visual_scan(project_id: str):
    return start_background_job(project_id, "Visual scan запущен", lambda d, s, logger: visual_scan_video(d, s, logger), kind="visual_scan")


@app.get("/api/projects/{project_id}/ocr-scan")
def get_ocr_scan(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "ocr_scan_report.json", {"enabled": False, "items": []})


@app.get("/api/projects/{project_id}/audio-events")
def get_audio_events(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "audio_events.json", {"enabled": False, "events": []})


@app.post("/api/projects/{project_id}/ocr-scan")
def post_ocr_scan(project_id: str):
    return start_background_job(project_id, "OCR scan запущен", lambda d, s, logger: ocr_scan_video(d, s, logger), kind="ocr_scan")


@app.post("/api/projects/{project_id}/audio-events")
def post_audio_events(project_id: str):
    return start_background_job(
        project_id, "IRL audio events запущены", lambda d, s, logger: detect_audio_events(d, s, logger), kind="audio_events"
    )


@app.post("/api/projects/{project_id}/srt")
def post_srt(project_id: str):
    d = project_dir(project_id)
    p = generate_srt(d)
    return {"path": str(p), "url": f"/api/projects/{project_id}/file/highlight_subtitles.srt"}


@app.get("/api/projects/{project_id}/result-check")
def get_result_check(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "last_result_check.json", read_json(d / "result_check.json", {}))


@app.post("/api/projects/{project_id}/result-check")
def post_result_check(project_id: str):
    d = project_dir(project_id)
    data = result_check(d)
    write_json(d / "result_check.json", data)
    return data


@app.get("/api/projects/{project_id}/outputs")
def get_outputs(project_id: str):
    d = project_dir(project_id)
    return list_output_files(d)


@app.post("/api/projects/{project_id}/cancel")
def cancel_project_job(project_id: str):
    d = project_dir(project_id)
    return request_cancel(d)


@app.post("/api/projects/{project_id}/preview")
def start_preview(project_id: str):
    return start_background_job(project_id, "Preview-safe запущен", lambda d, s, logger: create_preview_video(d, s, logger), kind="preview")


@app.post("/api/projects/{project_id}/rough-preview")
def start_rough_preview(project_id: str):
    return start_background_job(
        project_id, "Rough-cut preview запущен", lambda d, s, logger: render_rough_cut_preview(d, s, logger), kind="rough_preview"
    )


@app.post("/api/projects/{project_id}/hls-preview")
def start_hls_preview(project_id: str):
    return start_background_job(
        project_id, "HLS/proxy preview запущен", lambda d, s, logger: create_hls_proxy_preview(d, s, logger), kind="hls_preview"
    )


@app.post("/api/projects/{project_id}/timeline-export")
def post_timeline_export(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    try:
        return export_edit_timelines(d, settings)
    except RuntimeError as exc:
        result = {"ok": False, "error": str(exc), "exports": []}
        write_json(d / "timeline_export_report.json", result)
        return result


@app.get("/api/projects/{project_id}/timeline-export")
def get_timeline_export(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "timeline_export_report.json", {"ok": False, "exports": []})


@app.post("/api/projects/{project_id}/thumbnail-ideas")
def post_thumbnail_ideas(project_id: str):
    def task(d: Path, settings: dict[str, Any], logger: JobLogger):
        result = generate_thumbnail_ideas(d, settings, logger)
        logger.set_status("done", 100, f"Thumbnail ideas готовы: {len(result.get('ideas', []))}")

    return start_background_job(project_id, "Thumbnail ideas запущены", task, kind="thumbnail_ideas")


@app.get("/api/projects/{project_id}/thumbnail-ideas")
def get_thumbnail_ideas(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "thumbnail_ideas.json", {"ok": False, "ideas": []})


@app.post("/api/projects/{project_id}/compare-mode")
def post_compare_mode(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    try:
        return build_compare_mode(d, settings)
    except RuntimeError as exc:
        result = {"ok": False, "error": str(exc), "A_dense": {"segments": 0}, "B_story": {"segments": 0}}
        write_json(d / "compare_mode.json", result)
        return result


@app.get("/api/projects/{project_id}/compare-mode")
def get_compare_mode(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "compare_mode.json", {"ok": False})


@app.get("/api/projects/{project_id}/preview-status")
def get_preview_status(project_id: str):
    d = project_dir(project_id)
    info = preview_status(d)
    rough = d / "preview" / "rough_cut_preview.mp4"
    info["rough_exists"] = rough.exists() and rough.stat().st_size > 1024
    info["rough_path"] = "preview/rough_cut_preview.mp4"
    info["rough_url"] = f"/api/projects/{project_id}/file/preview/rough_cut_preview.mp4"
    info["rough_size_mb"] = round(rough.stat().st_size / 1024 / 1024, 2) if rough.exists() else 0
    return info


@app.get("/api/projects/{project_id}/pre-render-check")
def get_pre_render(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "pre_render_check.json", {"ok": False, "warnings": ["Проверка ещё не запускалась."], "errors": [], "segments": 0})


@app.post("/api/projects/{project_id}/pre-render-check")
def post_pre_render(project_id: str):
    d = project_dir(project_id)
    return pre_render_check(d)


@app.get("/api/projects/{project_id}/simple-log")
def get_simple_log(project_id: str):
    d = project_dir(project_id)
    return web_safe_payload(simple_log_summary(d))


@app.post("/api/projects/{project_id}/render-factory")
def start_render_factory(project_id: str):
    d0 = project_dir(project_id)
    settings0 = load_project(d0).get("settings", default_settings())
    fresh = freshness_report(d0, settings0)
    if not fresh.get("segments_current"):
        raise HTTPException(status_code=409, detail={"message": "Content Factory нельзя рендерить из устаревшего монтажа. Обновите анализ/Review.", "code": "STALE_SEGMENTS", "freshness": fresh})
    def task(d: Path, settings: dict[str, Any], logger: JobLogger):
        result = render_factory_versions(d, settings, logger)
        write_json(d / "last_factory_render.json", result)

    return start_background_job(project_id, "Рендер всех версий Content Factory", task, kind="render_factory")


@app.post("/api/projects/{project_id}/render-shorts")
def start_render_shorts(project_id: str):
    d0 = project_dir(project_id)
    settings0 = load_project(d0).get("settings", default_settings())
    fresh = freshness_report(d0, settings0)
    if not fresh.get("segments_current"):
        raise HTTPException(status_code=409, detail={"message": "Shorts нельзя собрать из устаревшего монтажа. Обновите анализ/Review.", "code": "STALE_SEGMENTS", "freshness": fresh})
    def task(d: Path, settings: dict[str, Any], logger: JobLogger):
        logger.heartbeat("shorts_prepare", 2, "Shorts export: подготовка кандидатов и вертикального рендера", eta_seconds=None)
        logger.log("Shorts Live: старт экспорта Shorts. Каждый клип будет отображаться в прогрессе.")
        result = render_shorts_candidates(d, settings, logger)
        write_json(d / "last_shorts_render.json", result)
        logger.log(f"Shorts Live: готово клипов: {len(result.get('rendered', []))}")

    return start_background_job(project_id, "Shorts export запущен", task, kind="shorts")


def _persist_short_editor_candidate(d: Path, short_index: int, payload: ShortRenderRequest) -> dict[str, Any]:
    """Called only after lifecycle admission; share validation for save/render."""
    if short_index < 1:
        raise HTTPException(422, "short_index должен начинаться с 1")
    with project_metadata_lock(d):
        settings = load_project(d).get("settings", default_settings())
        try:
            validate_short_editor_bounds(
                payload.start, payload.end, settings, cached_source_duration(d, allow_probe=False)
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        factory_dir = project_paths(d)["factory"]
        candidates_path = factory_dir / "shorts_candidates.json"
        candidates = read_json(candidates_path, []) or []
        if not isinstance(candidates, list) or short_index > len(candidates) or not isinstance(candidates[short_index - 1], dict):
            raise HTTPException(404, "Shorts-кандидат не найден")
        updated = {**candidates[short_index - 1], **payload.model_dump(),
                   "editor_identity": _shorts_edit_identity(candidates[short_index - 1]),
                   "duration_seconds": round(payload.end - payload.start, 3),
                   "bounds_edited": True, "render_dirty": True}
        if payload.caption_text is None:
            updated.pop("caption_text", None)
        candidates[short_index - 1] = updated
        write_json(candidates_path, candidates)
        factory_path = factory_dir / "content_factory_manifest.json"
        factory = read_json(factory_path, {}) or {}
        factory = factory if isinstance(factory, dict) else {}
        factory["shorts"] = candidates
        write_json(factory_path, factory)
        return updated


@app.put("/api/projects/{project_id}/shorts/{short_index}")
def save_short_candidate(project_id: str, short_index: int, payload: ShortRenderRequest):
    d = project_dir(project_id)
    with project_lifecycle_lock(d):
        with jobs_lock:
            if project_id in jobs and jobs[project_id].is_alive():
                raise HTTPException(409, "Дождитесь завершения текущей задачи перед сохранением Short")
        candidate = _persist_short_editor_candidate(d, short_index, payload)
    return {"ok": True, "candidate": candidate}


@app.post("/api/projects/{project_id}/shorts/{short_index}/render")
def start_render_one_short(project_id: str, short_index: int, payload: ShortRenderRequest):
    """Persist one editor card and regenerate only its atomic output."""
    d = project_dir(project_id)
    project = load_project(d)
    settings = project.get("settings", default_settings())
    fresh = freshness_report(d, settings)
    if not fresh.get("segments_current"):
        raise HTTPException(status_code=409, detail={"message": "Shorts нельзя собрать из устаревшего монтажа.", "code": "STALE_SEGMENTS", "freshness": fresh})
    prepared: dict[str, Any] = {}

    def prepare(project_dir_: Path):
        prepared["candidate"] = _persist_short_editor_candidate(project_dir_, short_index, payload)

    def task(project_dir_: Path, runtime_settings: dict[str, Any], logger: JobLogger):
        result = render_shorts_candidates(
            project_dir_, runtime_settings, logger, only_indexes={short_index}
        )
        write_json(project_dir_ / "last_shorts_render.json", result)

    result = start_background_job(
        project_id,
        f"Перегенерация Short {short_index}",
        task,
        kind="shorts_single",
        prepare=prepare,
    )
    return {**result, **prepared}


@app.get("/api/projects/{project_id}/visual-quality")
def get_visual_quality(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "visual_quality_report.json", {"recommendations": ["Visual Quality отчёт ещё не создан."]})


@app.post("/api/projects/{project_id}/visual-quality")
def post_visual_quality(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    return visual_quality_report(d, settings)


@app.get("/api/projects/{project_id}/quality-core")
def get_quality_core(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "quality_core_report.json", {"segments": 0, "candidates": 0, "duration": "00:00:00.00"})


@app.post("/api/projects/{project_id}/quality-core")
def post_quality_core(project_id: str):
    d = project_dir(project_id)
    settings = load_project(d).get("settings", default_settings())
    return build_quality_core_report(d, settings)


@app.get("/api/projects/{project_id}/adaptive-recommendation")
def get_adaptive_recommendation(project_id: str):
    d = project_dir(project_id)
    return web_safe_payload(read_json(d / "adaptive_recommendation.json", {}))


@app.post("/api/projects/{project_id}/adaptive-recommendation")
def post_adaptive_recommendation(project_id: str):
    d = project_dir(project_id)
    project = load_project(d)
    settings = project.get("settings", default_settings())
    return web_safe_payload(adaptive_auto_settings(d, settings))


@app.post("/api/projects/{project_id}/adaptive-settings")
def post_adaptive_settings(project_id: str):
    d = project_dir(project_id)
    project = load_project(d)
    settings = project.get("settings", default_settings())
    result = apply_adaptive_settings(d, settings)
    # Make sure recommendation-written settings also pass API validation and carry app version.
    project = load_project(d)
    project["settings"] = validate_settings(project.get("settings", {}))
    save_project(d, project)
    return web_safe_payload(result)


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str, request: Request):
    d = project_dir(project_id)
    quarantine: Path | None = None
    with project_lifecycle_lock(d):
        with jobs_lock:
            if project_id in jobs and jobs[project_id].is_alive():
                raise HTTPException(409, "Нельзя удалить проект, пока задача запущена")
        if WEB_ACCOUNTS_ENABLED:
            # Move out of the live namespace first; this is atomic on the same
            # volume and can be rolled back if the DB transaction fails.
            quarantine = d.with_name(f".deleting-{project_id}-{uuid.uuid4().hex[:8]}")
            os.replace(d, quarantine)
        else:
            shutil.rmtree(d)
    if WEB_ACCOUNTS_ENABLED:
        actor = getattr(request.state, "user", None)
        try:
            with session_scope() as db:
                db.execute(sa_delete(JobRecord).where(JobRecord.project_id == project_id))
                db.execute(sa_delete(ProjectMembership).where(ProjectMembership.project_id == project_id))
                record = db.get(ProjectRecord, project_id)
                if record:
                    db.delete(record)
                if actor:
                    from ..infrastructure.auth.service import audit

                    audit(db, "project.deleted", actor.id, "project", project_id, {}, request.client.host if request.client else "")
        except Exception:
            if quarantine and quarantine.exists() and not d.exists():
                try:
                    os.replace(quarantine, d)
                except OSError:
                    pass
            raise
        if quarantine and quarantine.exists():
            try:
                shutil.rmtree(quarantine)
            except OSError:
                # DB no longer exposes the project; leave a hidden quarantine for
                # a later maintenance pass rather than resurrecting a deleted ID.
                pass
    return {"ok": True}


@app.post("/api/projects/{project_id}/clear-cache")
def clear_project_cache(project_id: str):
    d = project_dir(project_id)
    with project_lifecycle_lock(d):
        with jobs_lock:
            if project_id in jobs and jobs[project_id].is_alive():
                raise HTTPException(409, "Нельзя чистить кэш, пока задача запущена")
        removed: list[str] = []
        failed: list[dict[str, str]] = []
        for name in RECOMPUTABLE_CACHE_PATHS:
            path = d / name
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
                else:
                    continue
                if path.exists():
                    raise OSError("path still exists after deletion")
                removed.append(name)
            except OSError as exc:
                failed.append({"path": name, "error": str(exc)})
    if failed:
        raise HTTPException(status_code=409, detail={"message": "Часть кэша не удалось удалить. Закрой программы, которые держат файлы, и повтори.", "removed": removed, "failed": failed})
    return {"ok": True, "removed": removed, "failed": []}


EXPORT_SKIP_DIRS = {"exports", "render_parts", "transcript_chunks", "frames", "preview", "hls", "__pycache__"}
EXPORT_SKIP_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".ts", ".wav", ".tmp", ".part"}
EXPORT_SKIP_NAMES = {"input.mp4", "input.mkv", "audio_16k.wav", "local_auth_token.txt", "jobs.sqlite3"}


def _should_export_project_file(rel: str, path: Path) -> bool:
    parts = set(Path(rel).parts)
    if parts & EXPORT_SKIP_DIRS:
        return False
    if path.name in EXPORT_SKIP_NAMES:
        return False
    if path.suffix.lower() in EXPORT_SKIP_SUFFIXES:
        return False
    # Large binaries make project sharing unreliable. Full media export should be
    # a separate explicit job, not the default metadata zip.
    try:
        if path.stat().st_size > 25 * 1024 * 1024:
            return False
    except Exception:
        return False
    return True


@app.post("/api/projects/{project_id}/export")
def export_project(project_id: str):
    d = project_dir(project_id)
    exports = d / "exports"
    exports.mkdir(exist_ok=True)
    out = exports / f"{safe_name(d.name)}_project_metadata_export.zip"
    included = 0
    skipped = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in d.rglob("*"):
            if path == out or path.is_dir():
                continue
            rel = path.relative_to(d).as_posix()
            if not _should_export_project_file(rel, path):
                skipped += 1
                continue
            zf.write(path, rel)
            included += 1
    return {
        "ok": True,
        "kind": "metadata",
        "path": out.relative_to(d).as_posix(),
        "url": f"/api/projects/{project_id}/file/{out.relative_to(d).as_posix()}",
        "size_mb": round(out.stat().st_size / 1024 / 1024, 2),
        "included_files": included,
        "skipped_heavy_or_runtime_files": skipped,
        "message": "Экспортированы метаданные проекта без исходного видео, WAV/chunks, outputs и секретов.",
    }


@app.get("/api/projects/{project_id}/open-folder")
def open_folder(project_id: str):
    d = project_dir(project_id)
    if WEB_ACCOUNTS_ENABLED:
        return {"ok": False, "web_mode": True, "message": "В web-режиме скачивай готовые файлы из раздела Экспорт."}
    return {"ok": True, "path": str(d.resolve()), "message": "Открой эту папку вручную в проводнике/файловом менеджере."}


@app.post("/api/projects/{project_id}/clip-preview")
def create_clip_preview(project_id: str, payload: ClipPreviewRequest):
    """Create a small browser-safe H.264/AAC clip for reliable repeated preview.

    Seeking repeatedly inside a multi-hour Twitch VOD can exhaust or confuse the
    browser decoder, especially for TS/MKV files with sparse keyframes.  The UI
    therefore previews a cached short MP4 instead of keeping the full source open.
    """
    d = project_dir(project_id)
    source = source_video_path(d)
    if not source.exists() or source.is_dir():
        raise HTTPException(404, "Source video not found")
    start = max(0.0, float(payload.start))
    end = float(payload.end)
    if not math.isfinite(start) or not math.isfinite(end):
        raise HTTPException(422, "start/end must be finite")
    if end <= start:
        raise HTTPException(422, "end must be greater than start")
    if end - start > 300:
        raise HTTPException(422, "preview clip cannot exceed 300 seconds")
    total = float(video_duration(source) or 0)
    if total > 0:
        if start >= total - 0.05:
            raise HTTPException(422, "Clip starts after the end of the source video")
        end = min(end, total)
    duration = end - start
    if duration < 0.25:
        raise HTTPException(422, "Clip is too short")

    stat = source.stat()
    identity = f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{start:.3f}|{end:.3f}"
    cache_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    rel = Path("preview") / "clips" / f"clip_{cache_key}.mp4"
    out = d / rel
    out.parent.mkdir(parents=True, exist_ok=True)

    with clip_preview_locks_guard:
        lock = clip_preview_locks.setdefault(f"{project_id}:{cache_key}", threading.Lock())
    with lock:
        if payload.force:
            out.unlink(missing_ok=True)
        if not out.exists() or out.stat().st_size < 4096:
            temp = out.with_suffix(".tmp.mp4")
            temp.unlink(missing_ok=True)
            ffmpeg = which("ffmpeg") or "ffmpeg"
            # Preview quality is intentionally lower than final export quality.  The
            # old `veryfast` preset plus a fixed 180-second timeout could fail on
            # 4K/60 FPS Twitch VODs or modest CPUs even though FFmpeg was working.
            # VFR also prevents pathological frame duplication when a downloaded
            # Twitch MP4 contains timestamp discontinuities.
            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-fflags",
                "+genpts+discardcorrupt",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(source),
                "-t",
                f"{duration:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-sn",
                "-dn",
                "-vf",
                "scale='min(1280,iw)':-2",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
                "-fps_mode",
                "vfr",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                "-avoid_negative_ts",
                "make_zero",
                str(temp),
            ]
            # Allow slower machines more time while keeping a hard upper bound so
            # a corrupt source cannot occupy a worker forever.
            preview_timeout = max(180, min(900, int(math.ceil(duration * 6 + 60))))
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=preview_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                temp.unlink(missing_ok=True)
                diagnostic = exc.stderr or exc.stdout or ""
                if isinstance(diagnostic, bytes):
                    diagnostic = diagnostic.decode("utf-8", errors="replace")
                detail = (
                    f"FFmpeg не успел подготовить preview за {preview_timeout} секунд. "
                    "Исходник может быть слишком тяжёлым, повреждённым или содержать плохие таймстампы."
                )
                if diagnostic:
                    detail += " Последний вывод FFmpeg: " + str(diagnostic)[-500:]
                raise HTTPException(status_code=504, detail=detail) from exc
            except OSError as exc:
                temp.unlink(missing_ok=True)
                raise HTTPException(status_code=500, detail=f"Не удалось запустить FFmpeg: {exc}") from exc
            if result.returncode != 0 or not temp.exists() or temp.stat().st_size < 4096:
                temp.unlink(missing_ok=True)
                raise HTTPException(500, "Не удалось подготовить безопасный preview: " + (result.stderr or "FFmpeg error")[-500:])
            info = video_info(temp)
            actual_duration = float(info.get("duration") or 0)
            if actual_duration < max(0.2, duration - 1.5):
                temp.unlink(missing_ok=True)
                raise HTTPException(500, "Preview получился неполным")
            os.replace(temp, out)

    return {
        "ok": True,
        "url": f"/api/projects/{project_id}/file/{rel.as_posix()}",
        "cache_key": cache_key,
        "start": start,
        "end": end,
        "duration": duration,
        "size_bytes": out.stat().st_size,
    }


def _youtube_desktop_only() -> None:
    if WEB_ACCOUNTS_ENABLED:
        raise HTTPException(501, "YouTube publishing сейчас доступен только в локальной desktop-версии.")


def _youtube_redirect_uri(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/api/youtube/oauth/callback"
    hostname = (request.url.hostname or "127.0.0.1").lower()
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        hostname = "127.0.0.1"
    port = request.url.port or 8000
    return f"http://{hostname}:{port}/api/youtube/oauth/callback"


@app.get("/api/youtube/status")
def youtube_status(refresh: bool = Query(False)):
    _youtube_desktop_only()
    return get_youtube_status(refresh_account=refresh)


@app.post("/api/youtube/credentials")
async def youtube_credentials(file: UploadFile = File(...)):
    _youtube_desktop_only()
    filename = str(file.filename or "").lower()
    if not filename.endswith(".json"):
        raise HTTPException(400, "Выбери JSON-файл OAuth Client из Google Cloud.")
    try:
        return save_client_secrets(await file.read())
    except YouTubeIntegrationError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/youtube/connect")
def youtube_connect(request: Request):
    _youtube_desktop_only()
    try:
        return create_authorization_url(_youtube_redirect_uri(request))
    except YouTubeIntegrationError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/youtube/oauth/callback", name="youtube_oauth_callback")
def youtube_oauth_callback(request: Request, state: str = Query(""), code: str = Query(""), error: str = Query("")):
    _youtube_desktop_only()
    if error:
        message = html.escape(f"Google отменил подключение: {error}")
        return HTMLResponse(
            f"<!doctype html><meta charset='utf-8'><title>YouTube</title><h2>Подключение не выполнено</h2><p>{message}</p>", status_code=400
        )
    if not state or not code:
        return HTMLResponse("<!doctype html><meta charset='utf-8'><h2>Не хватает OAuth-параметров.</h2>", status_code=400)
    try:
        result = complete_oauth(state=state, authorization_response=str(request.url))
        title = str(result.get("channel_title") or "YouTube")
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><title>YouTube подключён</title>"
            f"<h2>Канал подключён</h2><p>{html.escape(title)}</p><p>Вернись в Highlight Studio и нажми «Обновить статус».</p>"
        )
    except YouTubeIntegrationError as exc:
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><title>Ошибка YouTube</title>"
            f"<h2>Подключение не выполнено</h2><p>{html.escape(str(exc))}</p>",
            status_code=400,
        )


@app.post("/api/youtube/disconnect")
def youtube_disconnect(remove_credentials: bool = Query(False)):
    _youtube_desktop_only()
    return clear_youtube_connection(remove_client_secrets=remove_credentials)


@app.get("/api/projects/{project_id}/youtube-upload-report")
def youtube_upload_report(project_id: str):
    d = project_dir(project_id)
    return read_json(d / "youtube_upload_report.json", {"ok": False, "uploaded": [], "failed": []})


@app.post("/api/projects/{project_id}/youtube-upload")
def start_youtube_upload(project_id: str, payload: YouTubeUploadRequest):
    _youtube_desktop_only()
    d = project_dir(project_id)
    allowed = {str(item.get("path") or "") for item in list_output_files(d) if str(item.get("kind") or "") in {"video", "short"}}
    requested = [item.model_dump() for item in payload.items]
    invalid = [item["file_path"] for item in requested if item["file_path"] not in allowed]
    if invalid:
        raise HTTPException(400, f"Разрешено загружать только готовые output-файлы проекта: {invalid[0]}")
    settings_now = load_project(d).get("settings", default_settings())
    for item in requested:
        publishable, reason = output_is_publishable(d, settings_now, item["file_path"])
        if not publishable:
            raise HTTPException(status_code=409, detail={"message": reason, "file_path": item["file_path"], "code": "STALE_OUTPUT"})

    def task(project_dir_: Path, settings: dict[str, Any], logger: JobLogger):
        logger.heartbeat("youtube_upload_prepare", 2, "YouTube: подготовка загрузки")
        result = upload_project_videos(project_dir_, requested, logger)
        write_json(project_dir_ / "youtube_upload_report.json", result)

    return start_background_job(project_id, "Загрузка на YouTube запущена", task, kind="youtube_upload")


@app.get("/api/projects/{project_id}/source-video")
def get_source_video(project_id: str):
    d = project_dir(project_id)
    path = source_video_path(d)
    if not path.exists() or path.is_dir():
        raise HTTPException(404, "Source video not found. Возможно, исходный файл был перемещён после Fast Import.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(400, "Unsupported source video extension")
    return FileResponse(path)


@app.get("/api/projects/{project_id}/native-path/{file_path:path}")
def get_native_file_path(project_id: str, file_path: str):
    """Resolve a project file for the trusted desktop shell.

    The browser fallback continues to stream files through /file/.  Returning an
    absolute path is enabled only for desktop mode and still uses the same
    project-boundary validation as the download endpoint.
    """
    if os.environ.get("HIGHLIGHT_STUDIO_DESKTOP", "0") != "1":
        raise HTTPException(404, "Desktop integration is not active")
    d = project_dir(project_id).resolve()
    target = (d / file_path).resolve()
    try:
        target.relative_to(d)
    except ValueError as exc:
        raise HTTPException(404, "File not found") from exc
    if not target.exists() or target.is_dir():
        raise HTTPException(404, "File not found")
    return {"ok": True, "path": str(target), "name": target.name, "size_bytes": target.stat().st_size}


@app.get("/api/projects/{project_id}/file/{file_path:path}")
def get_file(project_id: str, file_path: str, request: Request):
    d = project_dir(project_id).resolve()
    target = (d / file_path).resolve()
    try:
        target.relative_to(d)
    except ValueError as exc:
        raise HTTPException(404, "File not found") from exc
    if not target.exists() or target.is_dir():
        raise HTTPException(404, "File not found")
    if WEB_ACCOUNTS_ENABLED:
        rel = target.relative_to(d).as_posix()
        public_roots = ("outputs/", "preview/", "shorts/", "hls/", "exports/")
        public_names = {"highlight_final.mp4", "highlight_subtitles.srt"}
        if not (rel.startswith(public_roots) or rel in public_names):
            actor = getattr(request.state, "user", None)
            if not actor:
                raise HTTPException(401, "Требуется вход")
            with session_scope() as db:
                role = project_role(db, actor, project_id)
            if role not in {"editor", "owner"} and getattr(actor, "global_role", "") != "admin":
                raise HTTPException(403, "Внутренние файлы проекта доступны только редактору или владельцу")
    return FileResponse(target)


# -----------------------------
# Production frontend fallback
# -----------------------------
# The packaged Windows build serves the prebuilt React UI from FastAPI on
# http://127.0.0.1:8000. This avoids Node/Vite startup problems on user PCs.
@app.get("/")
def frontend_index():
    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Frontend dist not found. Запусти setup_windows.bat или npm run build в frontend.",
                "expected": str(index),
            },
        )
    return FileResponse(
        index,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Highlight-Studio-Version": APP_SEMVER,
        },
    )


@app.get("/{full_path:path}")
def frontend_assets_or_index(full_path: str):
    # API routes are defined above; this route is only for the browser UI.
    if full_path.startswith("api/"):
        raise HTTPException(404, "API endpoint not found")
    safe = (FRONTEND_DIST / full_path).resolve()
    try:
        safe.relative_to(FRONTEND_DIST.resolve())
    except ValueError as exc:
        raise HTTPException(404, "File not found") from exc
    if safe.exists() and safe.is_file():
        cache_control = (
            "no-store, no-cache, must-revalidate, max-age=0"
            if safe.name in {"index.html", "release.json"}
            else "public, max-age=31536000, immutable"
        )
        return FileResponse(
            safe,
            headers={
                "Cache-Control": cache_control,
                "X-Highlight-Studio-Version": APP_SEMVER,
            },
        )
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(
            index,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Highlight-Studio-Version": APP_SEMVER,
            },
        )
    raise HTTPException(503, "Frontend dist not found")
