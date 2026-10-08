from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.artifacts import source_readiness, validate_media_file
from ..core.revisions import freshness_report
from ..core.utils import read_json, which
from ..core.disk_budget import estimate_render_disk_budget
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
        video = source_video_path(project_dir)
        source_duration = float((source.get("source") or {}).get("duration_seconds") or 0.0)
        if source_duration <= 0:
            from ..core.utils import video_duration

            source_duration = video_duration(video)
        selected_duration = sum(
            max(0.0, float(item.get("end") or 0.0) - float(item.get("start") or 0.0))
            for item in segments
            if isinstance(item, dict)
        )
        disk = estimate_render_disk_budget(
            project_dir,
            selected_duration_seconds=selected_duration,
            source_size_bytes=video.stat().st_size,
            source_duration_seconds=source_duration,
        )
        free = disk["free_bytes"]
        needed = disk["required_bytes"]
        disk_ok = bool(disk["ok"])
        disk_message = disk["message"]
        disk_filesystems = disk["filesystems"]
    except Exception as exc:
        free = needed = 0
        disk_ok = False
        disk_message = f"Не удалось проверить место для рендера: {exc}"
        disk_filesystems = []
    checks.append(
        {
            "id": "disk",
            "ok": disk_ok,
            "message": disk_message,
            "free_bytes": free,
            "required_bytes": needed,
            "filesystems": disk_filesystems,
        }
    )
    failed = [x for x in checks if not x.get("ok")]
    return {"ok": not failed, "checks": checks, "failed": failed, "freshness": fresh}
