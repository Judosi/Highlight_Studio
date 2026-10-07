from __future__ import annotations

from dataclasses import asdict

import highlight_studio.api.app as app
from highlight_studio.services import pipeline


def c(cid: int, start: float, score: float, **kwargs) -> pipeline.Candidate:
    return pipeline.Candidate(
        id=cid,
        start=start,
        end=start + kwargs.pop("duration", 45.0),
        score=score,
        title=kwargs.pop("title", f"moment {cid}"),
        reason=kwargs.pop("reason", "strong moment"),
        text_preview=kwargs.pop("text_preview", "понятная законченная сцена"),
        **kwargs,
    )


class Log:
    def __init__(self):
        self.lines: list[str] = []

    def log(self, msg: str) -> None:
        self.lines.append(str(msg))


def test_reconnect_ocr_is_high_confidence_non_primary():
    result = pipeline.detect_stream_content_class(
        "внутри старого клипа все смеются",
        ocr_texts=["ПРОПАЛ ИНТЕРНЕТ  ПОДКЛЮЧАЮСЬ"],
    )
    assert result["class"] == "reconnect"
    assert result["confidence"] >= 0.9
    assert result["source"] == "ocr"


def test_positive_words_do_not_cancel_reconnect_penalty():
    penalty = pipeline.water_penalty("пропал интернет, подключаюсь; внутри смех донат реакция жесть")
    assert penalty >= 4.0


def test_high_confidence_reconnect_can_never_fill_target():
    item = c(
        1,
        60,
        9.8,
        confidence=9.0,
        standalone_clarity=1.0,
        content_class="reconnect",
        content_class_confidence=0.98,
    )
    assert not pipeline.candidate_is_selectable(item, app.default_settings())


def test_live_reaction_is_allowed_when_semantically_strong():
    item = c(
        2,
        90,
        8.6,
        confidence=8.0,
        standalone_clarity=0.9,
        content_class="live_reaction",
        content_class_confidence=0.9,
    )
    assert pipeline.candidate_is_selectable(item, app.default_settings())


def test_quality_first_does_not_use_medium_scene_only_to_reach_minutes():
    settings = app.default_settings()
    settings.update({"quality_first_selection_enabled": True, "quality_first_min_score": 6.7})
    item = c(3, 120, 5.9, confidence=7.0, standalone_clarity=0.9)
    assert not pipeline.candidate_is_selectable(item, settings)


def test_temporal_fairness_gives_late_block_second_pass_without_forcing_final_distribution():
    settings = app.default_settings()
    settings.update(
        {
            "full_ai_coverage": False,
            "top_blocks_for_micro": 3,
            "temporal_fairness_enabled": True,
            "temporal_fairness_bucket_seconds": 900,
            "temporal_fairness_blocks_per_bucket": 1,
            "temporal_fairness_min_score": 4.5,
            "micro_global_score_floor": 9.5,
            "min_final_segments": 1,
        }
    )
    items = [
        c(1, 60, 9.9),
        c(2, 180, 9.8),
        c(3, 420, 9.7),
        c(4, 1600, 5.2),
        c(5, 3400, 5.1),
        c(6, 5200, 5.0),
        c(7, 6900, 5.4),
    ]
    selected, report = pipeline.select_micro_source_blocks(items, 1800, settings)
    starts = {round(x.start) for x in selected}
    assert 6900 in starts
    assert report["mode"] == "quality_plus_temporal_fairness"
    # Fairness is only for who receives micro AI, not a 25%-per-quarter montage rule.
    assert "forced_distribution" not in report


def test_duplicate_template_is_dropped_even_when_target_is_not_filled():
    settings = app.default_settings()
    settings["quality_first_selection_enabled"] = True
    items = [
        c(1, 0, 9.2, template_signature="reconnect:abc", semantic_topic="same scene"),
        c(2, 100, 9.0, template_signature="reconnect:abc", semantic_topic="same scene"),
        c(3, 200, 8.5, template_signature="", semantic_topic="different good scene"),
    ]
    out = pipeline.local_similarity_filter_keep_target(items, 1800, settings, Log())
    assert len(out) == 2
    assert sum(x.template_signature == "reconnect:abc" for x in out) == 1


