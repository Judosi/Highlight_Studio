from __future__ import annotations

import ctypes
import importlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any


def _run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return p.returncode, "\n".join(part for part in (p.stdout, p.stderr) if part).strip()
    except Exception as exc:
        return 127, str(exc)


def _cpu_name() -> str:
    name = (platform.processor() or "").strip()
    if name and name.lower() not in {"amd64", "x86_64"}:
        return name
    if os.name == "nt":
        code, out = _run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)",
            ],
            timeout=5,
        )
        if code == 0 and out.strip():
            return out.strip().splitlines()[0]
    if Path("/proc/cpuinfo").exists():
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("model name") and ":" in line:
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
    return platform.machine() or "Unknown CPU"


def _ram_total_bytes() -> int:
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        except Exception:
            pass
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except Exception:
        return 0


_RESOURCE_SAMPLE_LOCK = threading.Lock()
_RESOURCE_CPU_PREVIOUS: tuple[int, int] | None = None


def _cpu_counters() -> tuple[int, int] | None:
    """Return (busy_ticks, total_ticks) using native system counters."""
    if os.name == "nt":
        class FileTime(ctypes.Structure):
            _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

        idle = FileTime()
        kernel = FileTime()
        user = FileTime()
        try:
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return None
            def value(ft: FileTime) -> int:
                return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)
            idle_v = value(idle)
            kernel_v = value(kernel)
            user_v = value(user)
            total = kernel_v + user_v
            return max(0, total - idle_v), total
        except Exception:
            return None
    try:
        first = Path("/proc/stat").read_text(encoding="utf-8", errors="replace").splitlines()[0].split()[1:]
        values = [int(x) for x in first]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        return max(0, total - idle), total
    except Exception:
        return None


def _memory_snapshot() -> dict[str, Any]:
    total = 0
    available = 0
    load = None
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                total = int(status.ullTotalPhys)
                available = int(status.ullAvailPhys)
                load = float(status.dwMemoryLoad)
        except Exception:
            pass
    else:
        try:
            info: dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
                if ":" not in line:
                    continue
                key, rest = line.split(":", 1)
                value = int(rest.strip().split()[0]) * 1024
                info[key] = value
            total = int(info.get("MemTotal", 0))
            available = int(info.get("MemAvailable", info.get("MemFree", 0)))
            if total:
                load = 100.0 * (total - available) / total
        except Exception:
            pass
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_percent": round(float(load), 1) if load is not None else None,
    }


