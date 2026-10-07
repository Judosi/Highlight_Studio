from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import highlight_studio.api.app as main


def _request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers or []})


def test_atomic_add_segment_bypasses_whole_array_review_path(monkeypatch, tmp_path: Path):
    existing = [{"id": "old", "start": 10.0, "end": 20.0, "source_candidate_key": "old-key"}]
    saved = {}
    revs = iter(["rev-before", "rev-after"])
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    monkeypatch.setattr(main, "project_dir", lambda _project_id: tmp_path)
    monkeypatch.setattr(main, "load_project", lambda _d: {"settings": main.default_settings()})
    monkeypatch.setattr(main, "segments_revision", lambda *_args, **_kwargs: next(revs))
    monkeypatch.setattr(main, "project_paths", lambda _d: {"segments": tmp_path / "segments.json"})
    monkeypatch.setattr(main, "read_json", lambda *_args, **_kwargs: list(existing))
    monkeypatch.setattr(main, "source_video_path", lambda _d: source)
    monkeypatch.setattr(main, "video_duration", lambda _p: 120.0)
    monkeypatch.setattr(main, "cached_source_duration", lambda *_args, **_kwargs: 120.0)

    def fake_update(_d, items, *, source_duration=None, revision_context=None):
        saved["items"] = items
        saved["duration"] = source_duration
        saved["revision_context"] = revision_context
        return items

    monkeypatch.setattr(main, "update_segments", fake_update)
    result = main.add_project_segment(
        "p1",
        _request([(b"x-segments-revision", b"rev-before")]),
        {"item": {"id": "new", "start": 30.0, "end": 42.0}},
    )

    assert result["ok"] is True
    assert result["added"] is True
    assert result["segments_revision"] == "rev-after"
    assert [item["id"] for item in result["segments"]] == ["old", "new"]
    assert saved["duration"] == 120.0
    assert result["segments"][-1]["source_candidate_key"]


def test_atomic_add_segment_is_idempotent(monkeypatch, tmp_path: Path):
    existing = [{"id": "clip", "start": 30.0, "end": 42.0, "source_candidate_key": "clip-30.00-42.00"}]
    monkeypatch.setattr(main, "project_dir", lambda _project_id: tmp_path)
    monkeypatch.setattr(main, "load_project", lambda _d: {"settings": main.default_settings()})
    monkeypatch.setattr(main, "segments_revision", lambda *_args, **_kwargs: "rev")
    monkeypatch.setattr(main, "project_paths", lambda _d: {"segments": tmp_path / "segments.json"})
    monkeypatch.setattr(main, "read_json", lambda *_args, **_kwargs: list(existing))
    monkeypatch.setattr(main, "update_segments", lambda *_args, **_kwargs: pytest.fail("duplicate must not rewrite montage"))

    result = main.add_project_segment("p1", _request(), {"item": dict(existing[0])})
    assert result["ok"] is True
    assert result["added"] is False
    assert result["reason"] == "already_present"


def test_atomic_add_segment_rejects_overlap(monkeypatch, tmp_path: Path):
    existing = [{"id": "old", "start": 10.0, "end": 20.0}]
    monkeypatch.setattr(main, "project_dir", lambda _project_id: tmp_path)
    monkeypatch.setattr(main, "load_project", lambda _d: {"settings": main.default_settings()})
    monkeypatch.setattr(main, "segments_revision", lambda *_args, **_kwargs: "rev")
    monkeypatch.setattr(main, "project_paths", lambda _d: {"segments": tmp_path / "segments.json"})
    monkeypatch.setattr(main, "read_json", lambda *_args, **_kwargs: list(existing))

    with pytest.raises(HTTPException) as exc:
        main.add_project_segment("p1", _request(), {"item": {"id": "new", "start": 19.0, "end": 25.0}})
    assert exc.value.status_code == 409
    assert exc.value.detail["kind"] == "segment_overlap"


def test_101513_frontend_source_uses_atomic_add_and_result_first_review():
    root = Path(__file__).resolve().parents[1]
    source = (root / "frontend" / "src" / "app" / "App.jsx").read_text(encoding="utf-8")
    assert "/segments/add" in source
    assert "AI-нарезка готова" in source
    assert "Альтернативы (" in source
    assert "reviewTabTouched" in source
    assert "refreshGlobalJobs" in source
    assert "Ориентир по длительности, минут" in source
    assert "confirmReanalysis" in source
    assert "humanizePipelineStage" in source
    assert "beginProject('local', false)" in source


def test_101513_packaged_bundle_no_longer_uses_old_add_candidate_put_path():
    root = Path(__file__).resolve().parents[1]
    dist = root / "frontend" / "dist"
    bundle = next((dist / "assets").glob("index-*.js"))
    text = bundle.read_text(encoding="utf-8")
    assert "/segments/add" in text
    assert "let n=await Wa([...C,t]);return n&&yr(`final`),n" not in text
    assert "can't access lexical declaration" not in text
    assert not (dist / "ux-workflow-101513.js").exists()
    presentation = (dist / "ui-presentation-101515.js").read_text(encoding="utf-8")
    assert "fetch(" not in presentation
    assert "location.reload" not in presentation
    assert "stopImmediatePropagation" not in presentation
    assert "expected_revision" in text


def test_101513_terminal_job_sync_prevents_stale_analysis_screen():
    root = Path(__file__).resolve().parents[1]
    source = (root / "frontend" / "src" / "app" / "App.jsx").read_text(encoding="utf-8")
    presentation = (root / "frontend" / "dist" / "ui-presentation-101515.js").read_text(encoding="utf-8")
    assert "lastGlobalTerminalSyncRef" in source
    assert "refreshAll(project.id, scope)" in source
    assert "analysis job reaches a terminal successful state" in source
    assert "syncCompletedAnalysis" not in presentation
    assert "/dashboard-state" not in presentation
