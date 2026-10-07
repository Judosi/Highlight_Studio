from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

from .utils import read_json, which

ARTIFACTS: dict[str, str] = {
    "project": "project.json", "status": "status.json", "transcript": "transcript.json",
    "transcript_manifest": "transcript_manifest.json", "transcript_chunks": "transcript_chunks",
    "candidates": "candidates.json", "segments": "segments.json", "visual": "visual_scan_report.json",
    "ocr": "ocr_scan_report.json", "ocr_preprocessed": "ocr_preprocessed",
    "audio_dynamics": "audio_dynamics.json", "audio_events": "audio_events.json",
    "scene": "scene_times.json", "scene_manifest": "scene_times_manifest.json", "ai_batches": "ai_batches", "micro_batches": "micro_batches",
    "render_parts": "render_parts", "final_render": "outputs/highlight_final.mp4",
    "result_check": "last_result_check.json", "revision_state": "revision_state.json",
    "render_manifest": "render_manifest.json", "shorts_manifest": "shorts_manifest.json",
    "youtube_upload_state": "youtube_upload_state.json",
}

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ts"}


def _safe_project_relative(project_dir: Path, raw: Any) -> Path | None:
    value = str(raw or "").strip().replace("\\", "/")
    if not value:
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    root = project_dir.resolve(strict=False)
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def portable_source_fields(project_dir: Path, source: Path) -> dict[str, Any]:
    resolved = Path(source).expanduser().resolve(strict=False)
    fields: dict[str, Any] = {"source_video_path": str(resolved)}
    try:
        fields["source_video_relative_path"] = resolved.relative_to(project_dir.resolve(strict=False)).as_posix()
    except ValueError:
        fields["source_video_relative_path"] = ""
    fields["source_video_filename_hint"] = resolved.name
    return fields


def resolve_project_source(project_dir: Path) -> Path:
    root = Path(project_dir).resolve(strict=False)
    project = read_json(root / ARTIFACTS["project"], {}) or {}
    raw = str(project.get("source_video_path") or "").strip()
    candidates: list[Path] = []
    # Prefer the portable in-project path even when the old absolute path still
    # exists (for example after copying a project instead of moving it). This
    # keeps a copied project self-contained and prevents it from silently reading
    # media from the original project directory.
    relative = _safe_project_relative(root, project.get("source_video_relative_path"))
    if relative is not None:
        candidates.append(relative)
    if raw:
        source = Path(raw).expanduser()
        if source.is_absolute():
            candidates.append(source)
        else:
            safe_raw = _safe_project_relative(root, raw)
            if safe_raw is not None:
                candidates.append(safe_raw)
    filename = str(project.get("source_video_filename_hint") or project.get("original_filename") or (Path(raw).name if raw else "")).strip()
    if filename and Path(filename).name == filename:
        candidates.extend((root / filename, root / "twitch_cache" / filename))
    for suffix in sorted(VIDEO_SUFFIXES):
        candidates.append(root / f"input{suffix}")
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve(strict=False)
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            if resolved.is_file() and resolved.stat().st_size > 0 and resolved.suffix.lower() in VIDEO_SUFFIXES:
                return resolved
        except OSError:
            continue
    twitch_cache = root / "twitch_cache"
    if twitch_cache.is_dir():
        try:
            cached = [item for item in twitch_cache.rglob("*") if item.is_file() and item.suffix.lower() in VIDEO_SUFFIXES and item.stat().st_size > 1024]
            if cached:
                return max(cached, key=lambda item: item.stat().st_size).resolve(strict=False)
        except OSError:
            pass
    if raw:
        source = Path(raw).expanduser()
        if source.is_absolute():
            return source.resolve(strict=False)
        safe_raw = _safe_project_relative(root, raw)
        if safe_raw is not None:
            return safe_raw
    return root / "input.mp4"


def artifact_path(project_dir: Path, key: str) -> Path:
    return project_dir / ARTIFACTS[key]


SOURCE_SIGNATURE_VERSION = 2
SOURCE_FULL_HASH_LIMIT = 128 * 1024 * 1024
SOURCE_SAMPLE_COUNT = 64
SOURCE_SAMPLE_BYTES = 64 * 1024


def _partial_hash(path: Path, sample_bytes: int = SOURCE_SAMPLE_BYTES) -> str:
    """Strong-enough quick identity without re-reading multi-GB VODs on every poll.

    Small/medium sources are hashed completely. Large VODs use many evenly
    distributed samples instead of only first/middle/last, which prevents the
    confirmed blind spot where an internal edit preserved size+mtime.
    """
    st = path.stat()
    size = int(st.st_size)
    h = hashlib.sha256()
    h.update(f"v{SOURCE_SIGNATURE_VERSION}:{size}".encode("ascii"))
    if size <= SOURCE_FULL_HASH_LIMIT:
        with path.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        return h.hexdigest()
    width = max(4096, min(int(sample_bytes or SOURCE_SAMPLE_BYTES), 256 * 1024))
    max_offset = max(0, size - width)
    count = max(8, SOURCE_SAMPLE_COUNT)
    offsets = sorted({int(round(max_offset * i / (count - 1))) for i in range(count)})
    with path.open("rb") as f:
        for offset in offsets:
            h.update(offset.to_bytes(8, "big", signed=False))
            f.seek(offset)
            h.update(f.read(width))
    return h.hexdigest()


