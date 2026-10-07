from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_root_is_intentionally_minimal():
    root_files = {p.name for p in ROOT.iterdir() if p.is_file()}
    allowed = {
        ".gitignore", ".gitattributes", "EXTRACT_BEFORE_STARTING.txt", "README.md", "RELEASE_CHECKSUMS.json",
        "START_HERE.bat", "alembic.ini", "pyproject.toml", "release_identity.json",
    }
    assert root_files <= allowed
    assert not any(name.startswith(("BUILD_INFO_", "CHANGE_REPORT_", "CHECK_")) for name in root_files)
    assert not any(name.startswith("audit_") for name in root_files)


def test_auxiliary_commands_are_grouped_by_purpose():
    expected = {
        "commands/launch/ADVANCED_START_OPTIONS.bat",
        "commands/launch/START_DESKTOP.bat",
        "commands/launch/START_WEB_LOCAL.bat",
        "commands/launch/STOP_WEB_LOCAL.bat",
        "commands/setup/INSTALL_FFMPEG.bat",
        "commands/setup/INSTALL_GPU_ACCELERATION.bat",
        "commands/diagnostics/VERIFY_RUNNING_VERSION.bat",
    }
    for rel in expected:
        assert (ROOT / rel).is_file(), rel


def test_historical_release_material_is_not_mixed_with_runtime_files():
    assert (ROOT / "docs/releases/10.15/history/build-info/BUILD_INFO_V101517_RU.txt").is_file()
    assert (ROOT / "docs/releases/10.15/history/change-reports/CHANGE_REPORT_V101517_RU.md").is_file()
    assert (ROOT / "tools/release/checks/legacy/CHECK_V101517.bat").is_file()
    assert (ROOT / "docs/audits/legacy/screenshots/audit_review.png").is_file()


def test_moved_bat_commands_resolve_application_root():
    for rel in [
        "commands/launch/START_DESKTOP.bat",
        "commands/launch/START_WEB_LOCAL.bat",
        "commands/setup/INSTALL_GPU_ACCELERATION.bat",
        "commands/diagnostics/VERIFY_RUNNING_VERSION.bat",
    ]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert 'set "HS_ROOT=' in text
        assert 'cd /d "%HS_ROOT%"' in text


def test_release_tools_share_one_layout_contract():
    make_release = (ROOT / "tools/release/make_release.py").read_text(encoding="utf-8")
    verify_archive = (ROOT / "tools/release/verify_archive.py").read_text(encoding="utf-8")
    assert "REQUIRED_RELEASE_FILES" in make_release
    assert "REQUIRED_RELEASE_FILES" in verify_archive
    assert (ROOT / "tools/release/release_layout.py").is_file()
