from __future__ import annotations

from datetime import timedelta
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ...core.settings import (
    COOKIE_SECURE,
    DEPLOYMENT_MODE,
    DEV_AUTH_TOKENS,
    PUBLIC_BASE_URL,
    REQUIRE_EMAIL_VERIFICATION,
    WEB_ACCOUNTS_ENABLED,
)
from ..database.engine import database_backend, session_scope
from ..database.models import AuditLog, ProjectMembership, User
from .mailer import send_account_email
from .service import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    add_or_update_member,
    change_password,
    create_auth_session,
    issue_account_token,
    enforce_public_auth_request_rate_limit,
    login_user,
    register_user,
    registration_allowed,
    reset_password_with_token,
    revoke_all_user_sessions,
    revoke_session,
    revoke_session_by_id,
    list_user_sessions,
    user_dict,
    verify_email_with_token,
    project_role,
    remove_member,
    audit,
)

router = APIRouter(prefix="/api")


class RegisterRequest(BaseModel):
    email: str
    display_name: str = Field("", max_length=120)
    password: str = Field(min_length=10, max_length=256)
    remember: bool = True


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=256)
    remember: bool = True


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=20, max_length=500)
    new_password: str = Field(min_length=10, max_length=256)


class EmailVerificationRequest(BaseModel):
    token: str = Field(min_length=20, max_length=500)


class PasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=256)


class MemberRequest(BaseModel):
    email: str
    role: str = Field(pattern="^(owner|editor|viewer)$")


class UserRoleRequest(BaseModel):
    global_role: str = Field(pattern="^(user|admin)$")
    is_active: bool


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _set_auth_cookies(response: Response, token: str, csrf: str, max_age: int) -> None:
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=max_age, path="/")
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=COOKIE_SECURE, samesite="lax", max_age=max_age, path="/")


@router.get("/auth/status")
def auth_status(request: Request):
    if not WEB_ACCOUNTS_ENABLED:
        return {
            "accounts_enabled": False,
            "deployment_mode": DEPLOYMENT_MODE,
            "authenticated": True,
            "user": {"id": "local", "email": "local@device", "display_name": "Локальный пользователь", "global_role": "admin"},
        }
    user = getattr(request.state, "user", None)
    with session_scope() as db:
        allowed = registration_allowed(db)
    return {
        "accounts_enabled": True,
        "deployment_mode": DEPLOYMENT_MODE,
        "authenticated": bool(user),
        "registration_allowed": allowed,
        "database_backend": database_backend(),
        "user": user_dict(user) if user else None,
    }


@router.post("/auth/register")
def register(payload: RegisterRequest, request: Request, response: Response):
    if not WEB_ACCOUNTS_ENABLED:
        raise HTTPException(409, "Регистрация не используется в desktop-режиме")
    try:
        with session_scope() as db:
            user = register_user(db, str(payload.email), payload.display_name, payload.password, _ip(request))
            verify_token = issue_account_token(db, user, "verify_email", timedelta(hours=24))
            token = csrf = ""
            if not REQUIRE_EMAIL_VERIFICATION:
                token, csrf, _ = create_auth_session(db, user, request.headers.get("user-agent", ""), _ip(request), payload.remember)
            result = user_dict(user)
        verification_link = f"{PUBLIC_BASE_URL}/verify-email?token={verify_token}" if PUBLIC_BASE_URL else ""
        delivered = send_account_email(
            result["email"],
            "Подтверждение email — Highlight Studio",
            f"Подтверди email: {verification_link or verify_token}",
        )
        if token and csrf:
            _set_auth_cookies(response, token, csrf, (30 if payload.remember else 1) * 86400)
        payload_result = {
            "ok": True,
            "user": result,
            "verification_email_sent": delivered,
            "requires_verification": REQUIRE_EMAIL_VERIFICATION,
        }
        if DEV_AUTH_TOKENS:
            payload_result["verification_token"] = verify_token
        return payload_result
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response):
    if not WEB_ACCOUNTS_ENABLED:
        raise HTTPException(409, "Вход не используется в desktop-режиме")
    auth_error: Exception | None = None
    token = csrf = ""
    result = None
    with session_scope() as db:
        try:
            user = login_user(db, str(payload.email), payload.password, _ip(request))
            token, csrf, _ = create_auth_session(db, user, request.headers.get("user-agent", ""), _ip(request), payload.remember)
            result = user_dict(user)
        except (PermissionError, ValueError) as exc:
            # Keep failed-attempt counters and audit entries by committing the
            # transaction before returning the generic authentication error.
            auth_error = exc
    if auth_error is not None:
        raise HTTPException(401, str(auth_error)) from auth_error
    _set_auth_cookies(response, token, csrf, (30 if payload.remember else 1) * 86400)
    return {"ok": True, "user": result}


