from __future__ import annotations

import json
import os
import random
import hashlib
import secrets
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import requests

from ...core.settings import DATA_DIR
from ...core.utils import OperationCancelled, read_json, write_json
from ...core.artifacts import ARTIFACTS, source_file_signature

YOUTUBE_DIR = DATA_DIR / "youtube"
CLIENT_SECRETS_PATH = YOUTUBE_DIR / "client_secrets.json"
TOKEN_PATH = YOUTUBE_DIR / "token.json"
ACCOUNT_PATH = YOUTUBE_DIR / "account.json"
OAUTH_STATE_PATH = YOUTUBE_DIR / "oauth_state.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
RETRIABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
UPLOAD_INIT_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
CHUNK_SIZE = 2 * 1024 * 1024  # Must be a multiple of 256 KiB for resumable uploads.


class YouTubeIntegrationError(RuntimeError):
    pass


def _ensure_dir() -> None:
    YOUTUBE_DIR.mkdir(parents=True, exist_ok=True)


def _secure_write_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_dir()
    write_json(path, payload)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def dependencies_available() -> bool:
    return True


def _client_config() -> tuple[str, dict[str, Any]]:
    payload = read_json(CLIENT_SECRETS_PATH, {}) or {}
    root_key = "installed" if isinstance(payload.get("installed"), dict) else "web" if isinstance(payload.get("web"), dict) else ""
    if not root_key:
        raise YouTubeIntegrationError("OAuth JSON не настроен. Загрузите файл из Google Cloud.")
    return root_key, payload[root_key]


def save_client_secrets(raw: bytes) -> dict[str, Any]:
    if len(raw) > 256 * 1024:
        raise YouTubeIntegrationError("Файл OAuth слишком большой.")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YouTubeIntegrationError("Это невалидный JSON-файл OAuth от Google Cloud.") from exc

    root_key = "installed" if isinstance(payload.get("installed"), dict) else "web" if isinstance(payload.get("web"), dict) else ""
    if not root_key:
        raise YouTubeIntegrationError("В JSON нет секции installed или web.")
    client = payload[root_key]
    required = ["client_id", "client_secret", "auth_uri", "token_uri"]
    missing = [key for key in required if not str(client.get(key) or "").strip()]
    if missing:
        raise YouTubeIntegrationError(f"В OAuth JSON отсутствуют поля: {', '.join(missing)}")

    _secure_write_json(CLIENT_SECRETS_PATH, {root_key: client})
    TOKEN_PATH.unlink(missing_ok=True)
    ACCOUNT_PATH.unlink(missing_ok=True)
    OAUTH_STATE_PATH.unlink(missing_ok=True)
    return {
        "ok": True,
        "credentials_configured": True,
        "client_type": root_key,
        "message": "OAuth-клиент YouTube сохранён. Теперь подключите канал.",
    }


def create_authorization_url(redirect_uri: str) -> dict[str, Any]:
    _, client = _client_config()
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": str(client["client_id"]),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    auth_url = f"{str(client['auth_uri']).rstrip('?')}?{urlencode(params)}"
    _secure_write_json(OAUTH_STATE_PATH, {"state": state, "redirect_uri": redirect_uri, "created_at": time.time()})
    return {"ok": True, "authorization_url": auth_url, "expires_in": 600}


def _google_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:1000] or f"HTTP {response.status_code}"
    error = payload.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        message = str(error.get("message") or "")
        errors = error.get("errors") or []
        reason = str(errors[0].get("reason") or "") if errors and isinstance(errors[0], dict) else ""
        description = str(error.get("error_description") or "")
        return message or description or reason or json.dumps(error, ensure_ascii=False)[:1000]
    return str(payload.get("error_description") or payload)[:1000]


