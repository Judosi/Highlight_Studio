from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from highlight_studio.core.utils import OperationCancelled, read_json, write_json
from highlight_studio.integrations.twitch import source


class RecordingLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.statuses: list[dict] = []

    def log(self, message: str) -> None:
        self.messages.append(message)

    def set_status(self, state: str, progress: float, message: str, **details) -> None:
        self.statuses.append({"state": state, "progress": progress, "message": message, **details})


def make_vod_project(
    project_dir: Path,
    *,
    engine: str = "yt-dlp",
    start: float | None = 120.0,
    end: float | None = 3720.0,
    format_selector: str = "best",
    quality: str = "best",
    fallback_enabled: bool = False,
) -> dict:
    project_dir.mkdir(parents=True, exist_ok=True)
    project = {
        "id": project_dir.name,
        "source_type": "twitch",
        "source_url": "https://www.twitch.tv/videos/1234567890",
        "settings": {},
        "twitch": {
            "url": "https://www.twitch.tv/videos/1234567890",
            "source_kind": "vod",
            "download_engine": engine,
            "fallback_enabled": fallback_enabled,
            "start_seconds": start,
            "end_seconds": end,
            "format_selector": format_selector,
            "quality": quality,
        },
    }
    write_json(project_dir / "project.json", project)
    return project


def patch_ytdlp_runtime(monkeypatch, runner) -> None:
    monkeypatch.setattr(source, "_module_or_cmd_available", lambda *args: True)
    monkeypatch.setattr(source, "_run_twitch_cmd_streamed", runner)
    monkeypatch.setattr(
        source,
        "validate_media_file",
        lambda path, **kwargs: {
            "ok": Path(path).is_file() and Path(path).stat().st_size > 1024,
            "duration_seconds": 3600,
        },
    )


@pytest.mark.parametrize("percent", [30, 70, 90])
def test_interrupted_ytdlp_download_resumes_existing_part_and_ytdl(tmp_path, monkeypatch, percent):
    project_dir = tmp_path / f"resume-{percent}"
    make_vod_project(project_dir)
    calls: list[Path] = []
    partial_bytes = percent * 4096

    def runner(cmd, **kwargs):
        cache_dir = Path(kwargs["cache_dir"])
        calls.append(cache_dir)
        part = cache_dir / "source.mp4.part"
        state = cache_dir / "source.mp4.ytdl"
        if len(calls) == 1:
            part.write_bytes(b"p" * partial_bytes)
            state.write_text(json.dumps({"downloaded": percent}), encoding="utf-8")
            return SimpleNamespace(returncode=1, stdout="connection interrupted")
        assert cache_dir == calls[0]
        assert part.stat().st_size == partial_bytes
        assert read_json(state, {}) == {"downloaded": percent}
        assert "--continue" in cmd
        (cache_dir / "source.mp4").write_bytes(b"v" * 4096)
        return SimpleNamespace(returncode=0, stdout="download complete")

    patch_ytdlp_runtime(monkeypatch, runner)
    with pytest.raises(RuntimeError, match="не смог подготовить VOD"):
        source.prepare_twitch_source(project_dir, {}, RecordingLogger())

    result = source.prepare_twitch_source(project_dir, {}, RecordingLogger())

    assert result["status"] == "ready"
    assert len(calls) == 2
    assert calls[0] == calls[1]


def test_ytdlp_does_not_delete_compatible_partials_before_resume(tmp_path, monkeypatch):
    project_dir = tmp_path / "keep-partials"
    cache_dir = project_dir / "twitch_cache" / "existing"
    cache_dir.mkdir(parents=True)
    part = cache_dir / "source.mp4.part"
    state = cache_dir / "source.mp4.ytdl"
    part.write_bytes(b"partial-data" * 500)
    state.write_text('{"fragment_index": 42}', encoding="utf-8")
    make_vod_project(project_dir)
    observed: dict[str, bool] = {}

    def runner(cmd, **kwargs):
        observed["part"] = part.exists()
        observed["ytdl"] = state.exists()
        (cache_dir / "source.mp4").write_bytes(b"v" * 4096)
        return SimpleNamespace(returncode=0, stdout="ok")

    patch_ytdlp_runtime(monkeypatch, runner)
    twitch = read_json(project_dir / "project.json", {})["twitch"]
    info = source.classify_twitch_url(twitch["url"], "vod")
    source._prepare_ytdlp_vod(project_dir, cache_dir, info, twitch, {}, RecordingLogger())

    assert observed == {"part": True, "ytdl": True}


