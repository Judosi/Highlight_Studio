from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
import uuid
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ...core.settings import ALLOW_PUBLIC_REGISTRATION, PROJECTS_DIR, REQUIRE_EMAIL_VERIFICATION, SESSION_DAYS
from ..database.models import (
    AccountToken,
    AuditLog,
    AuthRateLimit,
    AuthSession,
    ProjectMembership,
    ProjectRecord,
    User,
    UserPreference,
)

SESSION_COOKIE = "highlight_studio_session"
CSRF_COOKIE = "highlight_studio_csrf"
ROLE_RANK = {"viewer": 10, "editor": 20, "owner": 30}
GLOBAL_ROLES = {"user", "admin"}
PROJECT_ROLES = set(ROLE_RANK)
_hasher = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if len(value) > 320 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Укажи корректный email")
    return value


def validate_password(password: str) -> None:
    if len(password) < 10:
        raise ValueError("Пароль должен содержать минимум 10 символов")
    if len(password) > 256:
        raise ValueError("Пароль слишком длинный")
    if password.lower() in {"password123", "1234567890", "qwerty12345"}:
        raise ValueError("Выбери более надёжный пароль")


def hash_password(password: str) -> str:
    validate_password(password)
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def user_dict(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "global_role": user.global_role,
        "is_active": user.is_active,
        "email_verified": user.email_verified,
        "created_at": user.created_at.isoformat(),
    }


def audit(
    db: Session,
    action: str,
    actor_user_id: str | None = None,
    resource_type: str = "",
    resource_id: str = "",
    details: dict | None = None,
    ip: str = "",
) -> None:
    db.add(
        AuditLog(
            id=str(uuid.uuid4()),
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip[:80],
        )
    )


def registration_allowed(db: Session) -> bool:
    count = db.scalar(select(func.count()).select_from(User)) or 0
    return count == 0 or ALLOW_PUBLIC_REGISTRATION


def register_user(db: Session, email: str, display_name: str, password: str, ip: str = "") -> User:
    email = normalize_email(email)
    # Serialize registration bootstrap on PostgreSQL so two simultaneous first
    # sign-ups cannot both become global administrators.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": 847_109_001})
    if not registration_allowed(db):
        raise PermissionError("Регистрация временно закрыта")
    if db.scalar(select(User).where(User.email == email)):
        raise ValueError("Пользователь с таким email уже существует")
    count = db.scalar(select(func.count()).select_from(User)) or 0
    user = User(
        id=str(uuid.uuid4()),
        email=email,
        display_name=(display_name.strip() or email.split("@")[0])[:120],
        password_hash=hash_password(password),
        global_role="admin" if count == 0 else "user",
    )
    db.add(user)
    db.flush()
    audit(db, "auth.register", user.id, "user", user.id, {"bootstrap_admin": count == 0}, ip)
    return user


def _rate_limit_key(email: str, ip: str) -> str:
    return _sha(f"{normalize_email(email)}|{ip.strip().lower()[:80]}")


def enforce_public_auth_request_rate_limit(
    db: Session,
    purpose: str,
    email: str,
    ip: str = "",
    *,
    max_requests: int = 5,
    window_minutes: int = 15,
    block_minutes: int = 15,
) -> None:
    """Rate-limit public account recovery/verification requests.

    The key intentionally combines purpose + normalized account hint + IP while
    the public response remains identical for existing and non-existing users.
    This protects mail/token endpoints without introducing account enumeration.
    """
    now = utcnow()
    key_hash = _sha(f"public:{purpose}|{normalize_email(email)}|{ip.strip().lower()[:80]}")
    row = db.scalar(select(AuthRateLimit).where(AuthRateLimit.key_hash == key_hash).with_for_update())
    if not row:
        row = AuthRateLimit(key_hash=key_hash, attempt_count=0, window_started_at=now)
        db.add(row)
        db.flush()
    if row.blocked_until and aware(row.blocked_until) > now:
        raise PermissionError("Слишком много запросов. Попробуй позже")
    if aware(row.window_started_at) < now - timedelta(minutes=window_minutes):
        row.attempt_count = 0
        row.window_started_at = now
        row.blocked_until = None
    row.attempt_count += 1
    if row.attempt_count > max_requests:
        row.blocked_until = now + timedelta(minutes=block_minutes)
        raise PermissionError("Слишком много запросов. Попробуй позже")


