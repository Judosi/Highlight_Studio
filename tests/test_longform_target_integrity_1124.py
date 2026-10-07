from __future__ import annotations

import json
from pathlib import Path

from highlight_studio.services import pipeline


class Logger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, message: str) -> None:
        self.lines.append(str(message))


def settings() -> dict:
    return {
        "target_minutes": 35,
        "target_fill_ratio": 0.94,
        "quality_first_min_score": 6.7,
        "fill_target_min_score": 5.0,
        "micro_cut_enabled": True,
        "micro_window_seconds": 45,
        "micro_min_seconds": 12,
        "micro_max_seconds": 80,
        "semantic_quality_guard_enabled": True,
        "non_primary_reject_confidence": 0.72,
        "longform_context_refill_enabled": True,
        "ocr_enabled": False,
    }


def block(
    idx: int,
    *,
    content_class: str = "primary_live",
    confidence: float = 0.9,
    score: float = 7.4,
    ai_score: float | None = None,
    visual_score: float = 0.8,
    text: str | None = None,
) -> pipeline.Candidate:
    start = float((idx - 1) * 240)
    return pipeline.Candidate(
        id=idx,
        start=start,
        end=start + 240,
        score=score,
        ai_score=score if ai_score is None else ai_score,
        visual_score=visual_score,
        confidence=7.0,
        title=f"Сцена {idx}",
        reason="оценка крупного блока",
        text_preview=text or "Я сейчас играю, чат, пацаны, смотрите что происходит на этом уровне",
        decision="remove",
        standalone_clarity=0.6,
        content_class=content_class,
        content_class_confidence=confidence,
        semantic_topic=f"topic-{idx}",
    )


def test_live_interaction_evidence_distinguishes_current_gameplay() -> None:
    evidence = pipeline.detect_live_interaction_evidence(
        "Пацаны, чат, я сейчас играю этот уровень, таймер поставил модератор"
    )
    assert evidence["strong"] is True
    assert {"audience", "stream", "live_request", "gameplay", "present_reaction"} & set(evidence["signals"])


def test_coarse_ai_prerecorded_false_positive_is_promoted_to_live(tmp_path: Path) -> None:
    item = block(1, content_class="prerecorded", confidence=0.95, score=3.8, ai_score=6.8)
    out = pipeline.apply_stream_content_guard(
        tmp_path,
        [item],
        settings(),
        Logger(),
        stage="block_pre_micro",
    )

    assert out[0].content_class == "primary_live"
    assert out[0].content_class_confidence >= 0.72
    assert "transcript live interaction" in out[0].content_evidence


def test_ai_only_prerecorded_without_live_evidence_stays_ineligible_for_context(tmp_path: Path) -> None:
    item = block(
        1,
        content_class="prerecorded",
        confidence=0.95,
        score=7.0,
        text="спокойная беседа о поездке и погоде",
    )
    guarded = pipeline.apply_stream_content_guard(
        tmp_path,
        [item],
        settings(),
        Logger(),
        stage="block_pre_micro",
    )
    assert guarded[0].content_class == "prerecorded"
    assert guarded[0].content_class_confidence < 0.72

    chosen, added = pipeline.refill_with_primary_live_context(
        tmp_path,
        guarded,
        [],
        [pipeline.TranscriptSegment(0, 30, item.text_preview)],
        240,
        180,
        settings(),
        Logger(),
    )
    assert chosen == []
    assert added == []


