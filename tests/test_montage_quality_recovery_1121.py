from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from highlight_studio.core.utils import read_json, write_json
from highlight_studio.services import pipeline


class Logger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, message: str) -> None:
        self.lines.append(str(message))


def make_candidate(
    idx: int,
    start: float,
    *,
    duration: float = 60.0,
    score: float = 8.0,
    confidence: float = 7.0,
    parent_id: int = 0,
    topic: str | None = None,
) -> pipeline.Candidate:
    return pipeline.Candidate(
        id=idx,
        start=start,
        end=start + duration,
        score=score,
        ai_score=score,
        confidence=confidence,
        standalone_clarity=0.75,
        title=topic or f"Сцена {idx}",
        reason=topic or f"Уникальная причина {idx}",
        semantic_topic=topic or f"topic-{idx}",
        text_preview=topic or f"Уникальный текст {idx}",
        parent_id=parent_id,
        decision="keep",
        content_class="primary_live",
        content_class_confidence=0.95,
    )


def selection_settings() -> dict:
    return {
        "refill_after_dedup_enabled": True,
        "target_fill_ratio": 0.94,
        "min_final_segments": 2,
        "max_final_segments": 80,
        "micro_cut_enabled": True,
        "micro_window_seconds": 45,
        "micro_min_seconds": 12,
        "micro_max_seconds": 80,
        "micro_speech_gap_seconds": 4,
        "micro_source_duration_multiplier": 4,
        "top_blocks_for_micro": 36,
        "quality_first_selection_enabled": True,
        "quality_first_min_score": 6.7,
        "quality_first_min_confidence": 5.6,
        "quality_first_min_clarity": 0.5,
        "quality_recovery_score_relaxation": 0.5,
        "semantic_quality_guard_enabled": True,
        "non_primary_reject_confidence": 0.72,
        "fill_target_min_score": 5.0,
        "temporal_fairness_enabled": True,
        "temporal_fairness_bucket_seconds": 900,
        "temporal_fairness_blocks_per_bucket": 2,
        "temporal_fairness_min_score": 4.5,
        "micro_global_score_floor": 7.6,
    }


def test_transcript_timing_repair_splits_multi_minute_silence_from_word_times() -> None:
    original = pipeline.TranscriptSegment(
        100.0,
        280.0,
        "Первая фраза вторая фраза",
        [
            {"start": 100.0, "end": 101.0, "word": "Первая"},
            {"start": 101.1, "end": 102.0, "word": "фраза"},
            {"start": 277.0, "end": 278.0, "word": "вторая"},
            {"start": 278.1, "end": 279.0, "word": "фраза"},
        ],
    )

    repaired, report = pipeline.normalize_transcript_timing([original])

    assert len(repaired) == 2
    assert max(item.end - item.start for item in repaired) < 3
    assert report["split_input_segments"] == 1
    assert report["removed_silence_seconds"] > 170


def test_micro_windows_never_cross_long_silence_or_hard_duration_limit() -> None:
    candidate = make_candidate(1, 0, duration=300)
    transcript = [
        pipeline.TranscriptSegment(2, 25, "начало сцены"),
        pipeline.TranscriptSegment(170, 260, "продолжение после большой паузы"),
    ]
    windows = pipeline.build_micro_windows_for_candidate(candidate, transcript, selection_settings())

    assert windows
    assert all(0 < item["end"] - item["start"] <= 80.001 for item in windows)
    assert all(not (item["start"] < 100 < item["end"]) for item in windows)


def test_micro_source_pool_uses_quality_recovery_budget_instead_of_two_x_target_cliff() -> None:
    items = [make_candidate(i, (i - 1) * 240, duration=240, score=7.0) for i in range(1, 22)]
    selected, report = pipeline.select_micro_source_blocks(items, 1800, selection_settings())

    assert len(selected) == 21
    assert report["source_duration_multiplier"] == 4
    assert report["eligible_coverage_percent"] == 100


def test_contiguous_micro_siblings_are_not_removed_as_semantic_duplicates() -> None:
    log = Logger()
    first = make_candidate(1, 0, duration=45, parent_id=9, topic="одна развивающаяся сцена")
    second = make_candidate(2, 45, duration=45, parent_id=9, topic="одна развивающаяся сцена")
    out = pipeline.local_similarity_filter_keep_target([first, second], 120, selection_settings(), log)

    assert [item.id for item in out] == [1, 2]


def test_refill_has_bounded_primary_live_quality_recovery_tier(tmp_path: Path) -> None:
    settings = selection_settings()
    log = Logger()
    strong = make_candidate(1, 0, score=8.2)
    recovery_a = make_candidate(2, 80, score=6.3, confidence=5.35)
    recovery_b = make_candidate(3, 160, score=6.25, confidence=5.4)

    assert not pipeline.candidate_is_selectable(recovery_a, settings)
    assert pipeline.candidate_is_quality_recovery_selectable(recovery_a, settings)

    out = pipeline.refill_after_dedup(
        tmp_path,
        [strong, recovery_a, recovery_b],
        [strong],
        180,
        settings,
        log,
    )

    assert {item.id for item in out} == {1, 2, 3}
    report = read_json(tmp_path / "selection_refill_after_dedup.json", {})
    assert report["added_by_pass"]["quality_recovery"] == 2


def test_quality_report_marks_large_target_shortfall_as_not_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline,
        "project_paths",
        lambda _: {
            "segments": tmp_path / "segments.json",
            "candidates": tmp_path / "candidates.json",
            "quality": tmp_path / "quality_report.json",
        },
    )
    segment = make_candidate(1, 0, duration=300, score=8.2)
    write_json(tmp_path / "project.json", {"settings": {"target_minutes": 30, "target_fill_ratio": 0.94}})
    write_json(tmp_path / "segments.json", [asdict(segment)])
    write_json(tmp_path / "candidates.json", [asdict(segment)])

    report = pipeline.quality_report(tmp_path)

    assert report["target_fill_percent"] < 20
    assert report["quality_score"] < 80
    assert any("короче ориентира" in item for item in report["recommendations"])
