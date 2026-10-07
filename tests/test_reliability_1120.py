from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.src.highlight_studio.api import app as app_module
from backend.src.highlight_studio.core.artifacts import resolve_project_source
from backend.src.highlight_studio.core.utils import read_json, write_json
from backend.src.highlight_studio.infrastructure.resource_manager import ResourceManager
from backend.src.highlight_studio.services import pipeline


def test_nested_resource_lease_is_reentrant_but_counts_one_consumer() -> None:
    resource = f"nested-{uuid.uuid4().hex}"

    with ResourceManager.lease(resource, capacity=1):
        outer = ResourceManager.snapshot()[resource]
        with ResourceManager.lease(resource, capacity=1):
            nested = ResourceManager.snapshot()[resource]
        after_nested = ResourceManager.snapshot()[resource]
    released = ResourceManager.snapshot()[resource]

    assert outer["active"] == nested["active"] == after_nested["active"] == 1
    assert [outer["lease_depth"], nested["lease_depth"], after_nested["lease_depth"]] == [1, 2, 1]
    assert released["active"] == released["lease_depth"] == 0


def test_nested_resource_lease_still_serializes_other_threads() -> None:
    resource = f"nested-cross-thread-{uuid.uuid4().hex}"
    outer_ready = threading.Event()
    allow_outer_exit = threading.Event()
    order: list[str] = []

    def owner() -> None:
        with ResourceManager.lease(resource, capacity=1):
            with ResourceManager.lease(resource, capacity=1):
                order.append("owner")
                outer_ready.set()
                assert allow_outer_exit.wait(2)

    def waiter() -> None:
        assert outer_ready.wait(2)
        with ResourceManager.lease(resource, capacity=1):
            order.append("waiter")

    first = threading.Thread(target=owner)
    second = threading.Thread(target=waiter)
    first.start()
    second.start()
    assert outer_ready.wait(2)
    assert order == ["owner"]
    allow_outer_exit.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive() and not second.is_alive()
    assert order == ["owner", "waiter"]


def test_copied_project_prefers_its_relative_media_over_old_absolute_path(tmp_path: Path) -> None:
    original = tmp_path / "original"
    copied = tmp_path / "copied"
    original.mkdir()
    copied.mkdir()
    old_video = original / "input.mp4"
    local_video = copied / "input.mp4"
    old_video.write_bytes(b"old-project-media")
    local_video.write_bytes(b"copied-project-media")
    write_json(copied / "project.json", {
        "source_video_path": str(old_video),
        "source_video_relative_path": "input.mp4",
    })

    assert resolve_project_source(copied) == local_video.resolve()


def test_relative_source_path_cannot_escape_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"private-media")
    write_json(project / "project.json", {"source_video_path": "../outside.mp4"})

    assert resolve_project_source(project) == project / "input.mp4"


def test_failed_short_recovery_rebuilds_manifest_row_without_old_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "input.mp4").write_bytes(b"x")
    factory = tmp_path / "content_factory"
    output_dir = tmp_path / "outputs" / "shorts"
    factory.mkdir()
    output_dir.mkdir(parents=True)
    output = output_dir / "short_01.mp4"
    output.write_bytes(b"known-good-short-without-manifest" * 512)
    write_json(factory / "shorts_candidates.json", [{"start": 0, "end": 5, "score": 9, "title": "Recovered"}])
    monkeypatch.setattr(pipeline, "video_duration", lambda path: 5.0 if Path(path).name == "short_01.mp4" else 10.0)
    monkeypatch.setattr(pipeline, "video_info", lambda _path: {"width": 1080, "height": 1920})
    monkeypatch.setattr(pipeline, "audio_streams", lambda _path: [])
    monkeypatch.setattr(pipeline, "video_encode_args", lambda *_args, **_kwargs: (["-c:v", "libx264"], "libx264"))
    monkeypatch.setattr(pipeline, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="ffmpeg failed"))

    result = pipeline.render_shorts_candidates(
        tmp_path,
        {
            "shorts_count": 1,
            "shorts_burn_subtitles": False,
            "shorts_trim_silence": False,
            "shorts_min_seconds": 2,
            "shorts_max_seconds": 60,
            "shorts_normalize_audio": False,
        },
        pipeline.JobLogger(tmp_path),
        only_indexes={1},
    )

    row = result["rendered"][0]
    assert row["index"] == 1
    assert row["path"] == "outputs/shorts/short_01.mp4"
    assert row["title"] == "Recovered"
    assert row["stale_preserved"] is True
    assert read_json(tmp_path / "shorts_render_manifest.json", {})["rendered"][0]["path"] == row["path"]


def test_job_prepare_is_not_called_when_project_job_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class RunningThread:
        @staticmethod
        def is_alive() -> bool:
            return True

    prepared: list[Path] = []
    monkeypatch.setattr(app_module, "project_dir", lambda _project_id: tmp_path)
    with app_module.jobs_lock:
        previous = dict(app_module.jobs)
        app_module.jobs.clear()
        app_module.jobs["busy-project"] = RunningThread()
    try:
        result = app_module.start_background_job(
            "busy-project",
            "must not start",
            lambda *_args: None,
            prepare=lambda project_dir: prepared.append(project_dir),
        )
    finally:
        with app_module.jobs_lock:
            app_module.jobs.clear()
            app_module.jobs.update(previous)

    assert result["started"] is False
    assert prepared == []


def test_job_prepare_failure_creates_no_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "project_dir", lambda _project_id: tmp_path)
    create_calls: list[str] = []
    monkeypatch.setattr(app_module, "create_job", lambda *_args, **_kwargs: create_calls.append("called"))

    with pytest.raises(RuntimeError, match="editor write failed"):
        app_module.start_background_job(
            f"prepare-failure-{uuid.uuid4().hex}",
            "must not start",
            lambda *_args: None,
            prepare=lambda _project_dir: (_ for _ in ()).throw(RuntimeError("editor write failed")),
        )

    assert create_calls == []


def test_background_job_return_cannot_leave_running_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = f"terminal-{uuid.uuid4().hex}"
    job_updates: list[dict[str, object]] = []
    task_returned = threading.Event()
    monkeypatch.setattr(app_module, "project_dir", lambda _project_id: tmp_path)
    monkeypatch.setattr(app_module, "create_job", lambda *_args, **_kwargs: "job-terminal-test")
    monkeypatch.setattr(app_module, "update_job", lambda _job_id, **kwargs: job_updates.append(kwargs))
    monkeypatch.setattr(app_module, "record_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module, "clear_cancel", lambda _project_dir: None)
    monkeypatch.setattr(app_module, "load_project", lambda _project_dir: {"settings": {}})
    monkeypatch.setattr(app_module, "runtime_optimized_settings", lambda settings, _project_dir: settings)
    monkeypatch.setattr(app_module, "save_cache_manifest", lambda *_args, **_kwargs: None)

    result = app_module.start_background_job(
        project_id,
        "Terminal contract",
        lambda *_args: task_returned.set(),
    )

    assert result["started"] is True
    assert task_returned.wait(2)
    deadline = time.time() + 2
    while time.time() < deadline:
        with app_module.jobs_lock:
            if project_id not in app_module.jobs:
                break
        time.sleep(0.01)

    status = read_json(tmp_path / "status.json", {})
    assert status["state"] == "done"
    assert status["progress"] == 100
    assert job_updates[-1]["state"] == "done"
