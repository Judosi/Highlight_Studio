from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.pipeline import (
    JobLogger,
    _shorts_filter_complex,
    _write_short_ass,
    _write_short_srt,
    normalize_shorts_candidates,
    render_shorts_candidates,
)
from backend.utils import read_json, write_json


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def test_normalize_shorts_rejects_invalid_and_overlap() -> None:
    items = [
        {"start": 10, "end": 25, "score": 9, "title": "strong"},
        {"start": 11, "end": 24, "score": 7, "title": "duplicate"},
        {"start": 40, "end": 35, "score": 10, "title": "backwards"},
        {"start": 98, "end": 130, "score": 8, "title": "clamped"},
        {"start": "NaN", "end": 5, "score": 8, "title": "invalid"},
    ]
    accepted, rejected = normalize_shorts_candidates(items, 100, limit=10, min_seconds=2, max_seconds=60)

    assert [item["title"] for item in accepted] == ["strong", "clamped"]
    assert accepted[1]["end"] == 100
    reasons = {item["reason"] for item in rejected}
    assert "overlap_duplicate" in reasons
    assert "end_not_after_start" in reasons


def test_short_srt_is_clipped_to_candidate(tmp_path: Path) -> None:
    write_json(
        tmp_path / "transcript.json",
        [
            {"start": 8.0, "end": 11.0, "text": "до начала"},
            {"start": 11.0, "end": 13.0, "text": "первая фраза"},
            {"start": 14.0, "end": 18.0, "text": "вторая фраза"},
            {"start": 20.0, "end": 22.0, "text": "после конца"},
        ],
    )
    out = tmp_path / "short.srt"
    count = _write_short_srt(tmp_path, 10.0, 20.0, out)
    text = out.read_text(encoding="utf-8")

    assert count == 3
    assert "00:00:00,000 --> 00:00:01,000" in text
    assert "первая фраза" in text
    assert "вторая фраза" in text
    assert "после конца" not in text


def test_shorts_v2_captions_are_short_and_ass_contains_hook(tmp_path: Path) -> None:
    write_json(
        tmp_path / "transcript.json",
        [
            {
                "start": 10.0,
                "end": 14.0,
                "text": "Это очень длинная реплика которая должна разбиться на короткие фразы",
                "words": [
                    {"start": 10.0 + index * 0.35, "end": 10.3 + index * 0.35, "word": word}
                    for index, word in enumerate("Это очень длинная реплика которая должна разбиться на короткие фразы".split())
                ],
            }
        ],
    )
    srt = tmp_path / "short.srt"
    ass = tmp_path / "short.ass"

    count = _write_short_srt(tmp_path, 10.0, 15.0, srt, max_words=5)
    _write_short_ass(
        tmp_path,
        10.0,
        15.0,
        ass,
        hook_title="Она сказала то, чего никто не ожидал",
        max_words=5,
    )

    assert count == 2
    srt_blocks = [block for block in srt.read_text(encoding="utf-8").strip().split("\n\n") if block]
    assert all(len(" ".join(block.splitlines()[2:]).split()) <= 5 for block in srt_blocks)
    ass_text = ass.read_text(encoding="utf-8-sig")
    assert "PlayResX: 1080" in ass_text
    assert "Style: Hook" in ass_text
    assert "Она сказала" in ass_text
    assert "{\\k" in ass_text


def test_smart_zoom_filter_enlarges_foreground_and_keeps_vertical_canvas() -> None:
    value = _shorts_filter_complex("smart_zoom")
    assert "scale=1280:1920" in value
    assert "crop='min(iw,1080)'" in value
    assert "overlay=(W-w)/2:(H-h)/2" in value
    assert value.endswith("[vout]")


@pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="FFmpeg/ffprobe is not installed")
def test_real_shorts_render_validates_and_cleans_stale_outputs(tmp_path: Path) -> None:
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
            "6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
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

    factory = tmp_path / "content_factory"
    out_dir = tmp_path / "outputs" / "shorts"
    factory.mkdir()
    out_dir.mkdir(parents=True)
    (out_dir / "short_09.mp4").write_bytes(b"stale")
    write_json(
        factory / "shorts_candidates.json",
        [
            {"start": 0.5, "end": 4.5, "score": 9, "title": "valid"},
            {"start": 100, "end": 110, "score": 10, "title": "past eof"},
            {"start": 4, "end": 2, "score": 10, "title": "backwards"},
        ],
    )
    write_json(
        tmp_path / "transcript.json",
        [{"start": 1.0, "end": 2.5, "text": "тестовые субтитры"}],
    )

    result = render_shorts_candidates(
        tmp_path,
        {
            "shorts_count": 3,
            "shorts_vertical_reframe": True,
            "shorts_reframe_mode": "blur_background",
            "shorts_burn_subtitles": False,
            "shorts_normalize_audio": False,
            "shorts_render_preset": "ultrafast",
            "shorts_crf": 32,
            "shorts_min_seconds": 2,
            "shorts_max_seconds": 60,
        },
        JobLogger(tmp_path),
    )

    assert len(result["rendered"]) == 1
    assert not (out_dir / "short_09.mp4").exists()
    output = out_dir / "short_01.mp4"
    assert output.stat().st_size > 10_000
    assert result["rendered"][0]["width"] == 1080
    assert result["rendered"][0]["height"] == 1920
    assert result["rendered"][0]["audio_normalized"] is False
    assert {item["reason"] for item in result["rejected"]} >= {"end_not_after_start"}
    manifest = read_json(tmp_path / "shorts_render_manifest.json", {})
    assert manifest["rendered"][0]["title"] == "valid"


@pytest.mark.skipif(not FFMPEG, reason="FFmpeg is not installed")
def test_blur_background_keeps_off_center_subject_visible(tmp_path: Path) -> None:
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
            "color=c=black:s=320x180:r=15",
            "-vf",
            "drawbox=x=8:y=45:w=50:h=90:color=red:t=fill,drawbox=x=135:y=45:w=50:h=90:color=green:t=fill",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    factory = tmp_path / "content_factory"
    factory.mkdir()
    write_json(factory / "shorts_candidates.json", [{"start": 0, "end": 3, "score": 9, "title": "left subject"}])

    result = render_shorts_candidates(
        tmp_path,
        {
            "shorts_count": 1,
            "shorts_vertical_reframe": True,
            "shorts_reframe_mode": "blur_background",
            "shorts_burn_subtitles": False,
            "shorts_normalize_audio": False,
            "shorts_render_preset": "ultrafast",
            "shorts_crf": 32,
            "shorts_min_seconds": 2,
        },
        JobLogger(tmp_path),
    )
    output = tmp_path / result["rendered"][0]["path"]
    frame = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "1",
            "-i",
            str(output),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        timeout=30,
        check=True,
    ).stdout
    red_pixels = 0
    for offset in range(0, len(frame) - 2, 3):
        red, green, blue = frame[offset : offset + 3]
        if red > 140 and red > green * 1.4 and red > blue * 1.4:
            red_pixels += 1
    assert red_pixels > 5_000
