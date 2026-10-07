from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from backend.src.highlight_studio.api import app as api_module
from backend.src.highlight_studio.infrastructure import migrations, runtime_state, support_bundle
from backend.src.highlight_studio.core.utils import read_json, write_json


def auth_headers() -> dict[str, str]:
    return {"X-Local-Token": api_module.LOCAL_AUTH_TOKEN}


def test_project_migration_is_idempotent_and_creates_schema(tmp_path, monkeypatch):
    project_dir = tmp_path / "legacy"
    project_dir.mkdir()
    write_json(project_dir / "project.json", {"name": "Legacy", "settings": None, "source_video_path": 123})
    monkeypatch.setattr(migrations, "MIGRATION_STATUS_PATH", tmp_path / "migration_status.json")

    first = migrations.migrate_all_projects(tmp_path)
    second = migrations.migrate_all_projects(tmp_path)
    project = read_json(project_dir / "project.json", {})

    assert first["ok"] is True
    assert first["migrated_count"] == 1
    assert second["migrated_count"] == 0
    assert project["project_schema_version"] == migrations.CURRENT_PROJECT_SCHEMA_VERSION
    assert isinstance(project["settings"], dict)
    assert project["source_video_path"] == "123"
    assert len(project["migration_history"]) == migrations.CURRENT_PROJECT_SCHEMA_VERSION


