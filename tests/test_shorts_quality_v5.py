from pathlib import Path
from types import SimpleNamespace
import importlib.util
import shutil
import subprocess
import sys

import pytest

from highlight_studio.services.pipeline import (
    _shorts_candidate_pool,
    _shorts_semantic_rejected,
    shorts_candidate_score,
    optimize_short_boundaries,
    _write_short_ass,
    shorts_quality_components,
    _manual_caption_transcript,
    _short_render_fingerprint,
    _shorts_filter_complex,
    _sensevoice_parse_features,
    _apply_sensevoice_features,
    _primary_face_points,
    _facecam_layout,
    _shorts_refined_transcript,
    _ffmpeg_filter_path,
    _ensure_shorts_candidates_current,
    _shorts_selection_fingerprint,
)
from highlight_studio.core.utils import write_json


def test_v5_pool_uses_micro_candidates(tmp_path: Path):
    write_json(tmp_path / "micro_candidates.json", [{"id": 11, "start": 10, "end": 30, "score": 8, "title": "угарная реакция"}])
    write_json(tmp_path / "block_candidates.json", [])
    write_json(tmp_path / "candidates.json", [])
    write_json(tmp_path / "segments.json", [])
    rows = _shorts_candidate_pool(tmp_path)
    assert rows and rows[0]["shorts_source"] == "micro"


def test_v5_semantic_guard_rejects_replay():
    assert _shorts_semantic_rejected({"content_class": "replay", "content_class_confidence": 0.95}, {"non_primary_reject_confidence": 0.72})
    assert not _shorts_semantic_rejected({"content_class": "primary_live", "content_class_confidence": 0.95}, {})


def test_v5_funny_reaction_scores_above_boring(tmp_path: Path):
    write_json(tmp_path / "audio_events.json", {"events": [{"time": 15, "end_time": 16, "type": "sudden_reaction", "score": 0.8}]})
    funny = {"start": 10, "end": 25, "score": 7.5, "title": "Угарная смешная реакция, неожиданный донат", "standalone_clarity": 0.9}
    boring = {"start": 40, "end": 55, "score": 7.5, "title": "спокойный разговор", "standalone_clarity": 0.5}
    assert shorts_candidate_score(tmp_path, funny, {})["short_score"] > shorts_candidate_score(tmp_path, boring, {})["short_score"]


def test_v5_boundary_optimizer_respects_max_duration(tmp_path: Path):
    write_json(tmp_path / "transcript.json", [
        {"start": 0, "end": 20, "text": "setup"},
        {"start": 40, "end": 45, "text": "payoff"},
        {"start": 46, "end": 50, "text": "reaction"},
    ])
    write_json(tmp_path / "audio_events.json", {"events": [{"time": 45, "type": "sudden_reaction", "score": 0.9}]})
    out = optimize_short_boundaries(tmp_path, {"start": 0, "end": 80}, {"shorts_max_seconds": 45, "shorts_min_seconds": 3}, 100)
    assert out["end"] - out["start"] <= 45.001
    assert out["reaction_timestamp"] > out["payoff_timestamp"]


def test_v5_ass_defaults_are_large_and_safe(tmp_path: Path):
    write_json(tmp_path / "transcript.json", [{"start": 0, "end": 2, "text": "Очень смешной момент", "words": []}])
    out = tmp_path / "short.ass"
    count = _write_short_ass(tmp_path, 0, 3, out, hook_title="Смотри!", max_words=4)
    text = out.read_text(encoding="utf-8-sig")
    assert count == 1
    assert "Style: Caption,Arial,72" in text
    assert "Style: Hook,Arial,82" in text
    assert ",380,1" in text


def test_v5_optional_components_are_nonfatal():
    data = shorts_quality_components()
    assert data["version"] == "5.1"
    assert data["whisperx"]["required"] is False
    assert data["sensevoice"]["required"] is False
    assert data["face_tracking"]["required"] is False



def test_v51_manual_caption_text_is_authoritative_but_keeps_word_timing():
    timing = [{
        "start": 10.0, "end": 12.0, "text": "Привет Влад",
        "words": [
            {"word": "Привет", "start": 10.0, "end": 10.7},
            {"word": "Влад", "start": 10.8, "end": 11.4},
        ],
    }]
    rows = _manual_caption_transcript("Привет Влада!", start=10.0, end=12.0, timing_rows=timing)
    assert rows and rows[0]["text"] == "Привет Влада!"
    words = rows[0]["words"]
    assert [word["word"] for word in words] == ["Привет", "Влада!"]
    assert words[0]["start"] == 10.0
    assert words[0]["end"] > words[0]["start"]
    assert words[1]["end"] <= 12.0


