from __future__ import annotations

import base64
import csv
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from backend.src.highlight_studio.infrastructure import crash_reporting, paid_beta, release_gate, runtime_state
from tools.quality.evaluate_vod_benchmark import evaluate


def test_crash_report_redacts_paths_email_and_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_reporting, "CRASH_QUEUE_PATH", tmp_path / "crashes.jsonl")
    result = crash_reporting.record_crash(
        source="backend",
        error_type="RuntimeError",
        message=r"token=abc person@example.com D:\Private Videos\clip.mp4 /home/name/private.mov",
        context={"route": "/api/test", "private": "must not pass"},
    )
    assert result["crash_id"]
    text = (tmp_path / "crashes.jsonl").read_text(encoding="utf-8")
    assert "person@example.com" not in text
    assert "Private Videos" not in text
    assert "/home/name" not in text
    assert "abc" not in text
    assert "private" not in result["context"]


def test_crash_upload_requires_consent_and_https(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_reporting, "CRASH_QUEUE_PATH", tmp_path / "crashes.jsonl")
    monkeypatch.setattr(paid_beta, "PRIVACY_PATH", tmp_path / "privacy.json")
    monkeypatch.setattr(runtime_state, "ONBOARDING_PATH", tmp_path / "onboarding.json")
    crash_reporting.record_crash(source="backend", error_type="Error", message="boom")
    monkeypatch.setenv("HIGHLIGHT_STUDIO_CRASH_REPORT_URL", "http://unsafe.example")
    result = crash_reporting.flush_crash_reports()
    assert result["uploaded"] == 0
    assert result["queued_reports"] == 1


def test_benchmark_evaluator_computes_release_metrics(tmp_path):
    path = tmp_path / "benchmark.csv"
    fields = [
        "vod_id",
        "category",
        "duration_hours",
        "ground_truth_moments",
        "candidates_hit",
        "candidates_total",
        "candidates_usable",
        "duplicate_candidates",
        "cut_context_candidates",
        "render_ok",
        "project_ok",
        "time_saved_percent",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "vod_id": "1",
                "category": "gaming",
                "duration_hours": "2",
                "ground_truth_moments": "10",
                "candidates_hit": "8",
                "candidates_total": "20",
                "candidates_usable": "14",
                "duplicate_candidates": "1",
                "cut_context_candidates": "1",
                "render_ok": "yes",
                "project_ok": "yes",
                "time_saved_percent": "60",
            }
        )
    report = evaluate(path)
    assert report["vod_count"] == 1
    assert report["moment_recall_percent"] == 80.0
    assert report["usable_candidates_percent"] == 70.0
    assert report["duplicate_candidates_percent"] == 5.0
    assert report["median_time_saved_percent"] == 60.0


def test_benchmark_template_placeholders_do_not_count_as_vods(tmp_path: Path) -> None:
    path = tmp_path / "benchmark.csv"
    path.write_text(
        "vod_id,category,duration_hours,ground_truth_moments,candidates_hit,candidates_total,"
        "candidates_usable,duplicate_candidates,cut_context_candidates,render_ok,project_ok,time_saved_percent\n"
        "VOD-001,,,,,,,,,,,\n"
        "VOD-002,gaming,2,10,8,20,14,1,1,yes,yes,60\n",
        encoding="utf-8",
    )
    report = evaluate(path)
    assert report["vod_count"] == 1
    assert report["moment_recall_percent"] == 80.0


def test_release_gate_blocks_missing_external_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(release_gate, "EVIDENCE_PATH", tmp_path / "evidence.json")
    monkeypatch.setattr(release_gate, "BENCHMARK_REPORT_PATH", tmp_path / "benchmark.json")
    for env in release_gate.URL_ENV.values():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv("HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64", raising=False)
    report = release_gate.release_readiness()
    assert report["ready_for_mass_release"] is False
    assert report["blocker_count"] > 0
    assert any(item["id"] == "evidence_signed" and not item["ok"] for item in report["checks"])


def test_release_gate_accepts_complete_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(release_gate, "EVIDENCE_PATH", tmp_path / "evidence.json")
    monkeypatch.setattr(release_gate, "BENCHMARK_REPORT_PATH", tmp_path / "benchmark.json")
    key = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64", base64.urlsafe_b64encode(key).decode().rstrip("="))
    for env in release_gate.URL_ENV.values():
        monkeypatch.setenv(env, "https://release.example.test/service")
    benchmark = {
        "vod_count": 30,
        "moment_recall_percent": 82,
        "usable_candidates_percent": 68,
        "duplicate_candidates_percent": 4,
        "cut_context_percent": 8,
        "render_success_percent": 100,
        "project_success_percent": 100,
        "median_time_saved_percent": 55,
    }
    release_gate.BENCHMARK_REPORT_PATH.write_text(json.dumps(benchmark), encoding="utf-8")
    release_gate.EVIDENCE_PATH.write_text(
        json.dumps(
            {
                "windows_devices_tested": 12,
                "production_sessions": 200,
                "crashed_sessions": 1,
                "artifacts_signed": True,
                "installer_verified": True,
                "update_verified": True,
            }
        ),
        encoding="utf-8",
    )
    report = release_gate.release_readiness()
    assert report["ready_for_mass_release"] is True
    assert report["blocker_count"] == 0


def test_reference_license_server_activation(tmp_path, monkeypatch):
    from services.license_server import app as service

    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_PRIVATE_KEY_B64", base64.urlsafe_b64encode(raw).decode().rstrip("="))
    monkeypatch.setenv("HIGHLIGHT_STUDIO_LICENSE_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr(service, "DB_PATH", tmp_path / "licenses.sqlite3")
    service.init_db()
    client = TestClient(service.app)
    created = client.post(
        "/admin/licenses",
        headers={"Authorization": "Bearer admin-secret"},
        json={"customer_id": "customer-1", "days": 30, "max_devices": 1},
    )
    assert created.status_code == 200
    key = created.json()["license_key"]
    activated = client.post("/activate", json={"license_key": key, "device_id": "d" * 64, "app_version": "v10.14.4"})
    assert activated.status_code == 200
    assert activated.json()["token"].startswith("HSB1.")
    second = client.post("/activate", json={"license_key": key, "device_id": "e" * 64, "app_version": "v10.14.4"})
    assert second.status_code == 409


def test_frontend_crash_endpoint_records_sanitized_report(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.src.highlight_studio.api import app as api_module

    monkeypatch.setattr(crash_reporting, "CRASH_QUEUE_PATH", tmp_path / "crashes.jsonl")
    client = TestClient(api_module.app)
    response = client.post(
        "/api/crash-reports/frontend",
        headers={"X-Local-Token": api_module.LOCAL_AUTH_TOKEN},
        json={"message": r"Failed D:\\Private\\clip.mp4", "stack": "person@example.com", "route": "/settings"},
    )
    assert response.status_code == 200
    text = (tmp_path / "crashes.jsonl").read_text(encoding="utf-8")
    assert "Private" not in text
    assert "person@example.com" not in text


def test_mass_release_cli_runs_directly(tmp_path: Path) -> None:
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "tools" / "release" / "check_mass_release.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["app_version"].startswith("v11.2.7")
    assert payload["ready_for_mass_release"] is False