def enforce_login_rate_limit(db: Session, email: str, ip: str = "") -> AuthRateLimit:
    now = utcnow()
    key_hash = _rate_limit_key(email, ip)
    row = db.scalar(select(AuthRateLimit).where(AuthRateLimit.key_hash == key_hash).with_for_update())
    if not row:
        row = AuthRateLimit(key_hash=key_hash, attempt_count=0, window_started_at=now)
        db.add(row)
        db.flush()
    if row.blocked_until and aware(row.blocked_until) > now:
        raise PermissionError("Слишком много попыток. Попробуй позже")
    if aware(row.window_started_at) < now - timedelta(minutes=15):
        row.attempt_count = 0
        row.window_started_at = now
        row.blocked_until = None
    return row


def register_login_failure(row: AuthRateLimit) -> None:
    row.attempt_count += 1
    if row.attempt_count >= 10:
        row.blocked_until = utcnow() + timedelta(minutes=15)
        row.attempt_count = 0


def clear_login_rate_limit(db: Session, row: AuthRateLimit) -> None:
    db.delete(row)


def login_user(db: Session, email: str, password: str, ip: str = "") -> User:
    email = normalize_email(email)
    limiter = enforce_login_rate_limit(db, email, ip)
    user = db.scalar(select(User).where(User.email == email))
    now = utcnow()
    if not user or not user.is_active:
        register_login_failure(limiter)
        raise PermissionError("Неверный email или пароль")
    if REQUIRE_EMAIL_VERIFICATION and not user.email_verified:
        raise PermissionError("Подтверди email перед входом")
    if user.locked_until and aware(user.locked_until) > now:
        raise PermissionError("Слишком много попыток. Попробуй позже")
    if not verify_password(user.password_hash, password):
        user.failed_login_count += 1
        if user.failed_login_count >= 5:
            user.locked_until = now + timedelta(minutes=15)
            user.failed_login_count = 0
        register_login_failure(limiter)
        audit(db, "auth.login_failed", user.id, "user", user.id, {}, ip)
        raise PermissionError("Неверный email или пароль")
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    clear_login_rate_limit(db, limiter)
    audit(db, "auth.login", user.id, "user", user.id, {}, ip)
    return user


def create_auth_session(db: Session, user: User, user_agent: str = "", ip: str = "", remember: bool = True) -> tuple[str, str, AuthSession]:
    now = utcnow()
    db.execute(
        delete(AuthSession).where(
            (AuthSession.expires_at <= now) | ((AuthSession.revoked_at.is_not(None)) & (AuthSession.revoked_at <= now - timedelta(days=7)))
        )
    )
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    days = SESSION_DAYS if remember else 1
    item = AuthSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_hash=_sha(token),
        csrf_hash=_sha(csrf),
        expires_at=now + timedelta(days=days),
        user_agent=user_agent[:500],
        ip_address=ip[:80],
    )
    db.add(item)
    db.flush()
    return token, csrf, item


def authenticate_token(db: Session, token: str) -> tuple[User, AuthSession] | None:
    if not token:
        return None
    item = db.scalar(select(AuthSession).where(AuthSession.token_hash == _sha(token), AuthSession.revoked_at.is_(None)))
    if not item or aware(item.expires_at) <= utcnow():
        return None
    user = db.get(User, item.user_id)
    if not user or not user.is_active:
        return None
    now = utcnow()
    if aware(item.last_seen_at) < now - timedelta(minutes=5):
        item.last_seen_at = now
    return user, item


