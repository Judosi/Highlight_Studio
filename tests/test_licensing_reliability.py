from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.src.highlight_studio.infrastructure import licensing
from backend.src.highlight_studio.core.utils import read_json, write_json
from services.license_server import app as license_service


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def signed_token(private_key: Ed25519PrivateKey, payload: dict) -> str:
    encoded = b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signed = f"HSB1.{encoded}".encode("ascii")
    return f"HSB1.{encoded}.{b64url(private_key.sign(signed))}"


def corrupt_signature(token: str) -> str:
    prefix, payload, encoded_signature = token.split(".")
    signature = bytearray(base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4)))
    signature[0] ^= 0x01
    return f"{prefix}.{payload}.{b64url(bytes(signature))}"


def configure_client(tmp_path: Path, monkeypatch, private_key: Ed25519PrivateKey, *, device: str = "d" * 64) -> None:
    public_raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64", b64url(public_raw))
    monkeypatch.setattr(licensing, "LICENSE_PATH", tmp_path / "Лицензия с пробелами" / "license.json")
    monkeypatch.setattr(licensing, "TRIAL_PATH", tmp_path / "Лицензия с пробелами" / "trial.json")
    monkeypatch.setattr(licensing, "DEVICE_ID_PATH", tmp_path / "Лицензия с пробелами" / "device_identity.json", raising=False)
    monkeypatch.setattr(licensing, "device_id", lambda: device)