@router.post("/auth/request-password-reset")
def request_password_reset(payload: PasswordResetRequest, request: Request):
    reset_token = ""
    recipient = ""
    with session_scope() as db:
        try:
            normalized = payload.email.strip().lower()
            enforce_public_auth_request_rate_limit(db, "password_reset", normalized, _ip(request))
            user = db.scalar(select(User).where(User.email == normalized, User.is_active.is_(True)))
            if user:
                reset_token = issue_account_token(db, user, "password_reset", timedelta(hours=1))
                recipient = user.email
        except PermissionError as exc:
            raise HTTPException(429, str(exc)) from exc
        except Exception:
            reset_token = ""
    if reset_token and recipient:
        link = f"{PUBLIC_BASE_URL}/reset-password?token={reset_token}" if PUBLIC_BASE_URL else ""
        send_account_email(
            recipient,
            "Сброс пароля — Highlight Studio",
            f"Сбросить пароль: {link or reset_token}",
        )
    result = {
        "ok": True,
        "message": "Если аккаунт существует, инструкция отправлена на email.",
        # Public response is intentionally independent of account existence and
        # SMTP delivery to avoid account enumeration.
        "email_sent": True,
    }
    if DEV_AUTH_TOKENS and reset_token:
        result["reset_token"] = reset_token
    return result


@router.post("/auth/reset-password")
def reset_password(payload: PasswordResetConfirmRequest):
    try:
        with session_scope() as db:
            reset_password_with_token(db, payload.token, payload.new_password)
        return {"ok": True, "message": "Пароль изменён. Войди с новым паролем."}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/auth/request-email-verification")
def request_email_verification(payload: PasswordResetRequest, request: Request):
    verify_token = ""
    recipient = ""
    with session_scope() as db:
        normalized = payload.email.strip().lower()
        try:
            enforce_public_auth_request_rate_limit(db, "verify_email", normalized, _ip(request))
        except PermissionError as exc:
            raise HTTPException(429, str(exc)) from exc
        user = db.scalar(select(User).where(User.email == normalized, User.is_active.is_(True)))
        if user and not user.email_verified:
            verify_token = issue_account_token(db, user, "verify_email", timedelta(hours=24))
            recipient = user.email
    if verify_token and recipient:
        link = f"{PUBLIC_BASE_URL}/verify-email?token={verify_token}" if PUBLIC_BASE_URL else ""
        send_account_email(
            recipient,
            "Подтверждение email — Highlight Studio",
            f"Подтверди email: {link or verify_token}",
        )
    result = {
        "ok": True,
        "message": "Если аккаунт существует и ещё не подтверждён, инструкция отправлена на email.",
        "email_sent": True,
    }
    if DEV_AUTH_TOKENS and verify_token:
        result["verification_token"] = verify_token
    return result


@router.post("/auth/verify-email")
def verify_email(payload: EmailVerificationRequest):
    try:
        with session_scope() as db:
            user = verify_email_with_token(db, payload.token)
            result = user_dict(user)
        return {"ok": True, "user": result, "message": "Email подтверждён."}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/auth/resend-verification")
def resend_verification(request: Request):
    actor = getattr(request.state, "user", None)
    if not actor:
        raise HTTPException(401, "Требуется вход")
    with session_scope() as db:
        user = db.get(User, actor.id)
        if user.email_verified:
            return {"ok": True, "message": "Email уже подтверждён."}
        verify_token = issue_account_token(db, user, "verify_email", timedelta(hours=24))
        recipient = user.email
    link = f"{PUBLIC_BASE_URL}/verify-email?token={verify_token}" if PUBLIC_BASE_URL else ""
    delivered = send_account_email(
        recipient,
        "Подтверждение email — Highlight Studio",
        f"Подтверди email: {link or verify_token}",
    )
    result = {"ok": True, "email_sent": delivered, "message": "Инструкция подготовлена."}
    if DEV_AUTH_TOKENS:
        result["verification_token"] = verify_token
    return result


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        with session_scope() as db:
            revoke_session(db, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Требуется вход")
    return {"user": user_dict(user)}


@router.get("/auth/sessions")
def sessions(request: Request):
    actor = getattr(request.state, "user", None)
    current = getattr(request.state, "auth_session", None)
    if not actor:
        raise HTTPException(401, "Требуется вход")
    with session_scope() as db:
        rows = list_user_sessions(db, actor.id)
        return {
            "sessions": [
                {
                    "id": item.id,
                    "current": bool(current and item.id == current.id),
                    "created_at": item.created_at.isoformat(),
                    "last_seen_at": item.last_seen_at.isoformat(),
                    "expires_at": item.expires_at.isoformat(),
                    "user_agent": item.user_agent,
                    "ip_address": item.ip_address,
                }
                for item in rows
            ]
        }


@router.delete("/auth/sessions/{session_id}")
def delete_session(session_id: str, request: Request, response: Response):
    actor = getattr(request.state, "user", None)
    current = getattr(request.state, "auth_session", None)
    if not actor:
        raise HTTPException(401, "Требуется вход")
    with session_scope() as db:
        removed = revoke_session_by_id(db, actor.id, session_id)
        if not removed:
            raise HTTPException(404, "Сессия не найдена")
        audit(db, "auth.session_revoked", actor.id, "session", session_id, {}, _ip(request))
    if current and current.id == session_id:
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True, "logged_out": bool(current and current.id == session_id)}