def validate_csrf(session: AuthSession, supplied: str) -> bool:
    return bool(supplied) and secrets.compare_digest(session.csrf_hash, _sha(supplied))


def revoke_session(db: Session, token: str) -> None:
    item = db.scalar(select(AuthSession).where(AuthSession.token_hash == _sha(token), AuthSession.revoked_at.is_(None)))
    if item:
        item.revoked_at = utcnow()


def list_user_sessions(db: Session, user_id: str) -> list[AuthSession]:
    now = utcnow()
    return list(
        db.scalars(
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
            .order_by(AuthSession.last_seen_at.desc())
        ).all()
    )


def revoke_session_by_id(db: Session, user_id: str, session_id: str) -> bool:
    item = db.scalar(
        select(AuthSession).where(
            AuthSession.id == session_id,
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    )
    if not item:
        return False
    item.revoked_at = utcnow()
    return True


def revoke_all_user_sessions(db: Session, user_id: str, except_session_id: str | None = None) -> int:
    rows = db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    ).all()
    now = utcnow()
    revoked = 0
    for item in rows:
        if except_session_id and item.id == except_session_id:
            continue
        item.revoked_at = now
        revoked += 1
    return revoked


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(user.password_hash, current_password):
        raise PermissionError("Текущий пароль неверен")
    user.password_hash = hash_password(new_password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    audit(db, "auth.password_changed", user.id, "user", user.id)


def ensure_project(db: Session, project_id: str, name: str, owner: User, storage_path: str | None = None) -> ProjectRecord:
    record = db.get(ProjectRecord, project_id)
    if not record:
        record = ProjectRecord(
            id=project_id, name=name[:240], storage_path=storage_path or str(PROJECTS_DIR / project_id), created_by=owner.id
        )
        db.add(record)
        db.flush()
    membership = db.scalar(
        select(ProjectMembership).where(ProjectMembership.project_id == project_id, ProjectMembership.user_id == owner.id)
    )
    if not membership:
        db.add(ProjectMembership(id=str(uuid.uuid4()), project_id=project_id, user_id=owner.id, role="owner"))
    return record


def project_role(db: Session, user: User, project_id: str) -> str | None:
    if user.global_role == "admin":
        return "owner"
    membership = db.scalar(
        select(ProjectMembership).where(ProjectMembership.project_id == project_id, ProjectMembership.user_id == user.id)
    )
    return membership.role if membership else None


def has_project_permission(db: Session, user: User, project_id: str, required: str) -> bool:
    role = project_role(db, user, project_id)
    return bool(role and ROLE_RANK.get(role, 0) >= ROLE_RANK.get(required, 999))


def accessible_project_ids(db: Session, user: User) -> set[str] | None:
    if user.global_role == "admin":
        return None
    return set(db.scalars(select(ProjectMembership.project_id).where(ProjectMembership.user_id == user.id)).all())


def add_or_update_member(db: Session, actor: User, project_id: str, email: str, role: str) -> ProjectMembership:
    if role not in PROJECT_ROLES:
        raise ValueError("Некорректная роль")
    if not has_project_permission(db, actor, project_id, "owner"):
        raise PermissionError("Только владелец может управлять участниками")
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if not user:
        raise ValueError("Пользователь не найден")
    membership = db.scalar(
        select(ProjectMembership).where(ProjectMembership.project_id == project_id, ProjectMembership.user_id == user.id)
    )
    if membership:
        if membership.role == "owner" and role != "owner":
            owners = (
                db.scalar(
                    select(func.count())
                    .select_from(ProjectMembership)
                    .where(
                        ProjectMembership.project_id == project_id,
                        ProjectMembership.role == "owner",
                    )
                )
                or 0
            )
            if owners <= 1:
                raise ValueError("У проекта должен остаться хотя бы один владелец")
        membership.role = role
    else:
        membership = ProjectMembership(id=str(uuid.uuid4()), project_id=project_id, user_id=user.id, role=role)
        db.add(membership)
    audit(db, "project.member_changed", actor.id, "project", project_id, {"user_id": user.id, "role": role})
    return membership


def remove_member(db: Session, actor: User, project_id: str, user_id: str) -> None:
    if not has_project_permission(db, actor, project_id, "owner"):
        raise PermissionError("Только владелец может управлять участниками")
    membership = db.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
    )
    if not membership:
        raise ValueError("Участник не найден")
    if membership.role == "owner":
        owners = (
            db.scalar(
                select(func.count())
                .select_from(ProjectMembership)
                .where(
                    ProjectMembership.project_id == project_id,
                    ProjectMembership.role == "owner",
                )
            )
            or 0
        )
        if owners <= 1:
            raise ValueError("У проекта должен остаться хотя бы один владелец")
    db.delete(membership)
    audit(db, "project.member_removed", actor.id, "project", project_id, {"user_id": user_id})


