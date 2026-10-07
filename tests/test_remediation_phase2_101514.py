from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.src.highlight_studio.infrastructure.database.models import AccountToken, Base
from backend.src.highlight_studio.infrastructure.auth.service import (
    enforce_public_auth_request_rate_limit,
    issue_account_token,
    register_user,
)


def _db(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'auth.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_public_recovery_rate_limit_is_persistent_and_bounded(tmp_path):
    db = _db(tmp_path)
    for _ in range(5):
        enforce_public_auth_request_rate_limit(db, "password_reset", "missing@example.com", "203.0.113.9")
        db.commit()
    with pytest.raises(PermissionError):
        enforce_public_auth_request_rate_limit(db, "password_reset", "missing@example.com", "203.0.113.9")


def test_new_account_token_does_not_invalidate_recent_valid_link(tmp_path):
    db = _db(tmp_path)
    user = register_user(db, "owner@example.com", "Owner", "very-secure-passphrase")
    first = issue_account_token(db, user, "password_reset", timedelta(hours=1))
    second = issue_account_token(db, user, "password_reset", timedelta(hours=1))
    db.commit()
    # Tokens are stored hashed, so the important regression is that two active
    # rows exist. The exact raw values merely prove a new link was issued.
    assert first != second
    active = db.query(AccountToken).filter_by(user_id=user.id, purpose="password_reset", used_at=None).all()
    assert len(active) == 2


def test_review_action_unknown_is_4xx_and_legacy_path_uses_segment_lock(tmp_path, monkeypatch):
    import backend.src.highlight_studio.api.app as appmod

    project_id = "a" * 12
    d = tmp_path / project_id
    d.mkdir()
    (d / "project.json").write_text('{"id":"' + project_id + '","settings":{}}', encoding="utf-8")
    (d / "segments.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(appmod, "PROJECTS_DIR", tmp_path)
    client = TestClient(appmod.app)
    headers = {"x-local-token": appmod.LOCAL_AUTH_TOKEN}
    r = client.post(f"/api/projects/{project_id}/review-action", json={"action": "bogus"}, headers=headers)
    assert r.status_code == 422
    src = Path(appmod.__file__).read_text(encoding="utf-8")
    fn = src[src.index('def review_action'):src.index('@app.post("/api/twitch/probe")')]
    assert "segment_mutation_locks" in fn


def test_clear_cache_reports_locked_delete_failure(tmp_path, monkeypatch):
    import backend.src.highlight_studio.api.app as appmod

    project_id = "b" * 12
    d = tmp_path / project_id
    d.mkdir()
    (d / "project.json").write_text('{"id":"' + project_id + '","settings":{}}', encoding="utf-8")
    frames = d / "frames"
    frames.mkdir()
    (frames / "x.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(appmod, "PROJECTS_DIR", tmp_path)
    original = appmod.shutil.rmtree

    def locked(path, *args, **kwargs):
        if Path(path).name == "frames":
            raise PermissionError("locked")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(appmod.shutil, "rmtree", locked)
    client = TestClient(appmod.app)
    r = client.post(f"/api/projects/{project_id}/clear-cache", headers={"x-local-token": appmod.LOCAL_AUTH_TOKEN})
    assert r.status_code == 409
    assert frames.exists()
    detail = r.json()["detail"]
    assert detail["failed"][0]["path"] == "frames"


def test_render_diagnostic_is_rewritten_to_canonical_path():
    from backend.src.highlight_studio.services import pipeline

    src = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert 'check["file"] = str(final)' in src
    assert 'write_json(project_dir / "last_result_check.json", check)' in src


def test_web_twitch_browser_cookie_credentials_are_forced_off():
    import backend.src.highlight_studio.api.app as appmod

    src = Path(appmod.__file__).read_text(encoding="utf-8")
    assert '"cookies_browser": "none" if WEB_ACCOUNTS_ENABLED' in src


def test_web_generic_file_endpoint_restricts_internal_files():
    import backend.src.highlight_studio.api.app as appmod

    src = Path(appmod.__file__).read_text(encoding="utf-8")
    section = src[src.index('def get_file('):]
    assert 'public_roots = ("outputs/", "preview/", "shorts/", "hls/", "exports/")' in section
    assert 'role not in {"editor", "owner"}' in section


def test_frontend_source_has_project_scope_guards_for_preset_and_access():
    root = Path(__file__).resolve().parents[1]
    src = (root / "frontend/src/app/App.jsx").read_text(encoding="utf-8")
    hardware = src[src.index("async function applyHardwarePreset"):src.index("async function browseLocal")]
    task = src[src.index("async function applyTaskPreset"):src.index("function prepareOneClickSettings")]
    assert "captureProjectScope(projectId)" in hardware and "isProjectScopeCurrent(scope)" in hardware
    assert "captureProjectScope(projectId)" in task and "isProjectScopeCurrent(scope)" in task
    access = src[src.index("setReviewVisibleCount(60)"):src.index("setYoutubeForm({")]
    assert "AbortController" in access and "isProjectScopeCurrent(scope)" in access


def test_new_project_only_carries_explicit_intent_settings():
    root = Path(__file__).resolve().parents[1]
    src = (root / "frontend/src/app/App.jsx").read_text(encoding="utf-8")
    section = src[src.index("async function applyCurrentSettingsToNewProject"):src.index("function activateNewProject")]
    assert "intentKeys" in section
    assert "custom_prompt" not in section
    assert "ai_batch_size" not in section