def _gpu_usage_snapshot() -> dict[str, Any]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"available": False}
    code, out = _run(
        [exe, "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"], timeout=4
    )
    if code != 0 or not out.strip():
        return {"available": False, "error": out[-300:] if out else "nvidia-smi failed"}
    try:
        parts = [x.strip() for x in out.splitlines()[0].split(",")]
        return {
            "available": True,
            "utilization_percent": float(parts[0]),
            "memory_used_mb": float(parts[1]),
            "memory_total_mb": float(parts[2]),
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def system_resource_snapshot() -> dict[str, Any]:
    """Cheap periodic CPU/RAM/GPU snapshot for performance telemetry.

    No third-party monitor is required. CPU percentage is derived from native
    cumulative counters, RAM from OS memory APIs, and NVIDIA utilization from a
    single short nvidia-smi query when available.
    """
    global _RESOURCE_CPU_PREVIOUS
    with _RESOURCE_SAMPLE_LOCK:
        counters = _cpu_counters()
        cpu_percent = None
        if counters is not None and _RESOURCE_CPU_PREVIOUS is not None:
            busy_delta = counters[0] - _RESOURCE_CPU_PREVIOUS[0]
            total_delta = counters[1] - _RESOURCE_CPU_PREVIOUS[1]
            if total_delta > 0:
                cpu_percent = max(0.0, min(100.0, 100.0 * busy_delta / total_delta))
        if counters is not None:
            _RESOURCE_CPU_PREVIOUS = counters
    return {
        "at": time.time(),
        "cpu_percent": round(cpu_percent, 1) if cpu_percent is not None else None,
        "memory": _memory_snapshot(),
        "gpu": _gpu_usage_snapshot(),
    }


_DLL_HANDLES: list[Any] = []
_CUDA_DLL_PROBE_HANDLES: list[Any] = []


def prepare_nvidia_dll_paths() -> list[str]:
    """Expose CUDA/cuDNN DLL folders installed by NVIDIA Python wheels on Windows.

    CTranslate2 loads CUDA libraries dynamically. On Windows the NVIDIA wheels
    place their DLLs under site-packages/nvidia/*/bin, which is not guaranteed
    to be in PATH. Adding those directories here makes the portable venv usable
    without asking the user to edit the global PATH.
    """
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return []
    discovered: list[str] = []
    roots: list[Path] = []
    for entry in sys.path:
        try:
            path = Path(entry)
        except Exception:
            continue
        if path.is_dir():
            roots.append(path)
    seen: set[str] = set()
    for root in roots:
        nvidia = root / "nvidia"
        if not nvidia.is_dir():
            continue
        for pattern in ("*/bin", "*/lib/x64", "*/lib"):
            for folder in nvidia.glob(pattern):
                if not folder.is_dir():
                    continue
                key = str(folder.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                try:
                    handle = os.add_dll_directory(str(folder.resolve()))
                    _DLL_HANDLES.append(handle)
                    discovered.append(str(folder.resolve()))
                except (OSError, FileNotFoundError):
                    continue
    return discovered


def _probe_windows_cuda_runtime_dlls() -> dict[str, Any]:
    """Verify the CUDA 12/cuDNN runtime expected by the Windows GPU bundle.

    ``ctranslate2.get_cuda_device_count()`` can succeed with only an NVIDIA
    driver present even though the first real Whisper inference later fails when
    CTranslate2 lazily loads cuBLAS/cuDNN.  Probe those DLLs up front so Auto
    mode selects CPU immediately instead of failing on chunk 1.
    """
    if os.name != "nt":
        return {"checked": False, "ok": True, "required": [], "loaded": [], "missing": []}

    required = ["cublas64_12.dll", "cublasLt64_12.dll", "cudnn_ops64_9.dll"]
    loaded: list[str] = []
    missing: list[dict[str, str]] = []
    for name in required:
        try:
            handle = ctypes.WinDLL(name)
            _CUDA_DLL_PROBE_HANDLES.append(handle)
            loaded.append(name)
        except Exception as exc:
            missing.append({"name": name, "error": str(exc)[:500]})
    return {
        "checked": True,
        "ok": not missing,
        "required": required,
        "loaded": loaded,
        "missing": missing,
    }


def _nvidia_smi() -> dict[str, Any]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {
            "ok": False,
            "path": "",
            "gpus": [],
            "driver_version": "",
            "cuda_driver_version": "",
            "hint": "nvidia-smi не найден",
        }
    query = "name,memory.total,memory.free,driver_version,utilization.gpu,utilization.memory"
    code, out = _run([exe, f"--query-gpu={query}", "--format=csv,noheader,nounits"], timeout=8)
    gpus: list[dict[str, Any]] = []
    if code == 0:
        for index, line in enumerate(out.splitlines()):
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 6:
                continue
            try:
                total_mb = int(float(parts[1]))
            except Exception:
                total_mb = 0
            try:
                free_mb = int(float(parts[2]))
            except Exception:
                free_mb = 0
            try:
                util = int(float(parts[4]))
            except Exception:
                util = 0
            try:
                mem_util = int(float(parts[5]))
            except Exception:
                mem_util = 0
            gpus.append(
                {
                    "index": index,
                    "name": parts[0],
                    "memory_total_mb": total_mb,
                    "memory_free_mb": free_mb,
                    "driver_version": parts[3],
                    "utilization_gpu_percent": util,
                    "utilization_memory_percent": mem_util,
                }
            )
    version_code, version_out = _run([exe], timeout=8)
    cuda_driver = ""
    if version_code == 0:
        match = re.search(r"CUDA Version:\s*([0-9.]+)", version_out)
        if match:
            cuda_driver = match.group(1)
    driver = gpus[0].get("driver_version", "") if gpus else ""
    return {
        "ok": code == 0 and bool(gpus),
        "path": exe,
        "gpus": gpus,
        "driver_version": driver,
        "cuda_driver_version": cuda_driver,
        "hint": "NVIDIA GPU обнаружена" if gpus else (out[-500:] if out else "nvidia-smi не вернул GPU"),
    }


def _ctranslate2_probe() -> dict[str, Any]:
    # Native loader failures (SIGBUS/access violation) cannot be caught in
    # Python. Keep optional hardware discovery out of the server process.
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--probe-ctranslate2"]
    else:
        script = (
            "import sys,json;sys.path.insert(0," + repr(str(Path(__file__).resolve().parents[2])) + ");"
            "from highlight_studio.services.hardware import _ctranslate2_probe_in_process;"
            "print('HS_CT2_RESULT='+json.dumps(_ctranslate2_probe_in_process()))"
        )
        cmd = [sys.executable, "-c", script]
    code, output = _run(cmd, timeout=25)
    if code == 0:
        for line in output.splitlines():
            if line.startswith("HS_CT2_RESULT="):
                try:
                    result = json.loads(line.split("=", 1)[1])
                    if isinstance(result, dict) and isinstance(result.get("cuda_ok"), bool):
                        return result
                except (ValueError, TypeError):
                    break
    return {"ok": False, "installed": False, "version": "", "cuda_device_count": 0,
            "cuda_ok": False, "compute_types": [], "dll_dirs": [],
            "hint": f"Изолированная проверка CTranslate2 не завершилась (код {code}). Проверь GPU runtime; приложение продолжает работу."}


def _ctranslate2_probe_in_process() -> dict[str, Any]:
    dll_dirs = prepare_nvidia_dll_paths()
    try:
        ctranslate2 = importlib.import_module("ctranslate2")
    except Exception as exc:
        return {
            "ok": False,
            "installed": False,
            "version": "",
            "cuda_device_count": 0,
            "cuda_ok": False,
            "compute_types": [],
            "dll_dirs": dll_dirs,
            "hint": f"CTranslate2 import failed: {exc}",
        }
    version = str(getattr(ctranslate2, "__version__", "unknown"))
    try:
        count = int(ctranslate2.get_cuda_device_count())
    except Exception as exc:
        return {
            "ok": True,
            "installed": True,
            "version": version,
            "cuda_device_count": 0,
            "cuda_ok": False,
            "compute_types": [],
            "dll_dirs": dll_dirs,
            "hint": f"CTranslate2 CUDA probe failed: {exc}",
        }
    compute: list[str] = []
    if count > 0:
        try:
            compute = sorted(str(x) for x in ctranslate2.get_supported_compute_types("cuda", 0))
        except Exception:
            compute = []
    runtime_dlls = _probe_windows_cuda_runtime_dlls() if count > 0 else {
        "checked": os.name == "nt", "ok": count <= 0, "required": [], "loaded": [], "missing": []
    }
    cuda_ok = bool(count > 0 and runtime_dlls.get("ok", True))
    if count <= 0:
        hint = "CTranslate2 не видит CUDA-устройство"
    elif not runtime_dlls.get("ok", True):
        missing_names = ", ".join(str(item.get("name")) for item in runtime_dlls.get("missing", []) if isinstance(item, dict))
        hint = (
            "CTranslate2 видит GPU, но CUDA runtime неполный: не загружаются "
            + (missing_names or "обязательные CUDA/cuDNN DLL")
            + ". Установи commands/setup/INSTALL_GPU_ACCELERATION.bat; до этого используется CPU fallback."
        )
    else:
        hint = "CTranslate2 CUDA runtime готов"
    return {
        "ok": True,
        "installed": True,
        "version": version,
        "cuda_device_count": count,
        "cuda_ok": cuda_ok,
        "compute_types": compute,
        "dll_dirs": dll_dirs,
        "runtime_dlls": runtime_dlls,
        "hint": hint,
    }


def _ffmpeg_probe() -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {
            "ok": False,
            "path": "",
            "nvenc_advertised": False,
            "nvenc_runtime_ok": False,
            "nvdec_advertised": False,
            "hwaccels": [],
            "hint": "ffmpeg не найден",
        }
    _, encoders = _run([ffmpeg, "-hide_banner", "-encoders"], timeout=10)
    _, decoders = _run([ffmpeg, "-hide_banner", "-decoders"], timeout=10)
    _, hwaccels_text = _run([ffmpeg, "-hide_banner", "-hwaccels"], timeout=10)
    hwaccels = [line.strip() for line in hwaccels_text.splitlines() if line.strip() and "Hardware acceleration" not in line]
    nvenc_advertised = "h264_nvenc" in encoders
    nvdec_advertised = "cuda" in {x.lower() for x in hwaccels} or "h264_cuvid" in decoders or "hevc_cuvid" in decoders
    nvenc_runtime_ok = False
    nvenc_error = ""
    if nvenc_advertised:
        code, out = _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=64x64:r=1:d=0.1",
                "-frames:v",
                "1",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            timeout=12,
        )
        nvenc_runtime_ok = code == 0
        if not nvenc_runtime_ok:
            nvenc_error = out[-700:]
    return {
        "ok": True,
        "path": ffmpeg,
        "nvenc_advertised": nvenc_advertised,
        "nvenc_runtime_ok": nvenc_runtime_ok,
        "nvenc_error": nvenc_error,
        "nvdec_advertised": nvdec_advertised,
        "hwaccels": hwaccels,
        "hint": "NVENC готов" if nvenc_runtime_ok else ("NVENC есть в FFmpeg, но runtime test не прошёл" if nvenc_advertised else "NVENC отсутствует"),
    }


def _classify_vram(total_mb: int) -> str:
    if total_mb <= 0:
        return "none"
    if total_mb <= 4500:
        return "small"
    if total_mb <= 8500:
        return "medium"
    return "large"


def _choose_cuda_compute(supported: list[str], vram_mb: int) -> str:
    supported_set = set(supported)
    # Small/older cards benefit from a conservative INT8 path and lower VRAM
    # pressure. Modern GPUs with more memory prefer FP16 throughput.
    if vram_mb and vram_mb <= 6144:
        for candidate in ("int8_float16", "int8_float32", "int8", "float16", "float32"):
            if candidate in supported_set:
                return candidate
    for candidate in ("float16", "int8_float16", "int8_float32", "int8", "float32"):
        if candidate in supported_set:
            return candidate
    # If CTranslate2 could not enumerate compute types, stay conservative:
    # INT8 is widely supported on older CUDA-capable cards and avoids forcing
    # FP16 on Pascal-class GPUs such as GTX 1050 Ti.
    return "int8" if vram_mb and vram_mb <= 6144 else "float16"


def recommend_settings(capabilities: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    current = dict(current or {})
    cpu = capabilities.get("cpu") or {}
    memory = capabilities.get("memory") or {}
    nvidia = capabilities.get("nvidia") or {}
    ctranslate2 = capabilities.get("ctranslate2") or {}
    ffmpeg = capabilities.get("ffmpeg") or {}
    gpu = (nvidia.get("gpus") or [{}])[0] if nvidia.get("gpus") else {}
    vram_mb = int(gpu.get("memory_total_mb") or 0)
    logical = max(1, int(cpu.get("logical_threads") or 1))
    float(memory.get("total_gb") or 0.0)
    cuda_ok = bool(nvidia.get("ok") and ctranslate2.get("cuda_ok"))
    nvenc_ok = bool(ffmpeg.get("nvenc_runtime_ok"))
    cpu_workers = max(2, min(8, int(round(logical * 0.5)))) if logical >= 4 else 1
    gpu_jobs = 1 if vram_mb < 10000 else 2

    patch: dict[str, Any] = {
        "hardware_auto_optimize": True,
        "hardware_profile": "Auto",
        "hardware_detected_profile": capabilities.get("profile_id") or "AUTO_CPU",
        "cpu_worker_limit": cpu_workers,
        "gpu_job_limit": gpu_jobs,
        "gpu_vram_reserve_mb": 0 if vram_mb <= 0 else (768 if vram_mb <= 4500 else 1024),
        "whisper_device": "cuda" if cuda_ok else "cpu",
        "whisper_compute": _choose_cuda_compute(ctranslate2.get("compute_types") or [], vram_mb) if cuda_ok else "int8",
        "video_encoder": "h264_nvenc" if nvenc_ok else "libx264",
        # v10.15.7: hardware auto-tuning must not reduce analysis quality. Decode
        # may be benchmarked by Visual Scan when CUDA/NVDEC is advertised, but the
        # pipeline always keeps a CPU fallback with identical sampling/output.
        "hardware_decode": "auto" if bool(ffmpeg.get("nvdec_advertised")) else "off",
    }

    # IMPORTANT: AI batch sizes are logical prompt grouping, not concurrent GPU
    # jobs. 10.15.6 clamped them to 1 on <=6 GB cards, which created dozens of
    # unnecessary sequential Ollama requests without protecting VRAM. GPU
    # concurrency is controlled by gpu_job_limit instead, so preserve the user's
    # quality/coverage settings and only provide sensible defaults when absent.
    patch["ai_batch_size"] = max(1, min(4, int(current.get("ai_batch_size") or 3)))
    patch["micro_batch_size"] = max(1, min(12, int(current.get("micro_batch_size") or 8)))

    # Quality-critical knobs are intentionally preserved. Hardware detection may
    # choose devices/workers/encoders, but it must not silently downgrade the
    # Whisper model, visual coverage, OCR sampling or Ollama context.
    if "whisper_model" in current:
        patch["whisper_model"] = current.get("whisper_model")
    if "ollama_num_ctx" in current:
        patch["ollama_num_ctx"] = int(current.get("ollama_num_ctx") or 4096)
    if "visual_scan_max_samples" in current:
        patch["visual_scan_max_samples"] = int(current.get("visual_scan_max_samples") or 1200)
    if "ocr_every_n_visual_samples" in current:
        patch["ocr_every_n_visual_samples"] = int(current.get("ocr_every_n_visual_samples") or 2)
    return patch


@lru_cache(maxsize=2)
def detect_hardware_capabilities_cached(cache_bucket: int = 0) -> dict[str, Any]:
    logical = os.cpu_count() or 1
    total_ram = _ram_total_bytes()
    nvidia = _nvidia_smi()
    ctranslate2 = _ctranslate2_probe()
    ffmpeg = _ffmpeg_probe()
    gpu = (nvidia.get("gpus") or [{}])[0] if nvidia.get("gpus") else {}
    vram_mb = int(gpu.get("memory_total_mb") or 0)
    profile_bits = [f"CPU{logical}", f"RAM{int(round(total_ram / (1024 ** 3))) if total_ram else 0}GB"]
    if gpu.get("name"):
        safe_gpu = re.sub(r"[^A-Za-z0-9]+", "_", str(gpu.get("name"))).strip("_")
        profile_bits.extend([safe_gpu[:48], f"VRAM{max(1, round(vram_mb / 1024))}GB"])
    else:
        profile_bits.append("NO_NVIDIA")
    data: dict[str, Any] = {
        "ok": True,
        "detected_at": time.time(),
        "python": {"executable": sys.executable, "version": sys.version.split()[0]},
        "os": {"name": platform.system(), "version": platform.version(), "machine": platform.machine()},
        "cpu": {"name": _cpu_name(), "logical_threads": logical},
        "memory": {"total_bytes": total_ram, "total_gb": round(total_ram / (1024 ** 3), 1) if total_ram else 0.0},
        "nvidia": nvidia,
        "ctranslate2": ctranslate2,
        "ffmpeg": ffmpeg,
        "vram_class": _classify_vram(vram_mb),
        "profile_id": "AUTO_" + "_".join(profile_bits),
    }
    data["recommended_settings"] = recommend_settings(data)
    data["readiness_matrix"] = hardware_readiness_matrix(data)
    data["summary"] = hardware_summary(data)
    return data


def detect_hardware_capabilities(*, force: bool = False) -> dict[str, Any]:
    # Bucketed cache keeps dashboard polling cheap while force=True is available
    # for an explicit re-scan button/system check.
    bucket = int(time.time() // 15) if force else 0
    if force:
        detect_hardware_capabilities_cached.cache_clear()
    return detect_hardware_capabilities_cached(bucket)


def hardware_readiness_matrix(capabilities: dict[str, Any]) -> dict[str, Any]:
    """Separate detection from runtime readiness and effective fallback."""
    nvidia = capabilities.get("nvidia") or {}
    ctranslate2 = capabilities.get("ctranslate2") or {}
    ffmpeg = capabilities.get("ffmpeg") or {}
    recommended = capabilities.get("recommended_settings") or {}
    gpu_detected = bool(nvidia.get("ok") and (nvidia.get("gpus") or []))
    cuda_ready = bool(gpu_detected and ctranslate2.get("cuda_ok"))
    nvenc_advertised = bool(ffmpeg.get("nvenc_advertised"))
    nvenc_ready = bool(ffmpeg.get("nvenc_runtime_ok"))
    return {
        "nvidia_gpu": {
            "detected": gpu_detected,
            "runtime_ready": gpu_detected,
            "effective": "nvidia" if gpu_detected else "none",
            "details": nvidia.get("hint") or "",
        },
        "whisper_cuda": {
            "detected": gpu_detected,
            "runtime_ready": cuda_ready,
            "effective": str(recommended.get("whisper_device") or ("cuda" if cuda_ready else "cpu")),
            "details": ctranslate2.get("hint") or "",
        },
        "nvenc": {
            "detected": nvenc_advertised,
            "runtime_ready": nvenc_ready,
            "effective": str(recommended.get("video_encoder") or ("h264_nvenc" if nvenc_ready else "libx264")),
            "details": ffmpeg.get("hint") or ffmpeg.get("nvenc_error") or "",
        },
        "nvdec": {
            "detected": bool(ffmpeg.get("nvdec_advertised")),
            "runtime_ready": bool(ffmpeg.get("nvdec_advertised")),
            "effective": str(recommended.get("hardware_decode") or "off"),
            "details": "Capability only; Visual Scan benchmarks GPU decode before using it.",
        },
    }


def hardware_summary(capabilities: dict[str, Any]) -> str:
    cpu = capabilities.get("cpu") or {}
    memory = capabilities.get("memory") or {}
    nvidia = capabilities.get("nvidia") or {}
    gpu = (nvidia.get("gpus") or [{}])[0] if nvidia.get("gpus") else {}
    ctranslate2 = capabilities.get("ctranslate2") or {}
    ffmpeg = capabilities.get("ffmpeg") or {}
    parts = [f"{cpu.get('name', 'CPU')} · {cpu.get('logical_threads', '?')} потоков", f"RAM {memory.get('total_gb', '?')} GB"]
    if gpu.get("name"):
        parts.append(f"{gpu.get('name')} · {int(gpu.get('memory_total_mb') or 0) // 1024 or '?'} GB VRAM")
    else:
        parts.append("NVIDIA GPU не обнаружена")
    parts.append("CTranslate2 CUDA ✓" if ctranslate2.get("cuda_ok") else "CTranslate2 CUDA — CPU fallback")
    parts.append("NVENC ✓" if ffmpeg.get("nvenc_runtime_ok") else "NVENC — libx264")
    return " · ".join(parts)


def write_capabilities(path: Path, *, force: bool = False) -> dict[str, Any]:
    data = detect_hardware_capabilities(force=force)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
