from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from highlight_studio.core.utils import OperationCancelled, read_json, write_json
from highlight_studio.integrations.twitch import source as twitch_source
from highlight_studio.services import pipeline


GiB = 1024**3


def budget_module():
    return importlib.import_module("highlight_studio.core.disk_budget")


def test_existing_long_video_is_blocked_before_large_temp_is_created(monkeypatch, tmp_path: Path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"source")

    class FakeAI:
        def check(self, *_args, **_kwargs):
            return {"ok": True, "text_model_installed": True, "vision_model_installed": True}

    monkeypatch.setattr(pipeline, "project_paths", lambda _project: {"video": source})
    monkeypatch.setattr(pipeline, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(pipeline, "video_duration", lambda _path: 20 * 3600.0)
    monkeypatch.setattr(pipeline, "audio_streams", lambda _path: [0])
    monkeypatch.setattr(pipeline, "make_ai_client", lambda *_args, **_kwargs: FakeAI())
    monkeypatch.setattr(pipeline.shutil, "disk_usage", lambda _path: SimpleNamespace(free=3 * GiB))
    transcribe_calls: list[bool] = []
    monkeypatch.setattr(pipeline, "transcribe", lambda *_args: transcribe_calls.append(True) or [])

    report = pipeline.preflight(tmp_path, {"visual_mode": "Лёгкий"})
    disk = next(item for item in report["checks"] if item["name"] == "Свободное место")

    assert disk["status"] == "FAIL"
    with pytest.raises(RuntimeError, match="Свободное место"):
        pipeline.analyze(tmp_path, {"visual_mode": "Лёгкий"}, pipeline.JobLogger(tmp_path))
    assert transcribe_calls == []
    assert not (tmp_path / "audio_16k.wav").exists()


@pytest.mark.parametrize("hours", [1, 10, 20])
def test_analysis_budget_scales_with_duration_and_exact_pcm_formula(monkeypatch, tmp_path: Path, hours: int):
    disk_budget = budget_module()
    source = tmp_path / "input.mp4"
    source.write_bytes(b"already-present")
    monkeypatch.setattr(disk_budget, "filesystem_identity", lambda _path: "project-volume")
    monkeypatch.setattr(disk_budget, "disk_free_bytes", lambda _path: 100 * GiB)

    report = disk_budget.estimate_analysis_disk_budget(
        tmp_path,
        duration_seconds=hours * 3600,
        source_path=source,
        visual_scan_samples=0,
    )

    expected_pcm = hours * 3600 * 16_000 * 1 * 16 // 8 + 44
    assert disk_budget.pcm_wav_bytes(hours * 3600, 16_000, 1, 16) == expected_pcm
    assert report["components"]["peak_audio_temp_bytes"] == expected_pcm
    assert report["components"]["source_requirement_bytes"] == 0
    assert report["ok"] is True


def test_short_video_is_not_blocked_and_budget_is_not_fixed_five_gib(monkeypatch, tmp_path: Path):
    disk_budget = budget_module()
    source = tmp_path / "short.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(disk_budget, "filesystem_identity", lambda _path: "project-volume")
    monkeypatch.setattr(disk_budget, "disk_free_bytes", lambda _path: 1 * GiB)

    short = disk_budget.estimate_analysis_disk_budget(
        tmp_path,
        duration_seconds=60,
        source_path=source,
        visual_scan_samples=0,
    )
    long = disk_budget.estimate_analysis_disk_budget(
        tmp_path,
        duration_seconds=20 * 3600,
        source_path=source,
        visual_scan_samples=0,
    )

    assert short["ok"] is True
    assert short["required_bytes"] < GiB
    assert long["components"]["peak_audio_temp_bytes"] > short["components"]["peak_audio_temp_bytes"] * 1000
    assert long["required_bytes"] > short["required_bytes"] * 8
    assert long["ok"] is False


def test_pending_source_is_counted_but_existing_source_is_not(monkeypatch, tmp_path: Path):
    disk_budget = budget_module()
    source = tmp_path / "twitch_cache" / "source.mp4"
    monkeypatch.setattr(disk_budget, "filesystem_identity", lambda _path: "project-volume")
    monkeypatch.setattr(disk_budget, "disk_free_bytes", lambda _path: 100 * GiB)

    pending = disk_budget.estimate_analysis_disk_budget(
        tmp_path,
        duration_seconds=3600,
        source_path=source,
        pending_source_bytes=4 * GiB,
        visual_scan_samples=0,
    )
    source.parent.mkdir()
    source.write_bytes(b"downloaded")
    existing = disk_budget.estimate_analysis_disk_budget(
        tmp_path,
        duration_seconds=3600,
        source_path=source,
        pending_source_bytes=4 * GiB,
        visual_scan_samples=0,
    )

    assert pending["components"]["source_requirement_bytes"] == 4 * GiB
    assert existing["components"]["source_requirement_bytes"] == 0
    assert pending["required_bytes"] > existing["required_bytes"] + 3 * GiB


def test_pending_twitch_source_stops_before_downloader_when_space_is_insufficient(monkeypatch, tmp_path: Path):
    disk_budget = budget_module()
    project = {
        "source_type": "twitch",
        "source_url": "https://www.twitch.tv/videos/1234567890",
        "twitch": {
            "url": "https://www.twitch.tv/videos/1234567890",
            "source_kind": "vod",
            "download_engine": "yt-dlp",
            "fallback_enabled": False,
            "start_seconds": 0.0,
            "end_seconds": 3600.0,
            "format_selector": "best",
        },
    }
    write_json(tmp_path / "project.json", project)
    monkeypatch.setattr(disk_budget, "filesystem_identity", lambda _path: "project-volume")
    monkeypatch.setattr(disk_budget, "disk_free_bytes", lambda _path: GiB)
    downloader_calls: list[bool] = []
    monkeypatch.setattr(twitch_source, "_prepare_ytdlp_vod", lambda *_args, **_kwargs: downloader_calls.append(True))

    logger = SimpleNamespace(log=lambda *_args: None, set_status=lambda *_args, **_kwargs: None)
    with pytest.raises(twitch_source.DiskBudgetError, match="свободно.*требуется.*Освободите"):
        twitch_source.prepare_twitch_source(tmp_path, {}, logger)

    assert downloader_calls == []
    assert not list((tmp_path / "twitch_cache").rglob("*.part"))


def test_budget_checks_actual_temp_and_output_filesystems(monkeypatch, tmp_path: Path):
    disk_budget = budget_module()
    temp_root = tmp_path / "temporary disk"
    output_root = tmp_path / "output disk"
    temp_root.mkdir()
    output_root.mkdir()
    free = {"temp-volume": 3 * GiB, "output-volume": 100 * 1024**2}

    monkeypatch.setattr(
        disk_budget,
        "filesystem_identity",
        lambda path: "output-volume" if Path(path).is_relative_to(output_root) else "temp-volume",
    )
    monkeypatch.setattr(disk_budget, "disk_free_bytes", lambda path: free[disk_budget.filesystem_identity(path)])

    report = disk_budget.estimate_render_disk_budget(
        tmp_path,
        selected_duration_seconds=3600,
        source_size_bytes=8 * GiB,
        source_duration_seconds=10 * 3600,
        temp_dir=temp_root,
        output_path=output_root / "highlight final.mp4",
    )

    by_id = {item["filesystem_id"]: item for item in report["filesystems"]}
    assert set(by_id) == {"temp-volume", "output-volume"}
    assert by_id["temp-volume"]["path"] == str(temp_root)
    assert by_id["output-volume"]["path"] == str(output_root)
    assert by_id["output-volume"]["ok"] is False
    assert report["ok"] is False


def _patch_transcribe_runtime(monkeypatch, source: Path, *, cancelled: bool = False):
    commands: list[list[str]] = []
    extracted: list[tuple[Path, Path]] = []

    class Model:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, audio, **_kwargs):
            if cancelled:
                raise OperationCancelled("cancelled")
            return iter([SimpleNamespace(start=0.0, end=1.0, text="ok", words=[])]), None

        def close(self):
            pass

    import highlight_studio.services.whisper_worker as worker_module

    monkeypatch.setattr(worker_module, "WhisperProcess", Model)
    monkeypatch.setattr(pipeline, "project_paths", lambda project: {"video": source, "transcript": project / "transcript.json", "transcript_txt": project / "transcript.txt"})
    monkeypatch.setattr(pipeline, "transcript_fingerprint", lambda *_args: "generation")
    monkeypatch.setattr(pipeline, "transcript_fingerprint_legacy", lambda *_args: "legacy")
    monkeypatch.setattr(pipeline, "detect_hardware_capabilities", lambda: {"cpu": {"logical_threads": 2}})
    monkeypatch.setattr(pipeline, "video_duration", lambda _path: 120.0)
    monkeypatch.setattr(pipeline, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(pipeline, "extract_audio", lambda src, dst, _logger: extracted.append((src, dst)))

    def fake_run(cmd, **_kwargs):
        commands.append([str(item) for item in cmd])
        Path(cmd[-1]).write_bytes(b"wav")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(pipeline, "run_cmd", fake_run)
    return commands, extracted


def test_transcription_extracts_chunks_directly_from_source_and_cleans_them(monkeypatch, tmp_path: Path):
    source = tmp_path / "source video.mp4"
    source.write_bytes(b"source")
    commands, extracted = _patch_transcribe_runtime(monkeypatch, source)

    rows = pipeline.transcribe(tmp_path, {"whisper_device": "cpu", "chunk_seconds": 60}, pipeline.JobLogger(tmp_path))

    assert rows
    assert extracted == []
    assert commands and all(str(source) in cmd for cmd in commands)
    assert not list((tmp_path / "transcript_chunks").rglob("chunk_*.wav"))
    assert len(list((tmp_path / "transcript_chunks").rglob("transcript_*.json"))) == 2


def test_cancel_cleans_chunk_but_preserves_source_and_project_state(monkeypatch, tmp_path: Path):
    source = tmp_path / "исходник с пробелами.mp4"
    source.write_bytes(b"source")
    original_project = {"id": "p1", "name": "Пользовательский проект"}
    write_json(tmp_path / "project.json", original_project)
    _patch_transcribe_runtime(monkeypatch, source, cancelled=True)

    with pytest.raises(OperationCancelled):
        pipeline.transcribe(tmp_path, {"whisper_device": "cpu", "chunk_seconds": 60}, pipeline.JobLogger(tmp_path))

    assert source.read_bytes() == b"source"
    assert read_json(tmp_path / "project.json", {}) == original_project
    assert not list((tmp_path / "transcript_chunks").rglob("chunk_*.wav"))


def test_cleanup_is_project_scoped_idempotent_and_boundary_safe(tmp_path: Path):
    disk_budget = budget_module()
    project_a = tmp_path / "Проект с пробелами" / ("длинный-путь-" * 8)
    project_b = tmp_path / "other-project"
    for project in (project_a, project_b):
        (project / "transcript_chunks" / "generation").mkdir(parents=True)
        (project / "outputs").mkdir()
        (project / "audio_16k.wav").write_bytes(b"temporary full wav")
        (project / "transcript_chunks" / "generation" / "chunk_0001.wav").write_bytes(b"temporary chunk")
        (project / "transcript_chunks" / "generation" / "transcript_0001.json").write_text("[]", encoding="utf-8")
        (project / "input.mp4").write_bytes(b"source")
        (project / "outputs" / "highlight_final.mp4").write_bytes(b"final")
        write_json(project / "project.json", {"id": project.name})
    unrelated = project_a / "keep.wav"
    unrelated.write_bytes(b"user data")

    removed = disk_budget.cleanup_project_audio_temps(project_a)
    removed_again = disk_budget.cleanup_project_audio_temps(project_a)

    assert len(removed) == 2
    assert removed_again == []
    assert not (project_a / "audio_16k.wav").exists()
    assert not (project_a / "transcript_chunks" / "generation" / "chunk_0001.wav").exists()
    assert (project_a / "transcript_chunks" / "generation" / "transcript_0001.json").exists()
    assert (project_a / "input.mp4").read_bytes() == b"source"
    assert (project_a / "outputs" / "highlight_final.mp4").read_bytes() == b"final"
    assert unrelated.read_bytes() == b"user data"
    assert (project_b / "audio_16k.wav").exists()
    assert read_json(project_a / "project.json", {}) == {"id": project_a.name}


def test_restart_removes_only_stale_audio_temp_after_crash(tmp_path: Path):
    disk_budget = budget_module()
    project = tmp_path / "restart"
    chunks = project / "transcript_chunks" / "generation"
    chunks.mkdir(parents=True)
    (project / "audio_16k.wav").write_bytes(b"stale")
    (chunks / "chunk_0001.wav").write_bytes(b"stale")
    (chunks / "transcript_0001.json").write_text(json.dumps([{"start": 0, "end": 1, "text": "checkpoint"}]), encoding="utf-8")
    write_json(project / "project.json", {"state": "unchanged"})

    disk_budget.cleanup_project_audio_temps(project)

    assert not (project / "audio_16k.wav").exists()
    assert not (chunks / "chunk_0001.wav").exists()
    assert (chunks / "transcript_0001.json").exists()
    assert read_json(project / "project.json", {}) == {"state": "unchanged"}


def test_insufficient_message_contains_free_required_path_and_action(monkeypatch, tmp_path: Path):
    disk_budget = budget_module()
    source = tmp_path / "input.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(disk_budget, "filesystem_identity", lambda _path: "volume")
    monkeypatch.setattr(disk_budget, "disk_free_bytes", lambda _path: 100 * 1024**2)

    report = disk_budget.estimate_analysis_disk_budget(
        tmp_path,
        duration_seconds=20 * 3600,
        source_path=source,
        visual_scan_samples=0,
    )

    assert report["ok"] is False
    message = report["message"]
    assert "свобод" in message.lower()
    assert "треб" in message.lower()
    assert str(tmp_path) in message
    assert "освобод" in message.lower()
