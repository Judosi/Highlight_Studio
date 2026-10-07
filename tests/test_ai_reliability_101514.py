from __future__ import annotations

import json
from pathlib import Path

import pytest

import highlight_studio.api.app as app
from highlight_studio.integrations.ai.runtime import (
    AIExecutionController,
    AIResponseContractError,
    normalize_response_contract,
)
from highlight_studio.services import hardware, pipeline


def _caps_1050ti() -> dict:
    caps = {
        "profile_id": "AUTO_CPU12_RAM16GB_NVIDIA_GeForce_GTX_1050_Ti_VRAM4GB",
        "cpu": {"name": "Ryzen 5 2600", "logical_threads": 12},
        "memory": {"total_gb": 15.9},
        "nvidia": {"ok": True, "gpus": [{"name": "NVIDIA GeForce GTX 1050 Ti", "memory_total_mb": 4096}]},
        "ctranslate2": {"cuda_ok": True, "compute_types": ["float32", "int8", "int8_float32"], "cuda_device_count": 1},
        "ffmpeg": {"nvenc_runtime_ok": True, "nvdec_advertised": True},
    }
    caps["recommended_settings"] = hardware.recommend_settings(caps)
    return caps


def test_archived_error_payload_is_not_success(tmp_path: Path):
    ctl = AIExecutionController(settings={"ai_transport_retry_count": 2}, project_dir=tmp_path)
    with pytest.raises(AIResponseContractError, match="error payload"):
        ctl.execute_json(
            lambda: '{"error":"model could not satisfy the requested structure"}',
            operation="block_ai",
            model="qwen3:8b",
            collection_key="blocks",
            expected_ids={1, 2, 3},
            required_item_fields={"id", "score"},
        )
    trace = (tmp_path / "ai_runtime_trace.jsonl").read_text(encoding="utf-8")
    assert "response_contract_failed" in trace
    assert '"event": "request_succeeded"' not in trace
    summary = json.loads((tmp_path / "ai_runtime_summary.json").read_text(encoding="utf-8"))
    assert summary["operations"]["block_ai"]["failures"] == 1


def test_numeric_score_object_is_repaired_locally_without_second_inference(tmp_path: Path):
    calls = {"n": 0}

    def raw():
        calls["n"] += 1
        return '{"1":8.5,"2":7,"3":6.5}'

    ctl = AIExecutionController(settings={}, project_dir=tmp_path)
    out = ctl.execute_json(
        raw,
        operation="block_ai",
        model="qwen3:8b",
        collection_key="blocks",
        expected_ids={1, 2, 3},
        required_item_fields={"id", "score"},
    )
    assert calls["n"] == 1
    assert [item["id"] for item in out["blocks"]] == [1, 2, 3]
    assert [item["score"] for item in out["blocks"]] == [8.5, 7.0, 6.5]
    assert out["_hs_normalized_from"] == "numeric_object"


def test_empty_numeric_items_do_not_fake_complete_result():
    with pytest.raises(AIResponseContractError, match="missing score"):
        normalize_response_contract(
            {"1": {}, "2": {}, "3": {}},
            collection_key="blocks",
            expected_ids={1, 2, 3},
            required_item_fields={"id", "score"},
        )


def test_quality_first_completeness_is_enabled_by_default():
    settings = app.default_settings()
    assert settings["ai_batch_completeness_required"] is True
    with pytest.raises(RuntimeError, match="не все ID"):
        pipeline.require_complete_ai_result("AI batch 2", {1, 2, 3}, {1}, settings)


def test_context_safe_planner_preserves_every_item_and_respects_budget_when_possible():
    items = [{"id": i, "text": "x" * size} for i, size in enumerate([1000, 1500, 3000, 1200, 1800], start=1)]

    def build(batch):
        return "P" * 2500 + "".join(x["text"] for x in batch)

    planned = pipeline.plan_prompt_safe_batches(items, 3, build, 7000)
    flattened = [x["id"] for batch in planned for x in batch]
    assert flattened == [1, 2, 3, 4, 5]
    assert all(len(batch) <= 3 for batch in planned)
    assert all(len(build(batch)) <= 7000 for batch in planned if len(batch) > 1)


