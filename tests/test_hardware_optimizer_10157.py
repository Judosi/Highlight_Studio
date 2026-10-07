from __future__ import annotations

import json
import time
from pathlib import Path

import highlight_studio.api.app as app
from highlight_studio.core import utils
from highlight_studio.services import hardware, pipeline


def caps_4gb() -> dict:
    return {
        "profile_id": "AUTO_CPU12_RAM16GB_GeForce_GTX_1050_Ti_VRAM4GB",
        "cpu": {"name": "AMD Ryzen 5 2600", "logical_threads": 12},
        "memory": {"total_gb": 16.0},
        "nvidia": {"ok": True, "gpus": [{"name": "GeForce GTX 1050 Ti", "memory_total_mb": 4096}]},
        "ctranslate2": {"cuda_ok": True, "compute_types": ["int8", "int8_float32", "float32"]},
        "ffmpeg": {"nvenc_runtime_ok": True, "nvdec_advertised": True},
    }


def test_low_vram_auto_preserves_logical_ai_batch_and_quality_coverage():
    current = {
        "ai_batch_size": 4,
        "micro_batch_size": 8,
        "whisper_model": "small",
        "ollama_num_ctx": 4096,
        "visual_scan_max_samples": 1200,
        "ocr_every_n_visual_samples": 2,
    }
    rec = hardware.recommend_settings(caps_4gb(), current)
    assert rec["gpu_job_limit"] == 1
    assert rec["micro_batch_size"] == 8
    assert rec["ai_batch_size"] == 4
    assert rec["whisper_model"] == "small"
    assert rec["visual_scan_max_samples"] == 1200
    assert rec["ocr_every_n_visual_samples"] == 2
    assert rec["hardware_decode"] == "auto"


def test_10156_low_vram_batch_one_is_migrated_without_changing_models_or_coverage():
    legacy = app.default_settings()
    legacy.pop("hardware_quality_guard_enabled", None)
    legacy.update(
        {
            "hardware_auto_optimize": True,
            "hardware_profile": "Auto",
            "ai_batch_size": 1,
            "micro_batch_size": 1,
            "text_model": "qwen3:8b",
            "whisper_model": "small",
            "visual_scan_max_samples": 1200,
            "ocr_every_n_visual_samples": 2,
        }
    )
    out = app.validate_settings(legacy)
    assert out["hardware_quality_guard_enabled"] is True
    assert out["ai_batch_size"] == 3
    assert out["micro_batch_size"] == 8
    assert out["text_model"] == "qwen3:8b"
    assert out["whisper_model"] == "small"
    assert out["visual_scan_max_samples"] == 1200
    assert out["ocr_every_n_visual_samples"] == 2


def test_hardware_preset_quality_guard_does_not_downgrade_analysis(monkeypatch):
    monkeypatch.setattr(app, "detect_hardware_capabilities", lambda: caps_4gb())
    current = app.default_settings()
    current.update(
        {
            "hardware_quality_guard_enabled": True,
            "text_model": "qwen3:8b",
            "whisper_model": "small",
            "visual_mode": "Средний",
            "visual_scan_interval_seconds": 5,
            "visual_scan_max_samples": 1200,
            "ocr_every_n_visual_samples": 2,
            "top_blocks_for_micro": 36,
            "micro_batch_size": 8,
        }
    )
    out = app.hardware_preset_settings(current, "auto_balanced")
    for key in (
        "text_model",
        "whisper_model",
        "visual_mode",
        "visual_scan_interval_seconds",
        "visual_scan_max_samples",
        "ocr_every_n_visual_samples",
        "top_blocks_for_micro",
    ):
        assert out[key] == current[key]
    assert out["micro_batch_size"] == 8


