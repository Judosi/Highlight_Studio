from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..core.artifacts import source_readiness, validate_media_file
from ..core.revisions import freshness_report
from ..core.utils import read_json, which
from .pipeline import pre_render_check, source_video_path


def authoritative_source_gate(project_dir: Path, *, deep_media_check: bool = False) -> dict[str, Any]:
    video = source_video_path(project_dir)
    cheap = source_readiness(video)
    if not cheap.get("ok"):
        return {"ok": False, "code": "source_missing", "message": cheap.get("message"), "source": cheap}
    if deep_media_check:
        media = validate_media_file(video)
        if not media.get("ok"):
            return {"ok": False, "code": "source_invalid", "message": media.get("message"), "source": media}
        return {"ok": True, "source": media}
    return {"ok": True, "source": cheap}


def authoritative_render_gate(project_dir: Path, settings: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    source = authoritative_source_gate(project_dir, deep_media_check=True)
    checks.append({"id": "source", **source})
    fresh = freshness_report(project_dir, settings)
    checks.append({"id": "segments_revision", "ok": bool(fresh.get("segments_current")), "message": "Монтаж соответствует текущему анализу." if fresh.get("segments_current") else "Монтаж устарел относительно текущего анализа/настроек."})
    ffmpeg = which("ffmpeg")
    ffprobe = which("ffprobe")
    checks.append({"id": "ffmpeg", "ok": bool(ffmpeg), "message": "FFmpeg доступен." if ffmpeg else "FFmpeg не найден."})
    checks.append({"id": "ffprobe", "ok": bool(ffprobe), "message": "FFprobe доступен." if ffprobe else "FFprobe не найден."})
    segments = read_json(project_dir / "segments.json", []) or []
    checks.append({"id": "segments", "ok": bool(segments), "message": f"Фрагментов: {len(segments)}" if segments else "Нет фрагментов для рендера."})
    try:
        pre = pre_render_check(project_dir, settings)
    except Exception as exc:
        pre = {"ok": False, "message": f"Pre-render check failed: {exc}"}
    checks.append({"id": "pre_render", "ok": bool(pre.get("ok")), "message": pre.get("message") or "Техническая проверка рендера.", "details": pre})
    try:
        free = shutil.disk_usage(project_dir).free
        video_size = source_video_path(project_dir).stat().st_size
        needed = max(512 * 1024 * 1024, int(video_size * 0.20))
        disk_ok = free > needed
    except Exception:
        free = 0
        needed = 0
        disk_ok = False
    checks.append({"id": "disk", "ok": disk_ok, "message": "Свободного места достаточно." if disk_ok else "Недостаточно свободного места для безопасного рендера.", "free_bytes": free, "required_bytes": needed})
    failed = [x for x in checks if not x.get("ok")]
    return {"ok": not failed, "checks": checks, "failed": failed, "freshness": fresh}
