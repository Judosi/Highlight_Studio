from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

import highlight_studio.api.app as app
from highlight_studio.services import hardware
from highlight_studio.services import pipeline


def caps_4gb() -> dict:
    data = {
        "profile_id": "AUTO_CPU12_RAM16GB_GeForce_GTX_1050_Ti_VRAM4GB",
        "cpu": {"name": "Ryzen 5 2600", "logical_threads": 12},
        "memory": {"total_gb": 16.0},
        "nvidia": {
            "ok": True,
            "gpus": [{"name": "GeForce GTX 1050 Ti", "memory_total_mb": 4096, "memory_free_mb": 3800}],
        },
        "ctranslate2": {
            "cuda_ok": True,
            "compute_types": ["int8", "int8_float32", "float32"],
            "cuda_device_count": 1,
            "version": "4.8.1",
        },
        "ffmpeg": {"nvenc_runtime_ok": True, "nvdec_advertised": True},
    }
    data["recommended_settings"] = hardware.recommend_settings(data)
    return data


def caps_cpu_only() -> dict:
    data = {
        "profile_id": "AUTO_CPU8_RAM16GB_NO_NVIDIA",
        "cpu": {"name": "CPU", "logical_threads": 8},
        "memory": {"total_gb": 16.0},
        "nvidia": {"ok": False, "gpus": []},
        "ctranslate2": {"cuda_ok": False, "compute_types": [], "cuda_device_count": 0},
        "ffmpeg": {"nvenc_runtime_ok": False, "nvdec_advertised": False},
    }
    data["recommended_settings"] = hardware.recommend_settings(data)
    return data


def test_4gb_nvidia_profile_uses_cuda_nvenc_and_conservative_limits():
    rec = hardware.recommend_settings(
        caps_4gb(),
        {"ai_batch_size": 4, "micro_batch_size": 8, "visual_scan_max_samples": 1200, "ocr_every_n_visual_samples": 2, "whisper_model": "base"},
    )
    assert rec["whisper_device"] == "cuda"
    assert rec["whisper_compute"] == "int8_float32"
    assert rec["whisper_model"] == "base"
    assert rec["video_encoder"] == "h264_nvenc"
    assert rec["cpu_worker_limit"] == 6
    assert rec["gpu_job_limit"] == 1
    assert rec["gpu_vram_reserve_mb"] == 768
    assert rec["ai_batch_size"] == 4
    assert rec["micro_batch_size"] == 8
    assert rec["hardware_decode"] == "auto"


def test_cpu_only_profile_remains_fully_functional():
    rec = hardware.recommend_settings(caps_cpu_only(), {"ai_batch_size": 2, "micro_batch_size": 2})
    assert rec["whisper_device"] == "cpu"
    assert rec["whisper_compute"] == "int8"
    assert rec["video_encoder"] == "libx264"
    assert rec["cpu_worker_limit"] == 4
    assert rec["gpu_vram_reserve_mb"] == 0


def test_legacy_auto_profile_migrates_old_cpu_defaults_to_portable_auto():
    legacy = app.default_settings()
    legacy.pop("hardware_auto_optimize", None)
    legacy["hardware_profile"] = "GTX1050Ti_16GB_Ryzen2600"
    legacy["whisper_device"] = "cpu"
    legacy["whisper_compute"] = "int8"
    legacy["video_encoder"] = "libx264"
    out = app.validate_settings(legacy)
    assert out["hardware_auto_optimize"] is True
    assert out["hardware_profile"] == "Auto"
    assert out["whisper_device"] == "auto"
    assert out["whisper_compute"] == "auto"
    assert out["video_encoder"] == "auto"


def test_runtime_auto_fields_resolve_to_effective_machine_settings(monkeypatch, tmp_path: Path):
    caps = caps_4gb()
    monkeypatch.setattr(app, "detect_hardware_capabilities", lambda: caps)
    monkeypatch.setattr(app, "recommend_hardware_settings", lambda capabilities, current: hardware.recommend_settings(capabilities, current))
    settings = app.default_settings()
    settings.update(
        {
            "hardware_auto_optimize": True,
            "whisper_device": "auto",
            "whisper_compute": "auto",
            "video_encoder": "auto",
            "cpu_worker_limit": 0,
            "ai_batch_size": 4,
            "micro_batch_size": 8,
        }
    )
    out = app.runtime_optimized_settings(settings, tmp_path)
    assert out["whisper_device"] == "cuda"
    assert out["whisper_compute"] == "int8_float32"
    assert out["video_encoder"] == "h264_nvenc"
    assert out["cpu_worker_limit"] == 6
    assert out["ai_batch_size"] == 4
    assert (tmp_path / "hardware_runtime.json").exists()


def test_auto_encoder_requires_real_nvenc_runtime_success(monkeypatch):
    logger = type("Logger", (), {"log": lambda self, msg: None})()
    monkeypatch.setattr(pipeline, "detect_hardware_capabilities", lambda: caps_4gb())
    args, encoder = pipeline.video_encode_args("ffmpeg", {"video_encoder": "auto", "render_preset": "veryfast", "crf": 23}, logger)
    assert encoder == "h264_nvenc"
    assert "h264_nvenc" in args

    bad = caps_4gb()
    bad["ffmpeg"] = {"nvenc_runtime_ok": False}
    bad["recommended_settings"] = hardware.recommend_settings(bad)
    monkeypatch.setattr(pipeline, "detect_hardware_capabilities", lambda: bad)
    args, encoder = pipeline.video_encode_args("ffmpeg", {"video_encoder": "auto", "render_preset": "veryfast", "crf": 23}, logger)
    assert encoder == "libx264"
    assert "libx264" in args


def test_invalid_manual_hardware_values_are_rejected():
    bad = app.default_settings()
    bad["whisper_device"] = "magic"
    with pytest.raises(HTTPException) as exc:
        app.validate_settings(bad)
    assert exc.value.status_code == 422


def test_unknown_cuda_compute_list_stays_conservative_on_low_vram():
    assert hardware._choose_cuda_compute([], 4096) == "int8"
