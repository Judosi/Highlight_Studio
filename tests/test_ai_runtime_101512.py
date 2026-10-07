from __future__ import annotations

import threading
import time

import pytest
import requests

import highlight_studio.api.app as app
from highlight_studio.integrations.ai.runtime import (
    AIExecutionController,
    AIResponseFormatError,
    AIResourceManager,
    AITransportError,
)
from highlight_studio.services import hardware, pipeline


def test_runtime_retries_only_transient_transport(tmp_path):
    calls = {"n": 0}

    def request_raw():
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("temporary connection refused")
        return '{"items":[{"id":1}]}'

    ctl = AIExecutionController(settings={"ai_transport_retry_count": 2, "gpu_job_limit": 1}, project_dir=tmp_path)
    out = ctl.execute_json(request_raw, operation="block_ai", model="qwen3:8b")
    assert out["items"][0]["id"] == 1
    assert calls["n"] == 2
    assert (tmp_path / "ai_runtime_trace.jsonl").exists()
    assert (tmp_path / "ai_runtime_summary.json").exists()


def test_runtime_exposes_normalized_transport_error_to_pipeline(tmp_path):
    def request_raw():
        raise requests.ReadTimeout("local Ollama read timed out")

    ctl = AIExecutionController(
        settings={"ai_transport_retry_count": 1, "gpu_job_limit": 1},
        project_dir=tmp_path,
    )
    with pytest.raises(AITransportError, match="read timed out") as exc:
        ctl.execute_json(request_raw, operation="micro_ai", model="qwen3:8b")
    assert exc.value.transient is True


def test_runtime_does_not_transport_retry_bad_json(tmp_path):
    calls = {"n": 0}

    def request_raw():
        calls["n"] += 1
        return "not-json-at-all"

    ctl = AIExecutionController(settings={"ai_transport_retry_count": 3}, project_dir=tmp_path)
    try:
        ctl.execute_json(request_raw, operation="micro_ai", model="qwen3:8b")
    except AIResponseFormatError:
        pass
    else:
        raise AssertionError("expected AIResponseFormatError")
    # Format/semantic errors are owned by pipeline retry logic, not multiplied
    # inside the transport controller.
    assert calls["n"] == 1


def test_local_json_repair_accepts_python_literal_without_second_inference(tmp_path):
    calls = {"n": 0}

    def request_raw():
        calls["n"] += 1
        return "{'blocks': [{'id': 4, 'score': 8.5, 'keep': True}]}"

    ctl = AIExecutionController(settings={}, project_dir=tmp_path)
    out = ctl.execute_json(request_raw, operation="block_ai", model="qwen3:8b")
    assert out["blocks"][0]["id"] == 4
    assert out["blocks"][0]["keep"] is True
    assert calls["n"] == 1


def test_heavy_ai_resource_lease_serializes_capacity_one():
    order: list[str] = []
    entered = threading.Event()

    def first():
        with AIResourceManager.lease("test_gpu_101512", capacity=1):
            order.append("first-enter")
            entered.set()
            time.sleep(0.12)
            order.append("first-exit")

    def second():
        entered.wait(1)
        with AIResourceManager.lease("test_gpu_101512", capacity=1):
            order.append("second-enter")

    a = threading.Thread(target=first)
    b = threading.Thread(target=second)
    a.start()
    b.start()
    a.join(2)
    b.join(2)
    assert order == ["first-enter", "first-exit", "second-enter"]


def test_heavy_ai_resource_callers_with_different_limits_still_coordinate():
    resource = "test_gpu_mixed_limits_101512"
    order: list[str] = []
    entered = threading.Event()

    def conservative():
        with AIResourceManager.lease(resource, capacity=1):
            order.append("one-enter")
            entered.set()
            time.sleep(0.12)
            order.append("one-exit")

    def permissive():
        entered.wait(1)
        with AIResourceManager.lease(resource, capacity=2):
            order.append("two-enter")

    a = threading.Thread(target=conservative)
    b = threading.Thread(target=permissive)
    a.start()
    b.start()
    a.join(2)
    b.join(2)
    assert order == ["one-enter", "one-exit", "two-enter"]


def test_hardware_matrix_separates_detected_from_runtime_ready():
    capabilities = {
        "nvidia": {"ok": True, "gpus": [{"name": "GTX 1050 Ti"}], "hint": "detected"},
        "ctranslate2": {"cuda_ok": False, "hint": "CUDA backend unavailable"},
        "ffmpeg": {"nvenc_advertised": True, "nvenc_runtime_ok": False, "nvdec_advertised": True, "hint": "runtime failed"},
        "recommended_settings": {"whisper_device": "cpu", "video_encoder": "libx264", "hardware_decode": "auto"},
    }
    matrix = hardware.hardware_readiness_matrix(capabilities)
    assert matrix["nvidia_gpu"]["detected"] is True
    assert matrix["whisper_cuda"]["detected"] is True
    assert matrix["whisper_cuda"]["runtime_ready"] is False
    assert matrix["whisper_cuda"]["effective"] == "cpu"
    assert matrix["nvenc"]["detected"] is True
    assert matrix["nvenc"]["runtime_ready"] is False
    assert matrix["nvenc"]["effective"] == "libx264"


def _candidate(cid: int, start: float, score: float, **kwargs) -> pipeline.Candidate:
    return pipeline.Candidate(
        id=cid,
        start=start,
        end=start + kwargs.pop("duration", 30.0),
        score=score,
        title=kwargs.pop("title", f"c{cid}"),
        reason=kwargs.pop("reason", "reason"),
        text_preview=kwargs.pop("text_preview", "text"),
        **kwargs,
    )


def test_candidate_trace_explains_selected_and_reconnect_rejection(tmp_path):
    settings = app.default_settings()
    good = _candidate(1, 100, 9.1, confidence=8.0, standalone_clarity=0.9, content_class="primary_live", content_class_confidence=0.9)
    bad = _candidate(2, 300, 9.8, confidence=9.0, standalone_clarity=1.0, content_class="reconnect", content_class_confidence=0.98)
    report = pipeline.build_candidate_decision_trace(tmp_path, [bad, good], [good], 7200, settings)
    by_id = {x["id"]: x for x in report["items"]}
    assert by_id[1]["selected"] is True
    assert by_id[1]["selection_reason"] == "selected"
    assert by_id[2]["selected"] is False
    assert by_id[2]["selection_reason"].startswith("semantic_guard:reconnect")
    assert report["selected_count"] == 1
    assert (tmp_path / "candidate_decision_trace.json").exists()


def test_release_defaults_keep_qwen3_8b_and_semantic_quality():
    settings = app.default_settings()
    assert settings["text_model"] == "qwen3:8b"
    assert settings["semantic_quality_guard_enabled"] is True
    assert settings["quality_first_selection_enabled"] is True
    assert settings["ai_transport_retry_count"] == 2
