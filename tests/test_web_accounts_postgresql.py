import os
from pathlib import Path
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.src.highlight_studio.infrastructure.database.models import Base, User
from backend.src.highlight_studio.infrastructure.auth.service import (
    register_user,
    login_user,
    create_auth_session,
    authenticate_token,
    ensure_project,
    has_project_permission,
    add_or_update_member,
)


def db(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'auth.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def subprocess_test_env(**overrides: str) -> dict[str, str]:
    """Return a UTF-8 subprocess environment that keeps Windows OS discovery intact."""
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if os.name == "nt":
        system_root = env.get("SystemRoot") or env.get("WINDIR") or f"{env.get('SystemDrive', 'C:')}\\Windows"
        env.setdefault("SystemRoot", system_root)
        env.setdefault("ComSpec", str(Path(system_root) / "System32" / "cmd.exe"))
    env.update(overrides)
    return env


def test_register_login_session(tmp_path):
    s = db(tmp_path)
    u = register_user(s, "Owner@Example.com", "Owner", "very-secure-passphrase")
    s.commit()
    assert u.global_role == "admin"
    assert login_user(s, "owner@example.com", "very-secure-passphrase").id == u.id
    token, csrf, _ = create_auth_session(s, u)
    s.commit()
    assert authenticate_token(s, token)[0].email == "owner@example.com"
    assert csrf


def test_project_roles(tmp_path):
    s = db(tmp_path)
    owner = register_user(s, "owner@example.com", "Owner", "very-secure-passphrase")
    member = User(id="member", email="member@example.com", display_name="Member", password_hash=owner.password_hash, global_role="user")
    s.add(member)
    s.flush()
    ensure_project(s, "a" * 12, "Demo", owner, "/tmp/demo")
    add_or_update_member(s, owner, "a" * 12, "member@example.com", "editor")
    s.commit()
    assert has_project_permission(s, member, "a" * 12, "viewer")
    assert has_project_permission(s, member, "a" * 12, "editor")
    assert not has_project_permission(s, member, "a" * 12, "owner")


def test_login_rate_limit_blocks_unknown_account(tmp_path):
    s = db(tmp_path)
    for _ in range(9):
        try:
            login_user(s, "missing@example.com", "wrong-password", "203.0.113.10")
        except PermissionError:
            pass
    try:
        login_user(s, "missing@example.com", "wrong-password", "203.0.113.10")
    except PermissionError as exc:
        assert "Неверный" in str(exc)
    try:
        login_user(s, "missing@example.com", "wrong-password", "203.0.113.10")
    except PermissionError as exc:
        assert "Слишком много" in str(exc)


def test_project_must_keep_an_owner(tmp_path):
    from backend.src.highlight_studio.infrastructure.auth.service import remove_member

    s = db(tmp_path)
    owner = register_user(s, "owner@example.com", "Owner", "very-secure-passphrase")
    ensure_project(s, "b" * 12, "Demo", owner, "/tmp/demo")
    s.commit()
    try:
        remove_member(s, owner, "b" * 12, owner.id)
    except ValueError as exc:
        assert "хотя бы один владелец" in str(exc)
    else:
        raise AssertionError("last owner removal must be rejected")


def test_user_preferences_are_isolated(tmp_path):
    from backend.src.highlight_studio.infrastructure.auth.service import get_user_preference, set_user_preference

    s = db(tmp_path)
    owner = register_user(s, "owner@example.com", "Owner", "very-secure-passphrase")
    second = User(id="second", email="second@example.com", display_name="Second", password_hash=owner.password_hash)
    s.add(second)
    s.flush()
    set_user_preference(s, owner.id, "best_settings", {"settings": {"crf": 20}})
    set_user_preference(s, second.id, "best_settings", {"settings": {"crf": 28}})
    s.commit()
    assert get_user_preference(s, owner.id, "best_settings")["settings"]["crf"] == 20
    assert get_user_preference(s, second.id, "best_settings")["settings"]["crf"] == 28


def test_password_reset_and_email_verification_tokens(tmp_path):
    from datetime import timedelta
    from backend.src.highlight_studio.infrastructure.auth.service import (
        issue_account_token,
        reset_password_with_token,
        verify_email_with_token,
        verify_password,
    )

    s = db(tmp_path)
    user = register_user(s, "owner@example.com", "Owner", "very-secure-passphrase")
    verify_token = issue_account_token(s, user, "verify_email", timedelta(hours=1))
    reset_token = issue_account_token(s, user, "password_reset", timedelta(hours=1))
    s.commit()
    verified = verify_email_with_token(s, verify_token)
    assert verified.email_verified is True
    reset = reset_password_with_token(s, reset_token, "new-very-secure-passphrase")
    assert verify_password(reset.password_hash, "new-very-secure-passphrase")
    s.commit()


def test_web_auth_status_and_route_guards_in_subprocess(tmp_path):
    import subprocess
    import textwrap

    script = textwrap.dedent(
        """
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.src.highlight_studio.infrastructure.database.engine import init_database

        init_database()
        with TestClient(app) as client:
            response = client.post('/api/auth/register', json={
                'email': 'owner@example.com',
                'display_name': 'Owner',
                'password': 'very-secure-password',
                'remember': True,
            })
            assert response.status_code == 200, response.text
            status = client.get('/api/auth/status')
            assert status.status_code == 200
            assert status.json()['authenticated'] is True
            csrf = client.cookies.get('highlight_studio_csrf')
            assert client.get('/api/local/roots').status_code == 404
            assert client.post('/api/auth/logout', headers={'x-csrf-token': csrf}).status_code == 200
            assert client.get('/api/auth/status').json()['authenticated'] is False
        """
    )
    env = subprocess_test_env(
        **{
            "HIGHLIGHT_STUDIO_DEPLOYMENT_MODE": "web",
            "HIGHLIGHT_STUDIO_DATABASE_URL": f"sqlite+pysqlite:///{tmp_path / 'web.db'}",
            "HIGHLIGHT_STUDIO_DATA_DIR": str(tmp_path / "data"),
            "HIGHLIGHT_STUDIO_PROJECTS_DIR": str(tmp_path / "projects"),
            "HIGHLIGHT_STUDIO_COOKIE_SECURE": "0",
            "HIGHLIGHT_STUDIO_ALLOW_REGISTRATION": "1",
            "HIGHLIGHT_STUDIO_ALLOW_SQLITE_WEB_TESTS": "1",
        },
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_web_viewer_cannot_read_server_logs_and_twitch_paths_are_redacted(tmp_path):
    import subprocess
    import textwrap

    script = textwrap.dedent(
        r"""
        from pathlib import Path
        from fastapi.testclient import TestClient
        import backend.src.highlight_studio.api.app as appmod
        from backend.main import app
        from backend.src.highlight_studio.infrastructure.database.engine import init_database, session_scope
        from backend.src.highlight_studio.infrastructure.auth.service import (
            add_or_update_member,
            create_auth_session,
            ensure_project,
            register_user,
        )

        init_database()
        project_id = 'c' * 12
        project_dir = Path(os.environ['HIGHLIGHT_STUDIO_PROJECTS_DIR']) / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / 'project.json').write_text('{"id":"' + project_id + '","name":"Demo"}', encoding='utf-8')
        (project_dir / 'logs.txt').write_text('server path C:\\Private\\video.mp4 api_key=sk-log-secret', encoding='utf-8')
        (project_dir / 'youtube_metadata.json').write_text(
            '{"title":"Safe","api_key":"sk-metadata-secret","nested":{"service_token":"nested-secret"}}',
            encoding='utf-8',
        )

        with session_scope() as db:
            owner = register_user(db, 'owner@example.com', 'Owner', 'very-secure-password')
            viewer = register_user(db, 'viewer@example.com', 'Viewer', 'very-secure-password')
            editor = register_user(db, 'editor@example.com', 'Editor', 'very-secure-password')
            ensure_project(db, project_id, 'Demo', owner, str(project_dir))
            add_or_update_member(db, owner, project_id, viewer.email, 'viewer')
            add_or_update_member(db, owner, project_id, editor.email, 'editor')
            viewer_token, _, _ = create_auth_session(db, viewer)
            editor_token, _, _ = create_auth_session(db, editor)

        appmod.twitch_tool_status = lambda settings: {
            'ok': True,
            'tool_path': r'F:\Private\TwitchDownloaderCLI.exe',
            'nested': {'storage_path': r'D:\Projects\cache'},
        }

        with TestClient(app) as client:
            client.cookies.set('highlight_studio_session', viewer_token)
            logs = client.get(f'/api/projects/{project_id}/logs')
            assert logs.status_code == 403, logs.text
            assert logs.json()['required_role'] == 'editor'
            simple = client.get(f'/api/projects/{project_id}/simple-log')
            assert simple.status_code == 403, simple.text
            dashboard = client.get(f'/api/projects/{project_id}/dashboard-state')
            assert dashboard.status_code == 200, dashboard.text
            assert dashboard.json()['logs'] == ''
            assert dashboard.json()['metadata']['api_key'] == ''
            assert dashboard.json()['metadata']['nested']['service_token'] == ''
            assert 'sk-metadata-secret' not in dashboard.text
            assert 'nested-secret' not in dashboard.text
            assert 'sk-log-secret' not in dashboard.text
            tools = client.get('/api/twitch/tools')
            assert tools.status_code == 200, tools.text
            assert tools.json()['tool_path'] == '', tools.text
            assert tools.json()['nested']['storage_path'] == '', tools.text

            client.cookies.set('highlight_studio_session', editor_token)
            editor_logs = client.get(f'/api/projects/{project_id}/logs')
            assert editor_logs.status_code == 200, editor_logs.text
            assert 'sk-log-secret' not in editor_logs.text
            assert 'C:\\Private' not in editor_logs.text, editor_logs.text
            editor_dashboard = client.get(f'/api/projects/{project_id}/dashboard-state')
            assert editor_dashboard.status_code == 200, editor_dashboard.text
            assert 'sk-log-secret' not in editor_dashboard.text
            assert '<REDACTED>' in editor_dashboard.json()['logs'], editor_dashboard.text
        """
    )
    env = subprocess_test_env(
        **{
            "HIGHLIGHT_STUDIO_DEPLOYMENT_MODE": "web",
            "HIGHLIGHT_STUDIO_DATABASE_URL": f"sqlite+pysqlite:///{tmp_path / 'web-privacy.db'}",
            "HIGHLIGHT_STUDIO_DATA_DIR": str(tmp_path / "data"),
            "HIGHLIGHT_STUDIO_PROJECTS_DIR": str(tmp_path / "projects"),
            "HIGHLIGHT_STUDIO_COOKIE_SECURE": "0",
            "HIGHLIGHT_STUDIO_ALLOW_REGISTRATION": "1",
            "HIGHLIGHT_STUDIO_ALLOW_SQLITE_WEB_TESTS": "1",
        },
    )
    result = subprocess.run(
        [sys.executable, "-c", "import os\n" + script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_database_requires_migrations_when_auto_create_is_disabled(tmp_path):
    import subprocess

    env = subprocess_test_env(
        **{
            "HIGHLIGHT_STUDIO_DEPLOYMENT_MODE": "web",
            "HIGHLIGHT_STUDIO_DATABASE_URL": f"sqlite+pysqlite:///{tmp_path / 'unmigrated.db'}",
            "HIGHLIGHT_STUDIO_AUTO_CREATE_DATABASE": "0",
            "HIGHLIGHT_STUDIO_ALLOW_SQLITE_WEB_TESTS": "1",
            "HIGHLIGHT_STUDIO_DATA_DIR": str(tmp_path / "data"),
            "HIGHLIGHT_STUDIO_PROJECTS_DIR": str(tmp_path / "projects"),
        },
    )
    code = """
from backend.src.highlight_studio.infrastructure.database.engine import init_database
try:
    init_database()
except RuntimeError as exc:
    assert 'alembic' in str(exc).lower()
else:
    raise AssertionError('unmigrated production database must be rejected')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_database_role_constraints_reject_invalid_values(tmp_path):
    import pytest
    from sqlalchemy.exc import IntegrityError
    from backend.src.highlight_studio.infrastructure.database.models import ProjectMembership

    s = db(tmp_path)
    owner = register_user(s, "owner@example.com", "Owner", "very-secure-passphrase")
    ensure_project(s, "d" * 12, "Demo", owner, "/tmp/demo")
    s.add(ProjectMembership(id="invalid", project_id="d" * 12, user_id=owner.id, role="superuser"))
    with pytest.raises(IntegrityError):
        s.commit()


def test_session_management_lists_and_revokes_devices(tmp_path):
    from backend.src.highlight_studio.infrastructure.auth.service import (
        list_user_sessions,
        revoke_all_user_sessions,
        revoke_session_by_id,
    )

    s = db(tmp_path)
    user = register_user(s, "sessions@example.com", "Sessions", "very-secure-passphrase")
    _, _, first = create_auth_session(s, user, "Browser One", "127.0.0.1", True)
    _, _, second = create_auth_session(s, user, "Browser Two", "127.0.0.2", True)
    s.commit()
    assert {row.id for row in list_user_sessions(s, user.id)} == {first.id, second.id}
    assert revoke_session_by_id(s, user.id, second.id)
    s.commit()
    assert [row.id for row in list_user_sessions(s, user.id)] == [first.id]
    assert revoke_all_user_sessions(s, user.id) == 1
    s.commit()
    assert list_user_sessions(s, user.id) == []


def test_web_mode_refuses_sqlite_without_explicit_test_override(tmp_path):
    import subprocess

    env = subprocess_test_env(
        **{
            "HIGHLIGHT_STUDIO_DEPLOYMENT_MODE": "web",
            "HIGHLIGHT_STUDIO_DATABASE_URL": f"sqlite+pysqlite:///{tmp_path / 'forbidden.db'}",
            "HIGHLIGHT_STUDIO_DATA_DIR": str(tmp_path / "data"),
            "HIGHLIGHT_STUDIO_PROJECTS_DIR": str(tmp_path / "projects"),
        },
    )
    env.pop("HIGHLIGHT_STUDIO_ALLOW_SQLITE_WEB_TESTS", None)
    result = subprocess.run(
        [sys.executable, "-c", "from backend.src.highlight_studio.core import settings"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "PostgreSQL" in result.stdout + result.stderr
