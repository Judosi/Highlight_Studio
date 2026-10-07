from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.main import default_settings
from backend.pipeline import JobLogger, render, result_check
from backend.utils import read_json, write_json


FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is not installed")
@pytest.mark.parametrize("directory", ["ascii", "Видео с пробелом ' и кириллицей"])
def test_real_ffmpeg_render_creates_valid_video_and_audio(tmp_path: Path, directory):
    tmp_path = tmp_path / directory
    tmp_path.mkdir()
    source = tmp_path / "input.mp4"
    completed = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=20",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000",
            "-t",
            "2.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    write_json(
        tmp_path / "segments.json",
        [
            {"id": 1, "start": 0.0, "end": 1.0, "score": 9, "title": "A", "reason": ""},
            {"id": 2, "start": 1.2, "end": 2.3, "score": 8, "title": "B", "reason": ""},
            {"id": 3, "start": 3.0, "end": 4.0, "score": 8, "title": "Past EOF", "reason": ""},
            {"id": 4, "start": "NaN", "end": 1.0, "score": 8, "title": "Invalid", "reason": ""},
        ],
    )
    settings = default_settings()
    settings.update(
        {
            "video_encoder": "libx264",
            "render_preset": "ultrafast",
            "crf": 32,
            "make_srt": False,
            "remove_silence": False,
        }
    )

    rendered = render(tmp_path, settings, JobLogger(tmp_path))
    report = result_check(tmp_path)

    assert report["ok"] is True
    assert report["audio_streams"] >= 1
    assert report["video"]["width"] == 320
    assert Path(rendered["output"]).exists()
    assert read_json(tmp_path / "render_parts" / "part_001.json", {})["encoder_used"] == "libx264"
    assert not (tmp_path / "render_parts" / "part_003.mp4").exists()
    assert not (tmp_path / "render_parts" / "part_004.mp4").exists()