@router.post("/auth/logout-all")
def logout_all(request: Request, response: Response):
    actor = getattr(request.state, "user", None)
    if not actor:
        raise HTTPException(401, "Требуется вход")
    with session_scope() as db:
        count = revoke_all_user_sessions(db, actor.id)
        audit(db, "auth.sessions_revoked_all", actor.id, "user", actor.id, {"count": count}, _ip(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True, "revoked": count}


@router.post("/auth/change-password")
def password(payload: PasswordRequest, request: Request, response: Response):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Требуется вход")
    try:
        with session_scope() as db:
            db_user = db.get(User, user.id)
            change_password(db, db_user, payload.current_password, payload.new_password)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return {"ok": True, "message": "Пароль изменён. Войди снова."}
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/admin/users")
def users(request: Request):
    actor = getattr(request.state, "user", None)
    if not actor or actor.global_role != "admin":
        raise HTTPException(403, "Требуются права администратора")
    with session_scope() as db:
        return {"users": [user_dict(x) for x in db.scalars(select(User).order_by(User.created_at.desc())).all()]}


@router.patch("/admin/users/{user_id}")
def update_user(user_id: str, payload: UserRoleRequest, request: Request):
    actor = getattr(request.state, "user", None)
    if not actor or actor.global_role != "admin":
        raise HTTPException(403, "Требуются права администратора")
    if actor.id == user_id and not payload.is_active:
        raise HTTPException(400, "Нельзя отключить собственную учётную запись")
    with session_scope() as db:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(404, "Пользователь не найден")
        if user.global_role == "admin" and (payload.global_role != "admin" or not payload.is_active):
            active_admins = (
                db.scalar(select(func.count()).select_from(User).where(User.global_role == "admin", User.is_active.is_(True))) or 0
            )
            if active_admins <= 1:
                raise HTTPException(400, "Нельзя удалить права у последнего активного администратора")
        previous = {"global_role": user.global_role, "is_active": user.is_active}
        user.global_role = payload.global_role
        user.is_active = payload.is_active
        audit(
            db,
            "admin.user_updated",
            actor.id,
            "user",
            user.id,
            {"before": previous, "after": {"global_role": user.global_role, "is_active": user.is_active}},
            _ip(request),
        )
        result = user_dict(user)
    return {"ok": True, "user": result}


@router.get("/projects/{project_id}/access")
def project_access(project_id: str, request: Request):
    actor = getattr(request.state, "user", None)
    if not actor:
        raise HTTPException(401, "Требуется вход")
    with session_scope() as db:
        role = project_role(db, actor, project_id)
    if not role:
        raise HTTPException(403, "Нет доступа к проекту")
    return {
        "role": role,
        "can_view": True,
        "can_edit": role in {"editor", "owner"},
        "can_manage": role == "owner" or actor.global_role == "admin",
    }


@router.get("/projects/{project_id}/members")
def members(project_id: str, request: Request):
    actor = getattr(request.state, "user", None)
    if not actor:
        raise HTTPException(401, "Требуется вход")
    with session_scope() as db:
        if project_role(db, actor, project_id) != "owner" and actor.global_role != "admin":
            raise HTTPException(403, "Недостаточно прав")
        rows = db.execute(
            select(ProjectMembership, User)
            .join(User, User.id == ProjectMembership.user_id)
            .where(ProjectMembership.project_id == project_id)
        ).all()
        return {"members": [{"user": user_dict(u), "role": m.role} for m, u in rows]}


@router.put("/projects/{project_id}/members")
def put_member(project_id: str, payload: MemberRequest, request: Request):
    actor = getattr(request.state, "user", None)
    if not actor:
        raise HTTPException(401, "Требуется вход")
    try:
        with session_scope() as db:
            actor_db = db.get(User, actor.id)
            m = add_or_update_member(db, actor_db, project_id, str(payload.email), payload.role)
            return {"ok": True, "role": m.role, "user_id": m.user_id}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/projects/{project_id}/members/{user_id}")
def delete_member(project_id: str, user_id: str, request: Request):
    actor = getattr(request.state, "user", None)
    if not actor:
        raise HTTPException(401, "Требуется вход")
    try:
        with session_scope() as db:
            actor_db = db.get(User, actor.id)
            remove_member(db, actor_db, project_id, user_id)
        return {"ok": True}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/admin/audit")
def admin_audit(request: Request, limit: int = 100):
    actor = getattr(request.state, "user", None)
    if not actor or actor.global_role != "admin":
        raise HTTPException(403, "Требуются права администратора")
    safe_limit = max(1, min(500, limit))
    with session_scope() as db:
        rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(safe_limit)).all()
        return {
            "items": [
                {
                    "id": row.id,
                    "action": row.action,
                    "actor_user_id": row.actor_user_id,
                    "resource_type": row.resource_type,
                    "resource_id": row.resource_id,
                    "details": row.details,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]
        }
