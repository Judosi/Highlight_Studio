from __future__ import annotations

import json
from pathlib import Path

import pytest

import highlight_studio.api.app as app
from highlight_studio.integrations.ai.runtime import AITransportError
from highlight_studio.services import pipeline


class _Logger:
    def __init__(self):
        self.messages: list[str] = []

    def log(self, message):
        self.messages.append(str(message))

    def set_status(self, *_args, **_kwargs):
        return None

    def set_step(self, *_args, **_kwargs):
        return None


def _candidate() -> pipeline.Candidate:
    return pipeline.Candidate(
        id=1,
        start=0,
        end=60,
        score=8.6,
        title="момент",
        reason="block AI",
        text_preview="сильный самостоятельный момент",
        content_class="primary_live",
        content_class_confidence=0.95,
        standalone_clarity=0.9,
    )


def test_micro_transport_timeout_recovers_and_finishes_single_window(monkeypatch, tmp_path: Path):
    class FakeAI:
        def __init__(self):
            self.operations: list[str] = []
            self.warmups = 0

        def warmup(self, model=None, timeout=120):
            self.warmups += 1
            return {"ok": True, "model": model, "elapsed_seconds": 0.01}

        def generate_json(self, prompt, *, operation="text_json", **_kwargs):
            self.operations.append(operation)
            if operation == "micro_ai":
                raise AITransportError("Ollama read timed out", transient=True)
            if operation == "micro_ai_single_id":
                return {
                    "clips": [
                        {
                            "id": 1,
                            "score": 8.8,
                            "keep": True,
                            "title": "восстановленный момент",
                        }
                    ]
                }
            raise AssertionError(operation)

    fake = FakeAI()
    monkeypatch.setattr(pipeline, "make_ai_client", lambda *_a, **_k: fake)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_a: None)
    settings = app.default_settings()
    settings.update({"micro_cut_enabled": True, "ai_retry_count": 3})

    out = pipeline.build_micro_candidates(
        tmp_path,
        [_candidate()],
        [pipeline.TranscriptSegment(start=0, end=60, text="сильный самостоятельный момент")],
        settings,
        _Logger(),
    )

    assert out[0].title == "восстановленный момент"
    assert fake.operations == ["micro_ai", "micro_ai_single_id"]
    coverage = json.loads((tmp_path / "ai_coverage_report.json").read_text(encoding="utf-8"))
    assert coverage["micro_ai_analyzed"] == coverage["micro_windows_total"] == 1
    assert coverage["micro_failed_batches"] == []
    runtime = json.loads((tmp_path / "ollama_micro_runtime.json").read_text(encoding="utf-8"))
    assert runtime["adaptive_single_mode_used"] is True
    assert runtime["runtime_recovery_count"] == 1
    assert runtime["final_ai_coverage_percent"] == 100.0


def test_low_memory_auto_profile_caps_micro_batch_without_dropping_windows():
    settings = {
        "micro_batch_size": 8,
        "hardware_detected_profile": "AUTO_CPU12_RAM16GB_NVIDIA_GeForce_GTX_1050_Ti_VRAM4GB",
        "hardware_manual_override_enabled": False,
    }
    assert pipeline.effective_micro_batch_size(settings) == 4
    settings["hardware_manual_override_enabled"] = True
    assert pipeline.effective_micro_batch_size(settings) == 8


def test_incomplete_micro_ai_is_retryable_and_preserves_previous_montage(tmp_path: Path):
    previous = [{"id": 1, "start": 10.0, "end": 42.0, "title": "старый монтаж"}]
    pipeline.write_json(pipeline.project_paths(tmp_path)["segments"], previous)
    pipeline.write_json(
        tmp_path / "ai_coverage_report.json",
        {
            "micro_windows_total": 144,
            "micro_ai_analyzed": 56,
            "micro_coverage_percent": 38.89,
            "micro_failed_batches": [{"batch": 8, "missing_ids": list(range(1, 9))}],
        },
    )
    logger = _Logger()

    with pytest.raises(RuntimeError, match="56/144"):
        pipeline.enforce_micro_ai_completion(tmp_path, app.default_settings(), logger)

    assert pipeline.read_json(pipeline.project_paths(tmp_path)["segments"], []) == previous
    health = pipeline.read_json(tmp_path / "analysis_health.json", {})
    assert health["outcome"] == "incomplete_micro_ai"
    assert health["analysis_complete"] is False
    assert health["retryable"] is True
    assert health["preserved_previous_segments"] == 1
    assert health["preserved_previous_seconds"] == 32.0


def test_normal_mode_allows_only_small_isolated_micro_shortfall(tmp_path: Path):
    pipeline.write_json(
        tmp_path / "ai_coverage_report.json",
        {"micro_windows_total": 100, "micro_ai_analyzed": 91, "micro_coverage_percent": 91.0},
    )
    result = pipeline.enforce_micro_ai_completion(tmp_path, app.default_settings(), _Logger())
    assert result["complete"] is True
    assert result["required_percent"] == 90.0
