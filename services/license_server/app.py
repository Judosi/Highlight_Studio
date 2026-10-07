from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

TOKEN_PREFIX = "HSB1"
KNOWN_ENTITLEMENTS = frozenset({"analysis", "render", "shorts", "twitch", "metadata_ai"})
DEFAULT_ENTITLEMENTS = sorted(KNOWN_ENTITLEMENTS)
DB_PATH = Path(os.environ.get("HIGHLIGHT_STUDIO_LICENSE_DB", "license_service.sqlite3")).resolve()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _private_key() -> Ed25519PrivateKey:
    value = os.environ.get("HIGHLIGHT_STUDIO_LICENSE_PRIVATE_KEY_B64", "").strip()
    if not value:
        raise RuntimeError("HIGHLIGHT_STUDIO_LICENSE_PRIVATE_KEY_B64 is required")
    raw = _b64decode(value)
    if len(raw) != 32:
        raise RuntimeError("License private key must be 32 raw bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_db() -> None:
    with closing(_connect()) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS licenses (
                license_key TEXT PRIMARY KEY,
                license_id TEXT NOT NULL UNIQUE,
                customer_id TEXT NOT NULL DEFAULT '',
                plan TEXT NOT NULL,
                entitlements_json TEXT NOT NULL,
                expires_at REAL NOT NULL DEFAULT 0,
                max_devices INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS devices (
                license_key TEXT NOT NULL,
                device_id TEXT NOT NULL,
                activated_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                PRIMARY KEY (license_key, device_id),
                FOREIGN KEY (license_key) REFERENCES licenses(license_key) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_licenses_customer ON licenses(customer_id);
            """
        )
        db.commit()


def _admin_guard(authorization: str = Header(default="")) -> None:
    expected = os.environ.get("HIGHLIGHT_STUDIO_LICENSE_ADMIN_TOKEN", "").strip()
    supplied = authorization.removeprefix("Bearer ").strip()
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(403, "Admin authorization failed")


def _clean_entitlements(values: list[str] | None) -> list[str]:
    chosen = sorted({str(value).strip() for value in (values or DEFAULT_ENTITLEMENTS) if str(value).strip() in KNOWN_ENTITLEMENTS})
    if not chosen:
        raise HTTPException(400, "At least one known entitlement is required")
    return chosen


def _issue_token(row: sqlite3.Row, device_id: str) -> str:
    now = time.time()
    payload = {
        "license_id": row["license_id"],
        "plan": row["plan"],
        "device_id": device_id,
        "issued_at": now,
        "not_before": now - 60,
        "expires_at": float(row["expires_at"] or 0),
        "entitlements": json.loads(row["entitlements_json"]),
    }
    encoded = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signed = f"{TOKEN_PREFIX}.{encoded}".encode("ascii")
    signature = _private_key().sign(signed)
    return f"{TOKEN_PREFIX}.{encoded}.{_b64url(signature)}"


def _verify_own_token(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise HTTPException(400, "Invalid token")
    try:
        signed = f"{parts[0]}.{parts[1]}".encode("ascii")
        _private_key().public_key().verify(_b64decode(parts[2]), signed)
        payload = json.loads(_b64decode(parts[1]))
    except (InvalidSignature, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Invalid token") from exc
    return payload


class LicenseCreate(BaseModel):
    customer_id: str = Field("", max_length=200)
    license_key: str = Field("", max_length=200)
    plan: str = Field("creator", min_length=1, max_length=80)
    days: int = Field(30, ge=1, le=3650)
    max_devices: int = Field(1, ge=1, le=20)
    entitlements: list[str] = Field(default_factory=lambda: list(DEFAULT_ENTITLEMENTS))


class ActivationRequest(BaseModel):
    license_key: str = Field(..., min_length=8, max_length=200)
    device_id: str = Field(..., min_length=16, max_length=128)
    app_version: str = Field("", max_length=100)


class TokenRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=10000)
    device_id: str = Field(..., min_length=16, max_length=128)
    app_version: str = Field("", max_length=100)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Highlight Studio License Service", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "highlight-studio-license", "time": time.time()}


@app.post("/admin/licenses", dependencies=[Depends(_admin_guard)])
def create_license(payload: LicenseCreate) -> dict[str, Any]:
    now = time.time()
    key = payload.license_key.strip() or "HS-" + "-".join(secrets.token_hex(2).upper() for _ in range(4))
    license_id = secrets.token_hex(12)
    entitlements = _clean_entitlements(payload.entitlements)
    with closing(_connect()) as db:
        try:
            db.execute(
                "INSERT INTO licenses VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    key,
                    license_id,
                    payload.customer_id.strip(),
                    payload.plan,
                    json.dumps(entitlements),
                    now + payload.days * 86400,
                    payload.max_devices,
                    now,
                    now,
                ),
            )
            db.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "License key already exists") from exc
    return {"ok": True, "license_key": key, "license_id": license_id, "expires_at": now + payload.days * 86400}


def _license_row(db: sqlite3.Connection, key: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM licenses WHERE license_key = ?", (key,)).fetchone()
    if not row or not row["active"]:
        raise HTTPException(404, "License not found or inactive")
    if row["expires_at"] and time.time() >= float(row["expires_at"]):
        raise HTTPException(402, "License expired")
    return row


@app.post("/activate")
def activate(payload: ActivationRequest) -> dict[str, Any]:
    with closing(_connect()) as db:
        row = _license_row(db, payload.license_key.strip())
        existing = db.execute(
            "SELECT 1 FROM devices WHERE license_key=? AND device_id=?", (row["license_key"], payload.device_id)
        ).fetchone()
        if not existing:
            count = db.execute("SELECT COUNT(*) FROM devices WHERE license_key=?", (row["license_key"],)).fetchone()[0]
            if count >= int(row["max_devices"]):
                raise HTTPException(409, "Device limit reached")
            db.execute("INSERT INTO devices VALUES (?, ?, ?, ?)", (row["license_key"], payload.device_id, time.time(), time.time()))
        else:
            db.execute(
                "UPDATE devices SET last_seen_at=? WHERE license_key=? AND device_id=?",
                (time.time(), row["license_key"], payload.device_id),
            )
        db.commit()
        return {"ok": True, "token": _issue_token(row, payload.device_id)}


@app.post("/refresh")
def refresh(payload: TokenRequest) -> dict[str, Any]:
    token_payload = _verify_own_token(payload.token)
    if str(token_payload.get("device_id") or "") != payload.device_id:
        raise HTTPException(403, "Wrong device")
    license_id = str(token_payload.get("license_id") or "")
    with closing(_connect()) as db:
        row = db.execute("SELECT * FROM licenses WHERE license_id=?", (license_id,)).fetchone()
        if not row:
            raise HTTPException(404, "License not found")
        row = _license_row(db, row["license_key"])
        device = db.execute("SELECT 1 FROM devices WHERE license_key=? AND device_id=?", (row["license_key"], payload.device_id)).fetchone()
        if not device:
            raise HTTPException(403, "Device is not active")
        db.execute(
            "UPDATE devices SET last_seen_at=? WHERE license_key=? AND device_id=?", (time.time(), row["license_key"], payload.device_id)
        )
        db.commit()
        return {"ok": True, "token": _issue_token(row, payload.device_id)}


@app.post("/deactivate")
def deactivate(payload: TokenRequest) -> dict[str, Any]:
    token_payload = _verify_own_token(payload.token)
    license_id = str(token_payload.get("license_id") or "")
    with closing(_connect()) as db:
        row = db.execute("SELECT license_key FROM licenses WHERE license_id=?", (license_id,)).fetchone()
        if row:
            db.execute("DELETE FROM devices WHERE license_key=? AND device_id=?", (row["license_key"], payload.device_id))
            db.commit()
    return {"ok": True, "deactivated": True}


@app.post("/webhooks/payment")
async def payment_webhook(request: Request, x_hs_signature: str = Header(default="")) -> dict[str, Any]:
    secret = os.environ.get("HIGHLIGHT_STUDIO_PAYMENT_WEBHOOK_SECRET", "").encode()
    body = await request.body()
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest() if secret else ""
    if not expected or not hmac.compare_digest(expected, x_hs_signature.strip()):
        raise HTTPException(403, "Invalid webhook signature")
    payload = json.loads(body)
    event = str(payload.get("event") or "")
    customer_id = str(payload.get("customer_id") or "")[:200]
    if event == "subscription.activated":
        created = create_license(
            LicenseCreate(
                customer_id=customer_id,
                license_key=str(payload.get("license_key") or ""),
                plan=str(payload.get("plan") or "creator"),
                days=int(payload.get("days") or 30),
                max_devices=int(payload.get("max_devices") or 1),
                entitlements=payload.get("entitlements") or DEFAULT_ENTITLEMENTS,
            )
        )
        return {"ok": True, "event": event, **created}
    if event == "subscription.cancelled":
        key = str(payload.get("license_key") or "")
        with closing(_connect()) as db:
            db.execute("UPDATE licenses SET active=0, updated_at=? WHERE license_key=?", (time.time(), key))
            db.commit()
        return {"ok": True, "event": event}
    raise HTTPException(400, "Unsupported event")