def source_file_signature(path: Path) -> dict[str, Any]:
    try:
        resolved = path.expanduser().resolve()
        st = resolved.stat()
        if not resolved.is_file():
            return {"path": str(resolved), "exists": True, "is_file": False}
        return {
            "path": str(resolved), "name": resolved.name, "exists": True, "is_file": True,
            "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns),
            "ctime_ns": int(getattr(st, "st_ctime_ns", 0) or 0),
            "signature_version": SOURCE_SIGNATURE_VERSION,
            "partial_sha256": _partial_hash(resolved),
        }
    except Exception as exc:
        return {"path": str(path), "exists": False, "is_file": False, "error": str(exc)[:300]}


def validate_media_file(path: Path, *, require_video: bool = True, timeout: int = 30) -> dict[str, Any]:
    try:
        resolved = path.expanduser().resolve()
    except Exception:
        resolved = path
    if not resolved.exists() or not resolved.is_file():
        return {"ok": False, "code": "missing", "message": "Исходный файл не найден.", "path": str(resolved)}
    if resolved.stat().st_size <= 0:
        return {"ok": False, "code": "empty", "message": "Исходный файл пустой (0 bytes).", "path": str(resolved)}
    if resolved.suffix.lower() not in VIDEO_SUFFIXES:
        return {"ok": False, "code": "extension", "message": "Формат видео не поддерживается.", "path": str(resolved)}
    ffprobe = which("ffprobe") or "ffprobe"
    try:
        proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(resolved)],
                              capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"ok": False, "code": "ffprobe_missing", "message": "FFprobe не найден. Невозможно проверить исходное видео.", "path": str(resolved)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "ffprobe_timeout", "message": "FFprobe не успел проверить исходное видео.", "path": str(resolved)}
    if proc.returncode != 0:
        return {"ok": False, "code": "ffprobe_failed", "message": "Файл не удалось прочитать как видео.",
                "detail": (proc.stderr or proc.stdout or "")[-1200:], "path": str(resolved)}
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "code": "ffprobe_json", "message": "FFprobe вернул некорректные метаданные.", "path": str(resolved)}
    streams = payload.get("streams") or []
    has_video = any(str(s.get("codec_type")) == "video" for s in streams if isinstance(s, dict))
    has_audio = any(str(s.get("codec_type")) == "audio" for s in streams if isinstance(s, dict))
    try:
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if require_video and not has_video:
        return {"ok": False, "code": "no_video", "message": "В файле нет видеопотока.", "path": str(resolved)}
    if not math.isfinite(duration) or duration <= 0:
        return {"ok": False, "code": "invalid_duration", "message": "У видео некорректная длительность.", "path": str(resolved)}
    return {"ok": True, "path": str(resolved), "size_bytes": resolved.stat().st_size,
            "duration_seconds": duration, "has_video": has_video, "has_audio": has_audio}


def source_readiness(path: Path) -> dict[str, Any]:
    """Cheap authoritative source readiness check.

    ``path`` may be either a media file or a project directory. Supporting the
    project directory keeps every caller on one contract and correctly handles
    Fast Import sources stored outside the project tree.
    """
    candidate = Path(path)
    if candidate.exists() and candidate.is_dir():
        candidate = resolve_project_source(candidate)
    try:
        resolved = candidate.expanduser().resolve()
    except Exception:
        resolved = candidate
    exists = resolved.exists() and resolved.is_file()
    size = resolved.stat().st_size if exists else 0
    supported = resolved.suffix.lower() in VIDEO_SUFFIXES if exists else False
    ok = bool(exists and size > 0 and supported)
    return {"ok": ok, "ready": ok, "exists": bool(exists), "is_file": bool(exists), "size_bytes": int(size),
            "supported_extension": bool(supported), "path": str(resolved),
            "message": "Источник готов." if ok else "Исходный файл проекта больше недоступен или некорректен."}

RECOMPUTABLE_CACHE_PATHS: tuple[str, ...] = (
    ARTIFACTS["transcript_chunks"], ARTIFACTS["transcript_manifest"], ARTIFACTS["visual"], ARTIFACTS["ocr"],
    ARTIFACTS["ocr_preprocessed"], ARTIFACTS["audio_dynamics"], ARTIFACTS["audio_events"], ARTIFACTS["scene"], ARTIFACTS["scene_manifest"],
    ARTIFACTS["ai_batches"], ARTIFACTS["micro_batches"], ARTIFACTS["render_parts"], "frames", "preview", "hls",
    "audio_16k.wav", "audio.wav", "block_candidates.json", "micro_candidates.json", "visual_quality_report.json",
    "quality_core_report.json", "pre_render_check.json", "adaptive_recommendation.json", "cache_manifest.json",
)
