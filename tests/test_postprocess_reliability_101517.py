from __future__ import annotations

from pathlib import Path

from highlight_studio.core.utils import read_json
from highlight_studio.services import pipeline


class Logger:
    def __init__(self):
        self.lines: list[str] = []

    def log(self, message: str) -> None:
        self.lines.append(str(message))

    def set_status(self, *args, **kwargs) -> None:
        pass


def candidate(idx: int, start: float, *, title: str | None = None, template: str = "", topic: str = "") -> pipeline.Candidate:
    return pipeline.Candidate(
        id=idx,
        start=start,
        end=start + 60,
        score=8.4,
        confidence=7.4,
        standalone_clarity=0.85,
        title=title or f"Уникальная сцена {idx}",
        reason=f"Причина сцены {idx}",
        text_preview=f"Текст самостоятельного момента {idx}",
        semantic_topic=topic or f"topic-{idx}",
        template_signature=template,
        content_class="primary_live",
        content_class_confidence=0.95,
    )


def settings() -> dict:
    return {
        "refill_after_dedup_enabled": True,
        "target_fill_ratio": 0.94,
        "min_final_segments": 2,
        "max_final_segments": 20,
        "micro_cut_enabled": True,
        "micro_max_seconds": 80,
        "quality_first_selection_enabled": True,
        "quality_first_min_score": 6.7,
        "quality_first_min_confidence": 5.6,
        "quality_first_min_clarity": 0.5,
        "semantic_quality_guard_enabled": True,
        "non_primary_reject_confidence": 0.72,
        "fill_target_min_score": 5.0,
    }


def test_refill_filters_first_then_restores_duration_from_unused_unique_candidates(tmp_path: Path):
    log = Logger()
    s = settings()
    # Six initially selected clips look long enough (360 s), but three are exact
    # template duplicates. 10.15.19 filtered them *after* refill and returned
    # only 180 s. Unused unique candidates must now be added after filtering.
    a1 = candidate(1, 0, template="scene-a", topic="a")
    a2 = candidate(2, 70, template="scene-a", topic="a")
    b1 = candidate(3, 140, template="scene-b", topic="b")
    b2 = candidate(4, 210, template="scene-b", topic="b")
    c1 = candidate(5, 280, template="scene-c", topic="c")
    c2 = candidate(6, 350, template="scene-c", topic="c")
    extras = [candidate(7, 500), candidate(8, 570), candidate(9, 640)]

    out = pipeline.refill_after_dedup(tmp_path, [a1, a2, b1, b2, c1, c2, *extras], [a1, a2, b1, b2, c1, c2], 300, s, log)

    assert pipeline.candidates_total_duration(out) >= 300
    assert len({c.template_signature for c in out if c.template_signature}) == 3
    assert any("Refill after_dedup" in line for line in log.lines)


def test_refill_aims_for_real_target_not_only_94_percent_floor(tmp_path: Path):
    log = Logger()
    s = settings()
    chosen = [candidate(1, 0), candidate(2, 70), candidate(3, 140), candidate(4, 210)]  # 240s
    extras = [candidate(5, 300), candidate(6, 370)]
    out = pipeline.refill_after_dedup(tmp_path, [*chosen, *extras], chosen, 300, s, log)
    assert pipeline.candidates_total_duration(out) >= 300


class StorylineAI:
    def __init__(self):
        self.primary_calls = 0
        self.rescue_calls = 0
        self.prompts: list[str] = []

    def warmup(self, model: str, timeout: int):
        return {"ok": True, "elapsed_seconds": 0.1}

    def generate_json(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        operation = kwargs.get("operation")
        expected = set(kwargs.get("expected_ids") or set())
        if operation == "storyline_ai":
            self.primary_calls += 1
            if self.primary_calls == 1:
                # Reproduce the real failure from 10.15.19.
                raise RuntimeError("AI returned an error payload: Invalid request: The provided text is not a valid JSON object.")
        if operation == "storyline_ai_rescue":
            self.rescue_calls += 1
        return {
            "segments": [
                {"id": item_id, "keep": True, "extra_start_seconds": 0, "extra_end_seconds": 0, "semantic_topic": f"ok-{item_id}", "reason": "связно"}
                for item_id in sorted(expected)
            ]
        }


def test_storyline_splits_large_request_and_rescues_failed_batch(tmp_path: Path, monkeypatch):
    fake = StorylineAI()
    monkeypatch.setattr(pipeline, "make_ai_client", lambda *a, **k: fake)
    monkeypatch.setattr(pipeline, "effective_text_model", lambda s: "qwen3:8b")
    log = Logger()
    chosen = [candidate(i, i * 90.0) for i in range(1, 11)]
    transcript = [pipeline.TranscriptSegment(c.start - 5, c.end + 5, f"контекст {c.id}") for c in chosen]
    s = settings() | {"storyline_enabled": True, "storyline_batch_size": 4, "storyline_rescue_batch_size": 2, "ollama_timeout": 900}

    out = pipeline.storyline_pass(tmp_path, chosen, transcript, 2000, s, log)

    assert len(out) == 10
    assert fake.primary_calls == 3
    assert fake.rescue_calls == 2
    runtime = read_json(tmp_path / "storyline_runtime.json", {})
    assert runtime["resolved"] == 10
    assert runtime["unresolved_ids"] == []
    assert all(len(prompt) < 12000 for prompt in fake.prompts)


class DedupAI:
    def __init__(self):
        self.global_failed = False
        self.rescue_calls = 0

    def warmup(self, model: str, timeout: int):
        return {"ok": True, "elapsed_seconds": 0.1}

    def generate_json(self, prompt: str, **kwargs):
        operation = kwargs.get("operation")
        if operation == "dedup_ai" and not self.global_failed:
            self.global_failed = True
            raise RuntimeError("Ollama stream stalled for 45s without data")
        if operation == "dedup_ai_rescue":
            self.rescue_calls += 1
            # Drop candidate 2 when present in the rescue window.
            return {"drop_ids": [2] if "ID=2 " in prompt else [], "groups": []}
        return {"drop_ids": [], "groups": []}


def test_dedup_global_stall_uses_rescue_batches_and_marks_duplicate_removed(tmp_path: Path, monkeypatch):
    fake = DedupAI()
    monkeypatch.setattr(pipeline, "make_ai_client", lambda *a, **k: fake)
    monkeypatch.setattr(pipeline, "effective_text_model", lambda s: "qwen3:8b")
    log = Logger()
    chosen = [candidate(i, i * 90.0) for i in range(1, 9)]
    s = settings() | {"dedup_enabled": True, "dedup_rescue_batch_size": 6, "ollama_timeout": 900}

    out = pipeline.dedup_pass(tmp_path, chosen, chosen, s, log)

    assert fake.rescue_calls >= 1
    assert 2 not in {c.id for c in out}
    removed = next(c for c in chosen if c.id == 2)
    assert removed.decision == "remove"
    assert "dedup_remove=ai_duplicate" in removed.reason
    assert any("rescue" in line.lower() for line in log.lines)
