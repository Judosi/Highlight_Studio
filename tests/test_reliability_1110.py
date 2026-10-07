from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from pydantic import ValidationError

from backend.src.highlight_studio.api import app as app_module
from backend.src.highlight_studio.core.artifacts import portable_source_fields, resolve_project_source
from backend.src.highlight_studio.core.durable_pipeline import DurablePipelineState
from backend.src.highlight_studio.core.revisions import source_revision
from backend.src.highlight_studio.core.utils import read_json, write_json
from backend.src.highlight_studio.integrations.ai.ollama import OllamaClient
from backend.src.highlight_studio.integrations.ai.runtime import AICircuitBreaker, AITransportError
from backend.src.highlight_studio.services import pipeline


def test_durable_pipeline_recovers_running_stage(tmp_path: Path) -> None:
    state = DurablePipelineState(tmp_path)
    state.update_stage("transcription", state="running", current=2, total=8, fingerprint="abc")

    recovered = state.recover_interrupted()

    assert recovered["state"] == "partial"
    assert recovered["stages"]["transcription"]["state"] == "partial"
    assert recovered["stages"]["transcription"]["recoverable"] is True


def test_project_move_keeps_content_revision_and_resolves_relative_source(tmp_path: Path) -> None:
    original = tmp_path / "first" / "project"
    original.mkdir(parents=True)
    video = original / "input.mp4"
    video.write_bytes(b"portable-media-content")
    write_json(original / "project.json", {"id": "p", **portable_source_fields(original, video)})
    before = source_revision(original)

    moved = tmp_path / "second" / "project"
    moved.parent.mkdir()
    original.rename(moved)

    assert resolve_project_source(moved) == (moved / "input.mp4").resolve()
    assert source_revision(moved) == before


def test_ai_circuit_opens_and_recovers_after_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr("backend.src.highlight_studio.integrations.ai.runtime.time.monotonic", lambda: now[0])
    circuit = AICircuitBreaker("test-1110", {"ai_circuit_failure_threshold": 2, "ai_circuit_cooldown_seconds": 10})
    circuit.success()
    circuit.failure()
    assert circuit.before_request() is True
    circuit.failure()
    assert circuit.before_request() is False
    now[0] += 11
    assert circuit.before_request() is True


def test_ollama_distinguishes_first_token_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        raw = SimpleNamespace()
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def raise_for_status(self): return None
        def iter_lines(self, **_): raise requests.Timeout("read timed out")

    captured: dict[str, object] = {}
    def fake_post(*_args, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    client = OllamaClient(settings={"ai_ttft_timeout": 123, "ai_stream_stall_timeout": 17})
    with pytest.raises(AITransportError, match="no first token within 123s"):
        client._post_generate({"model": "qwen"}, 240)
    assert captured["timeout"] == (10, 123)


def test_single_short_contract_and_route_are_exposed() -> None:
    request = app_module.ShortRenderRequest(
        title="Сильный момент", start=1.25, end=12.5, reframe_mode="blur_background"
    )
    assert request.end > request.start
    with pytest.raises(ValidationError):
        app_module.ShortRenderRequest(title="bad", start=3, end=2, reframe_mode="auto")
    routes = {route.path for route in app_module.app.routes if getattr(route, "path", None)}
    assert "/api/projects/{project_id}/shorts/{short_index}/render" in routes


def test_failed_single_short_regeneration_preserves_previous_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "input.mp4").write_bytes(b"x")
    factory = tmp_path / "content_factory"
    output_dir = tmp_path / "outputs" / "shorts"
    factory.mkdir()
    output_dir.mkdir(parents=True)
    output = output_dir / "short_01.mp4"
    previous_bytes = b"known-good-short" * 1024
    output.write_bytes(previous_bytes)
    write_json(factory / "shorts_candidates.json", [{"start": 0, "end": 5, "score": 9, "title": "one"}])
    write_json(tmp_path / "shorts_render_manifest.json", {"rendered": [{"index": 1, "path": "outputs/shorts/short_01.mp4", "title": "old"}]})
    monkeypatch.setattr(pipeline, "video_duration", lambda path: 5.0 if Path(path).name == "short_01.mp4" else 10.0)
    monkeypatch.setattr(pipeline, "video_info", lambda _path: {"width": 1080, "height": 1920})
    monkeypatch.setattr(pipeline, "audio_streams", lambda _path: [])
    monkeypatch.setattr(pipeline, "video_encode_args", lambda *_args, **_kwargs: (["-c:v", "libx264"], "libx264"))
    monkeypatch.setattr(pipeline, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="ffmpeg failed"))

    result = pipeline.render_shorts_candidates(
        tmp_path,
        {"shorts_count": 1, "shorts_burn_subtitles": False, "shorts_trim_silence": False,
         "shorts_min_seconds": 2, "shorts_max_seconds": 60, "shorts_normalize_audio": False},
        pipeline.JobLogger(tmp_path),
        only_indexes={1},
    )

    assert output.read_bytes() == previous_bytes
    assert result["failed"][0]["stale_preserved"] is True
    assert read_json(tmp_path / "shorts_render_manifest.json", {})["rendered"][0]["title"] == "old"



