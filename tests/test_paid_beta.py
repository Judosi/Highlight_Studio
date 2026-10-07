from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.src.highlight_studio.api import app as api_module
from backend.src.highlight_studio.infrastructure import licensing, paid_beta, runtime_state
from backend.src.highlight_studio.core.utils import write_json


def auth_headers() -> dict[str, str]:
    return {"X-Local-Token": api_module.LOCAL_AUTH_TOKEN}


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def signed_token(private_key: Ed25519PrivateKey, payload: dict) -> str:
    encoded = b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signed = f"HSB1.{encoded}".encode("ascii")
    return f"HSB1.{encoded}.{b64url(private_key.sign(signed))}"


def configure_license_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(licensing, "LICENSE_PATH", tmp_path / "license.json")
    monkeypatch.setattr(licensing, "TRIAL_PATH", tmp_path / "trial.json")
    monkeypatch.setattr(licensing, "device_id", lambda: "device-hash")


def test_trial_is_created_and_expires(tmp_path, monkeypatch):
    configure_license_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_TRIAL_DAYS", "14")
    licensing.start_trial(now=1_000_000)
    status = licensing.license_status(now=1_000_000)
    assert status["state"] == "trial"
    assert status["can_use"] is True
    assert status["days_remaining"] == 14
    expired = licensing.license_status(now=1_000_000 + 15 * 86400)
    assert expired["state"] == "expired"
    assert expired["can_use"] is False


def test_trial_does_not_start_before_legal_onboarding(tmp_path, monkeypatch):
    configure_license_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(licensing, "_trial_can_start", lambda: False)
    status = licensing.license_status(now=1_000_000)
    assert status["state"] == "not_started"
    assert status["days_remaining"] == 14
    assert not (tmp_path / "trial.json").exists()


