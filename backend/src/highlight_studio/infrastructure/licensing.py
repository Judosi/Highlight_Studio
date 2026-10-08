from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..core.settings import APP_ROOT, APP_VERSION, DATA_DIR
from ..core.utils import read_json, write_json

LICENSE_PATH = DATA_DIR / "license.json"
TRIAL_PATH = DATA_DIR / "trial.json"
DEVICE_ID_PATH = DATA_DIR / "device_identity.json"
DEFAULT_TRIAL_DAYS = 14
LICENSE_TOKEN_PREFIX = "HSB1"
DEFAULT_ENTITLEMENTS = ["analysis", "render", "shorts", "twitch", "metadata_ai"]
KNOWN_ENTITLEMENTS = frozenset(DEFAULT_ENTITLEMENTS)


def _now() -> float:
    return time.time()


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _paid_beta_config() -> dict[str, Any]:
    """Read the bundled paid-beta channel config for source/portable launches.

    Electron normally forwards these values through environment variables, but
    START_HERE.bat launches the FastAPI backend directly. Falling back to the
    bundled config keeps both launch paths on the same license configuration.
    """
    path = APP_ROOT / "desktop" / "electron" / "paid-beta-channel.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _configured_value(env_name: str, config_name: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    return str(_paid_beta_config().get(config_name) or "").strip()


def _public_key() -> Ed25519PublicKey | None:
    encoded = _configured_value("HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64", "licensePublicKeyB64")
    if not encoded:
        return None
    try:
        return Ed25519PublicKey.from_public_bytes(_b64url_decode(encoded))
    except Exception:
        return None


def _safe_https_url(value: str) -> str:
    candidate = str(value or "").strip().rstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return ""
    return candidate


def _legacy_device_id() -> str:
    source = "|".join(
        [
            platform.system(),
            platform.machine(),
            platform.node(),
            str(uuid.getnode()),
            "highlight-studio-paid-beta-v1",
        ]
    )
    return hashlib.sha256(source.encode("utf-8", errors="ignore")).hexdigest()


def device_id() -> str:
    """Persist the existing non-reversible ID so normal hardware drift cannot consume a new slot."""
    stored = read_json(DEVICE_ID_PATH, {}) or {}
    value = str(stored.get("device_id") or "") if isinstance(stored, dict) else ""
    if re.fullmatch(r"[0-9a-f]{64}", value):
        return value
    value = _legacy_device_id()
    write_json(
        DEVICE_ID_PATH,
        {
            "device_id": value,
            "algorithm": "legacy-hardware-hash-v1-persisted",
            "created_by_version": APP_VERSION,
        },
    )
    return value