def test_sixty_minute_source_reaches_thirty_five_minute_target_with_live_context(tmp_path: Path) -> None:
    # Mirrors the 11.2.3 incident: block AI falsely called many normal gameplay
    # blocks prerecorded, while the final micro montage contained only short
    # isolated reactions. Raw AI+visual evidence remains strong enough for a
    # quality-bounded long-form context pass after live reconciliation.
    blocks = []
    for idx in range(1, 16):
        false_class = "prerecorded" if idx in {7, 8, 9, 13, 14} else "primary_live"
        blocks.append(
            block(
                idx,
                content_class=false_class,
                confidence=0.95 if false_class == "prerecorded" else 0.9,
                score=3.1 if false_class == "prerecorded" else 7.2,
                ai_score=6.5 if false_class == "prerecorded" else 7.2,
                visual_score=0.9,
            )
        )
    log = Logger()
    for item in blocks:
        pipeline.apply_stream_content_guard(tmp_path, [item], settings(), log, stage="block_pre_micro")

    chosen = []
    for idx, parent_id in enumerate((1, 2, 3, 6, 10, 11, 12, 15), start=1):
        start = (parent_id - 1) * 240 + 20
        chosen.append(
            pipeline.Candidate(
                id=100 + idx,
                start=start,
                end=start + 15,
                score=8.2,
                title=f"Микро {idx}",
                reason="сильная реакция",
                text_preview="реакция",
                decision="keep",
                standalone_clarity=0.8,
                content_class="primary_live",
                content_class_confidence=0.9,
                parent_id=parent_id,
            )
        )
    transcript = [
        pipeline.TranscriptSegment(item.start, item.end, item.text_preview)
        for item in blocks
    ]

    result, added = pipeline.refill_with_primary_live_context(
        tmp_path,
        blocks,
        chosen,
        transcript,
        3600,
        2100,
        settings(),
        log,
    )

    total = pipeline.candidates_total_duration(result)
    assert 1974 <= total <= 2100.01
    assert added
    assert all(item.content_class == "primary_live" for item in added)
    assert all("связующий контекст" in item.title for item in added)
    ordered = sorted(result, key=lambda item: item.start)
    assert all(current.end <= following.start for current, following in zip(ordered, ordered[1:]))
    report = json.loads((tmp_path / "longform_context_refill.json").read_text(encoding="utf-8"))
    assert report["target_reached"] is True
    assert report["after_seconds"] >= 1974


def test_context_refill_never_uses_semantic_non_primary_blocks(tmp_path: Path) -> None:
    primary = [block(idx) for idx in range(1, 4)]
    forbidden = [block(idx, content_class="replay", confidence=0.98, score=9.5) for idx in range(4, 16)]
    transcript = [pipeline.TranscriptSegment(item.start, item.end, item.text_preview) for item in [*primary, *forbidden]]

    result, added = pipeline.refill_with_primary_live_context(
        tmp_path,
        [*primary, *forbidden],
        [],
        transcript,
        3600,
        2100,
        settings(),
        Logger(),
    )

    assert pipeline.candidates_total_duration(result) <= 720.01
    assert all(item.parent_id in {1, 2, 3} for item in added)
    assert all(item.content_class not in pipeline.NON_PRIMARY_CONTENT_CLASSES for item in added)
    report = json.loads((tmp_path / "longform_context_refill.json").read_text(encoding="utf-8"))
    assert report["target_reached"] is False


def test_sparse_speech_gameplay_uses_verified_anchored_live_context(tmp_path: Path) -> None:
    """Regression: 35 min target must not collapse to ~3 min on quiet gameplay.

    Every coarse block is confidently primary-live and contains a selected,
    high-confidence micro highlight, but Whisper only sees a short spoken burst
    inside each selected micro.  Speech-only refill therefore has no room to
    extend the montage even though the surrounding block is verified live.
    """
    blocks = [block(idx, content_class="primary_live", confidence=0.95, score=7.5, visual_score=0.9) for idx in range(1, 16)]
    chosen: list[pipeline.Candidate] = []
    transcript: list[pipeline.TranscriptSegment] = []
    for idx, parent in enumerate(blocks, start=1):
        start = parent.start + 20.0
        chosen.append(
            pipeline.Candidate(
                id=200 + idx,
                start=start,
                end=start + 15.0,
                score=8.4,
                title=f"Микро {idx}",
                reason="сильный момент текущего gameplay",
                text_preview="короткая реакция",
                decision="keep",
                standalone_clarity=0.85,
                content_class="primary_live",
                content_class_confidence=0.95,
                parent_id=parent.id,
            )
        )
        # Sparse speech is entirely inside the already selected micro clip.
        transcript.append(pipeline.TranscriptSegment(start + 3.0, start + 7.0, "короткая реплика"))

    result, added = pipeline.refill_with_primary_live_context(
        tmp_path,
        blocks,
        chosen,
        transcript,
        3600,
        2100,
        settings(),
        Logger(),
    )

    total = pipeline.candidates_total_duration(result)
    assert 1974 <= total <= 2100.01
    assert added
    assert all(item.parent_id in {block.id for block in blocks} for item in added)
    assert all(item.content_class == "primary_live" for item in added)
    report = json.loads((tmp_path / "longform_context_refill.json").read_text(encoding="utf-8"))
    assert report["target_reached"] is True