def configure_server(tmp_path: Path, monkeypatch, *, max_devices: int = 1) -> tuple[TestClient, Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_PRIVATE_KEY_B64", b64url(private_raw))
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setattr(license_service, "DB_PATH", tmp_path / "license service" / "licenses.sqlite3")
    license_service.init_db()
    client = TestClient(license_service.app)
    created = client.post(
        "/admin/licenses",
        headers={"Authorization": "Bearer test-admin-token"},
        json={"customer_id": "test-customer", "days": 30, "max_devices": max_devices},
    )
    assert created.status_code == 200
    return client, private_key, created.json()["license_key"]


def device_rows() -> list[sqlite3.Row]:
    db = license_service._connect()
    try:
        return list(db.execute("SELECT * FROM devices ORDER BY device_id").fetchall())
    finally:
        db.close()


def test_valid_activation_fake_signature_and_expiry(tmp_path: Path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    configure_client(tmp_path, monkeypatch, private_key)
    now = 2_000_000.0
    valid = signed_token(
        private_key,
        {
            "license_id": "valid-license",
            "device_id": "d" * 64,
            "issued_at": now,
            "expires_at": now + 3600,
            "entitlements": ["analysis"],
        },
    )
    assert licensing.verify_license_token(valid, now=now)["ok"] is True

    forged = corrupt_signature(valid)
    assert licensing.verify_license_token(forged, now=now)["reason"] == "invalid_signature"
    assert licensing.verify_license_token(valid, now=now + 3600)["reason"] == "expired"


def test_reactivation_same_device_uses_one_slot_and_survives_app_update(tmp_path: Path, monkeypatch):
    client, private_key, key = configure_server(tmp_path, monkeypatch)
    device = "a" * 64
    first = client.post("/activate", json={"license_key": key, "device_id": device, "app_version": "v11.2.7"})
    second = client.post("/activate", json={"license_key": key, "device_id": device, "app_version": "v99.0.0"})
    assert first.status_code == second.status_code == 200
    assert len(device_rows()) == 1

    configure_client(tmp_path, monkeypatch, private_key, device=device)
    write_json(licensing.LICENSE_PATH, {"token": second.json()["token"], "app_version": "v1.0.0"})
    status = licensing.license_status()
    assert status["state"] == "active"
    assert status["can_use"] is True


def test_deactivation_releases_exact_signed_device_and_rejects_foreign_device(tmp_path: Path, monkeypatch):
    client, _private_key, key = configure_server(tmp_path, monkeypatch, max_devices=2)
    own_device = "b" * 64
    foreign_device = "c" * 64
    activated = client.post("/activate", json={"license_key": key, "device_id": own_device, "app_version": "test"})
    assert activated.status_code == 200

    foreign = client.post(
        "/deactivate",
        json={"token": activated.json()["token"], "device_id": foreign_device, "app_version": "test"},
    )
    assert foreign.status_code == 403
    assert [row["device_id"] for row in device_rows()] == [own_device]

    own = client.post(
        "/deactivate",
        json={"token": activated.json()["token"], "device_id": own_device, "app_version": "test"},
    )
    assert own.status_code == 200
    assert device_rows() == []


class _BarrierCursor:
    def __init__(self, cursor: sqlite3.Cursor, barrier: threading.Barrier):
        self._cursor = cursor
        self._barrier = barrier

    def fetchone(self):
        row = self._cursor.fetchone()
        self._barrier.wait(timeout=5)
        return row

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _ObservedConnection:
    def __init__(self, connection: sqlite3.Connection, barrier: threading.Barrier):
        self._connection = connection
        self._barrier = barrier
        self._immediate = False

    def execute(self, sql: str, parameters=()):
        normalized = " ".join(sql.upper().split())
        cursor = self._connection.execute(sql, parameters)
        if normalized.startswith("BEGIN IMMEDIATE"):
            self._immediate = True
        if normalized.startswith("SELECT COUNT(*) FROM DEVICES") and not self._immediate:
            return _BarrierCursor(cursor, self._barrier)
        return cursor

    def __getattr__(self, name):
        return getattr(self._connection, name)


def test_concurrent_activation_is_atomic_and_never_leaks_sqlite_busy(tmp_path: Path, monkeypatch):
    _client, _private_key, key = configure_server(tmp_path, monkeypatch, max_devices=1)
    original_connect = license_service._connect
    barrier = threading.Barrier(2)

    def observed_connect():
        return _ObservedConnection(original_connect(), barrier)

    monkeypatch.setattr(license_service, "_connect", observed_connect)

    def activate(device: str):
        try:
            result = license_service.activate(
                license_service.ActivationRequest(license_key=key, device_id=device, app_version="concurrency-test")
            )
            return ("ok", result["ok"])
        except HTTPException as exc:
            return ("http", exc.status_code)
        except sqlite3.OperationalError as exc:
            return ("sqlite", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(activate, ["d" * 64, "e" * 64]))

    assert sorted(outcomes) == [("http", 409), ("ok", True)]
    assert len(device_rows()) == 1


def test_activation_transaction_rolls_back_if_token_signing_fails(tmp_path: Path, monkeypatch):
    _client, _private_key, key = configure_server(tmp_path, monkeypatch)
    monkeypatch.setattr(license_service, "_issue_token", lambda *_args: (_ for _ in ()).throw(RuntimeError("signing failed")))

    with pytest.raises(RuntimeError, match="signing failed"):
        license_service.activate(
            license_service.ActivationRequest(license_key=key, device_id="f" * 64, app_version="transaction-test")
        )

    assert device_rows() == []


def test_database_migration_adds_trial_state_without_changing_existing_licenses(tmp_path: Path, monkeypatch):
    database = tmp_path / "existing" / "licenses.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE licenses (
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
            CREATE TABLE devices (
                license_key TEXT NOT NULL,
                device_id TEXT NOT NULL,
                activated_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                PRIMARY KEY (license_key, device_id),
                FOREIGN KEY (license_key) REFERENCES licenses(license_key) ON DELETE CASCADE
            );
            """
        )
        db.execute(
            "INSERT INTO licenses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("HS-EXISTING", "license-existing", "customer", "creator", "[]", 0, 1, 1, 10.0, 10.0),
        )
        db.execute("INSERT INTO devices VALUES (?, ?, ?, ?)", ("HS-EXISTING", "d" * 64, 10.0, 10.0))
    monkeypatch.setattr(license_service, "DB_PATH", database)

    license_service.init_db()

    with sqlite3.connect(database) as db:
        assert db.execute("SELECT license_id FROM licenses").fetchone()[0] == "license-existing"
        assert db.execute("SELECT device_id FROM devices").fetchone()[0] == "d" * 64
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trial_devices'").fetchone()


def test_client_deactivation_uses_signed_device_after_local_device_drift(tmp_path: Path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    old_device = "1" * 64
    configure_client(tmp_path, monkeypatch, private_key, device="2" * 64)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_SERVER_URL", "https://license.example.test")
    token = signed_token(
        private_key,
        {"license_id": "drift", "device_id": old_device, "issued_at": time.time(), "expires_at": time.time() + 3600},
    )
    write_json(licensing.LICENSE_PATH, {"token": token})
    sent: list[dict] = []

    class Response:
        ok = True

    monkeypatch.setattr(licensing.requests, "post", lambda _url, *, json, timeout: sent.append(json) or Response())
    result = licensing.deactivate_license()

    assert sent[0]["device_id"] == old_device
    assert result["remote_released"] is True
    assert not licensing.LICENSE_PATH.exists()


def test_server_outage_preserves_license_so_deactivation_can_be_retried(tmp_path: Path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    configure_client(tmp_path, monkeypatch, private_key)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_SERVER_URL", "https://license.example.test")
    token = signed_token(
        private_key,
        {"license_id": "offline", "device_id": "d" * 64, "issued_at": time.time(), "expires_at": time.time() + 3600},
    )
    write_json(licensing.LICENSE_PATH, {"token": token})
    monkeypatch.setattr(licensing.requests, "post", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("offline")))

    result = licensing.deactivate_license()

    assert result["deactivated"] is False
    assert result["remote_released"] is False
    assert licensing.LICENSE_PATH.exists()


def test_corrupted_local_state_fails_closed_without_touching_user_files(tmp_path: Path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    configure_client(tmp_path, monkeypatch, private_key)
    licensing.LICENSE_PATH.parent.mkdir(parents=True)
    licensing.LICENSE_PATH.write_text("{broken", encoding="utf-8")
    licensing.TRIAL_PATH.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(licensing, "_trial_can_start", lambda: False)

    status = licensing.license_status(now=2_000_000)

    assert status["can_use"] is False
    assert status["state"] == "not_started"
    assert licensing.LICENSE_PATH.read_text(encoding="utf-8") == "{broken"


def test_clock_forward_expires_and_clock_before_not_before_is_rejected(tmp_path: Path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    configure_client(tmp_path, monkeypatch, private_key)
    token = signed_token(
        private_key,
        {"license_id": "clock", "device_id": "d" * 64, "not_before": 10_000, "expires_at": 20_000},
    )
    assert licensing.verify_license_token(token, now=9_999)["reason"] == "not_yet_valid"
    assert licensing.verify_license_token(token, now=20_000)["reason"] == "expired"


def test_device_id_remains_stable_when_hostname_and_network_adapter_change(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(licensing, "DEVICE_ID_PATH", tmp_path / "device_identity.json", raising=False)
    monkeypatch.setattr(licensing.platform, "system", lambda: "Windows")
    monkeypatch.setattr(licensing.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(licensing.platform, "node", lambda: "EDIT-PC")
    monkeypatch.setattr(licensing.uuid, "getnode", lambda: 111)
    first = licensing.device_id()

    monkeypatch.setattr(licensing.platform, "node", lambda: "RENAMED-PC")
    monkeypatch.setattr(licensing.uuid, "getnode", lambda: 222)
    second = licensing.device_id()

    assert second == first
    assert read_json(licensing.DEVICE_ID_PATH, {})["device_id"] == first


def test_existing_license_keeps_legacy_device_binding_during_identity_migration(tmp_path: Path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64", b64url(public_raw))
    monkeypatch.setattr(licensing, "LICENSE_PATH", tmp_path / "Данные пользователя" / "license.json")
    monkeypatch.setattr(licensing, "TRIAL_PATH", tmp_path / "Данные пользователя" / "trial.json")
    monkeypatch.setattr(licensing, "DEVICE_ID_PATH", tmp_path / "Данные пользователя" / "device_identity.json")
    monkeypatch.setattr(licensing.platform, "system", lambda: "Windows")
    monkeypatch.setattr(licensing.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(licensing.platform, "node", lambda: "CREATOR-PC")
    monkeypatch.setattr(licensing.uuid, "getnode", lambda: 123456789)
    legacy_source = "Windows|AMD64|CREATOR-PC|123456789|highlight-studio-paid-beta-v1"
    legacy_device = hashlib.sha256(legacy_source.encode()).hexdigest()
    token = signed_token(
        private_key,
        {"license_id": "existing", "device_id": legacy_device, "issued_at": time.time(), "expires_at": time.time() + 3600},
    )
    write_json(licensing.LICENSE_PATH, {"token": token})

    assert licensing.license_status()["state"] == "active"
    assert read_json(licensing.DEVICE_ID_PATH, {})["device_id"] == legacy_device


def test_license_key_is_not_exposed_in_network_error(tmp_path: Path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    configure_client(tmp_path, monkeypatch, private_key)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_SERVER_URL", "https://license.example.test")
    secret_key = "HS-VERY-PRIVATE-LICENSE-KEY"
    monkeypatch.setattr(
        licensing.requests,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(f"request body contained {secret_key}")),
    )

    result = licensing.activate_license(secret_key)

    assert result["ok"] is False
    assert secret_key not in result["message"]


def test_trial_cannot_be_restarted_by_deleting_or_rewriting_local_state(tmp_path: Path, monkeypatch):
    client, private_key, _key = configure_server(tmp_path, monkeypatch)
    configure_client(tmp_path, monkeypatch, private_key)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_SERVER_URL", "https://license.example.test")
    monkeypatch.setattr(licensing, "_trial_can_start", lambda: True)

    monkeypatch.setattr(
        licensing.requests,
        "post",
        lambda _url, *, json, timeout: client.post("/trial/start", json=json),
    )
    start = time.time()
    licensing.start_trial(now=start)
    original = read_json(licensing.TRIAL_PATH, {})
    expired_at = float(original["expires_at"])
    assert licensing.license_status(now=start + 2 * 86400)["state"] == "trial"
    rolled_back = licensing.license_status(now=start + 3600)
    assert rolled_back["state"] == "expired"
    assert rolled_back["invalid_license_reason"] == "clock_rollback"
    assert licensing.license_status(now=expired_at)["state"] == "expired"

    licensing.TRIAL_PATH.unlink()
    licensing.start_trial(now=expired_at)
    after_delete = licensing.license_status(now=expired_at)
    assert after_delete["state"] == "expired"

    write_json(
        licensing.TRIAL_PATH,
        {"started_at": expired_at, "device_id": "d" * 64, "trial_days": 90, "last_seen_at": expired_at},
    )
    after_rewrite = licensing.license_status(now=expired_at)
    assert after_rewrite["state"] == "expired"


def test_tampered_server_trial_fails_closed_while_offline(tmp_path: Path, monkeypatch):
    client, private_key, _key = configure_server(tmp_path, monkeypatch)
    configure_client(tmp_path, monkeypatch, private_key)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_SERVER_URL", "https://license.example.test")
    monkeypatch.setattr(licensing, "_trial_can_start", lambda: True)
    monkeypatch.setattr(
        licensing.requests,
        "post",
        lambda _url, *, json, timeout: client.post("/trial/start", json=json),
    )
    assert licensing.start_trial()["ok"] is True
    saved = read_json(licensing.TRIAL_PATH, {})
    saved["token"] = corrupt_signature(saved["token"])
    write_json(licensing.TRIAL_PATH, saved)
    monkeypatch.setattr(
        licensing.requests,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("offline")),
    )

    status = licensing.license_status()

    assert status["state"] == "trial_server_unavailable"
    assert status["can_use"] is False


def test_admin_api_requires_token_and_private_key_is_not_in_desktop_config(tmp_path: Path, monkeypatch):
    client, _private_key, _key = configure_server(tmp_path, monkeypatch)
    denied = client.post("/admin/licenses", json={"customer_id": "attacker"})
    assert denied.status_code == 403
    config = json.loads((Path(__file__).parents[1] / "desktop" / "electron" / "paid-beta-channel.json").read_text(encoding="utf-8"))
    assert "licensePrivateKeyB64" not in config
    assert "adminToken" not in config