def _trial_days() -> int:
    raw = os.environ.get("HIGHLIGHT_STUDIO_TRIAL_DAYS", "").strip()
    if not raw:
        raw = str(_paid_beta_config().get("trialDays") or DEFAULT_TRIAL_DAYS).strip()
    try:
        return max(1, min(90, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_TRIAL_DAYS


def _ensure_trial(now: float | None = None) -> dict[str, Any]:
    current = float(now if now is not None else _now())
    raw = read_json(TRIAL_PATH, {}) or {}
    if not isinstance(raw, dict) or not raw.get("started_at"):
        raw = {
            "started_at": current,
            "device_id": device_id(),
            "trial_days": _trial_days(),
            "created_by_version": APP_VERSION,
        }
        write_json(TRIAL_PATH, raw)
    return raw


def _network_failure_message(action: str, exc: Exception) -> str:
    # Exception text may contain request bodies supplied by adapters/proxies.
    # Expose only the error class; license keys and tokens must never be echoed.
    return f"{action}: сеть недоступна ({type(exc).__name__})."


def _server_url() -> str:
    return _safe_https_url(_configured_value("HIGHLIGHT_STUDIO_LICENSE_SERVER_URL", "licenseServerUrl"))


def _save_server_trial(token: str, *, now: float) -> dict[str, Any]:
    verified = verify_license_token(token, now=now)
    payload = verified.get("payload") if isinstance(verified.get("payload"), dict) else {}
    if verified.get("ok"):
        payload = verified["payload"]
    elif verified.get("reason") != "expired":
        return {"ok": False, "message": "Сервер вернул некорректный trial entitlement."}
    if payload.get("plan") != "paid_beta_trial" or str(payload.get("device_id") or "") != device_id():
        return {"ok": False, "message": "Сервер вернул trial entitlement для другого устройства."}
    write_json(
        TRIAL_PATH,
        {
            "mode": "server_signed",
            "token": token,
            "started_at": float(payload.get("trial_started_at") or payload.get("issued_at") or now),
            "expires_at": float(payload.get("expires_at") or 0),
            "device_id": device_id(),
            "last_seen_at": now,
            "updated_at": now,
        },
    )
    return {"ok": True, "payload": payload, "verification": verified}


def _request_server_trial(existing: dict[str, Any], *, now: float, timeout: int = 20) -> dict[str, Any]:
    server = _server_url()
    if not server:
        return {"ok": False, "message": "Сервер trial не настроен."}
    local_started_at = None
    if isinstance(existing, dict) and not existing.get("token"):
        try:
            candidate = float(existing.get("started_at") or 0)
            local_started_at = candidate if candidate > 0 else None
        except (TypeError, ValueError, OverflowError):
            local_started_at = None
    try:
        response = requests.post(
            f"{server}/trial/start",
            json={
                "device_id": device_id(),
                "app_version": APP_VERSION,
                "local_started_at": local_started_at,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        token = str((response.json() or {}).get("token") or "").strip()
    except Exception as exc:
        return {"ok": False, "message": _network_failure_message("Не удалось запустить пробный период", exc)}
    return _save_server_trial(token, now=now)


def start_trial(*, now: float | None = None, timeout: int = 20) -> dict[str, Any]:
    """Start a signed server trial when configured, otherwise preserve legacy offline behavior."""
    current = float(now if now is not None else _now())
    existing = read_json(TRIAL_PATH, {}) or {}
    if _server_url():
        return _request_server_trial(existing if isinstance(existing, dict) else {}, now=current, timeout=timeout)
    return _ensure_trial(current)


def _trial_can_start() -> bool:
    try:
        from .runtime_state import onboarding_status

        return bool(onboarding_status().get("completed"))
    except Exception:
        return False


def verify_license_token(token: str, *, now: float | None = None) -> dict[str, Any]:
    parts = str(token or "").strip().split(".")
    if len(parts) != 3 or parts[0] != LICENSE_TOKEN_PREFIX:
        return {"ok": False, "reason": "invalid_format"}
    key = _public_key()
    if key is None:
        return {"ok": False, "reason": "public_key_not_configured"}
    try:
        payload_bytes = _b64url_decode(parts[1])
        signature = _b64url_decode(parts[2])
        key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (InvalidSignature, ValueError, UnicodeError, json.JSONDecodeError):
        return {"ok": False, "reason": "invalid_signature"}
    except Exception:
        return {"ok": False, "reason": "invalid_token"}

    if not isinstance(payload, dict):
        return {"ok": False, "reason": "invalid_payload"}
    try:
        current = float(now if now is not None else _now())
        expires_at = float(payload.get("expires_at") or 0)
        not_before = float(payload.get("not_before") or 0)
    except (TypeError, ValueError, OverflowError):
        return {"ok": False, "reason": "invalid_payload"}
    if expires_at < 0 or not_before < 0:
        return {"ok": False, "reason": "invalid_payload"}
    token_device = str(payload.get("device_id") or "")[:128]
    if not_before and current < not_before:
        return {"ok": False, "reason": "not_yet_valid", "payload": payload}
    if expires_at and current >= expires_at:
        return {"ok": False, "reason": "expired", "payload": payload}
    if token_device not in {"", "*", device_id()}:
        return {"ok": False, "reason": "wrong_device", "payload": payload}
    entitlements = payload.get("entitlements") or DEFAULT_ENTITLEMENTS
    if not isinstance(entitlements, list):
        return {"ok": False, "reason": "invalid_payload"}
    payload["entitlements"] = sorted({str(item).strip() for item in entitlements if str(item).strip() in KNOWN_ENTITLEMENTS})
    payload["license_id"] = str(payload.get("license_id") or "")[:160]
    payload["plan"] = str(payload.get("plan") or "paid_beta")[:80]
    return {"ok": True, "payload": payload}


def _saved_token() -> str:
    raw = read_json(LICENSE_PATH, {}) or {}
    return str(raw.get("token") or "").strip() if isinstance(raw, dict) else ""


def license_status(*, now: float | None = None) -> dict[str, Any]:
    current = float(now if now is not None else _now())
    token = _saved_token()
    if token:
        verified = verify_license_token(token, now=current)
        if verified.get("ok"):
            payload = verified["payload"]
            expires_at = float(payload.get("expires_at") or 0)
            return {
                "ok": True,
                "state": "active",
                "can_use": True,
                "plan": str(payload.get("plan") or "paid_beta"),
                "license_id": str(payload.get("license_id") or ""),
                "expires_at": expires_at or None,
                "days_remaining": max(0, int((expires_at - current + 86399) // 86400)) if expires_at else None,
                "entitlements": payload.get("entitlements", DEFAULT_ENTITLEMENTS),
                "device_id": device_id()[:12],
                "device_code": device_id(),
                "message": "Лицензия активна.",
            }
        invalid_reason = verified.get("reason")
    else:
        invalid_reason = None

    trial = read_json(TRIAL_PATH, {}) or {}
    if _server_url():
        trial = trial if isinstance(trial, dict) else {}
        trial_token = str(trial.get("token") or "").strip()
        verified_trial: dict[str, Any] = {}
        if trial_token:
            verified_trial = verify_license_token(trial_token, now=current)
        trial_payload = verified_trial.get("payload") if isinstance(verified_trial.get("payload"), dict) else {}
        if verified_trial.get("ok"):
            trial_payload = verified_trial["payload"]
        if trial_payload.get("plan") == "paid_beta_trial" and str(trial_payload.get("device_id") or "") == device_id():
            expires_at = float(trial_payload.get("expires_at") or 0)
            try:
                last_seen_at = float(trial.get("last_seen_at") or trial_payload.get("issued_at") or current)
            except (TypeError, ValueError, OverflowError):
                last_seen_at = current
            clock_ok = current + 3600 >= last_seen_at
            active = bool(verified_trial.get("ok") and current < expires_at and clock_ok)
            if active and current > last_seen_at + 3600:
                trial["last_seen_at"] = current
                write_json(TRIAL_PATH, trial)
            return {
                "ok": True,
                "state": "trial" if active else "expired",
                "can_use": active,
                "plan": "paid_beta_trial" if active else "none",
                "license_id": "",
                "expires_at": expires_at,
                "days_remaining": max(0, int((expires_at - current + 86399) // 86400)) if active else 0,
                "entitlements": list(DEFAULT_ENTITLEMENTS) if active else [],
                "device_id": device_id()[:12],
                "device_code": device_id(),
                "message": "Пробный период активен." if active else "Пробный период завершён. Активируй лицензию.",
                "invalid_license_reason": invalid_reason or ("clock_rollback" if not clock_ok else None),
                "trial_protection": "server_signed",
            }
        if not _trial_can_start():
            return {
                "ok": True,
                "state": "not_started",
                "can_use": False,
                "plan": "none",
                "license_id": "",
                "expires_at": None,
                "days_remaining": _trial_days(),
                "entitlements": [],
                "device_id": device_id()[:12],
                "device_code": device_id(),
                "message": "Пробный период начнётся после завершения первого запуска.",
                "invalid_license_reason": invalid_reason,
                "trial_protection": "server_signed",
            }
        started = _request_server_trial(trial, now=current, timeout=5)
        if started.get("ok"):
            return license_status(now=current)
        return {
            "ok": False,
            "state": "trial_server_unavailable",
            "can_use": False,
            "plan": "none",
            "license_id": "",
            "expires_at": None,
            "days_remaining": 0,
            "entitlements": [],
            "device_id": device_id()[:12],
            "device_code": device_id(),
            "message": started.get("message") or "Для первого запуска trial нужен доступ к серверу лицензий.",
            "invalid_license_reason": invalid_reason,
            "trial_protection": "server_signed",
        }
    if not isinstance(trial, dict) or not trial.get("started_at"):
        if not _trial_can_start():
            return {
                "ok": True,
                "state": "not_started",
                "can_use": False,
                "plan": "none",
                "license_id": "",
                "expires_at": None,
                "days_remaining": _trial_days(),
                "entitlements": [],
                "device_id": device_id()[:12],
                "device_code": device_id(),
                "message": "Пробный период начнётся после завершения первого запуска.",
                "invalid_license_reason": invalid_reason,
            }
        trial = _ensure_trial(current)
    started_at = float(trial.get("started_at") or current)
    last_seen_at = float(trial.get("last_seen_at") or started_at)
    trial_days = int(trial.get("trial_days") or _trial_days())
    expires_at = started_at + trial_days * 86400
    clock_ok = current + 3600 >= last_seen_at
    trial_valid = current < expires_at and clock_ok and str(trial.get("device_id") or "") == device_id()
    if trial_valid and current > last_seen_at + 3600:
        trial["last_seen_at"] = current
        write_json(TRIAL_PATH, trial)
    if trial_valid:
        return {
            "ok": True,
            "state": "trial",
            "can_use": True,
            "plan": "paid_beta_trial",
            "license_id": "",
            "expires_at": expires_at,
            "days_remaining": max(0, int((expires_at - current + 86399) // 86400)),
            "entitlements": list(DEFAULT_ENTITLEMENTS),
            "device_id": device_id()[:12],
            "device_code": device_id(),
            "message": "Пробный период активен.",
            "invalid_license_reason": invalid_reason,
        }
    return {
        "ok": True,
        "state": "expired",
        "can_use": False,
        "plan": "none",
        "license_id": "",
        "expires_at": expires_at,
        "days_remaining": 0,
        "entitlements": [],
        "device_id": device_id()[:12],
        "device_code": device_id(),
        "message": "Пробный период завершён. Активируй лицензию, чтобы запускать новый анализ и рендер.",
        "invalid_license_reason": invalid_reason or ("clock_rollback" if not clock_ok else None),
    }


def activate_license(key_or_token: str, *, timeout: int = 20) -> dict[str, Any]:
    value = str(key_or_token or "").strip()
    if not value:
        return {"ok": False, "message": "Введи лицензионный ключ."}
    token = value
    if not value.startswith(f"{LICENSE_TOKEN_PREFIX}."):
        server = _safe_https_url(_configured_value("HIGHLIGHT_STUDIO_LICENSE_SERVER_URL", "licenseServerUrl"))
        if not server:
            return {"ok": False, "message": "Сервер активации не настроен в этой сборке."}
        try:
            response = requests.post(
                f"{server}/activate",
                json={"license_key": value, "device_id": device_id(), "app_version": APP_VERSION},
                timeout=timeout,
            )
            response.raise_for_status()
            token = str((response.json() or {}).get("token") or "").strip()
        except Exception as exc:
            return {"ok": False, "message": _network_failure_message("Не удалось связаться с сервером активации", exc)}
    verified = verify_license_token(token)
    if not verified.get("ok"):
        return {"ok": False, "message": f"Ключ не принят: {verified.get('reason', 'invalid_token')}"}
    write_json(LICENSE_PATH, {"token": token, "activated_at": _now(), "app_version": APP_VERSION})
    status = license_status()
    return {**status, "activated": True}


def refresh_license(*, timeout: int = 20) -> dict[str, Any]:
    token = _saved_token()
    if not token:
        return {"ok": False, "message": "Активная лицензия не найдена."}
    server = _server_url()
    if not server:
        return {**license_status(), "refreshed": False, "message": "Локальная лицензия проверена. Сервер обновления лицензии не настроен."}
    try:
        response = requests.post(
            f"{server}/refresh",
            json={"token": token, "device_id": device_id(), "app_version": APP_VERSION},
            timeout=timeout,
        )
        response.raise_for_status()
        next_token = str((response.json() or {}).get("token") or token).strip()
    except Exception as exc:
        current = license_status()
        return {**current, "refreshed": False, "message": _network_failure_message("Не удалось обновить лицензию", exc)}
    verified = verify_license_token(next_token)
    if not verified.get("ok"):
        return {"ok": False, "message": "Сервер вернул некорректную лицензию."}
    write_json(LICENSE_PATH, {"token": next_token, "activated_at": _now(), "app_version": APP_VERSION})
    return {**license_status(), "refreshed": True}


def deactivate_license(*, timeout: int = 10) -> dict[str, Any]:
    token = _saved_token()
    server = _server_url()
    remote_released = False
    if token and server:
        verified = verify_license_token(token)
        payload = verified.get("payload") if isinstance(verified.get("payload"), dict) else {}
        token_device = str(payload.get("device_id") or "")
        if not 16 <= len(token_device) <= 128:
            return {**license_status(), "deactivated": False, "remote_released": False, "message": "Лицензия повреждена; слот не освобождён."}
        try:
            response = requests.post(
                f"{server}/deactivate",
                json={"token": token, "device_id": token_device, "app_version": APP_VERSION},
                timeout=timeout,
            )
            remote_released = response.ok
        except Exception:
            remote_released = False
        if not remote_released:
            return {
                **license_status(),
                "deactivated": False,
                "remote_released": False,
                "message": "Сервер лицензий недоступен; локальная лицензия сохранена, чтобы повторить деактивацию.",
            }
    LICENSE_PATH.unlink(missing_ok=True)
    return {**license_status(), "deactivated": True, "remote_released": remote_released}


def entitlement_allowed(entitlement: str) -> bool:
    status = license_status()
    return bool(status.get("can_use") and entitlement in set(status.get("entitlements") or []))
