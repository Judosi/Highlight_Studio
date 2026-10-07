from __future__ import annotations

import json
from pathlib import Path
from tools.release.frontend_integrity import verify_frontend

from starlette.requests import Request

import highlight_studio.api.app as api
import highlight_studio.services.pipeline as pipeline


class Logger:
    def log(self, _message: str) -> None:
        pass


def request_with_revision(revision: str) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"x-segments-revision", revision.encode("ascii"))],
    })


def selection_settings() -> dict:
    return {
        "target_minutes": 60,
        "min_final_segments": 8,
        "max_final_segments": 80,
        "micro_cut_enabled": True,
        "micro_window_seconds": 45,
        "micro_min_seconds": 12,
        "micro_max_seconds": 80,
        "target_fill_ratio": 0.94,
        "refill_after_dedup_enabled": True,
        "quality_first_selection_enabled": True,
        "quality_first_min_score": 6.7,
        "quality_first_min_confidence": 5.6,
        "quality_first_min_clarity": 0.5,
        "semantic_quality_guard_enabled": True,
        "non_primary_reject_confidence": 0.72,
        "fill_target_min_score": 5.0,
        "strict_quality_mode": False,
    }


def candidate(index: int) -> pipeline.Candidate:
    # Alphabetic suffixes keep the local word-token duplicate checker honest.
    suffix = "".join(chr(97 + ((index // (26 ** power)) % 26)) for power in (0, 1, 2))
    start = float(index * 35)
    return pipeline.Candidate(
        id=index + 1,
        start=start,
        end=start + 30.0,
        score=8.0,
        title=f"moment{suffix}",
        reason=f"event{suffix}",
        text_preview=f"story{suffix}",
        confidence=8.0,
        decision="keep",
        standalone_clarity=0.9,
        content_class="primary_live",
        content_class_confidence=0.95,
        candidate_id=f"candidate-{index}",
    )


def test_sixty_minute_micro_montage_is_not_capped_at_eighty_segments(tmp_path: Path) -> None:
    settings = selection_settings()
    candidates = [candidate(index) for index in range(130)]
    chosen = candidates[:80]

    assert pipeline.effective_max_final_segments(settings, 3600.0) == 134
    result = pipeline.refill_after_dedup(tmp_path, candidates, chosen, 3600.0, settings, Logger())

    assert len(result) == 120
    assert pipeline.candidates_total_duration(result) == 3600.0
    report = json.loads((tmp_path / "selection_refill_after_dedup.json").read_text(encoding="utf-8"))
    assert report["quality_floor_reached"] is True
    assert report["after_segments"] == 120


def test_thirty_minute_default_keeps_existing_capacity() -> None:
    settings = selection_settings()
    settings["target_minutes"] = 30
    assert pipeline.effective_max_final_segments(settings, 1800.0) == 80


def test_cached_source_duration_migrates_existing_1121_analysis_without_ffprobe(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video-data")
    (tmp_path / "project.json").write_text(json.dumps({
        "source_video_relative_path": "input.mp4",
        "source_video_size_bytes": source.stat().st_size,
    }), encoding="utf-8")
    (tmp_path / "temporal_quality_report.json").write_text(
        json.dumps({"duration_seconds": 20749.7}), encoding="utf-8"
    )
    monkeypatch.setattr(pipeline, "video_duration", lambda _path: (_ for _ in ()).throw(AssertionError("ffprobe must not run")))

    assert pipeline.cached_source_duration(tmp_path) == 20749.7
    cache = json.loads((tmp_path / "source_duration_cache.json").read_text(encoding="utf-8"))
    assert cache["duration_seconds"] == 20749.7
    assert cache["source_size_bytes"] == source.stat().st_size


def test_atomic_add_reuses_verified_revision_context_without_rescanning_source(tmp_path: Path, monkeypatch) -> None:
    existing = [{"id": 1, "start": 0.0, "end": 10.0, "source_candidate_key": "old"}]
    (tmp_path / "segments.json").write_text(json.dumps(existing), encoding="utf-8")
    (tmp_path / "revision_state.json").write_text(json.dumps({
        "source_revision": "source-rev",
        "candidates_analysis_revision": "analysis-rev",
        "segments_analysis_revision": "analysis-rev",
        "segments_revision": "segments-rev",
    }), encoding="utf-8")
    captured: dict = {}

    monkeypatch.setattr(api, "project_dir", lambda _project_id: tmp_path)
    monkeypatch.setattr(api, "load_project", lambda _path: {"settings": api.default_settings()})
    monkeypatch.setattr(api, "project_paths", lambda _path: {"segments": tmp_path / "segments.json"})
    monkeypatch.setattr(api, "cached_source_duration", lambda *_args, **_kwargs: 120.0)
    monkeypatch.setattr(api, "segments_revision", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("deep source revision scan must not run")))

    def save(_path, items, *, source_duration=None, revision_context=None):
        captured["source_duration"] = source_duration
        captured["revision_context"] = revision_context
        return items

    monkeypatch.setattr(api, "update_segments", save)
    result = api.add_project_segment(
        "project",
        request_with_revision("segments-rev"),
        {"item": {"id": 2, "start": 20.0, "end": 35.0}},
    )

    assert result["ok"] is True
    assert result["fast_revision_path"] is True
    assert captured["source_duration"] == 120.0
    assert captured["revision_context"] == {
        "source_revision": "source-rev",
        "analysis_revision": "analysis-rev",
    }


def test_frontend_exposes_immediate_pending_and_saved_states() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "frontend" / "src" / "app" / "App.jsx").read_text(encoding="utf-8")
    styles = (root / "frontend" / "src" / "styles" / "studio-final.css").read_text(encoding="utf-8")
    bundle_path = next((root / "frontend" / "dist" / "assets").glob("index-1127-*.js"))
    bundle = bundle_path.read_text(encoding="utf-8")
    assert "Добавляю и сохраняю…" in source
    assert "Сохраняю монтаж…" in source
    assert "pendingCandidateKeys" in source
    assert "aria-busy" in source
    assert "isPending" in styles
    assert "Добавляю и сохраняю…" in bundle
    assert "Сохраняю монтаж…" in bundle
    assert verify_frontend(root) == []
    assert '"aria-busy"' in bundle