def test_offline_signed_license_activation(tmp_path, monkeypatch):
    configure_license_paths(tmp_path, monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64", b64url(public_raw))
    now = time.time()
    token = signed_token(
        private_key,
        {
            "license_id": "founder-001",
            "plan": "paid_beta",
            "device_id": "device-hash",
            "issued_at": now,
            "expires_at": now + 30 * 86400,
            "entitlements": ["analysis", "render"],
        },
    )
    result = licensing.activate_license(token)
    assert result["ok"] is True
    assert result["state"] == "active"
    assert licensing.entitlement_allowed("analysis") is True
    assert licensing.entitlement_allowed("shorts") is False


def test_offline_license_uses_bundled_config_in_portable_mode(tmp_path, monkeypatch):
    configure_license_paths(tmp_path, monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    config_dir = tmp_path / "desktop" / "electron"
    config_dir.mkdir(parents=True)
    (config_dir / "paid-beta-channel.json").write_text(
        json.dumps({"licensePublicKeyB64": b64url(public_raw), "trialDays": 21}),
        encoding="utf-8",
    )
    monkeypatch.delenv("HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64", raising=False)
    monkeypatch.delenv("HIGHLIGHT_STUDIO_TRIAL_DAYS", raising=False)
    monkeypatch.setattr(licensing, "APP_ROOT", tmp_path)
    token = signed_token(
        private_key,
        {
            "license_id": "portable-owner",
            "plan": "paid_beta",
            "device_id": "device-hash",
            "issued_at": time.time(),
            "expires_at": time.time() + 30 * 86400,
            "entitlements": ["analysis", "render", "shorts", "twitch", "metadata_ai"],
        },
    )
    result = licensing.activate_license(token)
    assert result["ok"] is True
    assert result["state"] == "active"
    assert licensing._trial_days() == 21


def test_invalid_device_license_is_rejected(tmp_path, monkeypatch):
    configure_license_paths(tmp_path, monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64", b64url(public_raw))
    token = signed_token(private_key, {"license_id": "x", "device_id": "other", "expires_at": time.time() + 3600})
    result = licensing.activate_license(token)
    assert result["ok"] is False
    assert "wrong_device" in result["message"]


def test_paid_operation_gate_blocks_expired_license(monkeypatch):
    monkeypatch.setattr(api_module, "license_status", lambda: {"can_use": False, "message": "expired", "entitlements": []})
    monkeypatch.setattr(api_module, "entitlement_allowed", lambda _name: False)
    with pytest.raises(HTTPException) as exc:
        api_module.require_paid_beta_entitlement("analysis")
    assert exc.value.status_code == 402


def test_privacy_consent_controls_telemetry_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(paid_beta, "PRIVACY_PATH", tmp_path / "privacy.json")
    monkeypatch.setattr(paid_beta, "TELEMETRY_QUEUE_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(paid_beta, "IDENTITY_PATH", tmp_path / "identity.json")
    monkeypatch.setattr(runtime_state, "ONBOARDING_PATH", tmp_path / "onboarding.json")
    assert paid_beta.record_event("app_started", {"platform": "linux"}) is False
    paid_beta.save_privacy_preferences(
        telemetry_enabled=True,
        crash_reports_enabled=False,
        accepted_privacy=True,
        accepted_terms=True,
    )
    assert paid_beta.record_event("job_failed", {"kind": "analyze", "error_code": "abc", "path": "/secret"}) is True
    text = (tmp_path / "events.jsonl").read_text()
    assert "/secret" not in text
    assert '"path"' not in text
    assert "abc" in text


def test_commerce_config_accepts_only_https(monkeypatch):
    monkeypatch.setenv("HIGHLIGHT_STUDIO_CHECKOUT_URL", "http://payments.example.test")
    monkeypatch.setenv("HIGHLIGHT_STUDIO_SUPPORT_URL", "https://support.example.test/help")
    data = paid_beta.commerce_config()
    assert data["configured"] is False
    assert data["checkout_url"] == ""
    assert data["support_url"].startswith("https://")


def test_beta_metrics_are_content_free(tmp_path):
    project = tmp_path / "p1"
    (project / "outputs").mkdir(parents=True)
    write_json(project / "project.json", {"id": "p1", "name": "private-title"})
    write_json(project / "status.json", {"state": "done"})
    write_json(project / "candidates.json", [{"text": "private transcript"}, {"text": "x"}])
    write_json(project / "segments.json", [{"start": 0, "end": 10}])
    write_json(project / "user_preferences.json", {"feedback": [{"label": "good"}]})
    (project / "outputs" / "highlight_final.mp4").write_bytes(b"x")
    result = paid_beta.beta_metrics(tmp_path)
    assert result["projects"] == 1
    assert result["candidates"] == 2
    assert result["final_segments"] == 1
    assert result["render_outputs"] == 1
    assert "private-title" not in json.dumps(result)
    assert "private transcript" not in json.dumps(result)


def test_paid_beta_api_status_and_legal_onboarding(tmp_path, monkeypatch):
    configure_license_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_state, "ONBOARDING_PATH", tmp_path / "onboarding.json")
    monkeypatch.setattr(paid_beta, "PRIVACY_PATH", tmp_path / "privacy.json")
    client = TestClient(api_module.app)
    denied = client.post(
        "/api/onboarding/complete",
        json={"ai_mode": "local", "telemetry_enabled": False, "accepted_privacy": False, "accepted_terms": False},
        headers=auth_headers(),
    )
    assert denied.status_code == 400
    completed = client.post(
        "/api/onboarding/complete",
        json={"ai_mode": "local", "telemetry_enabled": False, "accepted_privacy": True, "accepted_terms": True},
        headers=auth_headers(),
    )
    assert completed.status_code == 200
    assert completed.json()["completed"] is True
    status = client.get("/api/license/status", headers=auth_headers())
    assert status.status_code == 200
    assert status.json()["state"] == "trial"


def test_paid_beta_release_files_and_build_contract():
    root = Path(__file__).resolve().parents[1]
    package = json.loads((root / "desktop/electron/package.json").read_text(encoding="utf-8"))
    assert package["version"] == "11.2.7"
    assert "paid-beta-channel.json" in package["build"]["files"]
    build_script = (root / "scripts/windows/build_hybrid_release.ps1").read_text(encoding="utf-8")
    assert "HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64" in build_script
    assert "HIGHLIGHT_STUDIO_CHECKOUT_URL" in build_script
    assert "HIGHLIGHT_STUDIO_PRIVACY_URL" in build_script
    assert "HIGHLIGHT_STUDIO_TERMS_URL" in build_script
    assert "--require-paid-beta-config" in build_script
    assert (root / "tools/licensing/generate_keypair.py").exists()
    assert (root / "tools/licensing/sign_license.py").exists()


def test_signed_license_rejects_non_object_payload(tmp_path, monkeypatch):
    configure_license_paths(tmp_path, monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64", b64url(public_raw))
    token = signed_token(private_key, ["not", "an", "object"])
    assert licensing.verify_license_token(token)["reason"] == "invalid_payload"


def test_signed_license_filters_unknown_entitlements(tmp_path, monkeypatch):
    configure_license_paths(tmp_path, monkeypatch)
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64", b64url(public_raw))
    token = signed_token(
        private_key,
        {
            "license_id": "entitlements",
            "device_id": "device-hash",
            "expires_at": time.time() + 3600,
            "entitlements": ["analysis", "admin", "analysis"],
        },
    )
    result = licensing.verify_license_token(token)
    assert result["ok"] is True
    assert result["payload"]["entitlements"] == ["analysis"]


def test_telemetry_flush_preserves_events_after_first_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(paid_beta, "PRIVACY_PATH", tmp_path / "privacy.json")
    monkeypatch.setattr(paid_beta, "TELEMETRY_QUEUE_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(paid_beta, "IDENTITY_PATH", tmp_path / "identity.json")
    monkeypatch.setattr(runtime_state, "ONBOARDING_PATH", tmp_path / "onboarding.json")
    monkeypatch.setenv("HIGHLIGHT_STUDIO_TELEMETRY_URL", "https://telemetry.example.test/events")
    paid_beta.save_privacy_preferences(
        telemetry_enabled=True,
        crash_reports_enabled=False,
        accepted_privacy=True,
        accepted_terms=True,
    )
    for index in range(503):
        paid_beta.record_event("job_completed", {"kind": "render", "result": index})

    uploaded = []

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(_url, *, json, timeout):
        assert timeout == 15
        uploaded.extend(json["events"])
        return Response()

    monkeypatch.setattr(paid_beta.requests, "post", fake_post)
    result = paid_beta.flush_telemetry()
    assert result["uploaded"] == 500
    assert result["queued_events"] == 3
    assert len(uploaded) == 500
    assert len(paid_beta._read_jsonl(tmp_path / "events.jsonl", 1000)) == 3