def test_v51_short_render_fingerprint_tracks_asr_quality_dictionary_and_alignment(tmp_path: Path, monkeypatch):
    from highlight_studio.services import pipeline
    monkeypatch.setattr(pipeline, "video_content_signature", lambda _project: "video-content")
    monkeypatch.setattr(pipeline, "transcript_fingerprint", lambda _project, _settings: "transcript")
    item = {"title": "one", "caption_text": ""}
    base = {
        "shorts_caption_quality": "fast", "shorts_whisper_model": "small",
        "shorts_recognition_dictionary": "Влада", "shorts_precise_alignment": False,
        "language": "ru",
    }
    first = _short_render_fingerprint(tmp_path, base, item, 1, 10)
    assert _short_render_fingerprint(tmp_path, {**base, "shorts_caption_quality": "high"}, item, 1, 10) != first
    assert _short_render_fingerprint(tmp_path, {**base, "shorts_recognition_dictionary": "Влада,Эвелон"}, item, 1, 10) != first
    assert _short_render_fingerprint(tmp_path, {**base, "shorts_precise_alignment": True}, item, 1, 10) != first


def test_v51_smart_face_filter_uses_dynamic_face_trajectory():
    graph = _shorts_filter_complex(
        "smart_face",
        reframe_plan={
            "resolved_mode": "smart_face",
            "trajectory": [{"t": 0.0, "cx": 0.25}, {"t": 2.0, "cx": 0.75}],
        },
    )
    assert "crop=1080:1920" in graph
    assert "if(lt(t," in graph
    assert "clip(iw*(" in graph


def test_v51_gameplay_facecam_filter_builds_split_layout():
    graph = _shorts_filter_complex(
        "gameplay_facecam",
        reframe_plan={
            "resolved_mode": "gameplay_facecam",
            "facecam": {"x": 0.73, "y": 0.68, "w": 0.24, "h": 0.27, "position": "bottom"},
        },
    )
    assert "split=2[game][face]" in graph
    assert "vstack=inputs=2" in graph
    assert "[gamev][facev]" in graph


def test_v51_sensevoice_tags_raise_funny_reaction_scores():
    features = _sensevoice_parse_features("<|HAPPY|><|Laughter|><|Applause|>")
    assert features["laughter"] > 0
    assert features["applause"] > 0
    before = {"short_score": 5.0, "funny_score": 4.0, "reaction_score": 4.0, "payoff_score": 4.0}
    after = _apply_sensevoice_features(before, features)
    assert after["short_score"] > before["short_score"]
    assert after["funny_score"] > before["funny_score"]
    assert "sensevoice_laughter" in after["short_score_reasons"]


def test_v51_boundary_uses_audio_payoff_not_legacy_fixed_percent(tmp_path: Path):
    write_json(tmp_path / "transcript.json", [
        {"start": 5.0, "end": 8.0, "text": "короткий setup"},
        {"start": 22.0, "end": 24.0, "text": "он упал смешно!"},
        {"start": 24.1, "end": 27.0, "text": "ахаха вот это реакция"},
    ])
    write_json(tmp_path / "audio_events.json", {"events": [
        {"time": 23.0, "end_time": 24.0, "type": "laughter", "score": 0.95},
    ]})
    out = optimize_short_boundaries(tmp_path, {"start": 0.0, "end": 40.0}, {"shorts_max_seconds": 40, "shorts_min_seconds": 3}, 60)
    legacy = 0.0 + 40.0 * 0.66
    assert out["payoff_source"] != "fallback_center"
    assert abs(out["payoff_timestamp"] - legacy) > 0.5
    assert 22.0 <= out["payoff_timestamp"] <= 24.5


def test_v51_karaoke_caption_has_restrained_active_word_pop(tmp_path: Path):
    out = tmp_path / "short.ass"
    transcript = [{
        "start": 0.0, "end": 2.0, "text": "Очень смешно",
        "words": [
            {"word": "Очень", "start": 0.0, "end": 0.8},
            {"word": "смешно", "start": 0.8, "end": 2.0},
        ],
    }]
    assert _write_short_ass(tmp_path, 0, 2, out, transcript_override=transcript) == 1
    text = out.read_text(encoding="utf-8-sig")
    assert r"\fscx108\fscy108" in text
    assert r"\t(" in text



def test_v51_funny_search_toggle_reduces_comedy_bias(tmp_path: Path):
    item = {"start": 1, "end": 20, "score": 7.0, "title": "угарная смешная реакция ахаха", "standalone_clarity": 0.8}
    enabled = shorts_candidate_score(tmp_path, item, {"shorts_funny_search_enabled": True})["short_score"]
    disabled = shorts_candidate_score(tmp_path, item, {"shorts_funny_search_enabled": False})["short_score"]
    assert enabled > disabled


