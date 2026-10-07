from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core.settings import APP_VERSION, DATA_DIR, PROJECTS_DIR
from ..core.artifacts import portable_source_fields, resolve_project_source
from ..core.utils import read_json, write_json

CURRENT_PROJECT_SCHEMA_VERSION = 4
MIGRATION_STATUS_PATH = DATA_DIR / "migration_status.json"


def _migration_entry(from_version: int, to_version: int) -> dict[str, Any]:
    return {
        "from": from_version,
        "to": to_version,
        "app_version": APP_VERSION,
        "migrated_at": time.time(),
    }


def migrate_project(project_dir: Path) -> dict[str, Any]:
    project_path = project_dir / "project.json"
    raw = read_json(project_path, None)
    if not isinstance(raw, dict):
        return {"project_id": project_dir.name, "ok": False, "changed": False, "error": "project.json is missing or invalid"}

    original_version = int(raw.get("project_schema_version") or 0)
    if original_version > CURRENT_PROJECT_SCHEMA_VERSION:
        return {
            "project_id": str(raw.get("id") or project_dir.name),
            "ok": False,
            "changed": False,
            "error": f"Project schema {original_version} is newer than supported {CURRENT_PROJECT_SCHEMA_VERSION}",
        }

    changed = False
    version = original_version
    history = raw.get("migration_history")
    if not isinstance(history, list):
        history = []
        raw["migration_history"] = history
        changed = True

    if version < 1:
        raw.setdefault("id", project_dir.name)
        raw.setdefault("name", project_dir.name)
        if not isinstance(raw.get("settings"), dict):
            raw["settings"] = {}
        raw.setdefault("created_at", time.time())
        raw["updated_at"] = float(raw.get("updated_at") or time.time())
        history.append(_migration_entry(version, 1))
        version = 1
        changed = True

    if version < 2:
        source_path = raw.get("source_video_path")
        if source_path is not None and not isinstance(source_path, str):
            raw["source_video_path"] = str(source_path)
        raw.setdefault("source_type", "local" if raw.get("source_video_path") else raw.get("source_type", "local"))
        raw.setdefault("runtime", {})
        if not isinstance(raw.get("runtime"), dict):
            raw["runtime"] = {}
        history.append(_migration_entry(version, 2))
        version = 2
        changed = True

    if version < 3:
        resolved = resolve_project_source(project_dir)
        if resolved.exists() and resolved.is_file():
            previous = str(raw.get("source_video_path") or "")
            raw.update(portable_source_fields(project_dir, resolved))
            if previous and previous != str(resolved):
                raw["source_relocated_from"] = previous
                raw["source_relocated_at"] = time.time()
        raw.setdefault("source_video_relative_path", "")
        raw.setdefault("source_video_filename_hint", str(raw.get("original_filename") or ""))
        raw.setdefault("pipeline_state_schema_version", 1)
        history.append(_migration_entry(version, 3))
        version = 3
        changed = True


    if version < 4:
        settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
        settings.setdefault("shorts_reframe_mode", "auto")
        settings.setdefault("shorts_emotion_events_enabled", True)
        settings.setdefault("shorts_sensevoice_model", "iic/SenseVoiceSmall")
        settings.setdefault("shorts_emotion_top_n", 20)
        settings.setdefault("shorts_face_sample_fps", 1.0)
        settings.setdefault("shorts_caption_font_size", 72)
        settings.setdefault("shorts_caption_quality", "high")
        raw["settings"] = settings
        raw["pipeline_state_schema_version"] = 2
        history.append(_migration_entry(version, 4))
        version = 4
        changed = True

    if raw.get("project_schema_version") != CURRENT_PROJECT_SCHEMA_VERSION:
        raw["project_schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
        changed = True
    if raw.get("app_version") != APP_VERSION:
        raw["app_version"] = APP_VERSION
        changed = True
    if raw.get("settings_version") != APP_VERSION:
        raw["settings_version"] = APP_VERSION
        changed = True

    if changed:
        raw["updated_at"] = time.time()
        write_json(project_path, raw)

    return {
        "project_id": str(raw.get("id") or project_dir.name),
        "ok": True,
        "changed": changed,
        "from_version": original_version,
        "to_version": CURRENT_PROJECT_SCHEMA_VERSION,
    }


def migrate_all_projects(projects_dir: Path = PROJECTS_DIR) -> dict[str, Any]:
    started_at = time.time()
    results: list[dict[str, Any]] = []
    if projects_dir.exists():
        for item in sorted(projects_dir.iterdir(), key=lambda p: p.name.lower()):
            if item.is_dir() and (item / "project.json").exists():
                try:
                    results.append(migrate_project(item))
                except Exception as exc:  # pragma: no cover - defensive startup guard
                    results.append({"project_id": item.name, "ok": False, "changed": False, "error": str(exc)[:500]})

    report = {
        "ok": all(item.get("ok") for item in results),
        "current_project_schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "project_count": len(results),
        "migrated_count": sum(1 for item in results if item.get("changed")),
        "failed_count": sum(1 for item in results if not item.get("ok")),
        "results": results,
        "started_at": started_at,
        "finished_at": time.time(),
        "app_version": APP_VERSION,
    }
    write_json(MIGRATION_STATUS_PATH, report)
    return report


def migration_status() -> dict[str, Any]:
    return read_json(
        MIGRATION_STATUS_PATH,
        {
            "ok": True,
            "current_project_schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "project_count": 0,
            "migrated_count": 0,
            "failed_count": 0,
            "results": [],
            "app_version": APP_VERSION,
        },
    )