def test_auto_profile_uses_detected_cuda_even_if_legacy_project_persisted_cpu(monkeypatch, tmp_path: Path):
    caps = _caps_1050ti()
    monkeypatch.setattr(app, "detect_hardware_capabilities", lambda: caps)
    monkeypatch.setattr(app, "recommend_hardware_settings", lambda capabilities, current: hardware.recommend_settings(capabilities, current))
    settings = app.default_settings()
    settings.update({
        "hardware_profile": "Auto",
        "hardware_auto_optimize": True,
        "hardware_manual_override_enabled": False,
        "whisper_device": "cpu",
        "whisper_compute": "int8",
        "video_encoder": "libx264",
    })
    out = app.runtime_optimized_settings(settings, tmp_path)
    assert out["whisper_device"] == "cuda"
    assert out["whisper_compute"] == "int8_float32"
    assert out["video_encoder"] == "h264_nvenc"


def test_manual_override_can_still_force_cpu(monkeypatch):
    caps = _caps_1050ti()
    monkeypatch.setattr(app, "detect_hardware_capabilities", lambda: caps)
    monkeypatch.setattr(app, "recommend_hardware_settings", lambda capabilities, current: hardware.recommend_settings(capabilities, current))
    settings = app.default_settings()
    settings.update({
        "hardware_profile": "Auto",
        "hardware_auto_optimize": True,
        "hardware_manual_override_enabled": True,
        "whisper_device": "cpu",
        "whisper_compute": "int8",
        "video_encoder": "libx264",
    })
    out = app.runtime_optimized_settings(settings)
    assert out["whisper_device"] == "cpu"
    assert out["whisper_compute"] == "int8"
    assert out["video_encoder"] == "libx264"


def test_block_and_micro_primary_loops_use_bounded_semantic_retry_budgets():
    source = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert "semantic_retries = max(1, min(2, retry_budget))" in source
    assert "rescue_retries = max(1, min(3, retry_budget))" in source
    assert 'collection_key="blocks"' in source
    assert 'collection_key="clips"' in source
    assert "ai_batch_completeness_required" in source
    assert "ai_request_hard_timeout" in Path(app.__file__).read_text(encoding="utf-8")


def test_non_transient_hard_deadline_is_not_transport_retried(tmp_path: Path):
    from highlight_studio.integrations.ai.runtime import AITransportError

    calls = {"n": 0}

    def raw():
        calls["n"] += 1
        raise AITransportError("hard deadline", transient=False)

    ctl = AIExecutionController(settings={"ai_transport_retry_count": 3}, project_dir=tmp_path)
    with pytest.raises(AITransportError):
        ctl.execute_json(raw, operation="block_ai", model="qwen3:8b")
    assert calls["n"] == 1


def test_ollama_stream_has_hard_wall_clock_deadline(monkeypatch, tmp_path: Path):
    from highlight_studio.integrations.ai.ollama import OllamaClient
    from highlight_studio.integrations.ai.runtime import AITransportError
    import highlight_studio.integrations.ai.ollama as ollama_mod

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def raise_for_status(self): return None
        def iter_lines(self, decode_unicode=True):
            yield '{"response":"x","done":false}'

    monkeypatch.setattr(ollama_mod.requests, "post", lambda *a, **k: FakeResponse())
    times = iter([0.0, 91.0])
    monkeypatch.setattr(ollama_mod.time, "monotonic", lambda: next(times))
    client = OllamaClient(settings={"ai_request_hard_timeout": 90, "ai_request_active_hard_timeout": 90}, project_dir=tmp_path)
    with pytest.raises(AITransportError, match="hard deadline") as exc:
        client._post_generate({"model": "qwen3:8b", "prompt": "x"}, 900)
    assert exc.value.transient is False


class _MicroLogger:
    def __init__(self):
        self.messages: list[str] = []

    def log(self, message):
        self.messages.append(str(message))

    def set_status(self, *args, **kwargs):
        return None

    def set_step(self, *args, **kwargs):
        return None


def _micro_candidate() -> pipeline.Candidate:
    return pipeline.Candidate(
        id=1,
        start=0.0,
        end=60.0,
        score=8.4,
        title="strong block",
        reason="block AI",
        text_preview="сильный самостоятельный момент",
        content_class="primary_live",
        content_class_confidence=0.95,
        standalone_clarity=0.9,
    )