def test_durable_pipeline_stage_transition_closes_previous_stage(tmp_path: Path) -> None:
    state = DurablePipelineState(tmp_path)
    state.update_stage("transcribe", state="running", current=1, total=2)
    state.update_stage("transcribe", state="running", current=2, total=2)
    snapshot = state.update_stage("block_ai", state="running", current=1, total=2)
    assert snapshot["stages"]["transcribe"]["state"] == "completed"
    assert snapshot["stages"]["transcribe"]["progress_percent"] == 100.0
    snapshot = state.update_stage("block_ai", state="completed", current=2, total=2)
    assert snapshot["stages"]["block_ai"]["state"] == "completed"
    assert snapshot["state"] == "completed"
    assert "active_stage" not in snapshot


def test_durable_finalize_closes_active_running_stage(tmp_path: Path) -> None:
    state = DurablePipelineState(tmp_path)
    state.update_stage("shorts_render", state="running", current=2, total=5)
    snapshot = state.finalize("failed", error_code="SHORTS_RENDER_TEST")
    assert snapshot["state"] == "failed"
    assert snapshot["stages"]["shorts_render"]["state"] == "failed"
    assert "active_stage" not in snapshot


def test_project_schema_v4_migrates_shorts_defaults_and_pipeline_state(tmp_path: Path) -> None:
    from backend.src.highlight_studio.infrastructure.migrations import migrate_project
    write_json(tmp_path / "project.json", {
        "id": "old", "name": "old", "project_schema_version": 3,
        "settings": {}, "source_type": "local",
    })
    result = migrate_project(tmp_path)
    migrated = read_json(tmp_path / "project.json", {})
    assert result["ok"] is True
    assert migrated["project_schema_version"] == 4
    assert migrated["pipeline_state_schema_version"] == 2
    assert migrated["settings"]["shorts_reframe_mode"] == "auto"
    assert migrated["settings"]["shorts_caption_quality"] == "high"
    assert migrated["settings"]["shorts_emotion_events_enabled"] is True



def test_shorts_revision_tracks_count_vertical_mode_and_selection_controls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.highlight_studio.core import revisions
    monkeypatch.setattr(revisions, "source_revision", lambda _d: "source")
    monkeypatch.setattr(revisions, "segments_revision", lambda *_a, **_k: "segments")
    base = {
        "shorts_count": 5,
        "shorts_vertical_reframe": True,
        "shorts_candidate_pool_limit": 48,
        "shorts_llm_rerank_top": 10,
        "shorts_render_preset": "veryfast",
    }
    first = revisions.shorts_revision(tmp_path, base)
    for key, value in {
        "shorts_count": 6,
        "shorts_vertical_reframe": False,
        "shorts_candidate_pool_limit": 60,
        "shorts_llm_rerank_top": 12,
        "shorts_render_preset": "fast",
    }.items():
        assert revisions.shorts_revision(tmp_path, {**base, key: value}) != first, key