def set_user_preference(db: Session, user_id: str, key: str, value: dict) -> UserPreference:
    item = db.scalar(select(UserPreference).where(UserPreference.user_id == user_id, UserPreference.key == key))
    if not item:
        item = UserPreference(id=str(uuid.uuid4()), user_id=user_id, key=key, value=value)
        db.add(item)
    else:
        item.value = value
        item.updated_at = utcnow()
    return item


def get_user_preference(db: Session, user_id: str, key: str) -> dict | None:
    item = db.scalar(select(UserPreference).where(UserPreference.user_id == user_id, UserPreference.key == key))
    return dict(item.value) if item and isinstance(item.value, dict) else None


def issue_account_token(db: Session, user: User, purpose: str, lifetime: timedelta) -> str:
    """Issue a bounded token without invalidating every still-valid link.

    Keeping a small number of active tokens prevents an attacker (or accidental
    repeated click) from invalidating a recovery email that the user already
    received, while still bounding database growth.
    """
    now = utcnow()
    db.execute(
        delete(AccountToken).where(
            AccountToken.user_id == user.id,
            AccountToken.purpose == purpose,
            (AccountToken.used_at.is_not(None)) | (AccountToken.expires_at <= now),
        )
    )
    active = list(
        db.scalars(
            select(AccountToken)
            .where(
                AccountToken.user_id == user.id,
                AccountToken.purpose == purpose,
                AccountToken.used_at.is_(None),
                AccountToken.expires_at > now,
            )
            .order_by(AccountToken.created_at.asc())
        ).all()
    )
    # Keep at most two existing valid links plus the new one.
    for old in active[:-2]:
        db.delete(old)
    raw = secrets.token_urlsafe(40)
    db.add(
        AccountToken(
            id=str(uuid.uuid4()),
            user_id=user.id,
            purpose=purpose,
            token_hash=_sha(raw),
            expires_at=now + lifetime,
        )
    )
    return raw


def consume_account_token(db: Session, raw: str, purpose: str) -> User:
    item = db.scalar(
        select(AccountToken).where(
            AccountToken.token_hash == _sha(raw),
            AccountToken.purpose == purpose,
            AccountToken.used_at.is_(None),
        )
    )
    if not item or aware(item.expires_at) <= utcnow():
        raise ValueError("Ссылка недействительна или устарела")
    user = db.get(User, item.user_id)
    if not user or not user.is_active:
        raise ValueError("Пользователь недоступен")
    item.used_at = utcnow()
    return user


def reset_password_with_token(db: Session, raw: str, new_password: str) -> User:
    user = consume_account_token(db, raw, "password_reset")
    user.password_hash = hash_password(new_password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    audit(db, "auth.password_reset", user.id, "user", user.id)
    return user


def verify_email_with_token(db: Session, raw: str) -> User:
    user = consume_account_token(db, raw, "verify_email")
    user.email_verified = True
    audit(db, "auth.email_verified", user.id, "user", user.id)
    return user