def test_micro_ai_failed_warmup_degrades_to_block_candidates(monkeypatch, tmp_path: Path):
    class FakeAI:
        def __init__(self):
            self.warmups = 0
            self.unloads = 0
            self.generated = 0

        def warmup(self, model=None, timeout=120):
            self.warmups += 1
            return {"ok": False, "model": model, "error": "500 Internal Server Error"}

        def check(self, *args, **kwargs):
            return {"ok": True, "text_model_installed": True}

        def unload(self, *args, **kwargs):
            self.unloads += 1
            return {"ok": True}

        def generate_json(self, *args, **kwargs):
            self.generated += 1
            raise AssertionError("Micro AI requests must not start after unrecovered warmup")

    ai = FakeAI()
    monkeypatch.setattr(pipeline, "make_ai_client", lambda *a, **k: ai)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)
    settings = app.default_settings()
    settings.update({"micro_cut_enabled": True, "ai_retry_count": 2, "ai_strict_mode": False})
    source = [_micro_candidate()]
    transcript = [pipeline.TranscriptSegment(start=0, end=60, text="сильный самостоятельный момент")]
    logger = _MicroLogger()

    out = pipeline.build_micro_candidates(tmp_path, source, transcript, settings, logger)

    assert out
    assert out is not source
    assert out[0].score < source[0].score
    assert "degraded fallback" in out[0].reason
    # Initial bounded warmup plus one bounded batch-level recovery. The latter
    # is what prevents a temporary outage from disabling all remaining batches.
    assert ai.warmups == 4
    assert ai.unloads == 2
    assert ai.generated == 0
    health = json.loads((tmp_path / "ai_batch_health.json").read_text(encoding="utf-8"))
    assert health["stages"]["micro_ai"]["batches"]["1"]["state"] == "degraded"
    assert any("Micro AI runtime недоступен" in message for message in logger.messages)


def test_micro_ai_failed_warmup_remains_fatal_in_strict_mode(monkeypatch, tmp_path: Path):
    class FakeAI:
        def warmup(self, model=None, timeout=120):
            return {"ok": False, "model": model, "error": "500 Internal Server Error"}

        def check(self, *args, **kwargs):
            return {"ok": True, "text_model_installed": True}

        def unload(self, *args, **kwargs):
            return {"ok": True}

    monkeypatch.setattr(pipeline, "make_ai_client", lambda *a, **k: FakeAI())
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)
    settings = app.default_settings()
    settings.update({"micro_cut_enabled": True, "ai_retry_count": 2, "ai_strict_mode": True})
    source = [_micro_candidate()]
    transcript = [pipeline.TranscriptSegment(start=0, end=60, text="сильный самостоятельный момент")]

    with pytest.raises(RuntimeError, match="Micro AI warm-up"):
        pipeline.build_micro_candidates(tmp_path, source, transcript, settings, _MicroLogger())


def test_micro_ai_uses_semantic_retry_budget_for_non_transport_failure(monkeypatch, tmp_path: Path):
    class FakeAI:
        def __init__(self):
            self.primary_calls = 0

        def warmup(self, model=None, timeout=120):
            return {"ok": True, "model": model, "elapsed_seconds": 0.01}

        def generate_json(self, prompt, *, operation="text_json", **kwargs):
            if operation == "micro_ai":
                self.primary_calls += 1
                if self.primary_calls == 1:
                    raise ValueError("malformed semantic response")
                return {"clips": [{"id": 1, "score": 8.8, "keep": True, "title": "micro"}]}
            raise AssertionError(f"unexpected operation: {operation}")

    ai = FakeAI()
    monkeypatch.setattr(pipeline, "make_ai_client", lambda *a, **k: ai)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)
    settings = app.default_settings()
    settings.update({"micro_cut_enabled": True, "ai_retry_count": 2})
    source = [_micro_candidate()]
    transcript = [pipeline.TranscriptSegment(start=0, end=60, text="сильный самостоятельный момент")]

    out = pipeline.build_micro_candidates(tmp_path, source, transcript, settings, _MicroLogger())

    assert ai.primary_calls == 2
    assert out
    assert out is not source
    assert out[0].title == "micro"