def test_interrupted_tdcli_download_reuses_stable_temp_directory(tmp_path, monkeypatch):
    project_dir = tmp_path / "tdcli-resume"
    make_vod_project(project_dir, engine="twitchdownloadercli")
    calls: list[Path] = []

    def runner(cmd, **kwargs):
        cache_dir = Path(kwargs["cache_dir"])
        calls.append(cache_dir)
        fragment = cache_dir / "tdcli_temp" / "1234567890" / "segment-0042.ts"
        if len(calls) == 1:
            fragment.parent.mkdir(parents=True, exist_ok=True)
            fragment.write_bytes(b"downloaded-fragment")
            return SimpleNamespace(returncode=1, stdout="connection interrupted")
        assert cache_dir == calls[0]
        assert fragment.read_bytes() == b"downloaded-fragment"
        output = Path(cmd[cmd.index("--output") + 1])
        output.write_bytes(b"v" * 4096)
        return SimpleNamespace(returncode=0, stdout="download complete")

    monkeypatch.setattr(source, "_twitch_downloader_cli_cmd", lambda settings=None: ["TwitchDownloaderCLI.exe"])
    monkeypatch.setattr(source, "_run_twitch_cmd_streamed", runner)
    monkeypatch.setattr(source, "validate_media_file", lambda path, **kwargs: {"ok": Path(path).stat().st_size > 1024})

    with pytest.raises(RuntimeError, match="не смог подготовить VOD"):
        source.prepare_twitch_source(project_dir, {}, RecordingLogger())
    result = source.prepare_twitch_source(project_dir, {}, RecordingLogger())

    assert result["status"] == "ready"
    assert calls[0] == calls[1]


def test_same_download_parameters_use_stable_cache_identity(tmp_path, monkeypatch):
    project_dir = tmp_path / "stable-cache"
    make_vod_project(project_dir)
    attempt_caches: list[Path] = []

    def interrupted(project_dir, cache, info, twitch, settings, logger, **kwargs):
        attempt_caches.append(cache)
        (cache / "source.mp4.part").write_bytes(b"partial")
        raise OperationCancelled("simulated crash")

    monkeypatch.setattr(source, "_prepare_ytdlp_vod", interrupted)
    clock = [1000.0]

    def tick():
        clock[0] += 1.0
        return clock[0]

    monkeypatch.setattr(source.time, "time", tick)

    for _ in range(2):
        with pytest.raises(OperationCancelled):
            source.prepare_twitch_source(project_dir, {}, RecordingLogger())

    assert attempt_caches[0] == attempt_caches[1]
    manifest = read_json(attempt_caches[0] / "download_manifest.json", {})
    assert manifest["version"] == 1
    assert manifest["cache_id"] == attempt_caches[0].name
    assert manifest["identity"] == {
        "engine": "yt-dlp",
        "format": "best",
        "range": {"end_seconds": 3720.0, "start_seconds": 120.0},
        "vod_id": "1234567890",
    }


def test_range_format_and_engine_have_distinct_cache_identities(tmp_path, monkeypatch):
    project_dir = tmp_path / "distinct-cache"
    recorded: list[Path] = []

    def interrupted(project_dir, cache, info, twitch, settings, logger, **kwargs):
        recorded.append(cache)
        raise OperationCancelled("stop after selecting cache")

    monkeypatch.setattr(source, "_prepare_ytdlp_vod", interrupted)
    cases = [
        {"engine": "yt-dlp", "start": 0.0, "end": 100.0, "format_selector": "best"},
        {"engine": "yt-dlp", "start": 10.0, "end": 100.0, "format_selector": "best"},
        {"engine": "yt-dlp", "start": 0.0, "end": 100.0, "format_selector": "720p"},
        {"engine": "yt-dlp-aria2c", "start": 0.0, "end": 100.0, "format_selector": "best"},
    ]
    for case in cases:
        make_vod_project(project_dir, **case)
        with pytest.raises(OperationCancelled):
            source.prepare_twitch_source(project_dir, {}, RecordingLogger())

    assert len({path.name for path in recorded}) == len(cases)


def test_incompatible_manifest_is_quarantined_instead_of_resumed(tmp_path, monkeypatch):
    project_dir = tmp_path / "incompatible-cache"
    make_vod_project(project_dir)
    attempt_caches: list[Path] = []

    def interrupted(project_dir, cache, info, twitch, settings, logger, **kwargs):
        attempt_caches.append(cache)
        part = cache / "source.mp4.part"
        if len(attempt_caches) == 1:
            part.write_bytes(b"partial-from-other-settings")
        else:
            assert not part.exists(), "incompatible partial was offered to the downloader"
        raise OperationCancelled("simulated restart")

    monkeypatch.setattr(source, "_prepare_ytdlp_vod", interrupted)
    with pytest.raises(OperationCancelled):
        source.prepare_twitch_source(project_dir, {}, RecordingLogger())

    manifest_path = attempt_caches[0] / "download_manifest.json"
    manifest = read_json(manifest_path, {})
    manifest["identity"]["format"] = "incompatible-format"
    write_json(manifest_path, manifest)

    with pytest.raises(OperationCancelled):
        source.prepare_twitch_source(project_dir, {}, RecordingLogger())

    quarantined = list((project_dir / "twitch_cache" / "orphans").rglob("source.mp4.part"))
    assert quarantined and quarantined[0].read_bytes() == b"partial-from-other-settings"