def complete_oauth(*, state: str, authorization_response: str) -> dict[str, Any]:
    saved = read_json(OAUTH_STATE_PATH, {}) or {}
    if not saved or state != saved.get("state"):
        raise YouTubeIntegrationError("OAuth state не совпал. Начните подключение YouTube заново.")
    if time.time() - float(saved.get("created_at") or 0) > 600:
        OAUTH_STATE_PATH.unlink(missing_ok=True)
        raise YouTubeIntegrationError("Время подключения YouTube истекло. Начните заново.")

    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(authorization_response).query)
    code = str((query.get("code") or [""])[0])
    if not code:
        raise YouTubeIntegrationError("Google не вернул authorization code.")
    _, client = _client_config()
    try:
        response = requests.post(
            str(client["token_uri"]),
            data={
                "code": code,
                "client_id": str(client["client_id"]),
                "client_secret": str(client["client_secret"]),
                "redirect_uri": str(saved.get("redirect_uri") or ""),
                "grant_type": "authorization_code",
            },
            timeout=(15, 60),
        )
    except requests.RequestException as exc:
        raise YouTubeIntegrationError(f"Не удалось связаться с Google OAuth: {exc}") from exc
    if not response.ok:
        raise YouTubeIntegrationError(f"Google не выдал токен: {_google_error(response)}")
    token = response.json()
    if not token.get("access_token"):
        raise YouTubeIntegrationError("В ответе Google отсутствует access_token.")
    token["expires_at"] = time.time() + max(60, int(token.get("expires_in") or 3600) - 30)
    token["scopes"] = SCOPES
    _secure_write_json(TOKEN_PATH, token)
    OAUTH_STATE_PATH.unlink(missing_ok=True)
    account = _fetch_channel(_access_token(refresh=False))
    return {"ok": True, "connected": True, **account}