def test_ctranslate2_probe_requires_lazy_windows_runtime_dlls(monkeypatch):
    class FakeCTranslate2:
        __version__ = "4.8.1"

        @staticmethod
        def get_cuda_device_count():
            return 1

        @staticmethod
        def get_supported_compute_types(device, index):
            assert device == "cuda"
            return {"int8", "int8_float32", "float32"}

    monkeypatch.setattr(hardware.importlib, "import_module", lambda name: FakeCTranslate2 if name == "ctranslate2" else None)
    monkeypatch.setattr(hardware, "prepare_nvidia_dll_paths", lambda: [r"C:\\venv\\Lib\\site-packages\\nvidia\\cublas\\bin"])
    monkeypatch.setattr(
        hardware,
        "_probe_windows_cuda_runtime_dlls",
        lambda: {
            "checked": True,
            "ok": False,
            "required": ["cublas64_12.dll"],
            "loaded": [],
            "missing": [{"name": "cublas64_12.dll", "error": "not found"}],
        },
    )

    # DLL discovery now runs inside an isolated child process. Exercise its
    # original platform logic here; parent crash handling has separate tests.
    info = hardware._ctranslate2_probe_in_process()

    assert info["cuda_device_count"] == 1
    assert info["cuda_ok"] is False
    assert "cublas64_12.dll" in info["hint"]
    rec = hardware.recommend_settings(
        {
            "cpu": {"logical_threads": 12},
            "memory": {"total_gb": 16},
            "nvidia": {"ok": True, "gpus": [{"memory_total_mb": 4096}]},
            "ctranslate2": info,
            "ffmpeg": {"nvenc_runtime_ok": True, "nvdec_advertised": True},
        }
    )
    assert rec["whisper_device"] == "cpu"
    assert rec["whisper_compute"] == "int8"


def test_micro_ai_stalled_batch_degrades_to_block_candidates_in_normal_mode(monkeypatch, tmp_path: Path):
    from highlight_studio.integrations.ai.runtime import AITransportError

    class FakeAI:
        def __init__(self):
            self.operations: list[str] = []

        def warmup(self, model=None, timeout=120):
            return {"ok": True, "model": model, "elapsed_seconds": 0.01}

        def generate_json(self, prompt, *, operation="text_json", **kwargs):
            self.operations.append(operation)
            raise AITransportError("Ollama stream stalled for 45s without data", transient=True)

    ai = FakeAI()
    monkeypatch.setattr(pipeline, "make_ai_client", lambda *a, **k: ai)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)
    settings = app.default_settings()
    settings.update({"micro_cut_enabled": True, "ai_retry_count": 2, "ai_strict_mode": False})
    source = [_micro_candidate()]
    transcript = [pipeline.TranscriptSegment(start=0, end=60, text="сильный самостоятельный момент")]
    logger = _MicroLogger()

    out = pipeline.build_micro_candidates(tmp_path, source, transcript, settings, logger)

    assert out
    assert out is not source
    # Transport failures are not multiplied by semantic retries: one primary
    # request plus one targeted rescue after a bounded runtime recovery.
    assert ai.operations == ["micro_ai", "micro_ai_single_id", "micro_ai_single_id"]
    health = json.loads((tmp_path / "ai_batch_health.json").read_text(encoding="utf-8"))
    assert health["stages"]["micro_ai"]["batches"]["1"]["state"] == "degraded"
    assert any("Micro AI degraded" in message for message in logger.messages)


def test_block_ai_degraded_fallback_is_complete_and_conservative():
    batch = [
        {"id": 1, "text": ""},
        {"id": 2, "text": "Это длинный осмысленный разговор со множеством разных слов! " * 8},
    ]
    out = pipeline.build_block_ai_degraded_items(batch, "Ollama stream stalled for 45s without data")
    assert set(out) == {1, 2}
    assert out[1]["_degraded_fallback"] is True
    assert out[1]["decision"] == "maybe"
    assert 5.2 <= out[1]["score"] <= 6.7
    assert 5.2 <= out[2]["score"] <= 6.7
    assert out[2]["score"] >= out[1]["score"]
    assert out[2]["standalone_clarity"] >= 0.5
    assert "stream stalled" in out[2]["reason"]