def test_runtime_recovery_marks_interrupted_project_and_clean_shutdown(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    projects_dir = tmp_path / "projects"
    project_dir = projects_dir / "p1"
    data_dir.mkdir()
    project_dir.mkdir(parents=True)
    write_json(project_dir / "project.json", {"id": "p1", "settings": {}})
    write_json(project_dir / "status.json", {"state": "running", "stage": "render", "message": "busy"})
    monkeypatch.setattr(runtime_state, "SESSION_STATE_PATH", data_dir / "session.json")
    monkeypatch.setattr(runtime_state, "RECOVERY_REPORT_PATH", data_dir / "recovery.json")

    report = runtime_state.begin_runtime_session(projects_dir)
    status = read_json(project_dir / "status.json", {})
    assert report["recovered_count"] == 1
    assert status["state"] == "interrupted"
    assert status["recoverable"] is True

    runtime_state.mark_clean_shutdown()
    assert read_json(data_dir / "session.json", {})["clean_shutdown"] is True


def test_onboarding_endpoints_and_support_bundle_exclude_sensitive_media(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_state, "ONBOARDING_PATH", tmp_path / "onboarding.json")
    monkeypatch.setattr(support_bundle, "SUPPORT_DIR", tmp_path / "support")
    monkeypatch.setattr(support_bundle, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(support_bundle, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(support_bundle, "DATA_DIR", tmp_path / "data")
    (tmp_path / "projects").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "logs" / "backend.log").write_text("token=supersecret\npath=" + str(Path.home()), encoding="utf-8")

    client = TestClient(api_module.app)
    before = client.get("/api/onboarding", headers=auth_headers())
    assert before.status_code == 200
    assert before.json()["completed"] is False

    completed = client.post(
        "/api/onboarding/complete",
        json={"ai_mode": "local", "telemetry_enabled": False, "accepted_privacy": True, "accepted_terms": True},
        headers=auth_headers(),
    )
    assert completed.status_code == 200
    assert completed.json()["completed"] is True

    bundle = support_bundle.build_support_bundle(system_check={"ok": True})
    assert bundle.exists()
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert "summary.json" in names
        assert not any(name.endswith((".mp4", ".mkv", ".wav", "transcript.json")) for name in names)
        log_text = archive.read("logs/backend.log").decode("utf-8")
        assert "supersecret" not in log_text
        assert str(Path.home()) not in log_text
        assert "<REDACTED>" in log_text


def test_desktop_shutdown_endpoint_requires_loopback_secret(monkeypatch):
    calls: list[str] = []
    monkeypatch.setenv("HIGHLIGHT_STUDIO_DESKTOP_SHUTDOWN_TOKEN", "release-candidate-secret")
    api_module.app.state.desktop_shutdown_callback = lambda: calls.append("shutdown")
    client = TestClient(api_module.app, client=("127.0.0.1", 51000))

    denied = client.post("/api/desktop/shutdown", headers={"X-Desktop-Shutdown-Token": "wrong"})
    assert denied.status_code == 403

    accepted = client.post(
        "/api/desktop/shutdown",
        headers={"X-Desktop-Shutdown-Token": "release-candidate-secret"},
    )
    assert accepted.status_code == 200
    deadline = time.monotonic() + 1.0
    while not calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert calls == ["shutdown"]


def test_release_candidate_files_declare_updater_and_first_run_wizard():
    root = Path(__file__).resolve().parents[1]
    package = json.loads((root / "desktop/electron/package.json").read_text(encoding="utf-8"))
    assert package["version"] == "11.2.7"
    assert package["dependencies"]["electron-updater"]
    assert (root / "frontend/src/features/onboarding/FirstRunWizard.jsx").exists()
    assert (root / "scripts/windows/write_update_manifest.ps1").exists()
    assert (root / "scripts/windows/verify_windows_artifacts.ps1").exists()
    main_js = (root / "desktop/electron/main.js").read_text(encoding="utf-8")
    assert "HIGHLIGHT_STUDIO_DESKTOP_SHUTDOWN_TOKEN" in main_js
    assert "requestGracefulBackendShutdown" in main_js


def test_update_manifest_uses_sha512_and_selects_nsis_installer(tmp_path):
    from tools.desktop.write_update_manifest import sha512_base64, write_manifest

    installer = tmp_path / "Highlight-Studio-11.2.7-x64.exe"
    portable = tmp_path / "Highlight-Studio-Portable-11.2.7-x64.exe"
    installer.write_bytes(b"signed-installer-placeholder")
    portable.write_bytes(b"portable-placeholder")

    manifest = write_manifest(tmp_path, "11.2.7")
    text = manifest.read_text(encoding="utf-8")
    assert "version: 11.2.7" in text
    assert installer.name in text
    assert portable.name not in text
    assert sha512_base64(installer) in text
    assert f"size: {installer.stat().st_size}" in text


def test_support_bundle_redacts_nested_paths_and_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(support_bundle, "SUPPORT_DIR", tmp_path / "support")
    monkeypatch.setattr(support_bundle, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(support_bundle, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(support_bundle, "DATA_DIR", tmp_path / "data")
    (tmp_path / "projects").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    bundle = support_bundle.build_support_bundle(
        system_check={
            "ok": True,
            "checks": {"ffmpeg": {"path": str(Path.home() / "tools" / "ffmpeg.exe")}},
            "api_key": "secret-value",
        }
    )
    with zipfile.ZipFile(bundle) as archive:
        payload = json.loads(archive.read("system_check.json"))
    assert payload["api_key"] == "<REDACTED>"
    assert str(Path.home()) not in payload["checks"]["ffmpeg"]["path"]
    assert payload["checks"]["ffmpeg"]["path"].startswith("<USER_HOME>")


def test_windows_release_pipeline_requires_pinned_update_and_verification_tools():
    root = Path(__file__).resolve().parents[1]
    build_script = (root / "scripts/windows/build_hybrid_release.ps1").read_text(encoding="utf-8")
    workflow = (root / ".github/workflows/windows-desktop-build.yml").read_text(encoding="utf-8")
    package = json.loads((root / "desktop/electron/package.json").read_text(encoding="utf-8"))

    assert "HIGHLIGHT_STUDIO_FFMPEG_SHA256" in build_script
    assert "HIGHLIGHT_STUDIO_FFMPEG_URL" in build_script
    assert "verify_windows_artifacts.ps1" in build_script
    assert "write_update_manifest.py" in build_script
    assert "verify_engine_runtime.py" in build_script
    assert (root / "tools/desktop/verify_engine_runtime.py").exists()
    assert "HIGHLIGHT_STUDIO_UPDATE_URL" in workflow
    assert "WINDOWS_SIGN_PFX_BASE64" in workflow
    assert "HIGHLIGHT_STUDIO_SIGN_PFX_PATH" in build_script
    assert "$env:CSC_LINK = $env:HIGHLIGHT_STUDIO_SIGN_PFX_PATH" in build_script
    assert "release-channel.json" in package["build"]["files"]


def test_support_bundle_redacts_external_drive_paths_and_emails(tmp_path, monkeypatch):
    monkeypatch.setattr(support_bundle, "SUPPORT_DIR", tmp_path / "support")
    monkeypatch.setattr(support_bundle, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(support_bundle, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(support_bundle, "DATA_DIR", tmp_path / "data")
    (tmp_path / "projects").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    bundle = support_bundle.build_support_bundle(
        system_check={
            "windows_source": r"D:\Private Videos\client.mp4",
            "linux_source": "/mnt/external/private/client.mp4",
            "contact": "person@example.com",
        }
    )
    with zipfile.ZipFile(bundle) as archive:
        payload = archive.read("system_check.json").decode("utf-8")
    assert "Private Videos" not in payload
    assert "/mnt/external" not in payload
    assert "person@example.com" not in payload
    assert "<LOCAL_PATH>" in payload
    assert "<EMAIL_REDACTED>" in payload
