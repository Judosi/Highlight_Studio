from __future__ import annotations

from pathlib import Path

import backend.main as main
from highlight_studio.core.artifacts import ARTIFACTS
from highlight_studio.core import revisions as rev
from highlight_studio.core.utils import read_json, write_json


def make_project(root: Path, pid: str = "review-unlock") -> tuple[Path, dict]:
    d = root / pid
    d.mkdir(parents=True, exist_ok=True)
    src = d / "input.mp4"
    src.write_bytes(b"synthetic-source" * 100)
    settings = main.default_settings()
    settings["whisper_device"] = "cpu"
    settings["whisper_compute"] = "int8"
    write_json(d / "project.json", {
        "id": pid,
        "name": pid,
        "source_video_path": str(src),
        "settings": settings,
    })
    write_json(d / ARTIFACTS["candidates"], [
        {"id": 1, "candidate_id": "cand-1", "start": 0, "end": 20, "score": 9, "decision": "keep", "title": "A"}
    ])
    write_json(d / ARTIFACTS["segments"], [
        {"id": 1, "candidate_id": "cand-1", "start": 0, "end": 20, "score": 9, "decision": "keep", "title": "A"}
    ])
    return d, settings


def test_runtime_hardware_resolution_does_not_make_completed_analysis_stale(tmp_path):
    """Auto CPU->CUDA execution choice must not be a semantic analysis change."""
    d, saved = make_project(tmp_path)
    runtime = dict(saved)
    runtime["whisper_device"] = "cuda"
    runtime["whisper_compute"] = "int8_float32"

    # This mirrors the one-click worker: analysis receives runtime-optimized settings.
    rev.mark_analysis_complete(d, runtime)

    fresh = rev.freshness_report(d, saved)
    assert fresh["analysis_current"] is True
    assert fresh["candidates_current"] is True
    assert fresh["segments_current"] is True


def test_v101515_false_stale_project_is_repaired_without_rerunning_analysis(tmp_path):
    """A proven v10.15.15 runtime hash is migrated to the semantic hash in place."""
    d, saved = make_project(tmp_path)
    runtime_effective = {
        "whisper_device": "cuda",
        "whisper_compute": "int8_float32",
    }
    write_json(d / "hardware_runtime.json", {"effective_settings": runtime_effective})
    write_json(d / "status.json", {
        "state": "done",
        "progress": 100,
        "analysis_complete": True,
        "candidate_count": 1,
        "segment_count": 1,
    })

    src = rev.source_revision(d)
    legacy_ar = rev._legacy_runtime_analysis_revision_v101515(d, saved, source_rev=src)
    assert legacy_ar
    legacy_runtime_settings = dict(saved)
    legacy_runtime_settings.update(runtime_effective)
    legacy_sr = rev.segments_revision(
        d,
        legacy_runtime_settings,
        analysis_rev=legacy_ar,
        source_rev=src,
    )
    write_json(d / ARTIFACTS["revision_state"], {
        "schema_version": 1,
        "source_revision": src,
        "candidates_analysis_revision": legacy_ar,
        "segments_analysis_revision": legacy_ar,
        "segments_revision": legacy_sr,
        "rendered_revision": None,
        "shorts_revision": None,
    })

    fresh = rev.freshness_report(d, saved)
    assert fresh["analysis_current"] is True
    assert fresh["segments_current"] is True

    state = read_json(d / ARTIFACTS["revision_state"], {})
    assert state["candidates_analysis_revision"] == fresh["analysis_revision"]
    assert state["segments_analysis_revision"] == fresh["analysis_revision"]
    assert state["runtime_revision_migrated_from"] == "v10.15.15"


def test_v101515_repair_does_not_accept_real_semantic_setting_change(tmp_path):
    d, saved = make_project(tmp_path)
    write_json(d / "hardware_runtime.json", {
        "effective_settings": {"whisper_device": "cuda", "whisper_compute": "int8_float32"}
    })
    write_json(d / "status.json", {"state": "done", "analysis_complete": True})

    src = rev.source_revision(d)
    legacy_ar = rev._legacy_runtime_analysis_revision_v101515(d, saved, source_rev=src)
    runtime = dict(saved)
    runtime.update({"whisper_device": "cuda", "whisper_compute": "int8_float32"})
    write_json(d / ARTIFACTS["revision_state"], {
        "schema_version": 1,
        "source_revision": src,
        "candidates_analysis_revision": legacy_ar,
        "segments_analysis_revision": legacy_ar,
        "segments_revision": rev.segments_revision(d, runtime, analysis_rev=legacy_ar, source_rev=src),
    })

    changed = dict(saved)
    changed["text_model"] = str(saved.get("text_model") or "model") + "-changed"
    fresh = rev.freshness_report(d, changed)
    assert fresh["analysis_current"] is False
    assert fresh["segments_current"] is False