def test_block_ai_normal_mode_has_degraded_nonfatal_path():
    source = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert "Block AI degraded: batch" in source
    assert "build_block_ai_degraded_items(batch" in source
    assert 'state="degraded" if batch_degraded else "done"' in source
    assert "if strict_ai:" in source
    assert "недостающие блоки сохранены как transcript fallback" in source
    assert "Block AI circuit breaker" in source


def test_ollama_active_stream_can_outlive_base_hard_deadline(monkeypatch, tmp_path: Path):
    from highlight_studio.integrations.ai.ollama import OllamaClient
    import highlight_studio.integrations.ai.ollama as ollama_mod

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def raise_for_status(self): return None
        def iter_lines(self, decode_unicode=True):
            yield '{"response":"x","done":false}'
            yield '{"response":"y","done":true}'

    monkeypatch.setattr(ollama_mod.requests, "post", lambda *a, **k: FakeResponse())
    # started=0, first token at 10s, second token at 100s. Base deadline is 90s,
    # but the stream is alive and may continue to the bounded active deadline.
    times = iter([0.0, 10.0, 100.0])
    monkeypatch.setattr(ollama_mod.time, "monotonic", lambda: next(times))
    client = OllamaClient(
        settings={"ai_request_hard_timeout": 90, "ai_request_active_hard_timeout": 180},
        project_dir=tmp_path,
    )
    assert client._post_generate({"model": "qwen3:8b", "prompt": "x"}, 180) == "xy"


def test_block_item_cache_survives_batch_planning_changes(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        pipeline,
        "video_signature",
        lambda _d: {"size": 123, "partial_sha256": "abc", "signature_version": 2, "path": "ignored"},
    )
    settings = app.default_settings()
    block = {"id": 67, "start": 100.0, "end": 280.0, "text": "важный момент"}
    cache_dir = tmp_path / "ai_items"
    cache_dir.mkdir()
    item = {"id": 67, "score": 8.8, "decision": "keep", "title": "момент"}
    pipeline._save_block_ai_item_cache(cache_dir, tmp_path, settings, "qwen3:8b", "prompt", block, item)

    loaded = pipeline._load_block_ai_item_cache(cache_dir, tmp_path, settings, "qwen3:8b", "prompt", block)
    assert loaded is not None
    assert loaded["id"] == 67
    assert loaded["score"] == 8.8
    # Batch size/index are intentionally not inputs to the per-item fingerprint.
    assert "batch_i" not in pipeline._block_ai_item_fingerprint(tmp_path, settings, "qwen3:8b", "prompt", block)


def test_degraded_block_placeholder_never_poison_item_cache(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        pipeline,
        "video_signature",
        lambda _d: {"size": 123, "partial_sha256": "abc", "signature_version": 2},
    )
    settings = app.default_settings()
    block = {"id": 1, "start": 0.0, "end": 180.0, "text": "текст"}
    cache_dir = tmp_path / "ai_items"
    cache_dir.mkdir()
    fallback = pipeline.build_block_ai_degraded_items([block], "offline")[1]
    pipeline._save_block_ai_item_cache(cache_dir, tmp_path, settings, "qwen3:8b", "prompt", block, fallback)
    assert list(cache_dir.iterdir()) == []


def test_micro_item_cache_remaps_batch_local_id(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        pipeline,
        "video_signature",
        lambda _d: {"size": 123, "partial_sha256": "abc", "signature_version": 2},
    )
    settings = app.default_settings()
    window = {"start": 10.0, "end": 50.0, "parent_id": 7, "parent_score": 8.2, "text": "момент"}
    cache_dir = tmp_path / "micro_items"
    cache_dir.mkdir()
    pipeline._save_micro_ai_item_cache(
        cache_dir, tmp_path, settings, "qwen3:8b", window,
        {"id": 2, "score": 8.4, "keep": True, "title": "micro"},
    )
    loaded = pipeline._load_micro_ai_item_cache(cache_dir, tmp_path, settings, "qwen3:8b", window, 5)
    assert loaded is not None
    assert loaded["id"] == 5
    assert loaded["score"] == 8.4


