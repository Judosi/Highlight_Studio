from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

import highlight_studio.api.app as main
from highlight_studio.core.artifacts import source_file_signature
from highlight_studio.infrastructure.runtime_state import _recover_project_status
from highlight_studio.integrations.twitch import source as twitch_source


def _request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({"type": "http", "method": "PUT", "path": "/", "headers": headers or []})


def test_encoded_project_alias_is_rejected(tmp_path: Path, monkeypatch):
    p = tmp_path / "abc"
    p.mkdir()
    (p / "project.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    assert main.project_dir("abc") == p.resolve()
    for encoded in ["%61bc", "%2561bc", "%252561bc"]:
        with pytest.raises(HTTPException) as exc:
            main.project_dir(encoded)
        assert exc.value.status_code == 404


def test_source_signature_detects_internal_change_even_if_size_and_mtime_are_preserved(tmp_path: Path):
    p = tmp_path / "source.mp4"
    # Under the full-hash threshold: this reproduces the audit's exact 6 MiB blind spot.
    p.write_bytes(b"A" * (6 * 1024 * 1024))
    st = p.stat()
    a = source_file_signature(p)
    with p.open("r+b") as f:
        f.seek(1536 * 1024)
        f.write(b"B" * 4096)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    b = source_file_signature(p)
    assert a["size"] == b["size"]
    assert a["mtime_ns"] == b["mtime_ns"]
    assert a["partial_sha256"] != b["partial_sha256"]
    assert b["signature_version"] >= 2


def test_cancel_requested_recovers_to_interrupted(tmp_path: Path):
    d = tmp_path / "p1"
    d.mkdir()
    (d / "project.json").write_text("{}", encoding="utf-8")
    (d / "status.json").write_text(json.dumps({"state": "cancel_requested", "stage": "cancelling", "progress": 55}), encoding="utf-8")
    result = _recover_project_status(d)
    status = json.loads((d / "status.json").read_text(encoding="utf-8"))
    assert result and result["recoverable"] is True
    assert status["state"] == "interrupted"
    assert status["previous_state"] == "cancel_requested"


def test_settings_and_twitch_source_rmw_do_not_erase_each_other(tmp_path: Path, monkeypatch):
    d = tmp_path / "p1"
    d.mkdir()
    video = d / "new.mp4"
    video.write_bytes(b"video")
    (d / "project.json").write_text(json.dumps({
        "id": "p1", "settings": {**main.default_settings(), "target_minutes": 30},
        "source_video_path": "old.mp4", "twitch": {"status": "downloading"},
    }), encoding="utf-8")

    monkeypatch.setattr(main, "project_dir", lambda _pid: d)
    monkeypatch.setattr(twitch_source, "mark_source_changed", lambda *_a, **_k: None)

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def save():
        try:
            barrier.wait()
            main.save_settings("p1", {"target_minutes": 47})
        except Exception as exc:
            errors.append(exc)

    def source():
        try:
            barrier.wait()
            twitch_source._update_project_source(d, video, {"vod_id": "123"}, "ready")
        except Exception as exc:
            errors.append(exc)

    a = threading.Thread(target=save)
    b = threading.Thread(target=source)
    a.start()
    b.start()
    a.join()
    b.join()
    assert not errors
    final = json.loads((d / "project.json").read_text(encoding="utf-8"))
    assert final["settings"]["target_minutes"] == 47
    assert Path(final["source_video_path"]).name == "new.mp4"
    assert final["twitch"]["status"] == "ready"


def test_put_segments_uses_one_revision_winner(tmp_path: Path, monkeypatch):
    d = tmp_path / "p1"
    d.mkdir()
    (d / "segments.json").write_text(json.dumps([{"id":"old","start":0,"end":10}]), encoding="utf-8")
    source = d / "source.mp4"
    source.write_bytes(b"v")
    monkeypatch.setattr(main, "project_dir", lambda _pid: d)
    monkeypatch.setattr(main, "load_project", lambda _d: {"settings": main.default_settings()})
    monkeypatch.setattr(main, "project_paths", lambda _d: {"segments": d / "segments.json"})
    monkeypatch.setattr(main, "source_video_path", lambda _d: source)
    monkeypatch.setattr(main, "video_duration", lambda _p: 100.0)

    state = {"rev": "r0", "segments": [{"id":"old","start":0,"end":10}]}
    state_lock = threading.Lock()
    def rev(*_a, **_k):
        with state_lock:
            return state["rev"]
    def read(*_a, **_k):
        with state_lock:
            return list(state["segments"])
    def update(_d, items, source_duration=None, revision_context=None):
        with state_lock:
            state["segments"] = list(items)
            state["rev"] = "r1" if state["rev"] == "r0" else "r2"
        return list(items)
    monkeypatch.setattr(main, "segments_revision", rev)
    monkeypatch.setattr(main, "read_json", read)
    monkeypatch.setattr(main, "update_segments", update)

    barrier = threading.Barrier(2)
    statuses: list[int] = []
    def worker(end):
        barrier.wait()
        try:
            main.put_segments("p1", [{"id":"old","start":0,"end":end}], _request([(b"x-segments-revision", b"r0")]), Response())
            statuses.append(200)
        except HTTPException as exc:
            statuses.append(exc.status_code)
    t1=threading.Thread(target=worker,args=(20,))
    t2=threading.Thread(target=worker,args=(30,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sorted(statuses) == [200, 409]


def test_phase1_source_contains_manual_workflow_refresh_and_add_scope():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "app" / "App.jsx").read_text(encoding="utf-8")
    assert "async function openWorkflowStep(step)" in app
    assert "['review', 'export'].includes(step.id)" in app
    assert "const data = await refreshAll(projectId, scope)" in app
    assert "const scope = captureProjectScope(projectId)" in app
    assert "if (!isProjectScopeCurrent(scope)) return false" in app


def test_job_start_delete_and_cache_share_project_lifecycle_lock():
    root = Path(__file__).resolve().parents[1]
    app = (root / "backend" / "src" / "highlight_studio" / "api" / "app.py").read_text(encoding="utf-8")
    for fn in ("def start_background_job", "def delete_project", "def clear_project_cache"):
        start = app.index(fn)
        body = app[start:start + 9000]
        assert "project_lifecycle_lock" in body, fn


def test_workflow_steps_remain_clickable_for_authoritative_revalidation():
    root = Path(__file__).resolve().parents[1]
    source = (root / "frontend" / "src" / "app" / "App.jsx").read_text(encoding="utf-8")
    assert "data-locked={step.locked ? 'true' : 'false'}" in source
    assert "aria-disabled={step.locked}" not in source