def test_v51_facecam_cluster_survives_large_center_face_distractors():
    detections = []
    for frame in range(10):
        t = float(frame)
        # Stable small streamer facecam in bottom-right.
        detections.append({"frame": frame, "t": t, "cx": 0.86, "cy": 0.82, "w": 0.10, "h": 0.14, "score": 0.93})
        # Larger changing in-game/guest face around the centre.
        detections.append({"frame": frame, "t": t, "cx": 0.42 + frame * 0.02, "cy": 0.48, "w": 0.32, "h": 0.38, "score": 0.96})
    facecam = _facecam_layout(detections, sample_count=10)
    assert facecam is not None
    assert facecam["corner"] == "br"
    assert facecam["coverage"] == 1.0
    primary = _primary_face_points(detections)
    assert len(primary) == 10
    assert all(0.0 <= row["cx"] <= 1.0 for row in primary)


def test_v51_max_alignment_retries_after_optional_whisperx_becomes_available_without_rerunning_asr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from highlight_studio.services import pipeline

    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(pipeline, "video_content_signature", lambda _project: "video-content")
    monkeypatch.setattr(pipeline, "prepare_nvidia_dll_paths", lambda: None)
    monkeypatch.setattr(pipeline, "detect_hardware_capabilities", lambda: {"recommended_settings": {"whisper_device": "cpu", "whisper_compute": "int8"}, "ctranslate2": {"cuda_ok": False}, "cpu": {"logical_threads": 4}})

    def fake_run(cmd, **_kwargs):
        Path(cmd[-1]).write_bytes(b"RIFF" + b"0" * 2048)
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(pipeline, "run_cmd", fake_run)

    transcribe_calls = {"count": 0}
    class FakeWord:
        def __init__(self, word, start, end): self.word, self.start, self.end = word, start, end
    class FakeSegment:
        start, end, text = 0.0, 1.5, "Привет Влада"
        words = [FakeWord("Привет", 0.0, 0.6), FakeWord("Влада", 0.7, 1.4)]
    class FakeWhisperModel:
        def __init__(self, *_args, **_kwargs): pass
        def transcribe(self, *_args, **_kwargs):
            transcribe_calls["count"] += 1
            return iter([FakeSegment()]), SimpleNamespace()
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisperModel))

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None if name == "whisperx" else real_find_spec(name, *a, **k))
    settings = {"shorts_caption_quality": "max", "shorts_precise_alignment": True, "shorts_whisper_model": "small", "language": "ru"}
    first = _shorts_refined_transcript(tmp_path, video, 10.0, 12.0, settings, None, 1)
    assert first and first[0]["text"] == "Привет Влада"
    assert transcribe_calls["count"] == 1
    assert not list((tmp_path / "shorts_cache" / "alignment").glob("*.json"))

    align_calls = {"count": 0}
    fake_whisperx = SimpleNamespace(
        load_align_model=lambda **_kwargs: (object(), {"language": "ru"}),
        align=lambda *_args, **_kwargs: (
            align_calls.__setitem__("count", align_calls["count"] + 1)
            or {"segments": [{"start": 0.0, "end": 1.5, "text": "Привет Влада", "words": [{"word": "Привет", "start": 0.0, "end": 0.55}, {"word": "Влада", "start": 0.65, "end": 1.45}]}]}
        ),
    )
    monkeypatch.setitem(sys.modules, "whisperx", fake_whisperx)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: SimpleNamespace() if name == "whisperx" else real_find_spec(name, *a, **k))
    second = _shorts_refined_transcript(tmp_path, video, 10.0, 12.0, settings, None, 1)
    assert second and second[0]["words"][0]["end"] == 10.55
    assert transcribe_calls["count"] == 1, "alignment retry must reuse short-ASR cache"
    assert align_calls["count"] == 1
    assert list((tmp_path / "shorts_cache" / "alignment").glob("*.json"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg unavailable")
def test_v51_real_ffmpeg_accepts_smart_face_gameplay_and_ass_graphs(tmp_path: Path):
    ass = tmp_path / "captions.ass"
    transcript = [{
        "start": 0.0, "end": 0.8, "text": "Очень смешно",
        "words": [{"word": "Очень", "start": 0.0, "end": 0.35}, {"word": "смешно", "start": 0.35, "end": 0.8}],
    }]
    assert _write_short_ass(tmp_path, 0.0, 1.0, ass, transcript_override=transcript) == 1
    subtitle = _ffmpeg_filter_path(ass)
    plans = [
        ("smart_face", {"resolved_mode": "smart_face", "trajectory": [{"t": 0.0, "cx": 0.25}, {"t": 0.5, "cx": 0.75}]}),
        ("gameplay_facecam", {"resolved_mode": "gameplay_facecam", "facecam": {"x": 0.73, "y": 0.68, "w": 0.24, "h": 0.27, "position": "bottom"}}),
    ]
    for mode, plan in plans:
        graph = _shorts_filter_complex(mode, subtitle, subtitle_is_ass=True, reframe_plan=plan)
        result = subprocess.run([
            shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=2", "-t", "0.6",
            "-filter_complex", graph, "-map", "[vout]", "-f", "null", "-",
        ], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr



def test_v51_current_selection_plan_reuses_shortlist_without_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from highlight_studio.services import pipeline
    factory = tmp_path / "content_factory"
    factory.mkdir()
    existing = [{"id": 1, "start": 5.0, "end": 20.0, "title": "old current"}]
    write_json(factory / "shorts_candidates.json", existing)
    write_json(tmp_path / "shorts_quality_plan.json", {"fingerprint": "fresh", "rejected": [{"reason": "x"}]})
    monkeypatch.setattr(pipeline, "_shorts_selection_fingerprint", lambda *_a, **_k: "fresh")
    monkeypatch.setattr(pipeline, "build_shorts_quality_candidates", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not rebuild")))
    selected, rejected = _ensure_shorts_candidates_current(tmp_path, {"shorts_count": 1})
    assert selected == existing
    assert rejected == [{"reason": "x"}]


def test_v51_stale_selection_plan_rebuilds_and_preserves_manual_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from highlight_studio.services import pipeline
    factory = tmp_path / "content_factory"
    factory.mkdir()
    existing = [{
        "id": 9, "source_segment_id": 9, "shorts_source": "micro",
        "start": 5.0, "end": 20.0, "title": "moment",
        "caption_text": "РУЧНАЯ ПРАВКА", "hook_text": "HOOK", "reframe_mode": "smart_face",
    }]
    write_json(factory / "shorts_candidates.json", existing)
    write_json(factory / "content_factory_manifest.json", {"outputs": [{"minutes": 10}]})
    write_json(tmp_path / "shorts_quality_plan.json", {"fingerprint": "old"})
    monkeypatch.setattr(pipeline, "_shorts_selection_fingerprint", lambda *_a, **_k: "new")
    calls = {"n": 0}
    def fake_build(*_a, **_k):
        calls["n"] += 1
        write_json(tmp_path / "shorts_quality_plan.json", {"fingerprint": "new", "rejected": []})
        return ([{
            "id": 9, "source_segment_id": 9, "shorts_source": "micro",
            "start": 5.0, "end": 20.0, "title": "moment updated", "short_score": 9.0,
        }], [{"reason": "quality_below_threshold"}])
    monkeypatch.setattr(pipeline, "build_shorts_quality_candidates", fake_build)
    selected, rejected = _ensure_shorts_candidates_current(tmp_path, {"shorts_count": 1})
    assert calls["n"] == 1
    assert selected[0]["caption_text"] == "РУЧНАЯ ПРАВКА"
    assert selected[0]["hook_text"] == "HOOK"
    assert selected[0]["reframe_mode"] == "smart_face"
    assert selected[0]["short_score"] == 9.0
    assert rejected == [{"reason": "quality_below_threshold"}]
    manifest = __import__("json").loads((factory / "content_factory_manifest.json").read_text(encoding="utf-8"))
    assert manifest["outputs"] == [{"minutes": 10}]
    assert manifest["shorts"][0]["caption_text"] == "РУЧНАЯ ПРАВКА"


def test_v51_selection_fingerprint_tracks_selection_settings_not_caption_style(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from highlight_studio.services import pipeline
    # Keep this a pure fingerprint contract test without requiring real media.
    monkeypatch.setattr(pipeline, "video_content_signature", lambda _p: {"sha256": "video"})
    for name in ("micro_candidates.json", "block_candidates.json", "candidates.json", "segments.json", "audio_events.json", "transcript.json"):
        write_json(tmp_path / name, [])
    base = {
        "shorts_count": 5,
        "shorts_funny_search_enabled": True,
        "shorts_emotion_events_enabled": True,
        "shorts_llm_rerank_enabled": True,
        "shorts_caption_font_size": 72,
    }
    fp = _shorts_selection_fingerprint(tmp_path, base)
    assert _shorts_selection_fingerprint(tmp_path, {**base, "shorts_count": 8}) != fp
    assert _shorts_selection_fingerprint(tmp_path, {**base, "shorts_funny_search_enabled": False}) != fp
    assert _shorts_selection_fingerprint(tmp_path, {**base, "shorts_emotion_events_enabled": False}) != fp
    assert _shorts_selection_fingerprint(tmp_path, {**base, "shorts_llm_rerank_enabled": False}) != fp
    assert _shorts_selection_fingerprint(tmp_path, {**base, "shorts_caption_font_size": 88}) == fp