def test_balanced_preset_exists_and_is_semantic_quality_first():
    assert "balanced" in app.TASK_PRESETS
    settings = app.task_preset_settings(app.default_settings(), "balanced")
    assert settings["task_preset"] == "balanced"
    assert settings["task_preset_label"] == "Сбалансированный / смысл"
    assert settings["edit_mode"] == "Сбалансированный"
    assert settings["semantic_quality_guard_enabled"] is True
    assert settings["quality_first_selection_enabled"] is True
    assert settings["temporal_fairness_enabled"] is True
    assert settings["text_model"] == "qwen3:8b"
    assert settings["visual_scan_max_samples"] == 1200
    assert settings["ocr_every_n_visual_samples"] == 2


def test_twitch_auto_balanced_does_not_fall_back_to_irl_funny(monkeypatch, tmp_path):
    monkeypatch.setattr(
        app,
        "auto_video_probe",
        lambda project_dir, settings: {
            "source_type": "twitch",
            "duration_seconds": 7200,
            "file_weight": "medium",
        },
    )
    monkeypatch.setattr(app, "detect_hardware_capabilities", lambda: {"summary": "test hardware", "profile_id": "TEST"})
    monkeypatch.setattr(app, "hardware_preset_settings", lambda settings, preset: dict(settings))
    settings = app.default_settings()
    settings.update({"task_preset": "balanced", "content_type": "Auto"})
    rec = app.build_autopilot_recommendation(tmp_path, settings)
    assert rec["recommended_task_preset"] == "balanced"
    assert rec["settings_patch"]["edit_mode"] == "Сбалансированный"
    assert rec["settings_patch"]["task_preset"] == "balanced"


def test_semantic_fields_survive_candidate_serialization():
    item = c(
        9,
        42,
        8.2,
        content_class="primary_live",
        content_class_confidence=0.88,
        semantic_topic="история о поездке",
        parent_id=4,
        template_signature="primary:trip",
    )
    data = asdict(item)
    assert data["content_class"] == "primary_live"
    assert data["semantic_topic"] == "история о поездке"
    assert data["parent_id"] == 4
    assert data["template_signature"] == "primary:trip"


def test_coarse_block_guard_needs_repeated_ocr_before_hard_veto(monkeypatch, tmp_path):
    settings = app.default_settings()
    item = c(11, 0, 8.5, duration=240, confidence=8.0, standalone_clarity=0.9)
    monkeypatch.setattr(
        pipeline,
        "ocr_scan_video",
        lambda project_dir, settings, logger: {
            "items": [
                {"time": 10, "text": "обычный чат"},
                {"time": 60, "text": "ПРОПАЛ ИНТЕРНЕТ ПОДКЛЮЧАЮСЬ"},
                {"time": 120, "text": "обычный эфир"},
                {"time": 180, "text": "обычный чат"},
            ]
        },
    )
    out = pipeline.apply_stream_content_guard(tmp_path, [item], settings, Log(), stage="block_pre_micro")
    assert out[0].decision != "remove"
    assert out[0].content_class == "unknown"


def test_coarse_block_guard_vetoes_repeated_reconnect_template(monkeypatch, tmp_path):
    settings = app.default_settings()
    item = c(12, 0, 9.4, duration=240, confidence=8.0, standalone_clarity=0.9)
    monkeypatch.setattr(
        pipeline,
        "ocr_scan_video",
        lambda project_dir, settings, logger: {
            "items": [
                {"time": 10, "text": "ПРОПАЛ ИНТЕРНЕТ ПОДКЛЮЧАЮСЬ"},
                {"time": 60, "text": "ПРОПАЛ ИНТЕРНЕТ ПОДКЛЮЧАЮСЬ"},
                {"time": 120, "text": "обычный чат"},
                {"time": 180, "text": "обычный чат"},
            ]
        },
    )
    out = pipeline.apply_stream_content_guard(tmp_path, [item], settings, Log(), stage="block_pre_micro")
    assert out[0].decision == "remove"
    assert out[0].content_class == "reconnect"
    assert out[0].score <= 2.2