def _refresh_access_token(token: dict[str, Any]) -> dict[str, Any]:
    refresh_token = str(token.get("refresh_token") or "")
    if not refresh_token:
        raise YouTubeIntegrationError("Refresh token отсутствует. Подключите канал заново.")
    _, client = _client_config()
    try:
        response = requests.post(
            str(client["token_uri"]),
            data={
                "client_id": str(client["client_id"]),
                "client_secret": str(client["client_secret"]),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=(15, 60),
        )
    except requests.RequestException as exc:
        raise YouTubeIntegrationError(f"Не удалось обновить доступ к YouTube: {exc}") from exc
    if not response.ok:
        raise YouTubeIntegrationError(f"Google отклонил обновление доступа: {_google_error(response)}")
    refreshed = response.json()
    merged = {**token, **refreshed, "refresh_token": refreshed.get("refresh_token") or refresh_token}
    merged["expires_at"] = time.time() + max(60, int(refreshed.get("expires_in") or 3600) - 30)
    merged["scopes"] = SCOPES
    _secure_write_json(TOKEN_PATH, merged)
    return merged


def _access_token(*, refresh: bool = True) -> str:
    token = read_json(TOKEN_PATH, {}) or {}
    if not token:
        raise YouTubeIntegrationError("YouTube-канал ещё не подключён.")
    if refresh and time.time() >= float(token.get("expires_at") or 0):
        token = _refresh_access_token(token)
    value = str(token.get("access_token") or "")
    if not value:
        raise YouTubeIntegrationError("Токен YouTube повреждён. Подключите канал заново.")
    return value


def _authorized_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def _fetch_channel(access_token: str) -> dict[str, Any]:
    try:
        response = requests.get(
            CHANNELS_URL,
            params={"part": "snippet", "mine": "true"},
            headers=_authorized_headers(access_token),
            timeout=(15, 60),
        )
    except requests.RequestException as exc:
        raise YouTubeIntegrationError(f"Не удалось прочитать YouTube-канал: {exc}") from exc
    if not response.ok:
        raise YouTubeIntegrationError(f"YouTube не вернул канал: {_google_error(response)}")
    items = response.json().get("items") or []
    if not items:
        raise YouTubeIntegrationError("В выбранном Google-аккаунте не найден YouTube-канал.")
    item = items[0]
    snippet = item.get("snippet") or {}
    thumbnails = snippet.get("thumbnails") or {}
    thumb = (thumbnails.get("default") or thumbnails.get("medium") or {}).get("url", "")
    account = {
        "channel_id": str(item.get("id") or ""),
        "channel_title": str(snippet.get("title") or "YouTube channel"),
        "thumbnail_url": str(thumb or ""),
        "connected_at": time.time(),
    }
    _secure_write_json(ACCOUNT_PATH, account)
    return account


def get_youtube_status(*, refresh_account: bool = False) -> dict[str, Any]:
    status: dict[str, Any] = {
        "ok": True,
        "dependencies_available": True,
        "credentials_configured": CLIENT_SECRETS_PATH.exists(),
        "connected": False,
        "channel_id": "",
        "channel_title": "",
        "thumbnail_url": "",
    }
    if not TOKEN_PATH.exists():
        return status
    try:
        access_token = _access_token(refresh=True)
        account = read_json(ACCOUNT_PATH, {}) or {}
        if refresh_account or not account:
            account = _fetch_channel(access_token)
        status.update(account)
        status["connected"] = True
    except YouTubeIntegrationError as exc:
        status["error"] = str(exc)
    return status


def clear_youtube_connection(*, remove_client_secrets: bool = False) -> dict[str, Any]:
    TOKEN_PATH.unlink(missing_ok=True)
    ACCOUNT_PATH.unlink(missing_ok=True)
    OAUTH_STATE_PATH.unlink(missing_ok=True)
    if remove_client_secrets:
        CLIENT_SECRETS_PATH.unlink(missing_ok=True)
    return {"ok": True, "connected": False, "credentials_configured": CLIENT_SECRETS_PATH.exists()}


def _resolve_output(project_dir: Path, relative_path: str) -> Path:
    raw = str(relative_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ":" in raw.split("/")[0]:
        raise YouTubeIntegrationError("Некорректный путь к видео.")
    root = project_dir.resolve()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise YouTubeIntegrationError("Видео должно находиться внутри текущего проекта.") from exc
    if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_SUFFIXES:
        raise YouTubeIntegrationError(f"Видео не найдено: {raw}")
    if candidate.stat().st_size < 1024:
        raise YouTubeIntegrationError(f"Видео пустое или повреждено: {raw}")
    return candidate


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = str(raw or "").strip().lstrip("#")[:100]
        key = tag.casefold()
        if tag and key not in seen:
            seen.add(key)
            cleaned.append(tag)
    return cleaned[:30]


def _metadata_body(video_path: Path, item: dict[str, Any]) -> tuple[dict[str, Any], str]:
    title = str(item.get("title") or video_path.stem).strip()[:100] or video_path.stem[:100]
    description = str(item.get("description") or "")[:5000]
    privacy = str(item.get("privacy_status") or "private").lower()
    if privacy not in {"private", "unlisted", "public"}:
        privacy = "private"
    tags = _clean_tags(list(item.get("tags") or []))
    status_body: dict[str, Any] = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": bool(item.get("made_for_kids", False)),
        "containsSyntheticMedia": bool(item.get("contains_synthetic_media", False)),
    }
    publish_at = str(item.get("publish_at") or "").strip()
    if publish_at:
        status_body["privacyStatus"] = "private"
        status_body["publishAt"] = publish_at
    body: dict[str, Any] = {
        "snippet": {"title": title, "description": description, "categoryId": str(item.get("category_id") or "22")},
        "status": status_body,
    }
    if tags:
        body["snippet"]["tags"] = tags
    return body, title


def _init_resumable_upload(access_token: str, video_path: Path, item: dict[str, Any]) -> tuple[str, str, str]:
    body, title = _metadata_body(video_path, item)
    params = {
        "uploadType": "resumable",
        "part": "snippet,status",
        "notifySubscribers": "true" if bool(item.get("notify_subscribers", False)) else "false",
    }
    headers = {
        **_authorized_headers(access_token),
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/*",
        "X-Upload-Content-Length": str(video_path.stat().st_size),
    }
    try:
        response = requests.post(UPLOAD_INIT_URL, params=params, headers=headers, json=body, timeout=(20, 90))
    except requests.RequestException as exc:
        raise YouTubeIntegrationError(f"Не удалось начать загрузку: {exc}") from exc
    if not response.ok:
        raise YouTubeIntegrationError(f"YouTube отклонил публикацию: {_google_error(response)}")
    location = response.headers.get("Location")
    if not location:
        raise YouTubeIntegrationError("YouTube не вернул адрес resumable upload.")
    return location, title, str(body["status"]["privacyStatus"])


def _query_upload_offset(session_url: str, access_token: str, total_size: int) -> tuple[int, dict[str, Any] | None, bool]:
    """Return (offset, completed_payload, expired).

    Transient/network failures are errors and MUST NOT silently cause creation
    of a second resumable session. 404/410 explicitly mean the prior session is
    no longer usable and are the only condition that allows a new one.
    """
    try:
        response = requests.put(
            session_url,
            headers={**_authorized_headers(access_token), "Content-Length": "0", "Content-Range": f"bytes */{total_size}"},
            data=b"",
            timeout=(15, 60),
        )
    except requests.RequestException as exc:
        raise YouTubeIntegrationError(f"Не удалось проверить существующую YouTube upload session: {exc}") from exc
    if response.status_code in {200, 201}:
        return total_size, response.json(), False
    if response.status_code == 308:
        range_header = response.headers.get("Range", "")
        if "-" in range_header:
            try:
                return int(range_header.rsplit("-", 1)[1]) + 1, None, False
            except ValueError:
                pass
        return 0, None, False
    if response.status_code in {404, 410}:
        return 0, None, True
    raise YouTubeIntegrationError(f"YouTube не восстановил загрузку: {_google_error(response)}")


def _upload_state_path(project_dir: Path) -> Path:
    return project_dir / ARTIFACTS["youtube_upload_state"]


def _upload_intent_key(video_path: Path, item: dict[str, Any]) -> str:
    body, _ = _metadata_body(video_path, item)
    payload = {
        "file": source_file_signature(video_path),
        "metadata": body,
        "notify_subscribers": bool(item.get("notify_subscribers", False)),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _read_upload_state(project_dir: Path | None) -> dict[str, Any]:
    if project_dir is None:
        return {"schema_version": 1, "intents": {}}
    data = read_json(_upload_state_path(project_dir), {}) or {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema_version", 1)
    data.setdefault("intents", {})
    return data


def _save_upload_intent(project_dir: Path | None, key: str, patch: dict[str, Any]) -> dict[str, Any]:
    if project_dir is None:
        return dict(patch)
    state = _read_upload_state(project_dir)
    intents = state.setdefault("intents", {})
    previous = intents.get(key) if isinstance(intents.get(key), dict) else {}
    record = {**previous, **patch, "updated_at": time.time()}
    intents[key] = record
    write_json(_upload_state_path(project_dir), state)
    return record


def _raise_if_upload_cancelled(project_dir: Path | None) -> None:
    if project_dir is not None and (project_dir / "cancel.flag").exists():
        raise OperationCancelled("YouTube upload cancelled by user")


def _upload_result(video_path: Path, item: dict[str, Any], title: str, privacy: str, video_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "file_path": str(item.get("file_path") or ""),
        "file_name": video_path.name,
        "title": title,
        "video_id": video_id,
        "youtube_url": f"https://youtu.be/{video_id}",
        "privacy_status": privacy,
    }


def _upload_one(
    access_token: str,
    video_path: Path,
    item: dict[str, Any],
    *,
    project_dir: Path | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    total_size = video_path.stat().st_size
    _, title = _metadata_body(video_path, item)
    privacy = str((_metadata_body(video_path, item)[0].get("status") or {}).get("privacyStatus") or "private")
    intent_key = _upload_intent_key(video_path, item)
    state = _read_upload_state(project_dir)
    prior = (state.get("intents") or {}).get(intent_key) if isinstance(state.get("intents"), dict) else None
    prior = prior if isinstance(prior, dict) else {}

    if prior.get("status") == "completed" and prior.get("video_id"):
        return _upload_result(video_path, item, title, privacy, str(prior["video_id"]))

    _raise_if_upload_cancelled(project_dir)
    session_url = str(prior.get("session_url") or "")
    offset = int(prior.get("offset") or 0)
    response_payload: dict[str, Any] | None = None

    if session_url:
        offset, completed, expired = _query_upload_offset(session_url, access_token, total_size)
        if completed:
            video_id = str(completed.get("id") or "")
            if not video_id:
                raise YouTubeIntegrationError("YouTube подтвердил завершение старой session без video ID.")
            _save_upload_intent(project_dir, intent_key, {"status": "completed", "session_url": session_url, "offset": total_size, "video_id": video_id, "file_path": str(video_path)})
            return _upload_result(video_path, item, title, privacy, video_id)
        if expired:
            # If the final byte was already sent before a crash, a 404/410 no
            # longer proves that YouTube did *not* create the video. Starting a
            # fresh session here could publish a duplicate. Keep the intent in
            # an explicit ambiguous state and require reconciliation instead.
            prior_status = str(prior.get("status") or "").lower()
            prior_offset = int(prior.get("offset") or 0)
            if prior_status in {"finalizing", "ambiguous"} and prior_offset >= total_size:
                _save_upload_intent(project_dir, intent_key, {
                    "status": "ambiguous",
                    "session_url": session_url,
                    "offset": total_size,
                    "message": "Final chunk may already have been accepted; automatic retry is blocked to prevent duplicate publication.",
                })
                raise YouTubeIntegrationError(
                    "YouTube upload находится в неопределённом состоянии после сбоя: последний chunk мог быть принят. "
                    "Автоматический повтор заблокирован, чтобы не создать дубликат. Проверь канал и повтори публикацию только после ручной сверки."
                )
            _save_upload_intent(project_dir, intent_key, {"status": "expired", "session_url": session_url, "offset": 0})
            session_url = ""
            offset = 0
        else:
            _save_upload_intent(project_dir, intent_key, {"status": "uploading", "session_url": session_url, "offset": offset, "file_path": str(video_path)})

    if not session_url:
        _raise_if_upload_cancelled(project_dir)
        session_url, title, privacy = _init_resumable_upload(access_token, video_path, item)
        _save_upload_intent(project_dir, intent_key, {"status": "uploading", "session_url": session_url, "offset": 0, "file_path": str(video_path), "file_signature": source_file_signature(video_path), "title": title})

    with video_path.open("rb") as source:
        while offset < total_size:
            _raise_if_upload_cancelled(project_dir)
            source.seek(offset)
            chunk = source.read(min(CHUNK_SIZE, total_size - offset))
            if not chunk:
                break
            end = offset + len(chunk) - 1
            retry = 0
            while True:
                _raise_if_upload_cancelled(project_dir)
                last_error = ""
                if end + 1 == total_size:
                    # Persist uncertainty before the network side effect: the
                    # server may commit the video even if we never get a reply.
                    _save_upload_intent(project_dir, intent_key, {
                        "status": "finalizing", "session_url": session_url,
                        "offset": total_size, "confirmed_offset": offset,
                    })
                try:
                    response = requests.put(
                        session_url,
                        headers={
                            **_authorized_headers(access_token),
                            "Content-Type": "video/*",
                            "Content-Length": str(len(chunk)),
                            "Content-Range": f"bytes {offset}-{end}/{total_size}",
                        },
                        data=chunk,
                        timeout=(20, 30),
                    )
                except requests.RequestException as exc:
                    response = None
                    last_error = str(exc)
                _raise_if_upload_cancelled(project_dir)
                if response is not None and response.status_code in {200, 201}:
                    response_payload = response.json()
                    offset = total_size
                    _save_upload_intent(project_dir, intent_key, {"status": "finalizing", "session_url": session_url, "offset": offset})
                    break
                if response is not None and response.status_code == 308:
                    range_header = response.headers.get("Range", "")
                    if "-" in range_header:
                        try:
                            offset = int(range_header.rsplit("-", 1)[1]) + 1
                        except ValueError:
                            offset = end + 1
                    else:
                        offset = end + 1
                    _save_upload_intent(project_dir, intent_key, {"status": "uploading", "session_url": session_url, "offset": offset})
                    break
                status_code = response.status_code if response is not None else 0
                if response is not None and status_code not in RETRIABLE_STATUS_CODES:
                    raise YouTubeIntegrationError(f"Ошибка загрузки: {_google_error(response)}")
                if retry >= 5:
                    detail = _google_error(response) if response is not None else last_error
                    raise YouTubeIntegrationError(f"Загрузка прервалась после повторных попыток: {detail}")
                retry += 1
                # cancellation-aware backoff
                until = time.time() + min(20.0, (2**retry) + random.random())
                while time.time() < until:
                    _raise_if_upload_cancelled(project_dir)
                    time.sleep(min(0.25, max(0, until - time.time())))
                offset, completed, expired = _query_upload_offset(session_url, access_token, total_size)
                if expired:
                    # Bytes may already have reached YouTube; do not create a new
                    # session implicitly when status is ambiguous mid-upload.
                    raise YouTubeIntegrationError("YouTube resumable session expired during upload. Повторите загрузку вручную после проверки канала.")
                if completed:
                    response_payload = completed
                    offset = total_size
                    break
                _save_upload_intent(project_dir, intent_key, {"status": "uploading", "session_url": session_url, "offset": offset})
                if offset > end:
                    break
                source.seek(offset)
                chunk = source.read(min(CHUNK_SIZE, total_size - offset))
                end = offset + len(chunk) - 1

            if progress_callback:
                progress_callback(offset / max(1, total_size), f"Загружается: {video_path.name}")

    video_id = str((response_payload or {}).get("id") or "")
    if not video_id:
        # Critical exactly-once recovery: query the SAME durable session after a
        # lost final response before considering any new session.
        _, completed, expired = _query_upload_offset(session_url, access_token, total_size)
        if expired:
            _save_upload_intent(project_dir, intent_key, {"status": "ambiguous", "session_url": session_url, "offset": total_size})
            raise YouTubeIntegrationError("Финальный статус YouTube upload не подтверждён. Новая загрузка не создана, чтобы избежать дубликата.")
        response_payload = completed
        video_id = str((response_payload or {}).get("id") or "")
    if not video_id:
        _save_upload_intent(project_dir, intent_key, {"status": "ambiguous", "session_url": session_url, "offset": offset})
        raise YouTubeIntegrationError("YouTube завершил загрузку без подтверждённого video ID. Retry заблокирован до проверки существующей session.")
    _save_upload_intent(project_dir, intent_key, {"status": "completed", "session_url": session_url, "offset": total_size, "video_id": video_id, "file_path": str(video_path)})
    if progress_callback:
        progress_callback(1.0, f"Загружено: {video_path.name}")
    return _upload_result(video_path, item, title, privacy, video_id)


def upload_project_videos(project_dir: Path, items: list[dict[str, Any]], logger: Any | None = None) -> dict[str, Any]:
    if not items:
        raise YouTubeIntegrationError("Не выбрано ни одного видео для загрузки.")
    if len(items) > 50:
        raise YouTubeIntegrationError("За один запуск можно загрузить не более 50 видео.")
    access_token = _access_token(refresh=True)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    total = len(items)

    for index, item in enumerate(items):
        _raise_if_upload_cancelled(project_dir)
        relative_path = str(item.get("file_path") or "")
        video_path = _resolve_output(project_dir, relative_path)
        base = index / total
        span = 1.0 / total

        def on_progress(fraction: float, message: str) -> None:
            _raise_if_upload_cancelled(project_dir)
            overall = 3.0 + (base + span * max(0.0, min(1.0, fraction))) * 94.0
            if logger:
                logger.heartbeat("youtube_upload", overall, message, current_batch=index + 1, total_batches=total, stage_progress_percent=round(fraction * 100, 1))

        if logger:
            logger.log(f"YouTube: загрузка {index + 1}/{total}: {video_path.name}")
        try:
            result = _upload_one(access_token, video_path, item, project_dir=project_dir, progress_callback=on_progress)
            results.append(result)
            if logger:
                logger.log(f"YouTube: готово {result['youtube_url']}")
        except OperationCancelled:
            raise
        except Exception as exc:
            failure = {"file_path": relative_path, "error": str(exc)}
            failures.append(failure)
            if logger:
                logger.log(f"YouTube: ошибка {video_path.name}: {exc}")

    report = {"ok": bool(results) and not failures, "uploaded": results, "failed": failures, "uploaded_count": len(results), "failed_count": len(failures), "finished_at": time.time()}
    write_json(project_dir / "youtube_upload_report.json", report)
    if failures:
        if logger:
            logger.set_status("partial_failed" if results else "error", 99 if results else 0, f"YouTube: загружено {len(results)} из {total}, ошибок: {len(failures)}", stage="youtube_upload_partial_failed")
        raise YouTubeIntegrationError(f"YouTube batch завершён с ошибками: загружено {len(results)} из {total}; ошибок {len(failures)}")
    if logger:
        logger.set_status("done", 100, f"YouTube: загружено {len(results)} из {total}", stage="youtube_upload_done")
    if not results:
        raise YouTubeIntegrationError("Ни одно видео не загружено.")
    return report
