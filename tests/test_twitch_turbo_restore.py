from pathlib import Path
from tools.release.frontend_integrity import verify_frontend
from tools.release.release_layout import REQUIRED_RELEASE_BINARY_MIN_SIZES, REQUIRED_RELEASE_FILES

from backend.src.highlight_studio.integrations.twitch import source

ROOT = Path(__file__).resolve().parents[1]


def test_auto_prefers_bundled_twitchdownloader(monkeypatch):
    monkeypatch.setattr(
        source,
        "twitch_tool_status",
        lambda settings=None: {"recommended_engine": "twitchdownloadercli"},
    )
    assert source._select_vod_engine({"download_engine": "auto"}, {}) == "twitchdownloadercli"


def test_sync_cache_warning():
    assert "синхронизируемой" in source._twitch_cache_location_warning(Path("C:/Users/Test/OneDrive/Videos/cache"))
    assert source._twitch_cache_location_warning(Path("D:/HighlightStudio/projects/cache")) == ""


def test_source_launcher_forces_portable_twitch_cache():
    launcher = (ROOT / "scripts/windows/run_windows.bat").read_text(encoding="utf-8")
    assert 'set "HIGHLIGHT_STUDIO_PORTABLE=1"' in launcher
    assert 'set "HIGHLIGHT_STUDIO_PROJECTS_DIR=%CD%\\projects"' in launcher
    assert 'set "HIGHLIGHT_STUDIO_DATA_DIR=%CD%\\.highlight_studio"' in launcher


def test_simple_mode_forces_auto_turbo_payload():
    app = (ROOT / "frontend/src/app/App.jsx").read_text(encoding="utf-8")
    assert "const advancedTwitch = productMode === 'pro' || advancedToolsOpen" in app
    assert "const turboEngine = advancedTwitch ?" in app
    assert ": 'auto'" in app
    assert "twitch_download_engine: turboEngine" in app


def test_release_contract_requires_complete_turbo_binaries():
    tdcli = "vendor/twitchdownloadercli/TwitchDownloaderCLI.exe"
    aria2 = "vendor/aria2/aria2c.exe"
    assert {tdcli, aria2} <= REQUIRED_RELEASE_FILES
    assert REQUIRED_RELEASE_BINARY_MIN_SIZES[tdcli] == source.BUNDLED_TDCLI_MIN_BYTES
    assert REQUIRED_RELEASE_BINARY_MIN_SIZES[aria2] == source.BUNDLED_ARIA2_MIN_BYTES


def test_bundle_integrity_report_marks_release_tools_complete(monkeypatch, tmp_path):
    tdcli_dir = tmp_path / "twitchdownloadercli"
    aria2_dir = tmp_path / "aria2"
    tdcli_dir.mkdir()
    aria2_dir.mkdir()
    with (tdcli_dir / "TwitchDownloaderCLI.exe").open("wb") as stream:
        stream.truncate(source.BUNDLED_TDCLI_MIN_BYTES)
    with (aria2_dir / "aria2c.exe").open("wb") as stream:
        stream.truncate(source.BUNDLED_ARIA2_MIN_BYTES)
    monkeypatch.setattr(source, "BUNDLED_TWITCHDOWNLOADERCLI_DIR", tdcli_dir)
    monkeypatch.setattr(source, "BUNDLED_ARIA2_DIR", aria2_dir)
    report = source.bundled_turbo_integrity()
    assert report["twitchdownloadercli"]["ok"] is True
    assert report["aria2c"]["ok"] is True


def test_twitch_vod_creation_stays_on_source_step():
    app = (ROOT / "frontend/src/app/App.jsx").read_text(encoding="utf-8")
    start = app.index("async function twitchImport")
    end = app.index("async function runTwitchSpeedTest")
    block = app[start:end]
    assert "activateNewProject(p, 'import')" in block
    assert "setSourceChoice(twitchKind === 'live' ? 'twitch_live' : 'twitch_vod')" in block
    assert "window.open" not in block
    assert "Auto Turbo — рекомендуется" in app
    assert "Загрузка VOD" in app


def test_built_frontend_contains_vod_stability_fix():
    bundles = list((ROOT / "frontend" / "dist" / "assets").glob("index-*.js"))
    assert bundles, "built frontend bundle missing"
    built = bundles[0].read_text(encoding="utf-8")
    assert "Auto Turbo — рекомендуется" in built
    assert verify_frontend(ROOT) == []
    assert verify_frontend(ROOT) == []
