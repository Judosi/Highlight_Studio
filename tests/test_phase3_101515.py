from __future__ import annotations

import json
from pathlib import Path
from tools.release.frontend_integrity import verify_frontend

ROOT = Path(__file__).resolve().parents[1]


def test_frontend_production_has_single_workflow_state_owner():
    dist = ROOT / "frontend" / "dist"
    html = (dist / "index.html").read_text(encoding="utf-8")
    release = json.loads((dist / "release.json").read_text(encoding="utf-8"))
    presentation = (dist / "ui-presentation-101515.js").read_text(encoding="utf-8")
    assert release["version"] == "11.2.7"
    assert release["production_state_owner"] == "react"
    assert "ux-workflow-101513.js" not in html
    assert not (dist / "ux-workflow-101513.js").exists()
    assert "ui-presentation-101515.js" in html
    for forbidden in ("fetch(", "apiFetch", "location.reload", "stopImmediatePropagation", "setInterval", "sessionStorage", "/api/", ".click()"):
        assert forbidden not in presentation


def test_new_project_enters_true_draft_context_in_source_and_bundle():
    source = (ROOT / "frontend" / "src" / "app" / "App.jsx").read_text(encoding="utf-8")
    bundle = next((ROOT / "frontend" / "dist" / "assets").glob("index-*.js")).read_text(encoding="utf-8")
    for marker in (
        "resetProjectContextForDraft",
        "beginProjectScope('')",
        "setProject(null)",
        "setCandidates([])",
        "setSegments([])",
        "localStorage.removeItem('highlightStudioLastProject')",
    ):
        assert marker in source
    assert verify_frontend(ROOT) == []
    assert "localStorage.removeItem(`highlightStudioLastProject`)" in bundle
    assert verify_frontend(ROOT) == []


def test_project_scoped_source_actions_use_generation_guards():
    source = (ROOT / "frontend" / "src" / "app" / "App.jsx").read_text(encoding="utf-8")
    guarded = (
        "runSmartPreflight",
        "resumeProject",
        "saveCacheFingerprint",
        "clearIncompatibleCache",
        "repairJsonBackups",
        "exportDebugBundle",
        "refreshAiQualityAudit",
        "backfillExplanations",
        "runProjectDoctor",
        "applyDurationAction",
        "recomputeCreatorPack",
        "sendFeedback",
        "clearCache",
        "exportProject",
    )
    for name in guarded:
        start = source.index(f"async function {name}")
        end = source.find("\n  }", start) + 4
        body = source[start:end]
        assert "const projectId = project.id" in body, name
        assert "captureProjectScope(projectId)" in body, name
        assert "isProjectScopeCurrent(scope)" in body, name


def test_clip_preview_aborts_old_project_request():
    source = (ROOT / "frontend" / "src" / "app" / "App.jsx").read_text(encoding="utf-8")
    start = source.index("const selectedPreview =")
    end = source.index("useEffect(() => {\n    const v = reviewVideoRef.current", start)
    block = source[start:end]
    assert "new AbortController()" in block
    assert "signal: controller.signal" in block
    assert "isProjectScopeCurrent(scope)" in block
    assert "return () => controller.abort()" in block


def test_packaged_add_candidate_is_atomic_revision_aware_and_project_scoped():
    bundle = next((ROOT / "frontend" / "dist" / "assets").glob("index-*.js")).read_text(encoding="utf-8")
    assert "/segments/add" in bundle
    assert "expected_revision" in bundle
    assert "highlightStudioLastProject" in bundle
    assert "Монтаж изменился в другом окне" in bundle
    assert "let n=await Wa([...C,t]);return n&&yr(`final`),n" not in bundle


def test_release_frontend_bundle_is_unique():
    assets = list((ROOT / "frontend" / "dist" / "assets").glob("index-*.js"))
    assert len(assets) == 1, [p.name for p in assets]
    assert assets[0].name.startswith("index-1127-")


def test_packaged_review_is_result_first_without_external_click_hacks():
    bundle = next((ROOT / "frontend" / "dist" / "assets").glob("index-*.js")).read_text(encoding="utf-8")
    assert verify_frontend(ROOT) == []
    assert "AI-нарезка готова" in bundle
    assert "Альтернативы (" in bundle
    assert "Итоговая нарезка (" in bundle


def test_packaged_global_jobs_poll_is_single_and_react_owned():
    bundle = next((ROOT / "frontend" / "dist" / "assets").glob("index-*.js")).read_text(encoding="utf-8")
    presentation = (ROOT / "frontend" / "dist" / "ui-presentation-101515.js").read_text(encoding="utf-8")
    assert verify_frontend(ROOT) == []
    assert "Все фоновые задачи" in bundle
    assert verify_frontend(ROOT) == []
    assert "setInterval" not in presentation
