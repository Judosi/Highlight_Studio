from __future__ import annotations

import socket
from pathlib import Path

from tools.diagnostics.find_free_port import available
from tools.diagnostics.prepare_local_web_env import ensure_env, parse_env


ROOT = Path(__file__).resolve().parents[1]


def test_root_launchers_are_safe_and_explicit():
    launcher = (ROOT / "START_HERE.bat").read_text(encoding="utf-8")
    advanced = (ROOT / "commands/launch/ADVANCED_START_OPTIONS.bat").read_text(encoding="utf-8")
    assert "scripts\\windows\\run_windows.bat" in launcher
    assert "Do not run START_HERE.bat directly inside the ZIP archive" in launcher
    assert "pause >nul" in launcher
    assert "START_WEB_LOCAL.bat" in advanced
    assert "STOP_WEB_LOCAL.bat" in advanced
    assert (ROOT / "commands/launch/START_DESKTOP.bat").exists()
    assert (ROOT / "commands/launch/START_WEB_LOCAL.bat").exists()
    assert (ROOT / "commands/launch/STOP_WEB_LOCAL.bat").exists()


def test_desktop_launcher_verifies_the_exact_release_before_opening_browser():
    text = (ROOT / "scripts/windows/run_windows.bat").read_text(encoding="utf-8")
    required = [
        "HIGHLIGHT_STUDIO_APP_ROOT=%CD%",
        "verify_release_identity.py",
        "select_local_port.py 8154 8199",
        "v11.2.7-quality-recovery-audit",
        "studio-audited-v15",
        "wait_and_open.py",
    ]
    for marker in required:
        assert marker in text


def test_running_version_checker_requires_backend_and_frontend_identity():
    text = (ROOT / "commands/diagnostics/VERIFY_RUNNING_VERSION.bat").read_text(encoding="utf-8")
    assert "/api/health" in text
    assert "/release.json" in text
    assert "v11.2.7-quality-recovery-audit" in text
    assert "studio-audited-v15" in text


def test_local_web_launcher_enables_postgres_accounts_and_migrations():
    text = (ROOT / "scripts/windows/run_web_local.bat").read_text(encoding="utf-8")
    required = [
        "HIGHLIGHT_STUDIO_DEPLOYMENT_MODE=web",
        "HIGHLIGHT_STUDIO_DATABASE_URL=postgresql+psycopg://",
        "HIGHLIGHT_STUDIO_COOKIE_SECURE=0",
        "HIGHLIGHT_STUDIO_ALLOW_REGISTRATION=1",
        "alembic -c alembic.ini upgrade head",
        "docker-compose.local-web.yml",
        "projects_web",
        "find_free_port.py 8010 8099",
    ]
    for marker in required:
        assert marker in text


def test_local_postgres_is_bound_only_to_loopback_and_persistent():
    text = (ROOT / "deploy/docker-compose.local-web.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:${POSTGRES_PORT:-54329}:5432"' in text
    assert "local_web_postgres_data" in text
    assert "POSTGRES_PASSWORD" in text
    assert "healthcheck" in text


def test_local_web_env_is_strong_and_preserved(tmp_path):
    path = tmp_path / "web_local.env"
    first = ensure_env(path)
    assert len(first["POSTGRES_PASSWORD"]) >= 24
    assert first["POSTGRES_PORT"] == "54329"
    second = ensure_env(path)
    assert second["POSTGRES_PASSWORD"] == first["POSTGRES_PASSWORD"]
    assert parse_env(path)["POSTGRES_DB"] == "highlight_studio_web"


def test_find_free_port_detects_bound_socket():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        assert available(port) is False
    assert available(port) is True
