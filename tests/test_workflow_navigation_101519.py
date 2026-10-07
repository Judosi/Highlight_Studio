from pathlib import Path
from tools.release.frontend_integrity import verify_frontend

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "app" / "App.jsx"
DIST = ROOT / "frontend" / "dist"


def test_format_to_analysis_is_immediate_and_non_blocking():
    source = APP.read_text(encoding="utf-8")
    assert "function continueToAnalysis()" in source
    block = source.split("function continueToAnalysis()", 1)[1].split("async function uploadFile", 1)[0]
    assert "confirmFormat()" in block
    assert "setActiveStep('analysis')" in block
    assert "saveSettings(null, { refresh: false, announce: false }).catch" in block
    assert "await saveSettings()" not in block
    assert "onClick={continueToAnalysis}" in source


def test_packaged_bundle_has_the_same_navigation_fix():
    bundle = next((DIST / "assets").glob("index-1127-*.js")).read_text(encoding="utf-8")
    assert "Продолжить к анализу" in bundle
    assert verify_frontend(ROOT) == []
