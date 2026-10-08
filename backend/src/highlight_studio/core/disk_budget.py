from __future__ import annotations

import math
import os
import re
import shutil
from pathlib import Path
from typing import Any


MIB = 1024**2
PCM_WAV_HEADER_BYTES = 44
PCM_SAMPLE_RATE = 16_000
PCM_CHANNELS = 1
PCM_BITS_PER_SAMPLE = 16
DEFAULT_SOURCE_BITRATE_BPS = 8_000_000
MIN_RENDER_BITRATE_BPS = 1_000_000
VISUAL_SCAN_FRAME_BUDGET_BYTES = 512 * 1024
MIN_FILESYSTEM_RESERVE_BYTES = 256 * MIB
SAFETY_MARGIN_RATIO = 0.10


class DiskBudgetError(RuntimeError):
    pass


def pcm_wav_bytes(
    duration_seconds: float,
    sample_rate: int = PCM_SAMPLE_RATE,
    channels: int = PCM_CHANNELS,
    bits_per_sample: int = PCM_BITS_PER_SAMPLE,
) -> int:
    """Return the deterministic size of the PCM WAV format produced by FFmpeg."""
    duration = max(0.0, float(duration_seconds or 0.0))
    bytes_per_sample = max(1, int(bits_per_sample) // 8)
    payload = math.ceil(duration * max(1, int(sample_rate)) * max(1, int(channels)) * bytes_per_sample)
    return payload + PCM_WAV_HEADER_BYTES


def estimate_source_download_bytes(duration_seconds: float, bitrate_bps: int = DEFAULT_SOURCE_BITRATE_BPS) -> int:
    """Estimate bytes that a pending compressed source will actually add to disk."""
    duration = max(0.0, float(duration_seconds or 0.0))
    return math.ceil(duration * max(1, int(bitrate_bps)) / 8)


def _existing_ancestor(path: Path) -> Path:
    candidate = Path(path).expanduser()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def filesystem_identity(path: Path) -> str:
    """Identify the actual volume that will receive a file, including Windows drives."""
    existing = _existing_ancestor(Path(path))
    stat = existing.stat()
    drive = Path(existing).anchor.casefold() if os.name == "nt" else ""
    return f"{drive}:{stat.st_dev}"


def disk_free_bytes(path: Path) -> int:
    return int(shutil.disk_usage(_existing_ancestor(Path(path))).free)


def _format_bytes(value: int) -> str:
    gib = max(0, int(value)) / (1024**3)
    return f"{gib:.2f} GiB"


def _evaluate_components(kind: str, components: list[dict[str, Any]], summary: dict[str, int]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    for component in components:
        size = max(0, int(component.get("bytes") or 0))
        if size <= 0:
            continue
        path = Path(component["path"])
        volume = filesystem_identity(path)
        row = grouped.setdefault(
            volume,
            {
                "filesystem_id": volume,
                "path": str(path),
                "workload_bytes": 0,
                "components": [],
            },
        )
        row["workload_bytes"] += size
        row["components"].append({"name": component["name"], "bytes": size, "path": str(path)})

    filesystems: list[dict[str, Any]] = []
    for row in grouped.values():
        workload = int(row["workload_bytes"])
        margin = max(MIN_FILESYSTEM_RESERVE_BYTES, math.ceil(workload * SAFETY_MARGIN_RATIO))
        required = workload + margin
        free = disk_free_bytes(Path(row["path"]))
        row.update(
            {
                "safety_margin_bytes": margin,
                "required_bytes": required,
                "free_bytes": free,
                "ok": free >= required,
            }
        )
        filesystems.append(row)

    filesystems.sort(key=lambda item: item["filesystem_id"])
    failed = [item for item in filesystems if not item["ok"]]
    required_total = sum(int(item["required_bytes"]) for item in filesystems)
    free_total = sum(int(item["free_bytes"]) for item in filesystems)
    if failed:
        details = "; ".join(
            f"путь {item['path']}: свободно {_format_bytes(item['free_bytes'])}, "
            f"примерно требуется {_format_bytes(item['required_bytes'])}"
            for item in failed
        )
        message = f"Недостаточно места на диске ({details}). Освободите место или перенесите проект/output на другой диск."
    else:
        details = "; ".join(
            f"{item['path']}: свободно {_format_bytes(item['free_bytes'])}, требуется {_format_bytes(item['required_bytes'])}"
            for item in filesystems
        )
        message = f"Дискового места достаточно ({details})." if details else "Крупные временные файлы не требуются."
    return {
        "kind": kind,
        "ok": not failed,
        "required_bytes": required_total,
        "free_bytes": free_total,
        "components": summary,
        "filesystems": filesystems,
        "message": message,
    }


def estimate_analysis_disk_budget(
    project_dir: Path,
    *,
    duration_seconds: float,
    source_path: Path | None = None,
    pending_source_bytes: int = 0,
    chunk_seconds: int = 900,
    visual_scan_samples: int = 0,
    audio_dynamics_enabled: bool = True,
    temp_dir: Path | None = None,
) -> dict[str, Any]:
    """Estimate additional bytes needed to download and analyze one source."""
    project_dir = Path(project_dir)
    source_path = Path(source_path) if source_path is not None else project_dir / "input.mp4"
    temp_dir = Path(temp_dir) if temp_dir is not None else project_dir
    source_requirement = 0 if source_path.is_file() else max(0, int(pending_source_bytes or 0))
    full_pcm = pcm_wav_bytes(duration_seconds)
    chunk_pcm = pcm_wav_bytes(min(max(0.0, float(duration_seconds or 0.0)), max(1, int(chunk_seconds))))
    peak_audio = full_pcm if audio_dynamics_enabled else chunk_pcm
    visual_bytes = max(0, int(visual_scan_samples or 0)) * VISUAL_SCAN_FRAME_BUDGET_BYTES
    summary = {
        "source_requirement_bytes": source_requirement,
        "peak_audio_temp_bytes": peak_audio,
        "transcription_chunk_temp_bytes": chunk_pcm,
        "visual_scan_temp_bytes": visual_bytes,
        "render_export_temp_bytes": 0,
    }
    components = [
        {"name": "pending_source", "bytes": source_requirement, "path": source_path.parent},
        {"name": "peak_audio_temp", "bytes": peak_audio, "path": temp_dir},
        {"name": "visual_scan_frames", "bytes": visual_bytes, "path": project_dir / "visual_scan_frames"},
    ]
    return _evaluate_components("analysis", components, summary)


def estimate_render_disk_budget(
    project_dir: Path,
    *,
    selected_duration_seconds: float,
    source_size_bytes: int,
    source_duration_seconds: float,
    temp_dir: Path | None = None,
    output_path: Path | None = None,
    authoritative: bool = True,
) -> dict[str, Any]:
    """Estimate the actual render parts, pending final and canonical root copy."""
    project_dir = Path(project_dir)
    temp_dir = Path(temp_dir) if temp_dir is not None else project_dir / "render_parts"
    output_path = Path(output_path) if output_path is not None else project_dir / "outputs" / "highlight_final.mp4"
    if not output_path.is_absolute():
        output_path = project_dir / output_path
    duration = max(0.0, float(selected_duration_seconds or 0.0))
    source_duration = max(0.001, float(source_duration_seconds or 0.0))
    source_bytes_per_second = max(0.0, int(source_size_bytes or 0) / source_duration)
    encoded_bytes_per_second = max(source_bytes_per_second, MIN_RENDER_BITRATE_BPS / 8)
    estimated_output = math.ceil(duration * encoded_bytes_per_second * 1.15)
    root_copy = estimated_output if authoritative else 0
    summary = {
        "source_requirement_bytes": 0,
        "peak_audio_temp_bytes": 0,
        "render_parts_bytes": estimated_output,
        "pending_final_bytes": estimated_output,
        "root_final_copy_bytes": root_copy,
        "render_export_temp_bytes": estimated_output * 2 + root_copy,
    }
    components = [
        {"name": "render_parts", "bytes": estimated_output, "path": temp_dir},
        {"name": "pending_final", "bytes": estimated_output, "path": output_path.parent},
        {"name": "root_final_copy", "bytes": root_copy, "path": project_dir},
    ]
    return _evaluate_components("render", components, summary)


_CHUNK_WAV_RE = re.compile(r"^chunk_\d+\.wav$", re.IGNORECASE)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def cleanup_project_audio_temps(project_dir: Path, *, include_full: bool = True) -> list[str]:
    """Delete only known recomputable audio temps contained by one project."""
    root = Path(project_dir).resolve(strict=False)
    candidates: list[Path] = []
    if include_full:
        candidates.append(Path(project_dir) / "audio_16k.wav")
    chunks_root = Path(project_dir) / "transcript_chunks"
    if chunks_root.is_dir():
        candidates.extend(path for path in chunks_root.rglob("*.wav") if _CHUNK_WAV_RE.fullmatch(path.name))

    removed: list[str] = []
    for candidate in candidates:
        if candidate.is_symlink() or not _is_within(candidate, root):
            continue
        try:
            if candidate.is_file():
                candidate.unlink()
                removed.append(str(candidate))
        except OSError:
            continue
    return removed