def test_runtime_auto_records_quality_guard_and_keeps_qwen8b(monkeypatch, tmp_path: Path):
    caps = caps_4gb()
    monkeypatch.setattr(app, "detect_hardware_capabilities", lambda: caps)
    monkeypatch.setattr(app, "recommend_hardware_settings", lambda capabilities, current: hardware.recommend_settings(capabilities, current))
    settings = app.default_settings()
    settings.update(
        {
            "hardware_auto_optimize": True,
            "hardware_quality_guard_enabled": True,
            "text_model": "qwen3:8b",
            "whisper_model": "small",
            "whisper_device": "auto",
            "whisper_compute": "auto",
            "video_encoder": "auto",
            "hardware_decode": "auto",
            "micro_batch_size": 8,
            "visual_scan_max_samples": 1200,
            "ocr_every_n_visual_samples": 2,
        }
    )
    out = app.runtime_optimized_settings(settings, tmp_path)
    assert out["text_model"] == "qwen3:8b"
    assert out["whisper_model"] == "small"
    assert out["micro_batch_size"] == 8
    assert out["visual_scan_max_samples"] == 1200
    assert out["ocr_every_n_visual_samples"] == 2
    guard = json.loads((tmp_path / "quality_guard_runtime.json").read_text(encoding="utf-8"))
    assert guard["unchanged"] is True


def test_ocr_workers_for_ryzen_2600_16gb_are_bounded_to_three(monkeypatch):
    monkeypatch.setattr(pipeline, "detect_hardware_capabilities", lambda: caps_4gb())
    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 12)
    assert pipeline._ocr_worker_count({"cpu_worker_limit": 6}, 600) == 3


def test_visual_cuda_path_keeps_identical_sampling_filters(tmp_path: Path):
    video = tmp_path / "input.mp4"
    pattern = tmp_path / "scan_%05d.jpg"
    cpu = utils._visual_extract_command("ffmpeg", video, pattern, interval=5, width=360, max_samples=1200, decode_mode="cpu")
    cuda = utils._visual_extract_command("ffmpeg", video, pattern, interval=5, width=360, max_samples=1200, decode_mode="cuda")
    assert "-hwaccel" not in cpu
    assert cuda[cuda.index("-hwaccel") + 1] == "cuda"
    assert cpu[cpu.index("-vf") + 1] == cuda[cuda.index("-vf") + 1] == "fps=1/5.000000,scale=360:-2"
    assert cpu[cpu.index("-frames:v") + 1] == cuda[cuda.index("-frames:v") + 1] == "1200"


def test_overall_eta_never_shorter_than_current_real_counter_stage(tmp_path: Path):
    logger = pipeline.JobLogger(tmp_path)
    logger.started_at = time.time() - 600
    logger._stage_started["ocr_scan"] = time.time() - 60
    logger.set_step("ocr_scan", 50, 600, None, "OCR scan 50/600", global_start=70, global_end=82)
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["eta_seconds"] >= status["stage_eta_seconds"]
    assert (tmp_path / "performance_telemetry.json").exists()


def test_progress_state_route_is_available():
    paths = {route.path for route in app.app.routes if getattr(route, "path", None)}
    assert "/api/projects/{project_id}/progress-state" in paths


def test_micro_batch_partition_preserves_every_window_and_respects_context_budget():
    windows = [
        {"start": float(i), "end": float(i + 30), "text": ("важный момент " * 120) + str(i), "parent_id": i}
        for i in range(17)
    ]
    batches, char_budget = pipeline.partition_micro_windows_for_context(windows, max_items=8, num_ctx=4096)
    flattened = [item for batch in batches for item in batch]
    assert flattened == windows
    assert all(1 <= len(batch) <= 8 for batch in batches)
    assert len(batches) > 3  # context guard is allowed to split an unsafe logical batch
    assert char_budget >= 3200


def test_micro_batch_partition_can_still_group_short_windows_for_throughput():
    windows = [{"start": i, "end": i + 20, "text": "короткий фрагмент", "parent_id": i} for i in range(16)]
    batches, _ = pipeline.partition_micro_windows_for_context(windows, max_items=8, num_ctx=4096)
    assert [len(batch) for batch in batches] == [8, 8]