def test_fallback_cleans_failed_copy_only_after_valid_final_exists(tmp_path, monkeypatch):
    project_dir = tmp_path / "fallback-cleanup"
    make_vod_project(project_dir, engine="twitchdownloadercli", fallback_enabled=True)
    failed_files: list[Path] = []
    final_files: list[Path] = []

    def failed_tdcli(project_dir, cache, info, twitch, settings, logger):
        failed = cache / "failed-full.mp4"
        failed.write_bytes(b"f" * 4096)
        failed_files.append(failed)
        raise RuntimeError("tdcli failed after producing a file")

    def successful_ytdlp(project_dir, cache, info, twitch, settings, logger, **kwargs):
        assert failed_files[0].exists(), "failed attempt was cleaned before a valid replacement existed"
        final = cache / "source.mp4"
        final.write_bytes(b"v" * 4096)
        final_files.append(final)
        return {"status": "ready", "cached_video_path": str(final.resolve())}

    monkeypatch.setattr(source, "_prepare_tdcli_vod", failed_tdcli)
    monkeypatch.setattr(source, "_prepare_ytdlp_vod", successful_ytdlp)
    monkeypatch.setattr(source, "validate_media_file", lambda path, **kwargs: {"ok": Path(path) == final_files[0]})

    result = source.prepare_twitch_source(project_dir, {}, RecordingLogger())

    assert result["status"] == "ready"
    assert not failed_files[0].exists()
    assert final_files[0].exists()
    assert len(list((project_dir / "twitch_cache").rglob("*.mp4"))) == 1


def test_all_failed_fallback_attempts_are_preserved_for_resume(tmp_path, monkeypatch):
    project_dir = tmp_path / "failed-fallback"
    make_vod_project(project_dir, engine="twitchdownloadercli", fallback_enabled=True)
    partials: list[Path] = []

    def fail(project_dir, cache, info, twitch, settings, logger, **kwargs):
        partial = cache / f"{len(partials)}.part"
        partial.write_bytes(b"still-useful")
        partials.append(partial)
        raise RuntimeError("network down")

    monkeypatch.setattr(source, "_prepare_tdcli_vod", fail)
    monkeypatch.setattr(source, "_prepare_ytdlp_vod", fail)

    with pytest.raises(RuntimeError, match="не смог подготовить VOD"):
        source.prepare_twitch_source(project_dir, {}, RecordingLogger())

    assert len(partials) == 3
    assert all(path.exists() for path in partials)


def test_crash_does_not_corrupt_or_rewrite_project_state(tmp_path, monkeypatch):
    project_dir = tmp_path / "crash-state"
    original = make_vod_project(project_dir)

    def crash(project_dir, cache, info, twitch, settings, logger, **kwargs):
        (cache / "source.mp4.part").write_bytes(b"partial")
        raise OperationCancelled("application terminated")

    monkeypatch.setattr(source, "_prepare_ytdlp_vod", crash)
    with pytest.raises(OperationCancelled):
        source.prepare_twitch_source(project_dir, {}, RecordingLogger())

    assert read_json(project_dir / "project.json", {}) == original
    manifest_files = list((project_dir / "twitch_cache").rglob("download_manifest.json"))
    assert len(manifest_files) == 1
    assert read_json(manifest_files[0], {})["state"] == "partial"


def test_resume_cache_supports_windows_spaces_cyrillic_and_long_paths(tmp_path, monkeypatch):
    project_dir = tmp_path / "Проект с пробелами" / ("длинный-путь-" * 10)
    make_vod_project(project_dir)
    seen: list[Path] = []

    def interrupted(project_dir, cache, info, twitch, settings, logger, **kwargs):
        seen.append(cache)
        raise OperationCancelled("restart")

    monkeypatch.setattr(source, "_prepare_ytdlp_vod", interrupted)
    for _ in range(2):
        with pytest.raises(OperationCancelled):
            source.prepare_twitch_source(project_dir, {}, RecordingLogger())

    assert seen[0] == seen[1]
    assert "Проект с пробелами" in str(seen[0])
    assert len(str(seen[0])) > 180
    assert (seen[0] / "download_manifest.json").is_file()