def test_cancelled_status_keeps_actual_progress(tmp_path: Path):
    logger = pipeline.JobLogger(tmp_path)
    logger.set_status("running", 57, "working")
    logger.set_status("cancelled", 57, "cancelled")
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "cancelled"
    assert status["progress"] == 57


def test_structured_ollama_disables_thinking_by_default(tmp_path: Path):
    from highlight_studio.integrations.ai.ollama import OllamaClient

    client = OllamaClient(settings={}, project_dir=tmp_path)
    payload = client._json_payload("score", "qwen3:8b")
    assert payload["format"] == "json"
    assert payload["think"] is False

    opted_in = OllamaClient(settings={"ollama_think": True}, project_dir=tmp_path)
    assert opted_in._json_payload("score", "qwen3:8b")["think"] is True


def test_normal_preflight_treats_ai_outage_as_degradable_warning(monkeypatch, tmp_path: Path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"media")

    class FakeAI:
        def check(self, *_args, **_kwargs):
            return {"ok": False, "error": "Ollama offline", "models": []}

    monkeypatch.setattr(pipeline, "project_paths", lambda _d: {"video": video})
    monkeypatch.setattr(pipeline, "which", lambda name: f"/{name}")
    monkeypatch.setattr(pipeline, "video_duration", lambda _p: 60.0)
    monkeypatch.setattr(pipeline, "audio_streams", lambda _p: [0])
    monkeypatch.setattr(pipeline.shutil, "disk_usage", lambda _p: type("DU", (), {"free": 50 * 1024**3})())
    monkeypatch.setattr(pipeline, "make_ai_client", lambda *a, **k: FakeAI())

    settings = app.default_settings()
    settings.update({"ai_strict_mode": False, "full_ai_coverage": False})
    result = pipeline.preflight(tmp_path, settings)
    ai_check = next(x for x in result["checks"] if x["name"] == "AI Engine")
    assert ai_check["status"] == "WARN"
    assert result["ok"] is True

    strict = dict(settings)
    strict["ai_strict_mode"] = True
    strict_result = pipeline.preflight(tmp_path, strict)
    strict_ai_check = next(x for x in strict_result["checks"] if x["name"] == "AI Engine")
    assert strict_ai_check["status"] == "FAIL"
    assert strict_result["ok"] is False


def test_degraded_minimum_selection_never_uses_non_primary_or_explicit_reject(tmp_path: Path):
    logger = pipeline.JobLogger(tmp_path)
    settings = app.default_settings()
    settings.update({"ai_strict_mode": False, "full_ai_coverage": False})
    candidates = [
        pipeline.Candidate(1, 0, 40, 6.4, "safe", "", decision="maybe", standalone_clarity=0.48),
        pipeline.Candidate(
            2, 50, 90, 9.5, "replay", "", decision="keep", standalone_clarity=0.9,
            content_class="replay", content_class_confidence=0.95,
        ),
        pipeline.Candidate(3, 100, 140, 9.0, "reject", "", decision="remove", standalone_clarity=0.9),
        pipeline.Candidate(4, 150, 190, 5.0, "weak", "", decision="maybe", standalone_clarity=0.9),
    ]
    out = pipeline.degraded_minimum_selection(candidates, settings, 600.0, logger)
    assert [c.id for c in out] == [1]
    assert "degraded_minimum_selection" in out[0].reason


def test_analysis_fingerprints_ignore_source_path_and_timestamps(monkeypatch, tmp_path: Path):
    settings = app.default_settings()
    sig = {"size": 12345, "partial_sha256": "same-content", "signature_version": 2}
    monkeypatch.setattr(
        pipeline,
        "video_signature",
        lambda _d: {**sig, "path": "A:/old/input.mp4", "mtime_ns": 1, "ctime_ns": 2},
    )
    first = pipeline.transcript_fingerprint(tmp_path, settings)
    monkeypatch.setattr(
        pipeline,
        "video_signature",
        lambda _d: {**sig, "path": "F:/new/input.mp4", "mtime_ns": 999, "ctime_ns": 888},
    )
    second = pipeline.transcript_fingerprint(tmp_path, settings)
    assert first == second
