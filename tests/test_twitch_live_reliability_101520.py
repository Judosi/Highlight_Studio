from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from highlight_studio.integrations.twitch import source


class _Stop:
    def set(self) -> None:
        pass


class _Logger:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.statuses: list[dict] = []

    def log(self, message: str) -> None:
        self.logs.append(str(message))

    def set_status(self, state, progress, message, **extra) -> None:
        self.statuses.append({"state": state, "progress": progress, "message": message, **extra})


def _project(tmp_path: Path, minutes: int = 150) -> Path:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "source_type": "twitch",
                "source_url": "https://www.twitch.tv/example_channel",
                "twitch": {
                    "url": "https://www.twitch.tv/example_channel",
                    "source_kind": "live",
                    "live_record_minutes": minutes,
                },
            }
        ),
        encoding="utf-8",
    )
    return project_dir


def _patch_common(monkeypatch, recorded_seconds: float):
    signed_url = "https://signed.example.invalid/playlist.m3u8?token=TOP_SECRET_TOKEN"

    monkeypatch.setattr(source, "_module_or_cmd_available", lambda *args, **kwargs: True)
    monkeypatch.setattr(source, "which", lambda name: "ffmpeg" if name == "ffmpeg" else name)
    monkeypatch.setattr(source, "_python_module_cmd", lambda *args, **kwargs: ["streamlink"])
    monkeypatch.setattr(source, "_start_cache_progress_monitor", lambda *args, **kwargs: _Stop())
    monkeypatch.setattr(
        source,
        "validate_media_file",
        lambda *args, **kwargs: {
            "ok": True,
            "duration_seconds": recorded_seconds,
            "has_video": True,
            "has_audio": True,
        },
    )
    monkeypatch.setattr(source, "_update_project_source", lambda project_dir, out, twitch: {"twitch": twitch})

    def fake_run_cmd(cmd, timeout=None, project_dir=None, cancel_file=None):
        if "--stream-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, signed_url + "\n", None)
        raise subprocess.TimeoutExpired(cmd, timeout or 0, output="ffmpeg still running")

    monkeypatch.setattr(source, "run_cmd", fake_run_cmd)
    return signed_url


def test_near_complete_live_recording_is_salvaged_when_wallclock_guard_fires(monkeypatch, tmp_path):
    project_dir = _project(tmp_path, minutes=150)
    _patch_common(monkeypatch, recorded_seconds=8945.0)
    logger = _Logger()

    result = source.prepare_twitch_source(project_dir, {}, logger)

    assert result["status"] == "ready"
    assert result["live_record_seconds"] == 9000
    assert result["recorded_duration_seconds"] == 8945.0
    assert result["live_completion_mode"] == "wallclock_guard_salvaged"
    assert logger.statuses[-1]["state"] == "done"
    assert logger.statuses[-1]["progress"] == 100
    assert "02:29:05" in logger.statuses[-1]["message"]


def test_short_partial_live_recording_fails_with_safe_message_and_keeps_partial_file(monkeypatch, tmp_path):
    project_dir = _project(tmp_path, minutes=150)
    signed_url = _patch_common(monkeypatch, recorded_seconds=3600.0)
    logger = _Logger()

    with pytest.raises(RuntimeError) as exc_info:
        source.prepare_twitch_source(project_dir, {}, logger)

    message = str(exc_info.value)
    assert "Записано: 01:00:00" in message
    assert "ожидалось: 02:30:00" in message
    assert "twitch_cache/source_live.ts" in message
    assert signed_url not in message
    assert "TOP_SECRET_TOKEN" not in message



def test_existing_near_complete_live_cache_is_recovered_without_re_recording(monkeypatch, tmp_path):
    project_dir = _project(tmp_path, minutes=150)
    cache = project_dir / "twitch_cache"
    cache.mkdir()
    (cache / "source_live.ts").write_bytes(b"existing-recording")
    logger = _Logger()

    monkeypatch.setattr(source, "_module_or_cmd_available", lambda *args, **kwargs: True)
    monkeypatch.setattr(source, "which", lambda name: "ffmpeg" if name == "ffmpeg" else name)
    monkeypatch.setattr(
        source,
        "validate_media_file",
        lambda *args, **kwargs: {"ok": True, "duration_seconds": 8945.0, "has_video": True, "has_audio": True},
    )
    monkeypatch.setattr(source, "_update_project_source", lambda project_dir, out, twitch: {"twitch": twitch})

    def must_not_run(*args, **kwargs):
        raise AssertionError("A valid near-complete cache must be reused without starting Streamlink/FFmpeg")

    monkeypatch.setattr(source, "run_cmd", must_not_run)
    monkeypatch.setattr(source, "_python_module_cmd", lambda *args, **kwargs: ["streamlink"] )

    result = source.prepare_twitch_source(project_dir, {}, logger)

    assert result["status"] == "ready"
    assert result["live_completion_mode"] == "recovered_existing"
    assert result["recorded_duration_seconds"] == 8945.0
    assert logger.statuses[-1]["progress_source"] == "recovered_existing"

def test_live_progress_monitor_accepts_exact_target_duration_contract():
    text = Path(source.__file__).read_text(encoding="utf-8")
    assert "target_seconds=float(seconds)" in text
    assert "wallclock_grace = 90" in text
    assert "seconds + 300" not in text
