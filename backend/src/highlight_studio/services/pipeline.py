from __future__ import annotations

import json
import hashlib
import wave
import audioop
import math
import os
import re
import shutil
import subprocess
import time
import threading
import sys
import uuid
import difflib
from dataclasses import asdict
from pathlib import Path
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from ..integrations.ai.client import make_ai_client, effective_text_model, effective_vision_model
from ..integrations.ai.runtime import AIResourceManager, AITransportError
from ..infrastructure.project_locks import project_metadata_lock
from .hardware import detect_hardware_capabilities, prepare_nvidia_dll_paths, system_resource_snapshot
from .media_models import Candidate, TranscriptSegment
from .ai_policy import (
    ai_batch_completeness_required,
    ai_prompt_char_budget,
    ai_retry_count,
    extract_ai_items,
    normalize_ai_items_by_id,
    plan_prompt_safe_batches,
    require_complete_ai_result,
    scored_ai_items_complete,
    strict_ai_enabled,
    strict_missing_message,
)
from ..core.utils import (
    audio_streams,
    extract_frames_bulk,
    benchmark_visual_decode,
    extract_frame,
    is_transient_file_lock_error,
    overlaps,
    read_json,
    run_cmd,
    cancel_running_processes,
    tc,
    video_duration,
    video_info,
    which,
    write_json,
    OperationCancelled,
    replace_file_atomic,
)

from ..core.artifacts import resolve_project_source, source_file_signature, validate_media_file
from ..core.durable_pipeline import DurablePipelineState
from ..core.settings import APP_SEMVER
from ..core.revisions import (
    analysis_revision,
    freshness_report,
    mark_analysis_complete,
    mark_segments_updated,
    mark_render_complete,
    mark_render_failed,
    mark_shorts_complete,
    render_revision,
    segments_revision,
    segments_revision_from_items,
    source_revision,
)



def candidate_identity(c: Candidate) -> str:
    return str(c.candidate_id or c.id)


NON_PRIMARY_CONTENT_CLASSES = {"waiting", "reconnect", "intermission", "replay", "prerecorded", "advertisement"}
PRIMARY_CONTENT_CLASSES = {"primary_live", "live_reaction", "unknown"}


def effective_max_final_segments(settings: dict[str, Any], target_sec: float) -> int:
    """Return a duration-aware segment ceiling for the final montage.

    ``max_final_segments`` remains the user's minimum capacity preference, but
    a fixed value of 80 cannot hold a 60-minute montage when Micro-cut produces
    clips averaging roughly 20-35 seconds.  The effective ceiling therefore
    grows with the requested duration and the configured micro-window size.
    It is only a ceiling: score, semantic, overlap and duplicate guards still
    decide which clips are eligible, and selection still stops at the target.
    """
    configured = max(1, int(settings.get("max_final_segments", 80) or 80))
    minimum = max(1, int(settings.get("min_final_segments", 8) or 8))
    if not settings.get("micro_cut_enabled", True) or float(target_sec or 0.0) <= 0:
        return min(1000, max(configured, minimum))

    micro_min = max(1.0, float(settings.get("micro_min_seconds", 12) or 12))
    micro_window = max(micro_min, float(settings.get("micro_window_seconds", 45) or 45))
    micro_max = max(micro_min, float(settings.get("micro_max_seconds", 80) or 80))
    # Completed micro-scenes are commonly shorter than their source window.
    # A 60% occupancy assumption matches real Review Studio data while leaving
    # enough headroom for short setup/payoff scenes without forcing any filler.
    expected_clip_seconds = max(micro_min, min(micro_max, micro_window) * 0.60)
    duration_capacity = int(math.ceil(float(target_sec) / max(1.0, expected_clip_seconds)))
    return min(1000, max(configured, minimum, duration_capacity))


def candidate_is_selectable(c: Candidate, settings: dict[str, Any]) -> bool:
    decision = str(c.decision or "").strip().lower()
    if decision in {"remove", "reject", "rejected", "delete", "drop"}:
        return False

    # Quality guard: target duration is a ceiling, not permission to fill the
    # montage with a technical/replay screen.  High-confidence non-primary
    # content is never used for refill/final selection.
    if settings.get("semantic_quality_guard_enabled", True):
        cls = str(c.content_class or "unknown").strip().lower()
        confidence = float(c.content_class_confidence or 0.0)
        reject_conf = float(settings.get("non_primary_reject_confidence", 0.72) or 0.72)
        if cls in NON_PRIMARY_CONTENT_CLASSES and confidence >= reject_conf:
            return False

    min_score = float(settings.get("fill_target_min_score", 5.0) or 5.0)
    if settings.get("quality_first_selection_enabled", True):
        min_score = max(min_score, float(settings.get("quality_first_min_score", 6.7) or 6.7))
        min_conf = float(settings.get("quality_first_min_confidence", 5.6) or 5.6)
        min_clarity = float(settings.get("quality_first_min_clarity", 0.5) or 0.5)
        # Unknown confidence is allowed for older/fallback candidates, but an
        # explicitly weak model verdict must not be used just to reach 30 min.
        if float(c.confidence or 0.0) > 0 and float(c.confidence or 0.0) < min_conf:
            return False
        if float(c.standalone_clarity or 0.0) < min_clarity:
            return False
    return float(c.score or 0) >= min_score


def candidate_is_quality_recovery_selectable(c: Candidate, settings: dict[str, Any]) -> bool:
    """Bounded second-tier refill for review-worthy primary live moments.

    This tier only activates after the normal quality pass leaves a real target
    shortfall. It never revives model rejects, technical/replay classes or low
    clarity clips. The narrow score/confidence relaxation avoids losing a good
    scene to a rounding-scale threshold difference while still refusing filler.
    """
    decision = str(c.decision or "").strip().lower()
    if decision in {"remove", "reject", "rejected", "delete", "drop"}:
        return False
    cls = str(c.content_class or "unknown").strip().lower()
    cls_conf = float(c.content_class_confidence or 0.0)
    if cls not in {"primary_live", "live_reaction"}:
        return False
    if cls_conf < max(0.65, float(settings.get("non_primary_reject_confidence", 0.72) or 0.72)):
        return False

    quality_score = float(settings.get("quality_first_min_score", 6.7) or 6.7)
    quality_confidence = float(settings.get("quality_first_min_confidence", 5.6) or 5.6)
    quality_clarity = float(settings.get("quality_first_min_clarity", 0.5) or 0.5)
    recovery_score = max(
        float(settings.get("fill_target_min_score", 5.0) or 5.0),
        quality_score - float(settings.get("quality_recovery_score_relaxation", 0.5) or 0.5),
    )
    recovery_confidence = max(5.0, quality_confidence - 0.35)
    return (
        float(c.score or 0.0) >= recovery_score
        and (float(c.confidence or 0.0) <= 0 or float(c.confidence or 0.0) >= recovery_confidence)
        and float(c.standalone_clarity or 0.0) >= quality_clarity
    )


def degraded_minimum_selection(
    candidates: list[Candidate],
    settings: dict[str, Any],
    target_sec: float,
    logger: "JobLogger",
) -> list[Candidate]:
    """Last-resort, quality-bounded selector for a degraded normal analysis.

    This is deliberately *not* a target-filling heuristic. It is used only when
    the normal quality selector would otherwise return zero clips after an AI
    runtime outage. Explicit model rejects and high-confidence non-primary
    content remain forbidden. The relaxed floor is narrow enough to salvage a
    reviewable draft from transcript/visual/audio evidence without pretending
    that weak filler is a high-confidence AI highlight.
    """
    if not candidates or strict_ai_enabled(settings):
        return []

    max_final = effective_max_final_segments(settings, target_sec)
    quality_floor = float(settings.get("quality_first_min_score", 6.7) or 6.7)
    floor = max(5.8, min(6.25, quality_floor - 0.35))
    min_clarity = max(0.40, min(0.48, float(settings.get("quality_first_min_clarity", 0.5) or 0.5) - 0.05))
    reject_conf = float(settings.get("non_primary_reject_confidence", 0.72) or 0.72)
    max_dur = float(settings.get("micro_max_seconds", 75) or 75) * 1.5 if settings.get("micro_cut_enabled", True) else 300.0

    ranked = sorted(candidates, key=lambda x: candidate_selection_key(x, settings), reverse=True)
    chosen: list[Candidate] = []
    total = 0.0
    for c in ranked:
        if len(chosen) >= max_final or total >= target_sec:
            break
        decision = str(c.decision or "").strip().lower()
        if decision in {"remove", "reject", "rejected", "delete", "drop"}:
            continue
        cls = str(c.content_class or "unknown").strip().lower()
        if cls in NON_PRIMARY_CONTENT_CLASSES and float(c.content_class_confidence or 0.0) >= reject_conf:
            continue
        dur = float(c.end) - float(c.start)
        if dur <= 0 or dur > max_dur:
            continue
        if float(c.score or 0.0) < floor:
            continue
        if float(c.standalone_clarity or 0.0) < min_clarity:
            continue
        # Avoid direct timeline overlaps before the normal dedup/overlap passes.
        if any(max(float(c.start), float(x.start)) < min(float(c.end), float(x.end)) for x in chosen):
            continue
        c.reason = (str(c.reason or "") + " / degraded_minimum_selection").strip(" /")
        chosen.append(c)
        total += dur

    if chosen:
        logger.log(
            f"Degraded minimum montage recovery: выбрано {len(chosen)} безопасных фрагментов "
            f"({round(total / 60.0, 1)} мин), score floor={floor:.2f}."
        )
    return chosen


class JobLogger:
    """Project logger + real progress reporter.

    v9.1.2: status.json is no longer only a static percentage.  Whenever a
    stage has a real counter (chunks, AI batches, OCR frames, render parts),
    set_step() writes a stage_progress_percent, ETA for this stage, elapsed
    time, items/minute and an overall ETA.  The frontend shows both the global
    bar and the current-stage bar.
    """

    def __init__(self, project_dir: Path, reset_status: bool = True):
        self.project_dir = project_dir
        self.log_path = project_dir / "logs.txt"
        self.status_path = project_dir / "status.json"
        self.history_path = project_dir / "status_history.json"
        self.started_at = time.time()
        self._stage_started: dict[str, float] = {}
        self._last_progress = 0.0
        self._last_history_write = 0.0
        self._last_status_lock_warning = 0.0
        self.telemetry_path = project_dir / "performance_telemetry.json"
        self._telemetry: dict[str, Any] = {
            "version": APP_SEMVER,
            "started_at": self.started_at,
            "updated_at": self.started_at,
            "state": "running",
            "stages": {},
        }
        self._active_telemetry_stage: str | None = None
        self._last_telemetry_write = 0.0
        self._resource_agg: dict[str, dict[str, float]] = {}
        self._resource_lock = threading.Lock()
        self._resource_stop = threading.Event()
        self._resource_sampler_started = False
        self.pipeline_state = DurablePipelineState(project_dir)
        self._active_pipeline_stage: str | None = None
        # v10.0.4: background jobs create their logger after the UI has already
        # shown an optimistic/queued state.  The old constructor always wrote
        # "ready 0%" for a split second; if the frontend refreshed at that exact
        # moment, the progress bar disappeared until the next poll.  Keep the
        # default for direct project-created calls, but allow job runners to
        # opt out and preserve the queued/running state.
        if reset_status:
            self.set_status("ready", 0, "Готово", progress_source="ready")

    def log(self, text: str):
        from ..infrastructure.support_bundle import redact_text
        self.project_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(redact_text(text).rstrip() + "\n")

    def _global_eta(self, progress: float) -> float | None:
        progress = max(0.0, min(100.0, float(progress or 0)))
        elapsed = max(0.1, time.time() - self.started_at)
        if progress <= 0 or progress >= 100:
            return None
        remaining = elapsed * (100.0 - progress) / max(0.1, progress)
        return round(max(0.0, remaining), 1)

    def _append_history(self, payload: dict[str, Any]) -> None:
        # Keep a compact rolling timeline for debugging and UI history.  To
        # avoid excessive disk writes, save only every ~1.5s while running,
        # plus terminal states.
        now = time.time()
        if payload.get("state") == "running" and now - self._last_history_write < 1.5:
            return
        self._last_history_write = now
        try:
            history = read_json(self.history_path, []) or []
            if not isinstance(history, list):
                history = []
            history.append(
                {
                    "at": round(now, 3),
                    "state": payload.get("state"),
                    "progress": payload.get("progress"),
                    "stage": payload.get("stage"),
                    "stage_progress_percent": payload.get("stage_progress_percent"),
                    "message": payload.get("message"),
                    "eta_seconds": payload.get("eta_seconds"),
                    "stage_eta_seconds": payload.get("stage_eta_seconds"),
                }
            )
            write_json(self.history_path, history[-300:])
        except Exception:
            pass

    def _ensure_resource_sampler(self) -> None:
        if self._resource_sampler_started:
            return
        self._resource_sampler_started = True
        thread = threading.Thread(target=self._resource_sampler_loop, name="hs-performance-telemetry", daemon=True)
        thread.start()

    def _resource_sampler_loop(self) -> None:
        while not self._resource_stop.is_set():
            stage = self._active_telemetry_stage
            if stage:
                try:
                    self._accumulate_resource_sample(stage, system_resource_snapshot())
                except Exception:
                    pass
            if self._resource_stop.wait(10.0):
                break

    def _accumulate_resource_sample(self, stage: str, snapshot: dict[str, Any]) -> None:
        with self._resource_lock:
            agg = self._resource_agg.setdefault(
                stage,
                {"samples": 0.0, "cpu_sum": 0.0, "cpu_count": 0.0, "cpu_max": 0.0,
                 "ram_sum": 0.0, "ram_count": 0.0, "ram_max": 0.0,
                 "gpu_sum": 0.0, "gpu_count": 0.0, "gpu_max": 0.0,
                 "vram_sum": 0.0, "vram_count": 0.0, "vram_max": 0.0},
            )
            agg["samples"] += 1
            cpu = snapshot.get("cpu_percent")
            ram = (snapshot.get("memory") or {}).get("used_percent")
            gpu = snapshot.get("gpu") or {}
            gpu_pct = gpu.get("utilization_percent") if gpu.get("available") else None
            vram = gpu.get("memory_used_mb") if gpu.get("available") else None
            for value, sum_key, count_key, max_key in (
                (cpu, "cpu_sum", "cpu_count", "cpu_max"),
                (ram, "ram_sum", "ram_count", "ram_max"),
                (gpu_pct, "gpu_sum", "gpu_count", "gpu_max"),
                (vram, "vram_sum", "vram_count", "vram_max"),
            ):
                if value is None:
                    continue
                value = float(value)
                agg[sum_key] += value
                agg[count_key] += 1
                agg[max_key] = max(agg[max_key], value)
            stage_info = self._telemetry.setdefault("stages", {}).setdefault(stage, {"started_at": time.time()})
            stage_info["resources"] = {
                "samples": int(agg["samples"]),
                "cpu_percent_avg": round(agg["cpu_sum"] / agg["cpu_count"], 1) if agg["cpu_count"] else None,
                "cpu_percent_max": round(agg["cpu_max"], 1) if agg["cpu_count"] else None,
                "ram_percent_avg": round(agg["ram_sum"] / agg["ram_count"], 1) if agg["ram_count"] else None,
                "ram_percent_max": round(agg["ram_max"], 1) if agg["ram_count"] else None,
                "gpu_percent_avg": round(agg["gpu_sum"] / agg["gpu_count"], 1) if agg["gpu_count"] else None,
                "gpu_percent_max": round(agg["gpu_max"], 1) if agg["gpu_count"] else None,
                "vram_used_mb_avg": round(agg["vram_sum"] / agg["vram_count"], 1) if agg["vram_count"] else None,
                "vram_used_mb_max": round(agg["vram_max"], 1) if agg["vram_count"] else None,
            }

    def _write_telemetry(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_telemetry_write < 2.0:
            return
        self._last_telemetry_write = now
        self._telemetry["updated_at"] = now
        try:
            write_json(self.telemetry_path, self._telemetry)
        except OSError as exc:
            if not is_transient_file_lock_error(exc):
                raise

    def _record_telemetry_step(
        self,
        stage: str,
        current: int,
        total: int,
        stage_elapsed: float,
        stage_eta: float | None,
        items_per_min: float,
        global_progress: float,
    ) -> None:
        now = time.time()
        self._ensure_resource_sampler()
        if self._active_telemetry_stage and self._active_telemetry_stage != stage:
            previous = self._telemetry["stages"].get(self._active_telemetry_stage, {})
            if previous and not previous.get("completed_at"):
                previous["completed_at"] = now
                previous["duration_seconds"] = round(now - float(previous.get("started_at") or now), 2)
        self._active_telemetry_stage = stage
        stages = self._telemetry.setdefault("stages", {})
        info = stages.setdefault(stage, {"started_at": self._stage_started.get(stage, now)})
        info.update(
            {
                "last_updated_at": now,
                "current": int(current),
                "total": int(total),
                "progress_percent": round(100.0 * current / max(1, total), 2),
                "elapsed_seconds": round(float(stage_elapsed), 2),
                "eta_seconds": stage_eta,
                "items_per_minute": float(items_per_min),
                "global_progress": round(float(global_progress), 2),
            }
        )
        if current >= total:
            info["completed_at"] = now
            info["duration_seconds"] = round(float(stage_elapsed), 2)
        self._write_telemetry(force=current >= total)

    def _finalize_telemetry(self, state: str) -> None:
        now = time.time()
        if self._active_telemetry_stage:
            info = self._telemetry.get("stages", {}).get(self._active_telemetry_stage, {})
            if info and not info.get("completed_at"):
                info["completed_at"] = now
                info["duration_seconds"] = round(now - float(info.get("started_at") or now), 2)
        self._resource_stop.set()
        self._telemetry["state"] = state
        self._telemetry["finished_at"] = now
        self._telemetry["total_elapsed_seconds"] = round(now - self.started_at, 2)
        self._write_telemetry(force=True)

    def set_status(self, state: str, progress: float, message: str, **extra: Any):
        progress = max(0.0, min(100.0, float(progress or 0)))
        # Avoid visual rollback: if a job reports a generic estimated progress
        # lower than the previous value, keep the bar monotonic until done/error.
        if state == "running" and progress < self._last_progress and not extra.get("allow_progress_backwards"):
            progress = self._last_progress
        if state == "done":
            progress = 100.0
        elif state in ("cancelled", "error"):
            # Terminal does not mean completed. Preserve the truthful amount of
            # work already done so status.json, DB job state and UI agree.
            progress = max(progress, self._last_progress)
        self._last_progress = progress

        elapsed = round(time.time() - self.started_at, 1)
        eta = extra.get("eta_seconds")
        if eta is None and "eta_seconds" not in extra:
            eta = self._global_eta(progress)
        payload = {
            "state": state,
            "progress": round(progress, 2),
            "message": message,
            "updated_at": time.time(),
            "started_at": self.started_at,
            "elapsed_seconds": elapsed,
            "eta_seconds": eta,
            "progress_source": extra.pop("progress_source", "estimated_global" if state == "running" else state),
        }
        if eta is not None:
            payload["estimated_finish_at"] = time.time() + float(eta)
        payload.update(extra)
        stage = str(payload.get("stage") or self._active_pipeline_stage or "job")
        self._active_pipeline_stage = stage
        self.pipeline_state.update_stage(
            stage,
            state=state,
            current=payload.get("current_batch"),
            total=payload.get("total_batches"),
            message=message,
            error_code=payload.get("error_code"),
        )
        try:
            write_json(self.status_path, payload)
        except OSError as exc:
            # A progress file is advisory. A short-lived Windows lock from
            # Defender/Explorer must never abort a multi-hour VOD download.
            # ``write_json`` has already retried; keep the job alive and let the
            # next heartbeat persist the latest state. Real non-lock I/O errors
            # still propagate.
            if not is_transient_file_lock_error(exc):
                raise
            now = time.time()
            if now - self._last_status_lock_warning >= 10.0:
                self._last_status_lock_warning = now
                try:
                    self.log(f"[WARN] status.json временно занят Windows; задача продолжена: {exc}")
                except Exception:
                    pass
        self._append_history(payload)
        if state in ("done", "cancelled", "error"):
            self._finalize_telemetry(state)
        self.log(f"[{int(progress):3d}%] {message}")

    def set_step(
        self,
        stage: str,
        current: int,
        total: int,
        progress: float | None,
        message: str,
        *,
        global_start: float | None = None,
        global_end: float | None = None,
    ):
        """Write progress based on a real counter.

        If global_start/global_end are supplied, global progress is computed
        from the stage ratio.  Otherwise the explicit progress argument is used.
        This keeps old callers compatible while allowing new callers to provide
        more accurate weighted progress.
        """
        current = max(0, int(current))
        total = max(1, int(total))
        ratio = min(1.0, max(0.0, current / total))
        now = time.time()
        self._stage_started.setdefault(stage, now)
        stage_elapsed = max(0.1, now - self._stage_started[stage])
        stage_eta = round(stage_elapsed * (1 - ratio) / max(0.01, ratio), 1) if ratio > 0 else None
        items_per_min = round((current / stage_elapsed) * 60.0, 2) if stage_elapsed > 0 and current > 0 else 0.0
        if global_start is not None and global_end is not None:
            global_progress = float(global_start) + (float(global_end) - float(global_start)) * ratio
        else:
            global_progress = float(progress or 0)
        # Overall ETA must never claim the whole job finishes before the current
        # real-counter stage itself can finish. 10.15.6 could show e.g. 16 min
        # overall while OCR alone correctly estimated 36 min.
        global_eta = self._global_eta(global_progress)
        overall_eta = max(float(global_eta or 0), float(stage_eta or 0)) or None
        self._record_telemetry_step(stage, current, total, stage_elapsed, stage_eta, items_per_min, global_progress)
        self.set_status(
            "running",
            global_progress,
            message,
            eta_seconds=overall_eta,
            stage=stage,
            current_batch=current,
            total_batches=total,
            stage_progress_percent=round(100 * ratio, 2),
            batch_progress_percent=round(100 * ratio, 2),
            stage_eta_seconds=stage_eta,
            stage_elapsed_seconds=round(stage_elapsed, 1),
            items_per_minute=items_per_min,
            remaining_items=max(0, total - current),
            progress_source="real_counter",
        )

    def heartbeat(self, stage: str, progress: float, message: str, **extra: Any):
        self.set_status("running", progress, message, stage=stage, progress_source="heartbeat", **extra)

    def checkpoint(self, stage: str, *, state: str = "running", fingerprint: str | None = None,
                   current: int | None = None, total: int | None = None, message: str | None = None,
                   artifacts: list[str] | None = None, error_code: str | None = None,
                   detail: dict[str, Any] | None = None) -> dict[str, Any]:
        self._active_pipeline_stage = stage
        return self.pipeline_state.update_stage(
            stage, state=state, fingerprint=fingerprint, current=current, total=total,
            message=message, artifacts=artifacts, error_code=error_code, detail=detail,
        )


def source_video_path(project_dir: Path) -> Path:
    """Return the real video path.

    v8.7.1 supports large-file Fast Import: a project may reference an
    external source video instead of copying 10-20 GB into the project folder.
    All pipeline stages should use project_paths(project_dir)["video"] so the
    same code works for uploaded, linked and reference projects.
    """
    return resolve_project_source(project_dir)


def project_paths(project_dir: Path) -> dict[str, Path]:
    return {
        "video": source_video_path(project_dir),
        "project_video": project_dir / "input.mp4",
        "transcript": project_dir / "transcript.json",
        "transcript_txt": project_dir / "transcript.txt",
        "candidates": project_dir / "candidates.json",
        "segments": project_dir / "segments.json",
        "quality": project_dir / "quality_report.json",
        "factory": project_dir / "content_factory",
        "frames": project_dir / "frames",
        "outputs": project_dir / "outputs",
    }


SOURCE_DURATION_CACHE_VERSION = "source-duration-v1"


def remember_source_duration(project_dir: Path, duration: float, video: Path | None = None) -> float:
    """Persist a cheap, stat-bound duration cache for interactive edits."""
    try:
        value = float(duration)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(value) or value <= 0:
        return 0.0
    source = video or source_video_path(project_dir)
    payload: dict[str, Any] = {
        "version": SOURCE_DURATION_CACHE_VERSION,
        "duration_seconds": round(value, 6),
        "updated_at": time.time(),
    }
    try:
        stat = source.stat()
        payload.update({
            "source_name": source.name,
            "source_size_bytes": int(stat.st_size),
            "source_mtime_ns": int(stat.st_mtime_ns),
        })
    except OSError:
        pass
    write_json(project_dir / "source_duration_cache.json", payload)
    return value


def cached_source_duration(project_dir: Path, *, allow_probe: bool = True) -> float:
    """Return source duration without probing a multi-hour VOD when possible.

    New analyses write ``source_duration_cache.json``.  For projects created by
    11.2.1, the temporal/visual reports contain the same ffprobe-derived value;
    accepting them lets the very first manual add after upgrade use the fast
    path as well.  A real ffprobe remains the final fallback.
    """
    source = source_video_path(project_dir)
    try:
        stat = source.stat()
    except OSError:
        stat = None

    cache = read_json(project_dir / "source_duration_cache.json", {}) or {}
    if isinstance(cache, dict):
        try:
            value = float(cache.get("duration_seconds") or 0.0)
        except (TypeError, ValueError, OverflowError):
            value = 0.0
        signature_matches = bool(
            stat
            and int(cache.get("source_size_bytes") or -1) == int(stat.st_size)
            and int(cache.get("source_mtime_ns") or -1) == int(stat.st_mtime_ns)
        )
        if math.isfinite(value) and value > 0 and signature_matches:
            return value

    project = read_json(project_dir / "project.json", {}) or {}
    legacy_size_matches = bool(
        stat
        and int(project.get("source_video_size_bytes") or -1) == int(stat.st_size)
    )
    if legacy_size_matches:
        for artifact in ("temporal_quality_report.json", "visual_scan_report.json"):
            report = read_json(project_dir / artifact, {}) or {}
            if not isinstance(report, dict):
                continue
            try:
                value = float(report.get("duration_seconds") or 0.0)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(value) and value > 0:
                return remember_source_duration(project_dir, value, source)

    if allow_probe and stat and source.is_file():
        try:
            return remember_source_duration(project_dir, video_duration(source), source)
        except Exception:
            pass
    return 0.0


# ---------------- v8.6 Stability & Workflow helpers ----------------


CancelledError = OperationCancelled


def cancel_path(project_dir: Path) -> Path:
    return project_dir / "cancel.flag"


def request_cancel(project_dir: Path) -> dict[str, Any]:
    cancel_path(project_dir).write_text(str(time.time()), encoding="utf-8")
    killed = cancel_running_processes(project_dir)
    previous = read_json(project_dir / "status.json", {}) or {}
    progress = max(0.0, min(99.0, float(previous.get("progress", 0) or 0)))
    write_json(project_dir / "status.json", {**previous, "state": "cancel_requested", "progress": progress,
        "message": f"Остановка запрошена. Завершается текущая операция. Активных subprocess остановлено: {killed}.",
        "stage": "cancelling", "updated_at": time.time()})
    return {"ok": True, "cancel_requested": True, "subprocesses_killed": killed, "progress": progress}


def clear_cancel(project_dir: Path):
    try:
        cancel_path(project_dir).unlink()
    except FileNotFoundError:
        pass


def is_cancelled(project_dir: Path) -> bool:
    return cancel_path(project_dir).exists()


def check_cancel(project_dir: Path):
    if is_cancelled(project_dir):
        raise CancelledError("Остановлено пользователем")












def ai_activity_callback(
    logger: "JobLogger",
    *,
    stage: str,
    batch_index: int,
    total_batches: int,
    global_start: float,
    global_end: float,
    label: str,
) -> Callable[[float, int], None]:
    """Create a throttled status callback while Ollama is actively streaming."""
    last_report = [0.0]
    def report(elapsed: float, received_chars: int) -> None:
        if elapsed - last_report[0] < 12.0:
            return
        last_report[0] = elapsed
        ratio = max(0.0, min(1.0, batch_index / max(1, total_batches)))
        progress = global_start + (global_end - global_start) * ratio
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        logger.heartbeat(
            stage,
            progress,
            f"{label} · Ollama работает {minutes}:{seconds:02d} · получено {received_chars} символов",
            current_batch=batch_index + 1,
            total_batches=total_batches,
            remaining_items=max(0, total_batches - (batch_index + 1)),
            ai_elapsed_seconds=round(elapsed, 1),
            ai_received_chars=int(received_chars),
        )
    return report


def update_ai_coverage_report(project_dir: Path, **updates: Any) -> dict[str, Any]:
    path = project_dir / "ai_coverage_report.json"
    data = read_json(path, {}) or {}
    data.update(updates)
    data["updated_at"] = time.time()
    write_json(path, data)
    return data


def update_ai_batch_health(
    project_dir: Path,
    *,
    stage: str,
    batch: int,
    total: int,
    state: str,
    expected_ids: set[int] | None = None,
    actual_ids: set[int] | None = None,
    error: str = "",
    prompt_chars: int | None = None,
) -> dict[str, Any]:
    """Persist a truthful per-batch audit trail for recovery and diagnostics."""
    path = project_dir / "ai_batch_health.json"
    data = read_json(path, {}) or {}
    stages = data.setdefault("stages", {})
    stage_data = stages.setdefault(stage, {"batches": {}})
    key = str(int(batch))
    expected = set(expected_ids or set())
    actual = set(actual_ids or set())
    stage_data["batches"][key] = {
        "batch": int(batch),
        "total_batches": int(total),
        "state": str(state),
        "expected_ids": sorted(expected),
        "actual_ids": sorted(actual),
        "missing_ids": sorted(expected - actual),
        "error": str(error or "")[:2000],
        "prompt_chars": int(prompt_chars) if prompt_chars is not None else None,
        "updated_at": time.time(),
    }
    states = [str(x.get("state")) for x in stage_data["batches"].values() if isinstance(x, dict)]
    stage_data["completed_batches"] = sum(1 for x in states if x == "done")
    stage_data["degraded_batches"] = sum(1 for x in states if x == "degraded")
    stage_data["failed_batches"] = sum(1 for x in states if x == "failed")
    stage_data["total_batches"] = int(total)
    data["updated_at"] = time.time()
    write_json(path, data)
    return data












def preview_path(project_dir: Path) -> Path:
    return project_dir / "preview" / "preview.mp4"


def preview_status(project_dir: Path) -> dict[str, Any]:
    p = preview_path(project_dir)
    return {
        "exists": p.exists() and p.stat().st_size > 1024,
        "path": "preview/preview.mp4",
        "url": f"/api/projects/{project_dir.name}/file/preview/preview.mp4",
        "size_mb": round(p.stat().st_size / 1024 / 1024, 2) if p.exists() else 0,
    }


def create_preview_video(project_dir: Path, settings: dict[str, Any], logger: JobLogger) -> dict[str, Any]:
    """Create browser-safe preview.mp4 for reliable clip preview."""
    p = project_paths(project_dir)
    out = preview_path(project_dir)
    if out.exists() and out.stat().st_size > 1024:
        logger.log("Preview-safe video already exists.")
        return preview_status(project_dir)

    check_cancel(project_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = which("ffmpeg") or "ffmpeg"
    logger.heartbeat("preview_safe", 2, "Создаю preview-safe video 720p H.264/AAC")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(p["video"]),
        "-vf",
        "scale=-2:720",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(out),
    ]
    r = run_cmd(cmd, project_dir=project_dir, cancel_file=cancel_path(project_dir))
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 1024:
        raise RuntimeError("Не удалось создать preview.mp4:\n" + (r.stdout or ""))
    logger.set_status("done", 100, "Preview-safe video готов")
    return preview_status(project_dir)


def pre_render_check(project_dir: Path, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    p = project_paths(project_dir)
    segs = read_json(p["segments"], [])
    report = {
        "ok": True,
        "segments": len(segs),
        "warnings": [],
        "errors": [],
        "duration": tc(0),
        "avg_clip": tc(0),
        "longest_clip": tc(0),
        "shortest_clip": tc(0),
        "overlaps": [],
        "technical_risk": 0,
    }
    if not segs:
        report["ok"] = False
        report["errors"].append("Нет фрагментов в финальном монтаже.")
        write_json(project_dir / "pre_render_check.json", report)
        return report

    cleaned = []
    for i, s in enumerate(segs):
        try:
            start = float(s.get("start", 0))
            end = float(s.get("end", 0))
        except Exception:
            report["errors"].append(f"Фрагмент {i + 1}: неправильные start/end")
            continue
        if end <= start:
            report["errors"].append(f"Фрагмент {i + 1}: end <= start")
        cleaned.append((i + 1, start, end, s))

    durations = [max(0, e - st) for _, st, e, _ in cleaned]
    total = sum(durations)
    report["duration"] = tc(total)
    report["avg_clip"] = tc(total / max(1, len(durations)))
    report["longest_clip"] = tc(max(durations) if durations else 0)
    report["shortest_clip"] = tc(min(durations) if durations else 0)

    for a_i, a_s, a_e, _ in cleaned:
        for b_i, b_s, b_e, _ in cleaned:
            if b_i <= a_i:
                continue
            if overlaps(a_s, a_e, b_s, b_e):
                report["overlaps"].append([a_i, b_i])
    if report["overlaps"]:
        report["warnings"].append(f"Есть пересечения фрагментов: {report['overlaps'][:10]}")

    if len(segs) < 5:
        report["warnings"].append("Очень мало фрагментов. Ролик может выглядеть как 1-2 большие вырезки.")
    if durations and max(durations) > 360:
        report["warnings"].append("Есть фрагмент длиннее 6 минут. Возможно, монтаж будет однообразным.")
    if durations and min(durations) < 6:
        report["warnings"].append("Есть фрагмент короче 6 секунд. Проверь, не слишком ли резкая склейка.")

    tech_words = ["подключ", "микрофон", "камера", "ссылка", "ожид", "не слыш", "звук"]
    technical = 0
    for _, _, _, s in cleaned:
        low = (str(s.get("title", "")) + " " + str(s.get("reason", "")) + " " + str(s.get("text_preview", ""))).lower()
        if any(w in low for w in tech_words):
            technical += 1
    report["technical_risk"] = technical
    if technical:
        report["warnings"].append(f"Технических/организационных рисков: {technical}")

    if settings:
        try:
            target_sec = float(settings.get("target_minutes") or 0) * 60
            report["target_duration"] = tc(target_sec)
            report["target_seconds"] = round(target_sec, 2)
            report["current_seconds"] = round(total, 2)
            report["missing_seconds"] = round(max(0.0, target_sec - total), 2)
            if target_sec > 0 and total < target_sec * float(settings.get("target_fill_ratio", 0.94)):
                report["warnings"].append(
                    f"Итог короче цели: собрано {tc(total)} из {tc(target_sec)}. Используй Duration Control: добрать моменты или добавить контекст."
                )
        except Exception:
            pass

    if report["errors"]:
        report["ok"] = False
    write_json(project_dir / "pre_render_check.json", report)
    return report


def smart_preflight_warnings(project_dir: Path, settings: dict[str, Any], duration: float | None = None) -> list[str]:
    warnings = []
    try:
        duration = duration if duration is not None else video_duration(project_paths(project_dir)["video"])
    except Exception:
        duration = 0
    hours = duration / 3600 if duration else 0
    visual = settings.get("visual_mode", "Лёгкий")
    if hours >= 3 and visual in ("Средний", "Полный"):
        warnings.append("Для видео длиннее 3 часов Visual mode Средний/Полный может идти очень долго. Рекомендуется Лёгкий.")
    if hours >= 3 and settings.get("generate_metadata", False):
        warnings.append("Metadata лучше запускать после анализа отдельной кнопкой, чтобы не замедлять основной анализ.")
    if hours >= 3 and settings.get("make_srt", False):
        warnings.append("SRT на длинном видео может добавить время. Можно выключить и создать потом.")
    if int(settings.get("ai_batch_size", 3)) > 4:
        warnings.append("AI batch size больше 4 может вызывать timeout на локальных моделях.")
    if settings.get("micro_cut_enabled", True) and int(settings.get("top_blocks_for_micro", 30)) > 50:
        warnings.append("Top blocks for micro > 50 может сильно замедлить Micro-cut.")
    return warnings


def simple_log_summary(project_dir: Path) -> dict[str, Any]:
    p = project_paths(project_dir)
    return {
        "status": read_json(project_dir / "status.json", {}),
        "preflight": read_json(project_dir / "preflight.json", {}),
        "segments": len(read_json(p["segments"], [])),
        "candidates": len(read_json(p["candidates"], [])),
        "quality": read_json(p["quality"], {}),
        "pre_render_check": read_json(project_dir / "pre_render_check.json", {}),
        "outputs": list_output_files(project_dir) if "list_output_files" in globals() else [],
        "preview": preview_status(project_dir),
    }


# ---------------- v8.6.5 Quality Core helpers ----------------

QUALITY_CORE_VERSION = "quality-core-v10.4.0-semantic"
# Metadata-only 11.2.5 must reuse the verified 11.2.4 Micro-AI checkpoints.
MICRO_AI_POLICY_VERSION = "micro-ai-policy-v11.2.4-longform-live-context"
TRANSCRIPT_TIMING_NORMALIZATION_VERSION = "speech-gap-v2"


def normalize_transcript_timing(
    segments: list[TranscriptSegment],
    *,
    max_speech_gap: float = 4.0,
    max_segment_seconds: float = 30.0,
) -> tuple[list[TranscriptSegment], dict[str, Any]]:
    """Remove artificial multi-minute silence from Whisper segment timings.

    Faster-Whisper can return one segment whose first word is near the start of
    a music/silence interval and whose remaining words are several minutes
    later.  Treating that outer segment span as continuous speech poisons block
    prompts and creates overlong micro clips.  Word timestamps are the source
    of truth: suspicious segments are split at large word gaps and at a hard
    maximum speech span.  Ordinary short segments are preserved byte-for-byte.
    """

    normalized: list[TranscriptSegment] = []
    split_inputs = 0
    unresolved_long_inputs = 0
    removed_silence_seconds = 0.0
    max_input_span = 0.0

    for segment in sorted(segments, key=lambda item: (float(item.start), float(item.end))):
        start = max(0.0, float(segment.start))
        end = max(start, float(segment.end))
        span = end - start
        max_input_span = max(max_input_span, span)
        words: list[dict[str, Any]] = []
        for raw in segment.words or []:
            try:
                word_start = max(start, float(raw.get("start", start)))
                word_end = min(end, max(word_start, float(raw.get("end", word_start))))
            except Exception:
                continue
            word_text = str(raw.get("word", "") or "").strip()
            if word_text and word_end >= word_start:
                words.append({"start": word_start, "end": word_end, "word": word_text})
        words.sort(key=lambda item: (item["start"], item["end"]))

        largest_gap = max(
            (float(words[index]["start"]) - float(words[index - 1]["end"]) for index in range(1, len(words))),
            default=0.0,
        )
        suspicious = span > max_segment_seconds or largest_gap > max_speech_gap
        if not suspicious:
            normalized.append(segment)
            continue
        if not words:
            # Old third-party transcripts may not carry word timestamps.  Keep
            # their content intact; the micro-window builder still applies a
            # hard duration cap so such a segment cannot enter the montage as a
            # multi-minute clip.
            unresolved_long_inputs += 1
            normalized.append(segment)
            continue

        groups: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for word in words:
            if current:
                gap = float(word["start"]) - float(current[-1]["end"])
                projected_span = float(word["end"]) - float(current[0]["start"])
                if gap > max_speech_gap or projected_span > max_segment_seconds:
                    groups.append(current)
                    current = []
            current.append(word)
        if current:
            groups.append(current)

        produced: list[TranscriptSegment] = []
        for group in groups:
            group_start = max(start, float(group[0]["start"]) - 0.15)
            group_end = min(end, float(group[-1]["end"]) + 0.25)
            if group_end <= group_start:
                continue
            group_words = [
                {
                    "start": round(float(word["start"]), 3),
                    "end": round(float(word["end"]), 3),
                    "word": str(word["word"]),
                }
                for word in group
            ]
            text = " ".join(str(word["word"]) for word in group).strip()
            if not text:
                continue
            produced.append(
                TranscriptSegment(round(group_start, 3), round(group_end, 3), text, group_words)
            )

        if produced:
            split_inputs += 1
            removed_silence_seconds += max(0.0, span - sum(item.end - item.start for item in produced))
            normalized.extend(produced)
        else:
            unresolved_long_inputs += 1
            normalized.append(segment)

    normalized.sort(key=lambda item: (float(item.start), float(item.end)))
    report = {
        "version": TRANSCRIPT_TIMING_NORMALIZATION_VERSION,
        "input_segments": len(segments),
        "output_segments": len(normalized),
        "split_input_segments": split_inputs,
        "unresolved_long_inputs": unresolved_long_inputs,
        "removed_silence_seconds": round(removed_silence_seconds, 3),
        "max_input_span_seconds": round(max_input_span, 3),
        "max_output_span_seconds": round(
            max((float(item.end) - float(item.start) for item in normalized), default=0.0), 3
        ),
        "max_speech_gap_seconds": max_speech_gap,
        "max_segment_seconds": max_segment_seconds,
    }
    return normalized, report


def stable_hash(data: Any) -> str:
    """Stable short hash for settings/cache fingerprints."""
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def file_signature(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
        return {
            "name": path.name,
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
        }
    except Exception:
        return {"name": path.name, "missing": True}


def video_signature(project_dir: Path) -> dict[str, Any]:
    return source_file_signature(source_video_path(project_dir))


def video_content_signature(project_dir: Path) -> dict[str, Any]:
    """Content identity for expensive analysis caches.

    Paths/mtime/ctime are intentionally excluded so moving a portable project
    or touching the source file does not invalidate hours of transcript/AI work.
    Size + distributed partial SHA-256 still changes when the media content does.
    """
    sig = video_signature(project_dir)
    return {
        "size": sig.get("size"),
        "partial_sha256": sig.get("partial_sha256"),
        "signature_version": sig.get("signature_version"),
    }


def settings_subset(settings: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {k: settings.get(k) for k in keys}


def cache_matches(data: Any, fingerprint: str, key: str) -> bool:
    return isinstance(data, dict) and data.get("fingerprint") == fingerprint and isinstance(data.get(key), list)


def ai_batch_fingerprint(
    project_dir: Path, settings: dict[str, Any], model: str, prompt_base: str, batch_size: int, batch_i: int, batch: list[dict[str, Any]]
) -> str:
    return stable_hash(
        {
            "version": QUALITY_CORE_VERSION,
            "kind": "ai_batch",
            "video": video_content_signature(project_dir),
            "model": model,
            "prompt_base": prompt_base,
            "batch_size": batch_size,
            "batch_i": batch_i,
            "block_seconds": settings.get("block_seconds"),
            "language": settings.get("language"),
            "batch": [
                {"id": b.get("id"), "start": b.get("start"), "end": b.get("end"), "text_hash": stable_hash(b.get("text", ""))}
                for b in batch
            ],
        }
    )


def micro_batch_fingerprint(project_dir: Path, settings: dict[str, Any], model: str, batch_i: int, batch: list[dict[str, Any]]) -> str:
    return stable_hash(
        {
            "version": MICRO_AI_POLICY_VERSION,
            "kind": "micro_batch",
            "video": video_signature(project_dir),
            "model": model,
            "micro_window_seconds": settings.get("micro_window_seconds"),
            "micro_min_seconds": settings.get("micro_min_seconds"),
            "micro_max_seconds": settings.get("micro_max_seconds"),
            "top_blocks_for_micro": settings.get("top_blocks_for_micro"),
            "batch_i": batch_i,
            "batch": [
                {
                    "start": w.get("start"),
                    "end": w.get("end"),
                    "parent_id": w.get("parent_id"),
                    "parent_score": w.get("parent_score"),
                    "text_hash": stable_hash(w.get("text", "")),
                }
                for w in batch
            ],
        }
    )


def render_part_fingerprint(project_dir: Path, settings: dict[str, Any], start: float, end: float) -> str:
    return stable_hash(
        {
            "version": QUALITY_CORE_VERSION,
            "kind": "render_part",
            "video": video_signature(project_dir),
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "encoder": settings.get("video_encoder", "auto"),
            "preset": settings.get("render_preset", "veryfast"),
            "crf": settings.get("crf", 23),
            "remove_silence": effective_remove_silence(settings),
        }
    )


def transcript_fingerprint(project_dir: Path, settings: dict[str, Any]) -> str:
    return stable_hash(
        {
            "version": QUALITY_CORE_VERSION,
            "kind": "transcript",
            "video": video_content_signature(project_dir),
            "whisper_model": settings.get("whisper_model", "base"),
            "whisper_device": settings.get("whisper_device", "auto"),
            "whisper_compute": settings.get("whisper_compute", "auto"),
            "language": settings.get("language", "ru"),
            "chunk_seconds": settings.get("chunk_seconds", 900),
            "vad_filter": True,
            "word_timestamps": True,
        }
    )


def transcript_fingerprint_legacy(project_dir: Path, settings: dict[str, Any]) -> str:
    """10.15.20 fingerprint retained only to migrate existing transcripts."""
    return stable_hash(
        {
            "version": QUALITY_CORE_VERSION,
            "kind": "transcript",
            "video": video_signature(project_dir),
            "whisper_model": settings.get("whisper_model", "base"),
            "whisper_device": settings.get("whisper_device", "auto"),
            "whisper_compute": settings.get("whisper_compute", "auto"),
            "language": settings.get("language", "ru"),
            "chunk_seconds": settings.get("chunk_seconds", 900),
            "vad_filter": True,
            "word_timestamps": True,
        }
    )


def scene_detection_fingerprint(project_dir: Path, threshold: float = 0.35) -> str:
    return stable_hash(
        {
            "version": QUALITY_CORE_VERSION,
            "kind": "scene_detection",
            "video": video_content_signature(project_dir),
            "threshold": threshold,
        }
    )


def scene_detection_fingerprint_legacy(project_dir: Path, threshold: float = 0.35) -> str:
    return stable_hash(
        {
            "version": QUALITY_CORE_VERSION,
            "kind": "scene_detection",
            "video": video_signature(project_dir),
            "threshold": threshold,
        }
    )


def effective_remove_silence(settings: dict[str, Any]) -> bool:
    # silenceremove changes clip duration. Until subtitles are retimed through an
    # audio-edit map, keep SRT and source timeline in sync by disabling it when
    # subtitles are requested.
    return bool(settings.get("remove_silence", False)) and not bool(settings.get("make_srt", True))


@lru_cache(maxsize=32)
def ffmpeg_encoder_available(ffmpeg: str, encoder: str) -> bool:
    """Return whether the current FFmpeg build advertises an encoder.

    This is a capability check, not a guarantee that the user's GPU/driver can
    initialize it. The actual render command still remains the final authority.
    Results are cached because `ffmpeg -encoders` is otherwise executed once per
    segment during a long render.
    """
    encoder = str(encoder or "").strip()
    if not encoder or encoder == "libx264":
        return True
    try:
        completed = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            check=False,
        )
        if completed.returncode != 0:
            return False
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        # Match an encoder token, not a random substring in a description.
        return re.search(rf"(?m)^\s*[A-Z.]+\s+{re.escape(encoder)}(?:\s|$)", output or "") is not None
    except (OSError, subprocess.SubprocessError):
        return False


def video_encode_args(ffmpeg: str, settings: dict[str, Any], logger: JobLogger | None = None) -> tuple[list[str], str]:
    requested = str(settings.get("video_encoder") or "auto").strip().lower() or "auto"
    crf = str(settings.get("crf", 23))
    preset = str(settings.get("render_preset", "veryfast") or "veryfast")
    if requested == "auto":
        capabilities = detect_hardware_capabilities()
        requested = "h264_nvenc" if bool((capabilities.get("ffmpeg") or {}).get("nvenc_runtime_ok")) else "libx264"
        if logger:
            logger.log(f"Video encoder auto -> {requested}")
    if requested != "libx264" and not ffmpeg_encoder_available(ffmpeg, requested):
        if logger:
            logger.log(f"Encoder {requested} недоступен в текущем FFmpeg/GPU, использую libx264 fallback.")
        requested = "libx264"
    if requested in {"h264_nvenc", "hevc_nvenc"}:
        return ["-c:v", requested, "-preset", "p4", "-cq", crf], requested
    if requested in {"h264_qsv", "hevc_qsv"}:
        return ["-c:v", requested, "-global_quality", crf], requested
    if requested in {"h264_amf", "hevc_amf"}:
        return ["-c:v", requested, "-quality", "balanced", "-qp_i", crf, "-qp_p", crf], requested
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf], "libx264"


def candidate_duration(c: Candidate) -> float:
    return max(0.0, float(c.end) - float(c.start))


def candidates_total_duration(items: list[Candidate]) -> float:
    return sum(candidate_duration(c) for c in items)


def candidate_has_overlap(c: Candidate, chosen: list[Candidate]) -> bool:
    return any(overlaps(c.start, c.end, x.start, x.end) for x in chosen)


def candidate_density_value(c: Candidate) -> float:
    """Interest-per-time score. Capped so tiny clips don't dominate too much."""
    dur = max(8.0, candidate_duration(c))
    return float(c.score) / dur * 60.0


def edit_mode_prompt(settings: dict[str, Any]) -> str:
    mode = str(settings.get("edit_mode", "Сбалансированный") or "Сбалансированный")
    prompts = {
        "Плотно": "\nРежим монтажа: ПЛОТНО. Приоритет коротким сильным моментам, быстрый темп, минимум контекста и воды.",
        "С историей": "\nРежим монтажа: С ИСТОРИЕЙ. Сохраняй развитие ситуации, контекст до/после, понятную причинно-следственную линию.",
        "Только смешное": "\nРежим монтажа: ТОЛЬКО СМЕШНОЕ. Максимальный приоритет шуткам, смеху, мемам, абсурду и неожиданным реакциям.",
        "Только конфликт/реакции": "\nРежим монтажа: КОНФЛИКТ/РЕАКЦИИ. Максимальный приоритет спорам, сильным эмоциям, резким реакциям, донатам и чату.",
        "IRL плотный": "\nРежим монтажа: IRL ПЛОТНЫЙ. Быстрые реальные ситуации, резкие реакции, короткие сценки с понятным payoff, минимум ходьбы и ожидания.",
        "IRL история": "\nРежим монтажа: IRL ИСТОРИЯ. Сохраняй развитие встречи/конфликта/маршрута, добавляй контекст до реакции, оставляй понятную мини-сцену.",
        "IRL конфликт/хаос": "\nРежим монтажа: IRL КОНФЛИКТ/ХАОС. Приоритет неловкости, спорам, прохожим, охране, донат-провокациям, резким реакциям и неожиданным событиям.",
    }
    return prompts.get(mode, "\nРежим монтажа: СБАЛАНСИРОВАННЫЙ. Смешивай юмор, эмоции, историю и динамику без лишней воды.")


def resolved_content_type(settings: dict[str, Any]) -> str:
    """Return content_type with IRL inferred from edit_mode when UI left it on Auto."""
    ct = str(settings.get("content_type", "Auto") or "Auto")
    mode = str(settings.get("edit_mode", "") or "")
    if ct == "Auto" and mode.startswith("IRL"):
        return "IRL стрим"
    return ct


def content_type_prompt(settings: dict[str, Any]) -> str:
    ct = resolved_content_type(settings).lower()
    if "irl" in ct or "ирл" in ct or "улиц" in ct or "travel" in ct or "прогул" in ct:
        return """
Профиль контента: IRL-стрим. Ищи не только смешные реплики, но и ситуации в реальном мире:
- неожиданные встречи с прохожими, продавцами, охраной, друзьями или случайными персонажами;
- конфликт, неловкость, риск, хаос, резкую реакцию окружающих;
- донат/чат, который заставляет стримера что-то сделать или меняет ситуацию;
- смену локации, событие на улице, транспорт, магазин, кафе, толпу, визуальный прикол;
- личную историю, которая понятна новому зрителю;
- момент, где нужно добавить 5-15 секунд до начала, чтобы была понятна причина реакции.
Для IRL технический сбой можно оставить только если стример остаётся в основном live-контенте и сам сбой становится частью текущей сцены. Экран ожидания/reconnect/intermission, старые highlights/replay и prerecorded-вставки не становятся хорошим моментом из-за смеха или доната внутри них.
Если важный момент скорее визуальный, а в тексте мало слов, не завышай уверенность, но пометь как visual/context candidate.
"""
    if "game" in ct or "игр" in ct:
        return "\nПрофиль контента: игровой стрим. Приоритет clutch, провалам, реакции команды/чата, неожиданным игровым поворотам и понятным без полного матча моментам."
    if "sport" in ct or "спорт" in ct or "теннис" in ct:
        return "\nПрофиль контента: спорт/теннис. Приоритет ярким розыгрышам, спорным моментам, победам, провалам, эмоциям, реакциям чата и динамичным спортивным сценам. Убирай паузы между событиями."
    if "reaction" in ct or "реакц" in ct:
        return "\nПрофиль контента: реакции. Приоритет сильным эмоциям, шоку, смеху, спорам, разбору мемов/видео и резким сменам мнения."
    if "podcast" in ct or "подкаст" in ct or "interview" in ct or "интерв" in ct:
        return "\nПрофиль контента: разговорный формат. Приоритет историям, конфликтам мнений, ярким формулировкам, инсайтам и моментам, понятным без долгого контекста."
    return ""


def is_irl_settings(settings: dict[str, Any]) -> bool:
    ct = resolved_content_type(settings).lower()
    mode = str(settings.get("edit_mode", "") or "")
    return mode.startswith("IRL") or any(x in ct for x in ["irl", "ирл", "улиц", "travel", "прогул"])


def content_type_bonus(c: Candidate, settings: dict[str, Any]) -> float:
    if not is_irl_settings(settings):
        return 0.0
    text = f"{c.title} {c.reason} {c.text_preview}".lower()
    pos = [
        "прохож",
        "охран",
        "полици",
        "магаз",
        "улиц",
        "кафе",
        "метро",
        "такси",
        "транспорт",
        "донат",
        "чат",
        "сказал",
        "подош",
        "конфликт",
        "спор",
        "нелов",
        "реакц",
        "смех",
        "крик",
        "шок",
        "жесть",
        "странн",
        "случайн",
        "персонаж",
        "истори",
        "локац",
        "увидел",
    ]
    neg = ["настрой", "микрофон", "камера", "подключ", "ссылка", "ждем", "ожид", "тест", "звук"]
    bonus = min(1.2, sum(0.18 for w in pos if w in text))
    if any(w in text for w in neg) and not any(w in text for w in ["смеш", "смех", "конфликт", "реакц", "жесть", "донат"]):
        bonus -= 0.55
    # IRL usually needs a little context before the payoff. Penalize ultra-short isolated pieces.
    dur = candidate_duration(c)
    if 18 <= dur <= 120:
        bonus += 0.20
    elif dur < 8:
        bonus -= 0.35
    return bonus


def edit_mode_bonus(c: Candidate, settings: dict[str, Any]) -> float:
    mode = str(settings.get("edit_mode", "Сбалансированный") or "Сбалансированный")
    text = f"{c.title} {c.reason} {c.text_preview}".lower()
    dur = candidate_duration(c)
    bonus = 0.0
    if mode == "Плотно":
        if 10 <= dur <= 55:
            bonus += 0.45
        if dur > 90:
            bonus -= 0.65
        bonus += min(0.5, candidate_density_value(c) / 20.0)
    elif mode == "С историей":
        if 35 <= dur <= 180:
            bonus += 0.35
        if any(w in text for w in ["истори", "почему", "после", "из-за", "решил", "объяс"]):
            bonus += 0.35
        if dur < 10:
            bonus -= 0.35
    elif mode == "Только смешное":
        if any(w in text for w in ["смеш", "смех", "угар", "шут", "мем", "ор", "ха-ха", "абсурд"]):
            bonus += 0.9
        if any(w in text for w in ["техничес", "объяс", "ожид", "настрой"]):
            bonus -= 0.45
    elif mode == "Только конфликт/реакции":
        if any(w in text for w in ["конфликт", "спор", "реакц", "крич", "эмоц", "донат", "чат", "жесть", "шок"]):
            bonus += 0.9
        if any(w in text for w in ["спокой", "обычн", "объяс", "техничес"]):
            bonus -= 0.35
    elif mode == "IRL плотный":
        if 10 <= dur <= 70:
            bonus += 0.45
        if any(w in text for w in ["прохож", "донат", "чат", "смех", "жесть", "реакц", "нелов", "странн", "увидел"]):
            bonus += 0.65
        if dur > 120:
            bonus -= 0.6
    elif mode == "IRL история":
        if 25 <= dur <= 180:
            bonus += 0.45
        if any(w in text for w in ["истори", "почему", "подош", "сказал", "локац", "после", "решил", "встрет"]):
            bonus += 0.55
        if dur < 12:
            bonus -= 0.25
    elif mode == "IRL конфликт/хаос":
        if any(w in text for w in ["конфликт", "спор", "охран", "полици", "крик", "хаос", "жесть", "нелов", "донат", "прохож", "шок"]):
            bonus += 1.0
        if any(w in text for w in ["спокой", "обычн", "настрой", "ожид"]):
            bonus -= 0.4
    return bonus


def candidate_selection_key(c: Candidate, settings: dict[str, Any]) -> float:
    dur = max(8.0, candidate_duration(c))
    density = candidate_density_value(c)
    length_bonus = 0.25 if 15 <= dur <= float(settings.get("micro_max_seconds", 75)) else 0.0
    length_penalty = 0.45 if dur > float(settings.get("micro_max_seconds", 75)) * 1.6 else 0.0
    clarity = max(0.0, min(1.0, float(c.standalone_clarity or 0.0)))
    cls = str(c.content_class or "unknown").strip().lower()
    cls_conf = max(0.0, min(1.0, float(c.content_class_confidence or 0.0)))
    semantic_adjust = 0.0
    context_first = is_irl_settings(settings) and str(settings.get("edit_mode") or "") not in {
        "IRL плотный", "IRL конфликт/хаос", "Только смешное/конфликт",
    }
    if cls in {"primary_live", "live_reaction"} and cls_conf >= 0.6:
        semantic_adjust += 0.18
    elif cls in NON_PRIMARY_CONTENT_CLASSES:
        # This is only a ranking nudge; candidate_is_selectable/apply guard own
        # the actual veto so ambiguous transcript mentions are not over-filtered.
        semantic_adjust -= 1.3 * cls_conf
    return (
        float(c.score) * 0.78
        + density * (0.06 if context_first else 0.18)
        + float(c.audio_score or 0) * (0.12 if context_first else 0.45)
        + float(c.visual_score or 0) * 0.20
        + clarity * 0.35
        + semantic_adjust
        + length_bonus
        - length_penalty
        + edit_mode_bonus(c, settings)
        + content_type_bonus(c, settings)
        + float(c.confidence or 0) * 0.10
    )


def transcript_quality_score(text: str) -> float:
    text = (text or "").strip()
    if not text:
        return 0.0
    words = re.findall(r"[\wА-Яа-яЁё]+", text)
    score = min(10.0, len(words) / 22.0 * 10.0)
    if any(ch in text for ch in "!?…"):
        score += 0.4
    if len(set(words)) < max(4, len(words) * 0.35):
        score -= 0.7
    return round(max(0.0, min(10.0, score)), 2)


def update_candidate_confidence(c: Candidate) -> Candidate:
    if not c.transcript_score:
        c.transcript_score = transcript_quality_score(c.text_preview)
    if not c.ai_score:
        c.ai_score = float(c.score)
    ai_component = max(0.0, min(10.0, float(c.ai_score)))
    transcript_component = max(0.0, min(10.0, float(c.transcript_score)))
    audio_component = max(0.0, min(10.0, 5.0 + float(c.audio_score or 0) * 2.5))
    visual_component = max(0.0, min(10.0, 5.0 + float(c.visual_score or 0) * 2.0))
    penalty = min(3.0, float(c.penalty_score or 0) * 0.8)
    confidence = ai_component * 0.50 + transcript_component * 0.20 + audio_component * 0.15 + visual_component * 0.15 - penalty
    c.confidence = round(max(0.0, min(10.0, confidence)), 2)
    c.confidence_reason = (
        f"AI={round(ai_component, 2)}, transcript={round(transcript_component, 2)}, "
        f"audio={round(audio_component, 2)}, visual={round(visual_component, 2)}, penalty={round(penalty, 2)}"
    )
    return c


def update_confidences(candidates: list[Candidate]) -> list[Candidate]:
    return [update_candidate_confidence(c) for c in candidates]


def explain_candidate_for_viewer(c: Candidate, settings: dict[str, Any]) -> Candidate:
    """Add user-facing explanation fields to every candidate.

    Local LLMs often return a short technical reason.  The UI needs a stable,
    readable explanation even when the model response is minimal or older
    cached candidates are used.
    """
    text = f"{c.title} {c.reason} {c.text_preview}".lower()
    kind = str(c.moment_type or "general")
    mode = str(settings.get("edit_mode") or "Сбалансированный")
    preset = str(settings.get("task_preset_label") or settings.get("task_preset") or "")

    if "conflict" in kind or any(w in text for w in ["конфликт", "спор", "охран", "полици", "крик", "нелов", "хаос"]):
        what = "В моменте есть напряжение, спор, неловкость или резкая реакция."
        value = "Зрителю интересно узнать, чем закончится ситуация."
    elif "donation" in kind or "донат" in text:
        what = "Донат или чат меняет ход разговора и провоцирует реакцию."
        value = "Такой момент хорошо работает как интерактивная сцена со зрителями."
    elif "reaction" in kind or any(w in text for w in ["реакц", "шок", "жесть", "смех", "угар", "смеш"]):
        what = "Есть заметная эмоция: смех, удивление, шок или сильная реакция."
        value = "Реакцию легко понять даже без полного контекста стрима."
    elif "story" in kind or any(w in text for w in ["истори", "почему", "решил", "встрет", "после"]):
        what = "Фрагмент похож на мини-историю с причиной и развитием."
        value = "Такой кусок помогает ролику смотреться связно, а не случайной нарезкой."
    elif "visual" in kind or float(c.visual_score or 0) > 0:
        what = "Важна не только речь, но и визуальное событие или смена сцены."
        value = "Визуальный момент удерживает внимание и подходит для YouTube highlight."
    else:
        what = "AI нашёл фрагмент с потенциально интересной репликой или ситуацией."
        value = "Фрагмент можно быстро проверить в Review Studio и оставить только если он держит внимание."

    reasons = []
    if float(c.score or 0) >= 8.5:
        reasons.append("высокий score")
    if str(c.hook_potential or "").lower() == "high":
        reasons.append("сильный hook")
    if float(c.audio_score or 0) > 0:
        reasons.append("есть audio-динамика")
    if float(c.visual_score or 0) > 0:
        reasons.append("есть visual/scene bonus")
    if float(c.ocr_score or 0) > 0:
        reasons.append("есть OCR-сигнал")
    if float(c.confidence or 0) >= 7:
        reasons.append("уверенность высокая")
    if not reasons:
        reasons.append("подходит под выбранный task-пресет")

    why = "Выбран потому что: " + ", ".join(reasons[:4]) + "."
    if preset:
        why += f" Пресет: {preset}."
    if mode:
        why += f" Режим: {mode}."

    c.what_happens = c.what_happens or what
    c.why_selected = c.why_selected or why
    c.viewer_value = c.viewer_value or value
    if not c.risk:
        dur = max(0.0, float(c.end or 0) - float(c.start or 0))
        if dur < 8:
            c.risk = "Слишком короткий фрагмент: может не хватить контекста."
        elif float(c.standalone_clarity or 0) < 0.55:
            c.risk = "Нужен контекст за 5–10 секунд до начала."
        elif float(c.score or 0) < 6.5:
            c.risk = "Средний score: стоит проверить вручную перед финальным рендером."
        else:
            c.risk = "Низкий риск: момент должен быть понятен зрителю."
    c.ai_explanation = c.ai_explanation or f"{what} {why} {value} Риск: {c.risk}"
    return c


def enrich_candidate_explanations(candidates: list[Candidate], settings: dict[str, Any]) -> list[Candidate]:
    return [explain_candidate_for_viewer(c, settings) for c in candidates]


def apply_highlight_density_score(candidates: list[Candidate], settings: dict[str, Any], logger: JobLogger) -> list[Candidate]:
    """Boost dense clips and penalize long/low-density clips."""
    out = []
    for c in candidates:
        dur = candidate_duration(c)
        if dur <= 0:
            continue
        density = candidate_density_value(c)
        delta = 0.0
        if 12 <= dur <= float(settings.get("micro_max_seconds", 75)) and density >= 7.0:
            delta += 0.25
        if 15 <= dur <= 60 and float(c.score) >= 7.0:
            delta += 0.15
        if dur > 120 and density < 4.0:
            delta -= 0.45
        if dur > 240:
            delta -= 0.70
        if delta:
            c.score = round(max(0, min(10, float(c.score) + delta)), 2)
            if delta > 0:
                c.reason += f" / density_bonus={round(delta, 2)}"
            else:
                c.reason += f" / density_penalty={round(delta, 2)}"
                c.penalty_score += abs(delta)
        out.append(c)
    logger.log("Highlight density: применил оценку интересности на секунду.")
    return out


def analyze_audio_dynamics(project_dir: Path, settings: dict[str, Any], logger: JobLogger) -> dict[str, Any]:
    """Reuse source-verified audio evidence even after temporary WAV cleanup."""
    if not settings.get("audio_dynamics_enabled", True):
        return {"enabled": False, "db": []}

    audio = project_dir / "audio_16k.wav"
    cache = project_dir / "audio_dynamics.json"
    source_sig = video_content_signature(project_dir)
    cached = read_json(cache, None)
    if (isinstance(cached, dict) and cached.get("video_content_signature") == source_sig
            and source_sig.get("partial_sha256") and cached.get("enabled")
            and isinstance(cached.get("db"), list) and cached["db"]
            and all(isinstance(x, (int, float)) and math.isfinite(x) for x in cached["db"])):
        return cached
    if not audio.exists():
        source = source_video_path(project_dir)
        if not source.exists():
            logger.log("Audio dynamics skipped: исходник и проверенный кэш не найдены.")
            return {"enabled": False, "db": [], "reason": "source_and_cache_missing"}
        extract_audio(source, audio, logger)

    fp = stable_hash(
        {
            "version": QUALITY_CORE_VERSION,
            "kind": "audio_dynamics",
            "video": video_signature(project_dir),
            "audio": file_signature(audio),
        }
    )
    if isinstance(cached, dict) and cached.get("fingerprint") == fp and isinstance(cached.get("db"), list):
        return cached

    logger.set_status("running", 64, "Audio dynamics: анализ громкости/тишины")
    db_values: list[float] = []
    try:
        with wave.open(str(audio), "rb") as w:
            channels = w.getnchannels()
            rate = w.getframerate()
            width = w.getsampwidth()
            frames_per_window = max(1, int(rate))
            while True:
                raw = w.readframes(frames_per_window)
                if not raw:
                    break
                if channels > 1:
                    # audioop.tomono expects 16-bit-ish sample width; safe enough for ffmpeg wav.
                    try:
                        raw = audioop.tomono(raw, width, 0.5, 0.5)
                    except Exception:
                        pass
                rms = audioop.rms(raw, width) if raw else 0
                if rms <= 0:
                    db = -90.0
                else:
                    # 32768 is full-scale for signed 16-bit; works as a stable normalized proxy.
                    db = 20.0 * math.log10(max(1.0, rms) / 32768.0)
                db_values.append(round(max(-90.0, min(0.0, db)), 2))
    except Exception as exc:
        logger.log(f"Audio dynamics skipped: {exc}")
        return {"enabled": False, "db": [], "error": str(exc)}

    if not db_values:
        profile = {"enabled": False, "db": []}
        write_json(cache, profile)
        return profile

    sorted_db = sorted(db_values)

    def pct(p: float) -> float:
        idx = min(len(sorted_db) - 1, max(0, int((len(sorted_db) - 1) * p)))
        return sorted_db[idx]

    silent_threshold = -45.0
    profile = {
        "enabled": True,
        "fingerprint": fp,
        "video_content_signature": source_sig,
        "window_sec": 1.0,
        "db": db_values,
        "p50": pct(0.50),
        "p75": pct(0.75),
        "p90": pct(0.90),
        "p95": pct(0.95),
        "silent_threshold": silent_threshold,
        "silence_ratio": round(sum(1 for value in db_values if value <= silent_threshold) / max(1, len(db_values)), 4),
    }
    write_json(cache, profile)
    logger.log(f"Audio dynamics: {len(db_values)} сек, p50={profile['p50']}, p90={profile['p90']}, p95={profile['p95']}")
    return profile


def audio_stats_for_range(profile: dict[str, Any], start: float, end: float) -> dict[str, float]:
    db = profile.get("db", [])
    if not db:
        return {"avg": -90.0, "max": -90.0, "silence_ratio": 1.0, "peak_ratio": 0.0, "dynamic_range": 0.0}
    s = max(0, int(math.floor(start)))
    e = min(len(db), max(s + 1, int(math.ceil(end))))
    vals = [float(x) for x in db[s:e]]
    if not vals:
        return {"avg": -90.0, "max": -90.0, "silence_ratio": 1.0, "peak_ratio": 0.0, "dynamic_range": 0.0}
    silent_thr = float(profile.get("silent_threshold", -45.0))
    p90 = float(profile.get("p90", -20.0))
    avg = sum(vals) / len(vals)
    mx = max(vals)
    mn = min(vals)
    silence_ratio = sum(1 for v in vals if v <= silent_thr) / len(vals)
    peak_ratio = sum(1 for v in vals if v >= p90) / len(vals)
    return {
        "avg": round(avg, 2),
        "max": round(mx, 2),
        "silence_ratio": round(silence_ratio, 3),
        "peak_ratio": round(peak_ratio, 3),
        "dynamic_range": round(mx - mn, 2),
    }


def apply_audio_dynamics_to_candidates(
    project_dir: Path, candidates: list[Candidate], settings: dict[str, Any], logger: JobLogger
) -> list[Candidate]:
    profile = analyze_audio_dynamics(project_dir, settings, logger)
    if not profile.get("enabled") or not profile.get("db"):
        return candidates

    out = []
    for c in candidates:
        stats = audio_stats_for_range(profile, c.start, c.end)
        delta = 0.0
        # Peaks and dynamic range often indicate laughter/reaction/donation/stream events.
        if stats["peak_ratio"] >= 0.12:
            delta += 0.25
        if stats["peak_ratio"] >= 0.25:
            delta += 0.20
        if stats["dynamic_range"] >= 18:
            delta += 0.18
        # Silence/monotony often indicates waiting/water.
        if stats["silence_ratio"] >= 0.30:
            delta -= 0.35
        if stats["silence_ratio"] >= 0.55:
            delta -= 0.45
        if stats["peak_ratio"] < 0.03 and stats["dynamic_range"] < 8 and candidate_duration(c) > 45:
            delta -= 0.20

        if delta:
            c.audio_score = round(float(c.audio_score or 0) + delta, 2)
            c.score = round(max(0, min(10, float(c.score) + delta)), 2)
            if delta < 0:
                c.penalty_score += abs(delta)
            c.reason += f" / audio_delta={round(delta, 2)} peak={stats['peak_ratio']} silence={stats['silence_ratio']}"
        out.append(c)

    logger.log("Audio dynamics: применил score к кандидатам.")
    return out


def detect_audio_events(project_dir: Path, settings: dict[str, Any], logger: JobLogger) -> dict[str, Any]:
    """IRL audio event detector based on the cached 1-second dynamics profile."""
    profile = analyze_audio_dynamics(project_dir, settings, logger)
    db = [float(x) for x in profile.get("db", [])] if profile.get("enabled") else []
    events: list[dict[str, Any]] = []
    if not db:
        data = {"enabled": False, "events": []}
        write_json(project_dir / "audio_events.json", data)
        return data
    p90 = float(profile.get("p90", -20.0))
    p95 = float(profile.get("p95", -15.0))
    silent = float(profile.get("silent_threshold", -45.0))
    for i, value in enumerate(db):
        prev = db[i - 1] if i else value
        if value >= p95:
            events.append({"time": i, "type": "loud_peak", "score": 0.6, "db": value})
        elif value >= p90 and value - prev >= 8:
            events.append({"time": i, "type": "sudden_reaction", "score": 0.5, "db": value, "delta": round(value - prev, 2)})
        elif value <= silent and i > 3 and db[i - 1] > silent + 10:
            events.append({"time": i, "type": "sudden_silence", "score": 0.25, "db": value})
    # Merge dense nearby events for compact reports.
    compact: list[dict[str, Any]] = []
    for e in events:
        if compact and e["time"] - compact[-1]["time"] <= 2 and e["type"] == compact[-1]["type"]:
            compact[-1]["score"] = round(max(compact[-1]["score"], e["score"]), 2)
            compact[-1]["end_time"] = e["time"]
        else:
            e.setdefault("end_time", e["time"] + 1)
            compact.append(e)
    data = {"enabled": True, "window_sec": 1, "events": compact[:2000], "total_events": len(compact)}
    write_json(project_dir / "audio_events.json", data)
    logger.log(f"IRL audio events: найдено {len(compact)} событий.")
    return data


def _events_in_range(events: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    return [e for e in events if start <= float(e.get("time", 0)) <= end or start <= float(e.get("end_time", e.get("time", 0))) <= end]


def visual_sample_plan(duration_seconds: float, interval_seconds: float, max_samples: int) -> tuple[int, float, list[float]]:
    """Return a deterministic sample plan that always spans the whole source."""
    duration = max(0.0, float(duration_seconds or 0.0))
    interval = max(0.001, float(interval_seconds or 0.001))
    cap = max(1, int(max_samples or 1))
    requested_total = max(1, math.ceil(duration / interval))
    total = min(cap, requested_total)
    effective_interval = interval if requested_total <= cap else (duration / max(1, total - 1))
    timestamps = [min(duration, idx * effective_interval) for idx in range(total)]
    if timestamps and requested_total > cap:
        timestamps[-1] = duration
    return total, effective_interval, timestamps


def visual_scan_video(project_dir: Path, settings: dict[str, Any], logger: JobLogger) -> dict[str, Any]:
    """Fast whole-video visual scan.

    v8.9: scan frames are extracted with a single FFmpeg process. That is much
    faster and more reliable for 6-12 hour IRL streams than launching FFmpeg for
    every frame. A per-frame fallback remains for unusual files/FFmpeg builds.
    """
    if not settings.get("visual_scan_enabled", True):
        data = {"enabled": False, "reason": "disabled", "samples": [], "total_samples": 0}
        write_json(project_dir / "visual_scan_report.json", data)
        return data
    p = project_paths(project_dir)
    video = p["video"]
    interval = max(3, int(settings.get("visual_scan_interval_seconds", 5) or 5))
    max_samples = max(20, int(settings.get("visual_scan_max_samples", 1200) or 1200))
    cache = project_dir / "visual_scan_report.json"
    fp = stable_hash(
        {
            "version": "v10.15.8-quality-scan",
            "kind": "visual_scan",
            "video": video_signature(project_dir),
            "interval": interval,
            "max": max_samples,
            "hardware_decode": settings.get("hardware_decode", "off"),
        }
    )
    cached = read_json(cache, None)
    if isinstance(cached, dict) and cached.get("fingerprint") == fp:
        return cached
    try:
        dur = video_duration(video)
    except Exception as exc:
        data = {"enabled": False, "error": str(exc), "samples": []}
        write_json(cache, data)
        return data

    frames_dir = project_dir / "visual_scan_frames"
    # Cache cleanup only for our generated scan files so old/partial results do
    # not pollute the new fingerprint.
    frames_dir.mkdir(exist_ok=True)
    for old in frames_dir.glob("scan_*.jpg"):
        try:
            old.unlink()
        except Exception:
            pass

    total, effective_interval, sample_timestamps = visual_sample_plan(dur, interval, max_samples)
    logger.set_step("visual_scan", 0, total, None, f"Visual scan: извлекаю кадры 0/{total}", global_start=62, global_end=70)

    decode_mode = "cpu"
    decode_benchmark: dict[str, Any] = {"selected": "cpu", "reason": "hardware_decode_off"}
    if str(settings.get("hardware_decode") or "off").lower() == "auto":
        try:
            caps = detect_hardware_capabilities()
            if bool((caps.get("ffmpeg") or {}).get("nvdec_advertised")):
                decode_benchmark = benchmark_visual_decode(
                    video, project_dir, interval=effective_interval, width=360, cancel_file=cancel_path(project_dir)
                )
                decode_mode = str(decode_benchmark.get("selected") or "cpu")
                logger.log(
                    "Visual scan decode benchmark: "
                    f"CPU={decode_benchmark.get('cpu_seconds')}s, CUDA={decode_benchmark.get('cuda_seconds')}s, selected={decode_mode}"
                )
        except OperationCancelled:
            raise
        except Exception as exc:
            decode_benchmark = {"selected": "cpu", "reason": f"benchmark_error:{exc}"}
            decode_mode = "cpu"
            logger.log(f"Visual scan CUDA benchmark failed; CPU path kept: {exc}")

    ok_bulk, bulk_log = extract_frames_bulk(
        video, frames_dir, interval=effective_interval, width=360, max_samples=total, project_dir=project_dir,
        cancel_file=cancel_path(project_dir), decode_mode=decode_mode
    )
    # A driver/filter failure on the full source must never lose visual coverage.
    # Retry the exact same sample plan on CPU before using the slow per-frame path.
    if not ok_bulk and decode_mode == "cuda":
        logger.log("Visual scan CUDA full pass failed; retrying identical bulk scan on CPU.")
        for old in frames_dir.glob("scan_*.jpg"):
            try:
                old.unlink()
            except Exception:
                pass
        decode_mode = "cpu"
        ok_bulk, bulk_log = extract_frames_bulk(
            video, frames_dir, interval=effective_interval, width=360, max_samples=total, project_dir=project_dir,
            cancel_file=cancel_path(project_dir), decode_mode="cpu"
        )
    check_cancel(project_dir)

    samples: list[dict[str, Any]] = []
    files = sorted(frames_dir.glob("scan_*.jpg"))[:total]
    if ok_bulk and files:
        for idx, fp_img in enumerate(files):
            t = sample_timestamps[idx]
            item = {"index": idx + 1, "time": round(t, 2), "frame": fp_img.relative_to(project_dir).as_posix(), "exists": True}
            try:
                item["frame_size_kb"] = round(fp_img.stat().st_size / 1024, 1)
            except Exception:
                pass
            samples.append(item)
    else:
        logger.log(
            "Visual scan bulk extraction failed, falling back to per-frame extraction." + ("\n" + bulk_log[-1200:] if bulk_log else "")
        )
        for idx in range(total):
            check_cancel(project_dir)
            t = sample_timestamps[idx]
            fp_img = frames_dir / f"scan_{idx + 1:05d}.jpg"
            ok = fp_img.exists() and fp_img.stat().st_size > 0
            if not ok:
                ok = extract_frame(video, t, fp_img, width=360)
            item = {
                "index": idx + 1,
                "time": round(t, 2),
                "frame": fp_img.relative_to(project_dir).as_posix() if ok else "",
                "exists": bool(ok),
            }
            if ok:
                try:
                    item["frame_size_kb"] = round(fp_img.stat().st_size / 1024, 1)
                except Exception:
                    pass
            samples.append(item)
            if idx and idx % 50 == 0:
                logger.set_step("visual_scan", idx, total, None, f"Visual scan {idx}/{total}", global_start=62, global_end=70)

    logger.set_step(
        "visual_scan",
        len(samples),
        max(1, total),
        None,
        f"Visual scan готов: {len(samples)}/{total} кадров",
        global_start=62,
        global_end=70,
    )
    data = {
        "enabled": True,
        "fingerprint": fp,
        "interval_seconds": interval,
        "effective_interval_seconds": round(effective_interval, 6),
        "coverage_end_seconds": round(min(dur, max(0, total - 1) * effective_interval), 3),
        "duration_seconds": dur,
        "extraction_mode": (f"bulk_ffmpeg_{decode_mode}" if ok_bulk and files else "fallback_per_frame"),
        "decode_mode": decode_mode,
        "decode_benchmark": decode_benchmark,
        "quality_guard": {"coverage_preserved": True, "requested_samples": total},
        "samples": samples,
        "total_samples": len(samples),
    }
    write_json(cache, data)
    logger.log(f"Visual scan: {len(samples)} кадров, effective interval={effective_interval:.2f}s, mode={data['extraction_mode']}")
    return data


def _prepare_ocr_frame(frame_path: Path, project_dir: Path, settings: dict[str, Any]) -> Path:
    """Optionally crop/upscale a frame before Tesseract.

    IRL chat/donation text is often small. ROI + grayscale/upscale makes OCR
    much more useful while still keeping the feature optional and safe.
    ROI values are percentages from 0..1 relative to frame width/height.
    """
    if not settings.get("ocr_roi_enabled", False) and int(settings.get("ocr_upscale", 2) or 2) <= 1:
        return frame_path
    out_dir = project_dir / "ocr_preprocessed"
    out_dir.mkdir(exist_ok=True)
    preprocess_key = stable_hash({
        "version": "ocr-preprocess-v2",
        "frame": file_signature(frame_path),
        "roi": [settings.get("ocr_roi_enabled"), settings.get("ocr_roi_x"), settings.get("ocr_roi_y"), settings.get("ocr_roi_w"), settings.get("ocr_roi_h")],
        "upscale": settings.get("ocr_upscale", 2),
    })[:16]
    out = out_dir / f"{frame_path.stem}_{preprocess_key}{frame_path.suffix or '.jpg'}"
    if out.exists() and out.stat().st_size > 0:
        return out
    filters: list[str] = []
    if settings.get("ocr_roi_enabled", False):
        x = max(0.0, min(1.0, float(settings.get("ocr_roi_x", 0.0) or 0.0)))
        y = max(0.0, min(1.0, float(settings.get("ocr_roi_y", 0.0) or 0.0)))
        w = max(0.05, min(1.0, float(settings.get("ocr_roi_w", 1.0) or 1.0)))
        h = max(0.05, min(1.0, float(settings.get("ocr_roi_h", 1.0) or 1.0)))
        filters.append(f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}")
    scale = max(1, min(4, int(settings.get("ocr_upscale", 2) or 2)))
    if scale > 1:
        filters.append(f"scale=iw*{scale}:ih*{scale}:flags=lanczos")
    # Lightweight text-friendly preprocessing. We avoid thresholding because it
    # can destroy colorful donation/chat overlays.
    filters.append("format=gray")
    ffmpeg = which("ffmpeg") or "ffmpeg"
    cmd = [ffmpeg, "-y", "-i", str(frame_path), "-vf", ",".join(filters), "-q:v", "3", str(out)]
    try:
        r = run_cmd(cmd, timeout=20, project_dir=project_dir, cancel_file=cancel_path(project_dir))
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
    except Exception:
        pass
    return frame_path


def _ocr_worker_count(settings: dict[str, Any], sample_count: int) -> int:
    """Choose bounded OCR parallelism without changing OCR coverage or settings."""
    if sample_count <= 1:
        return 1
    configured_cpu = int(settings.get("cpu_worker_limit") or 0)
    logical = max(1, os.cpu_count() or 1)
    cpu_budget = configured_cpu if configured_cpu > 0 else max(1, logical // 2)
    # Tesseract and per-frame FFmpeg both use native CPU work. Three workers is
    # a good ceiling for a 6c/12t Ryzen + 16 GB machine; stronger CPUs may use 4.
    workers = max(1, min(4, max(1, cpu_budget // 2)))
    try:
        ram_gb = float((detect_hardware_capabilities().get("memory") or {}).get("total_gb") or 0)
        if ram_gb and ram_gb <= 8.5:
            workers = min(workers, 2)
        elif ram_gb and ram_gb <= 16.5:
            workers = min(workers, 3)
    except Exception:
        pass
    return max(1, min(workers, sample_count))


def _ocr_one_sample(
    project_dir: Path,
    settings: dict[str, Any],
    tesseract: str,
    sample_index: int,
    sample: dict[str, Any],
) -> tuple[int, dict[str, Any] | None, str]:
    """Process one OCR sample. Safe to run in a worker thread."""
    frame = sample.get("frame")
    if not frame:
        return sample_index, None, ""
    frame_path = project_dir / str(frame)
    ocr_frame = _prepare_ocr_frame(frame_path, project_dir, settings)
    langs = str(settings.get("ocr_languages", "rus+eng") or "rus+eng")
    try:
        r = run_cmd(
            [tesseract, str(ocr_frame), "stdout", "-l", langs, "--psm", "6"],
            timeout=25,
            project_dir=project_dir,
            cancel_file=cancel_path(project_dir),
        )
        text = (r.stdout or "").strip()
    except OperationCancelled:
        raise
    except Exception as exc:
        return sample_index, None, f"OCR failed at {sample.get('time')}: {exc}"
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return sample_index, None, ""
    low = cleaned.lower()
    kind = "screen_text"
    if any(k in low for k in ["донат", "donat", "donation", "₽", "rub", "$", "superchat"]):
        kind = "donation"
    elif any(k in low for k in ["чат", "chat", "message"]):
        kind = "chat"
    return sample_index, {"time": sample.get("time"), "type": kind, "text": cleaned[:500], "frame": frame}, ""


def ocr_scan_video(project_dir: Path, settings: dict[str, Any], logger: JobLogger) -> dict[str, Any]:
    """OCR scan for chat/donations/screen text with quality-preserving parallelism."""
    if not settings.get("ocr_enabled", True):
        return {"enabled": False, "reason": "disabled", "items": []}
    tesseract = which("tesseract")
    cache = project_dir / "ocr_scan_report.json"
    visual = visual_scan_video(project_dir, settings, logger)
    fp = stable_hash(
        {
            "version": "v10.15.8-parallel-ocr",
            "kind": "ocr",
            "visual_fp": visual.get("fingerprint"),
            "tesseract": bool(tesseract),
            "langs": settings.get("ocr_languages", "rus+eng"),
            "roi": [
                settings.get("ocr_roi_enabled"),
                settings.get("ocr_roi_x"),
                settings.get("ocr_roi_y"),
                settings.get("ocr_roi_w"),
                settings.get("ocr_roi_h"),
            ],
            "upscale": settings.get("ocr_upscale", 2),
            # Worker count intentionally excluded: it changes speed, not output.
        }
    )
    cached = read_json(cache, None)
    if isinstance(cached, dict) and cached.get("fingerprint") == fp:
        return cached
    if not tesseract:
        data = {
            "enabled": False,
            "fingerprint": fp,
            "reason": "Tesseract OCR не установлен",
            "items": [],
            "hint": "Установи tesseract-ocr, чтобы читать чат/донаты/надписи на кадрах.",
        }
        write_json(cache, data)
        logger.log("OCR scan skipped: tesseract не установлен.")
        return data

    samples = visual.get("samples", [])
    every = max(1, int(settings.get("ocr_every_n_visual_samples", 2) or 2))
    ocr_samples = [sample for n, sample in enumerate(samples) if n % every == 0]
    workers = _ocr_worker_count(settings, len(ocr_samples))
    logger.log(
        f"OCR scan: {len(ocr_samples)} кадров, workers={workers}, coverage=каждый {every}-й visual sample (без сокращения покрытия)."
    )
    logger.set_step("ocr_scan", 0, max(1, len(ocr_samples)), None, f"OCR scan 0/{len(ocr_samples)}", global_start=70, global_end=82)

    ordered_results: dict[int, dict[str, Any]] = {}
    completed = 0
    if workers <= 1:
        for idx, sample in enumerate(ocr_samples):
            check_cancel(project_dir)
            result_idx, item, error = _ocr_one_sample(project_dir, settings, tesseract, idx, sample)
            if error:
                logger.log(error)
            if item is not None:
                ordered_results[result_idx] = item
            completed += 1
            logger.set_step(
                "ocr_scan", completed, max(1, len(ocr_samples)), None, f"OCR scan {completed}/{len(ocr_samples)}", global_start=70, global_end=82
            )
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hs-ocr") as pool:
            futures = {
                pool.submit(_ocr_one_sample, project_dir, settings, tesseract, idx, sample): idx
                for idx, sample in enumerate(ocr_samples)
            }
            try:
                for future in as_completed(futures):
                    check_cancel(project_dir)
                    try:
                        result_idx, item, error = future.result()
                    except OperationCancelled:
                        raise
                    except Exception as exc:
                        result_idx, item, error = futures[future], None, f"OCR worker failed: {exc}"
                    if error:
                        logger.log(error)
                    if item is not None:
                        ordered_results[result_idx] = item
                    completed += 1
                    logger.set_step(
                        "ocr_scan", completed, max(1, len(ocr_samples)), None,
                        f"OCR scan {completed}/{len(ocr_samples)} · workers {workers}", global_start=70, global_end=82
                    )
            except OperationCancelled:
                for future in futures:
                    future.cancel()
                raise

    items = [ordered_results[idx] for idx in sorted(ordered_results)]
    data = {
        "enabled": True,
        "fingerprint": fp,
        "items": items,
        "total_items": len(items),
        "processed_samples": len(ocr_samples),
        "ocr_every_n_visual_samples": every,
        "workers": workers,
        "quality_guard": {"coverage_preserved": True, "sample_count": len(ocr_samples)},
    }
    write_json(cache, data)
    logger.log(f"OCR scan: найдено текстовых кадров {len(items)} из {len(ocr_samples)} проверенных; workers={workers}.")
    return data


def apply_irl_pipeline(project_dir: Path, candidates: list[Candidate], settings: dict[str, Any], logger: JobLogger) -> list[Candidate]:
    """Separate IRL scoring pass: visual samples + OCR + audio events + IRL keywords."""
    if not is_irl_settings(settings):
        return candidates
    logger.set_status("running", 68, "IRL pipeline: visual/OCR/audio events")
    visual = visual_scan_video(project_dir, settings, logger)
    ocr = ocr_scan_video(project_dir, settings, logger)
    audio = detect_audio_events(project_dir, settings, logger)
    audio_events = audio.get("events", []) if isinstance(audio, dict) else []
    ocr_items = ocr.get("items", []) if isinstance(ocr, dict) else []
    irl_keywords = [
        "охран",
        "полици",
        "магаз",
        "улиц",
        "прохож",
        "такси",
        "метро",
        "донат",
        "чат",
        "крик",
        "смех",
        "конфликт",
        "спор",
        "нелов",
        "странн",
    ]
    for c in candidates:
        text = f"{c.title} {c.reason} {c.text_preview}".lower()
        delta = 0.0
        if any(k in text for k in irl_keywords):
            delta += 0.35
            c.moment_type = c.moment_type if c.moment_type != "general" else "irl_event"
        nearby_audio = _events_in_range(audio_events, c.start, c.end)
        if nearby_audio:
            delta += min(0.7, 0.18 * len(nearby_audio))
            c.reason += f" / irl_audio_events={len(nearby_audio)}"
        nearby_ocr = [x for x in ocr_items if c.start <= float(x.get("time", 0)) <= c.end]
        if nearby_ocr:
            kind_bonus = 0.45 if any(x.get("type") in ("donation", "chat") for x in nearby_ocr) else 0.25
            delta += min(0.9, kind_bonus + 0.1 * len(nearby_ocr))
            c.ocr_score = round(min(1.5, kind_bonus + 0.1 * len(nearby_ocr)), 2)
            c.reason += f" / OCR={','.join(sorted({x.get('type', 'text') for x in nearby_ocr}))}"
            if any(x.get("type") == "donation" for x in nearby_ocr):
                c.moment_type = "donation"
        if delta:
            c.irl_score = round(float(c.irl_score or 0) + delta, 2)
            c.score = round(max(0, min(10, float(c.score) + delta)), 2)
        if c.hook_potential == "medium" and (c.score >= 8.5 or c.moment_type in {"donation", "conflict", "irl_event"}):
            c.hook_potential = "high"
    write_json(
        project_dir / "irl_pipeline_report.json",
        {"enabled": True, "visual_samples": visual.get("total_samples", 0), "ocr_items": len(ocr_items), "audio_events": len(audio_events)},
    )
    logger.log("IRL pipeline: применил visual/OCR/audio bonuses.")
    return candidates


def hook_first_story_order(project_dir: Path, chosen: list[Candidate], settings: dict[str, Any], logger: JobLogger) -> list[Candidate]:
    """Keep long-form excerpts in source order, including legacy hook-enabled projects."""
    ordered = sorted(chosen, key=lambda x: x.start)
    for item in ordered:
        if item.story_role == "hook":
            item.story_role = "body"
    write_json(project_dir / "story_order.json", {
        "hook_id": None, "order": "chronological",
        "note": "Фрагменты идут по времени исходника, без переноса лучшего момента в начало.",
    })
    return ordered


def apply_scene_proximity_to_candidates(
    candidates: list[Candidate], scene_times: list[float], settings: dict[str, Any], logger: JobLogger
) -> list[Candidate]:
    if not scene_times or settings.get("visual_mode", "Лёгкий") == "Выкл":
        return candidates

    out = []
    for c in candidates:
        inside = sum(1 for t in scene_times if c.start <= t <= c.end)
        near_start = any(abs(t - c.start) <= 2.0 for t in scene_times)
        near_end = any(abs(t - c.end) <= 2.0 for t in scene_times)
        delta = 0.0
        if inside:
            delta += min(0.45, inside * 0.10)
        if near_start or near_end:
            delta += 0.12
        # Don't over-weight visuals for talk streams.
        delta = min(delta, 0.55)
        if delta:
            c.visual_score = round(float(c.visual_score or 0) + delta, 2)
            c.score = round(max(0, min(10, float(c.score) + delta)), 2)
            c.reason += f" / visual_scene={round(delta, 2)}"
        out.append(c)
    logger.log("Visual events: применил scene proximity к кандидатам.")
    return out


def _candidate_local_duplicate_signature(c: Candidate) -> tuple[set[str], str]:
    key = re.sub(
        r"[^а-яa-z0-9]+", " ",
        (c.semantic_topic + " " + c.title + " " + c.reason).lower(),
    ).strip()
    words = set(key.split()[:16])
    template = str(c.template_signature or "").strip().lower()
    return words, template


def _candidate_is_contiguous_sibling(a: Candidate, b: Candidate, *, max_gap: float = 3.0) -> bool:
    """Return True for consecutive micro windows from the same source scene.

    Consecutive windows often repeat the same title/topic because they are two
    parts of one developing event. Removing the middle part as a semantic
    duplicate creates a visible jump cut and destroys setup/payoff continuity.
    """
    if int(a.parent_id or 0) <= 0 or int(a.parent_id or 0) != int(b.parent_id or 0):
        return False
    first, second = (a, b) if float(a.start) <= float(b.start) else (b, a)
    gap = float(second.start) - float(first.end)
    return -0.05 <= gap <= max_gap


def _candidate_is_local_duplicate(
    candidate: Candidate,
    chosen: list[Candidate],
    *,
    word_threshold: float = 0.76,
) -> bool:
    """Deterministic duplicate guard used both before and during target refill.

    Exact template repeats are always duplicates.  Word similarity is a softer
    signal; later refill passes may raise the threshold to admit a distinct
    scene from the same broad topic without reintroducing the same clip.
    """
    words, template = _candidate_local_duplicate_signature(candidate)
    for existing in chosen:
        if _candidate_is_contiguous_sibling(candidate, existing):
            continue
        existing_words, existing_template = _candidate_local_duplicate_signature(existing)
        if template and existing_template and template == existing_template:
            return True
        if words and existing_words:
            overlap_ratio = len(words & existing_words) / max(1, min(len(words), len(existing_words)))
            if overlap_ratio > word_threshold:
                return True
    return False


def local_similarity_filter_keep_target(
    items: list[Candidate], target_sec: float, settings: dict[str, Any], logger: JobLogger
) -> list[Candidate]:
    """Remove obvious local duplicates but do not break target duration too aggressively."""
    if len(items) < 3:
        return items
    target_floor = target_sec * float(settings.get("target_fill_ratio", 0.94))
    kept: list[Candidate] = []
    quality_first = bool(settings.get("quality_first_selection_enabled", True))
    threshold = float(settings.get("local_duplicate_word_threshold", 0.76) or 0.76)
    for c in sorted(items, key=lambda x: candidate_selection_key(x, settings), reverse=True):
        duplicate = _candidate_is_local_duplicate(c, kept, word_threshold=threshold)
        # Quality-first mode never keeps an actual duplicate only to pad time.
        # In legacy fill mode, preserve the historical soft-floor behaviour.
        if duplicate and (quality_first or candidates_total_duration(kept) >= target_floor):
            continue
        kept.append(c)
    out = sorted(kept, key=lambda x: x.start)
    if len(out) != len(items):
        logger.log(f"Local duplicate filter: {len(items)} -> {len(out)}")
    return out


def refill_after_dedup(
    project_dir: Path,
    candidates: list[Candidate],
    chosen: list[Candidate],
    target_sec: float,
    settings: dict[str, Any],
    logger: JobLogger,
    stage: str = "after_dedup",
) -> list[Candidate]:
    """Refill toward the requested duration without undoing deduplication.

    10.15.16 filled *before* the local duplicate filter.  The filter could then
    remove ten or more clips and leave a large shortfall with no second refill.
    10.15.20 filters first, then refills from unused candidates while checking
    duplicates at insertion time.  A second, conservative relaxation pass may
    admit a different high-quality scene from the same broad topic, but exact
    template repeats are still forbidden.
    """
    if not settings.get("refill_after_dedup_enabled", True):
        return chosen

    max_final = effective_max_final_segments(settings, target_sec)
    min_final = max(1, int(settings.get("min_final_segments", 8)))
    target_floor = target_sec * float(settings.get("target_fill_ratio", 0.94))

    before_count = len(chosen)
    before_total = candidates_total_duration(chosen)

    # Crucial ordering: remove duplicates *before* trying to hit target.
    chosen = local_similarity_filter_keep_target(chosen, target_sec, settings, logger)
    total = candidates_total_duration(chosen)
    used_ids = {candidate_identity(c) for c in chosen}
    ranked = sorted(candidates, key=lambda x: candidate_selection_key(x, settings), reverse=True)

    strict_threshold = float(settings.get("local_duplicate_word_threshold", 0.76) or 0.76)
    relaxed_threshold = max(strict_threshold, float(settings.get("refill_duplicate_relaxed_threshold", 0.90) or 0.90))
    quality_first = bool(settings.get("quality_first_selection_enabled", True))
    quality_min_score = float(settings.get("quality_first_min_score", 6.7) or 6.7)
    quality_min_clarity = float(settings.get("quality_first_min_clarity", 0.5) or 0.5)

    # Pass 1 preserves the exact duplicate policy. Pass 2 only runs if there is
    # still a real shortfall and allows strongly distinct scenes sharing topic
    # vocabulary; exact TEMPLATE matches remain blocked by the helper.
    passes = [("strict", strict_threshold)]
    if quality_first:
        passes.append(("quality_relaxed", relaxed_threshold))
        passes.append(("quality_recovery", relaxed_threshold))

    added_by_pass: dict[str, int] = {name: 0 for name, _ in passes}
    for pass_name, duplicate_threshold in passes:
        pass_target = target_floor if pass_name == "quality_recovery" else target_sec
        if total >= pass_target and len(chosen) >= min_final:
            break
        for c in ranked:
            if total >= pass_target and len(chosen) >= min_final:
                break
            if len(chosen) >= max_final:
                break
            identity = candidate_identity(c)
            if identity in used_ids:
                continue
            if pass_name == "quality_recovery":
                if not candidate_is_quality_recovery_selectable(c, settings):
                    continue
            elif not candidate_is_selectable(c, settings):
                continue
            dur = candidate_duration(c)
            if dur <= 0:
                continue
            if settings.get("strict_quality_mode", False) and (
                float(c.score) < float(settings.get("strict_quality_min_score", 7.2))
                or float(c.confidence or 0) < float(settings.get("strict_quality_min_confidence", 6.2))
            ):
                continue
            if settings.get("micro_cut_enabled", True) and dur > float(settings.get("micro_max_seconds", 75)) * 1.7:
                continue
            if candidate_has_overlap(c, chosen):
                continue
            if _candidate_is_local_duplicate(c, chosen, word_threshold=duplicate_threshold):
                continue
            if pass_name == "quality_relaxed":
                # Relax only duplicate vocabulary, never quality. This prevents
                # the duration target from pulling mediocre filler back in.
                if float(c.score or 0.0) < quality_min_score + 0.25:
                    continue
                if float(c.standalone_clarity or 0.0) < max(0.55, quality_min_clarity):
                    continue
            chosen.append(c)
            used_ids.add(identity)
            total += dur
            added_by_pass[pass_name] += 1

    if len(chosen) != before_count or abs(total - before_total) > 0.01:
        logger.log(
            f"Refill {stage}: {before_count} фрагм. {tc(before_total)} -> {len(chosen)} фрагм. {tc(total)} "
            f"(strict +{added_by_pass.get('strict', 0)}, relaxed +{added_by_pass.get('quality_relaxed', 0)})"
        )
    if total < target_floor:
        logger.log(
            f"Refill {stage}: безопасный добор остановлен на {tc(total)}; "
            f"до quality-floor {tc(target_floor)} не хватает {round(target_floor - total, 1)}с — слабые/повторные сцены не добавлены."
        )
    write_json(
        project_dir / f"selection_refill_{re.sub(r'[^a-z0-9_]+', '_', stage.lower())}.json",
        {
            "stage": stage,
            "target_seconds": round(target_sec, 2),
            "quality_floor_seconds": round(target_floor, 2),
            "before_segments": before_count,
            "before_seconds": round(before_total, 2),
            "after_segments": len(chosen),
            "after_seconds": round(total, 2),
            "added_by_pass": added_by_pass,
            "shortfall_seconds": round(max(0.0, target_sec - total), 2),
            "quality_floor_reached": total >= target_floor,
            "updated_at": time.time(),
        },
    )
    return sorted(chosen, key=lambda x: x.start)


def _subtract_occupied_ranges(
    start: float,
    end: float,
    occupied: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Return the parts of ``start..end`` that are not already in the montage."""
    pieces = [(float(start), float(end))]
    for used_start, used_end in sorted(occupied):
        next_pieces: list[tuple[float, float]] = []
        for piece_start, piece_end in pieces:
            if used_end <= piece_start or used_start >= piece_end:
                next_pieces.append((piece_start, piece_end))
                continue
            if used_start > piece_start:
                next_pieces.append((piece_start, min(piece_end, used_start)))
            if used_end < piece_end:
                next_pieces.append((max(piece_start, used_end), piece_end))
        pieces = next_pieces
        if not pieces:
            break
    return [(a, b) for a, b in pieces if b > a]


def refill_with_primary_live_context(
    project_dir: Path,
    block_candidates: list[Candidate],
    chosen: list[Candidate],
    transcript: list[TranscriptSegment],
    duration: float,
    target_sec: float,
    settings: dict[str, Any],
    logger: JobLogger,
    *,
    rejected_ranges: list[dict[str, Any]] | None = None,
) -> tuple[list[Candidate], list[Candidate]]:
    """Reach a long-form target with verified live context, never semantic filler.

    Micro AI deliberately extracts the shortest self-contained highlights.  For
    a 35-minute edit from a 60-minute source that can leave only 5-10 minutes of
    isolated punchlines even though the surrounding live gameplay/conversation
    is useful connective material.  This pass runs only after normal selection,
    dedup and storyline.  It adds non-overlapping context from coarse blocks
    that were classified as primary live and still clear a bounded parent-score
    floor.  Replays, prerecorded inserts, waiting/reconnect/intermission and ads
    are never eligible, regardless of the requested duration.

    Added rows are labelled as context in their title/reason so the Review
    Studio never pretends they were separate viral moments.
    """
    before_seconds = candidates_total_duration(chosen)
    target_sec = max(0.0, min(float(target_sec or 0.0), float(duration or target_sec or 0.0)))
    target_floor = target_sec * float(settings.get("target_fill_ratio", 0.94) or 0.94)
    if (
        not settings.get("longform_context_refill_enabled", True)
        or target_sec <= 0
        or before_seconds + 0.01 >= target_floor
        or not block_candidates
    ):
        return sorted(chosen, key=lambda item: item.start), []

    reject_conf = float(settings.get("non_primary_reject_confidence", 0.72) or 0.72)
    quality_floor = float(settings.get("quality_first_min_score", 6.7) or 6.7)
    parent_floor = max(
        float(settings.get("fill_target_min_score", 5.0) or 5.0),
        min(6.2, quality_floor - 0.8),
    )
    # Context scenes may be longer than a viral micro clip, but remain bounded
    # so the user can still review/remove them comfortably in the timeline.
    max_context = max(
        60.0,
        min(
            120.0,
            float(
                settings.get(
                    "longform_context_max_seconds",
                    max(100.0, float(settings.get("micro_max_seconds", 80) or 80)),
                )
                or 100.0
            ),
        ),
    )
    min_piece = max(3.0, float(settings.get("micro_min_seconds", 12) or 12) * 0.35)

    safe_blocks: list[tuple[Candidate, float, bool]] = []
    rejected_rows: list[dict[str, Any]] = []
    chosen_parent_ids = {int(item.parent_id or 0) for item in chosen if int(item.parent_id or 0) > 0}
    for block in block_candidates:
        cls = str(block.content_class or "unknown").strip().lower()
        confidence = float(block.content_class_confidence or 0.0)
        parent_strength = max(
            float(block.score or 0.0),
            float(block.ai_score or 0.0)
            + max(0.0, float(block.visual_score or 0.0))
            + max(0.0, float(block.audio_score or 0.0))
            + max(0.0, float(block.ocr_score or 0.0)),
        )
        reason = ""
        rejected = str(block.decision or "").lower() in {"remove", "reject", "rejected", "delete", "drop"}
        # 11.2.4 deliberately recovers a coarse false prerecorded verdict when
        # current live interaction or an independently kept Micro scene proves
        # it wrong. Preserve that bounded recovery; an ordinary reject is final.
        verified_override = (int(block.id) in chosen_parent_ids or
                             "transcript live interaction:" in str(block.content_evidence or ""))
        if cls in NON_PRIMARY_CONTENT_CLASSES:
            reason = f"semantic_guard:{cls}:{confidence:.2f}"
        elif cls not in {"primary_live", "live_reaction"}:
            reason = f"unsupported_class:{cls}"
        elif not math.isfinite(confidence) or confidence < reject_conf:
            reason = f"unconfirmed_live:{confidence}"
        elif rejected and not verified_override:
            reason = "explicit_parent_reject"
        elif parent_strength + 0.001 < parent_floor:
            reason = f"parent_score:{parent_strength:.2f}<{parent_floor:.2f}"
        if reason:
            rejected_rows.append({"id": block.id, "reason": reason})
            continue
        safe_blocks.append((block, parent_strength, int(block.id) in chosen_parent_ids))

    # First complete scenes around already selected micro moments, then use the
    # strongest remaining primary-live blocks.  This preserves narrative
    # continuity while still preferring quality globally.
    safe_blocks.sort(key=lambda row: (row[2], row[1], float(row[0].transcript_score or 0.0)), reverse=True)
    occupied = sorted((float(item.start), float(item.end)) for item in chosen)
    vetoes = [(float(row["start"]), float(row["end"])) for row in (rejected_ranges or []) if _metadata_valid_scene(row)]
    occupied.extend(vetoes)
    occupied.sort()
    # Context may bridge a short pause, but cannot fill a long speech gap on the
    # strength of the parent title alone. Word-normalized transcripts are used.
    speech_ranges: list[tuple[float, float]] = []
    for row in sorted(transcript, key=lambda s: s.start):
        if not str(row.text or "").strip() or row.end <= row.start:
            continue
        left, right = max(0., row.start - 1.), min(duration, row.end + 1.)
        if speech_ranges and left - speech_ranges[-1][1] <= 4.:
            speech_ranges[-1] = (speech_ranges[-1][0], max(speech_ranges[-1][1], right))
        else:
            speech_ranges.append((left, right))
    added: list[Candidate] = []
    total = before_seconds
    next_id = 1_000_000

    def range_text(start: float, end: float) -> str:
        return " ".join(
            str(item.text or "").strip()
            for item in transcript
            if float(item.end) > start and float(item.start) < end and str(item.text or "").strip()
        )[:900]

    for block, parent_strength, anchored in safe_blocks:
        if total + 0.01 >= target_sec:
            break
        block_start = max(0.0, float(block.start))
        block_end = min(float(duration or block.end), float(block.end))
        if block_end - block_start < min_piece:
            continue

        chunks: list[tuple[float, float, float]] = []
        cursor = block_start
        centers = [
            (a + b) / 2.0
            for a, b in occupied
            if b > block_start and a < block_end
        ]
        while cursor < block_end - 0.001:
            chunk_end = min(block_end, cursor + max_context)
            center = (cursor + chunk_end) / 2.0
            distance = min((abs(center - item) for item in centers), default=float("inf"))
            chunks.append((cursor, chunk_end, distance))
            cursor = chunk_end
        if anchored:
            chunks.sort(key=lambda item: (item[2], item[0]))

        for chunk_start, chunk_end, _distance in chunks:
            if total + 0.01 >= target_sec:
                break
            available = _subtract_occupied_ranges(chunk_start, chunk_end, occupied)
            # A selected high-confidence micro scene is independent proof that
            # its parent block is current live content.  Requiring Whisper speech
            # again here collapses quiet gameplay streams to only the spoken
            # reactions (for example ~3-4 min from a requested 35 min montage).
            # Keep speech-only refill for unanchored blocks, but allow the full
            # verified parent context around anchored live moments.  Explicit
            # Micro vetoes remain in ``occupied`` and are still never refilled.
            if not anchored:
                available = [
                    (max(a, c), min(b, e))
                    for a, b in available
                    for c, e in speech_ranges
                    if min(b, e) > max(a, c)
                ]
            for piece_start, piece_end in available:
                piece_duration = piece_end - piece_start
                if piece_duration < min_piece:
                    continue
                remaining = target_sec - total
                if remaining < min_piece:
                    break
                if piece_duration > remaining:
                    piece_end = piece_start + remaining
                    piece_duration = remaining
                next_id += 1
                context = Candidate(
                    id=next_id,
                    start=round(piece_start, 3),
                    end=round(piece_end, 3),
                    score=round(max(parent_floor, min(9.2, parent_strength - 0.2)), 2),
                    title=f"{str(block.title or 'Сцена')[:110]} — связующий контекст",
                    reason=(
                        f"longform_context_refill: проверенный {block.content_class}; "
                        f"parent_id={block.id}; parent_score={parent_strength:.2f}"
                    ),
                    text_preview=range_text(piece_start, piece_end),
                    transcript_score=transcript_quality_score(range_text(piece_start, piece_end)),
                    ai_score=float(block.ai_score or block.score or 0.0),
                    confidence=max(0.0, float(block.confidence or 0.0) - 0.2),
                    confidence_reason="Связующий контекст внутри проверенного live-блока",
                    decision="keep",
                    hook_potential="medium" if parent_strength >= 7.5 else "low",
                    moment_type=str(block.moment_type or "general"),
                    standalone_clarity=max(0.55, float(block.standalone_clarity or 0.0)),
                    content_class=str(block.content_class or "primary_live"),
                    content_class_confidence=float(block.content_class_confidence or 0.0),
                    content_evidence=str(block.content_evidence or "coarse live block")[:600],
                    semantic_topic=str(block.semantic_topic or block.title or "")[:160],
                    parent_id=int(block.id),
                    template_signature="",
                )
                added.append(context)
                occupied.append((float(context.start), float(context.end)))
                occupied.sort()
                total += piece_duration
                if total + 0.01 >= target_sec:
                    break

    combined = sorted([*chosen, *added], key=lambda item: item.start)
    report = {
        "version": "11.2.5",
        "enabled": True,
        "target_seconds": round(target_sec, 2),
        "target_floor_seconds": round(target_floor, 2),
        "before_segments": len(chosen),
        "before_seconds": round(before_seconds, 2),
        "safe_blocks": len(safe_blocks),
        "safe_block_ids": [int(row[0].id) for row in safe_blocks],
        "rejected_blocks": rejected_rows,
        "micro_veto_ranges": len(vetoes),
        "speech_ranges": len(speech_ranges),
        "parent_score_floor": round(parent_floor, 2),
        "added_segments": len(added),
        "added_seconds": round(candidates_total_duration(added), 2),
        "after_segments": len(combined),
        "after_seconds": round(candidates_total_duration(combined), 2),
        "target_reached": candidates_total_duration(combined) + 0.01 >= target_floor,
        "note": "Only primary-live context is added; semantic non-primary classes remain forbidden.",
        "updated_at": time.time(),
    }
    write_json(project_dir / "longform_context_refill.json", report)
    if added:
        logger.log(
            f"Long-form context refill: {len(chosen)} фрагм. {tc(before_seconds)} -> "
            f"{len(combined)} фрагм. {tc(candidates_total_duration(combined))}; "
            f"добавлен только primary-live контекст ({len(added)} частей)."
        )
    elif before_seconds < target_floor:
        logger.log(
            "Long-form context refill: безопасных primary-live блоков недостаточно; "
            "semantic guard не ослаблен ради длительности."
        )
    return combined, added

def build_temporal_quality_report(
    project_dir: Path,
    transcript: list[TranscriptSegment],
    block_candidates: list[Candidate],
    final_candidates: list[Candidate],
    chosen: list[Candidate],
    duration: float,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Audit temporal fairness without forcing artificial final uniformity."""
    duration = max(0.0, float(duration or 0.0))
    bucket_seconds = max(300.0, float(settings.get("temporal_fairness_bucket_seconds", 900) or 900))
    bucket_count = max(1, int(math.ceil(duration / bucket_seconds)))
    visual = read_json(project_dir / "visual_scan_report.json", {}) or {}
    ocr = read_json(project_dir / "ocr_scan_report.json", {}) or {}
    visual_samples = list(visual.get("samples") or []) if isinstance(visual, dict) else []
    ocr_items = list(ocr.get("items") or []) if isinstance(ocr, dict) else []

    rows: list[dict[str, Any]] = []
    for idx in range(bucket_count):
        start = idx * bucket_seconds
        end = min(duration, (idx + 1) * bucket_seconds)
        def in_bucket(value: float) -> bool:
            return start <= float(value) < (end if end > start else start + 0.001)
        blocks_here = [c for c in block_candidates if in_bucket(c.start)]
        final_here = [c for c in final_candidates if in_bucket(c.start)]
        chosen_here = [c for c in chosen if in_bucket(c.start)]
        scores = [float(c.score or 0.0) for c in final_here]
        rows.append({
            "index": idx + 1,
            "start": round(start, 2),
            "end": round(end, 2),
            "transcript_segments": sum(1 for t in transcript if t.end > start and t.start < end),
            "visual_samples": sum(1 for x in visual_samples if in_bucket(float(x.get("time", 0) or 0))),
            "ocr_items": sum(1 for x in ocr_items if in_bucket(float(x.get("time", 0) or 0))),
            "block_candidates": len(blocks_here),
            "micro_candidates": len(final_here),
            "selected": len(chosen_here),
            "selected_seconds": round(sum(candidate_duration(c) for c in chosen_here), 2),
            "average_score": round(sum(scores) / len(scores), 2) if scores else None,
            "max_score": round(max(scores), 2) if scores else None,
            "non_primary_candidates": sum(1 for c in final_here if str(c.content_class or "unknown").lower() in NON_PRIMARY_CONTENT_CLASSES),
        })

    primary_starts = [
        float(c.start) for c in block_candidates
        if str(c.content_class or "unknown").lower() in {"primary_live", "live_reaction"}
        and float(c.content_class_confidence or 0.0) >= 0.6
    ]
    selected_total = sum(candidate_duration(c) for c in chosen)
    quarter_seconds = duration / 4 if duration > 0 else 0
    quarters = []
    for q in range(4):
        q_start = q * quarter_seconds
        q_end = duration if q == 3 else (q + 1) * quarter_seconds
        q_items = [c for c in chosen if q_start <= float(c.start) < max(q_start + 0.001, q_end)]
        q_sec = sum(candidate_duration(c) for c in q_items)
        quarters.append({
            "quarter": q + 1, "start": round(q_start, 2), "end": round(q_end, 2),
            "selected_seconds": round(q_sec, 2),
            "selected_share_percent": round(100 * q_sec / selected_total, 2) if selected_total else 0.0,
        })

    report = {
        "version": "11.2.5",
        "duration_seconds": round(duration, 2),
        "bucket_seconds": bucket_seconds,
        "actual_stream_start_estimate": round(min(primary_starts), 2) if primary_starts else None,
        "rows": rows,
        "quarters": quarters,
        "selected_total_seconds": round(selected_total, 2),
        "note": "Temporal fairness is diagnostic/second-pass coverage only; final selection is not forced to equal shares.",
    }
    write_json(project_dir / "temporal_quality_report.json", report)
    return report


def _candidate_rejection_reason(c: Candidate, settings: dict[str, Any], chosen: list[Candidate] | None = None) -> str:
    decision = str(c.decision or "").strip().lower()
    if decision in {"remove", "reject", "rejected", "delete", "drop"}:
        return "model_or_semantic_decision_remove"
    cls = str(c.content_class or "unknown").strip().lower()
    reject_conf = float(settings.get("non_primary_reject_confidence", 0.72) or 0.72)
    if settings.get("semantic_quality_guard_enabled", True) and cls in NON_PRIMARY_CONTENT_CLASSES and float(c.content_class_confidence or 0.0) >= reject_conf:
        return f"semantic_guard:{cls}"
    if settings.get("micro_cut_enabled", True):
        max_duration = float(settings.get("micro_max_seconds", 75) or 75) * 1.7
        if candidate_duration(c) > max_duration:
            return "duration_guard:above_micro_limit"
    if chosen:
        selected_ids = {candidate_identity(item) for item in chosen}
        others = [item for item in chosen if candidate_identity(item) != candidate_identity(c)]
        if candidate_identity(c) not in selected_ids and candidate_has_overlap(c, others):
            return "overlap_with_selected"
        if candidate_identity(c) not in selected_ids and _candidate_is_local_duplicate(c, others, word_threshold=0.90):
            return "duplicate_of_selected"
    min_score = float(settings.get("fill_target_min_score", 5.0) or 5.0)
    if settings.get("quality_first_selection_enabled", True):
        min_score = max(min_score, float(settings.get("quality_first_min_score", 6.7) or 6.7))
        min_conf = float(settings.get("quality_first_min_confidence", 5.6) or 5.6)
        min_clarity = float(settings.get("quality_first_min_clarity", 0.5) or 0.5)
        if float(c.confidence or 0.0) > 0 and float(c.confidence or 0.0) < min_conf:
            if candidate_is_quality_recovery_selectable(c, settings):
                return "quality_recovery_eligible_but_not_selected"
            return "quality_guard:low_confidence"
        if float(c.standalone_clarity or 0.0) < min_clarity:
            return "quality_guard:low_standalone_clarity"
    if float(c.score or 0.0) < min_score:
        if candidate_is_quality_recovery_selectable(c, settings):
            return "quality_recovery_eligible_but_not_selected"
        return "quality_guard:low_score"
    if chosen:
        target_seconds = float(settings.get("target_minutes", 30) or 30) * 60.0
        if candidates_total_duration(chosen) >= target_seconds:
            return "target_duration_reached"
        if len(chosen) >= effective_max_final_segments(settings, target_seconds):
            return "effective_segment_capacity_reached"
    return "eligible_but_not_selected"


def build_candidate_decision_trace(
    project_dir: Path,
    candidates: list[Candidate],
    chosen: list[Candidate],
    duration: float,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Explain why every final-pool candidate did or did not reach montage.

    This report is intentionally deterministic and uses already-computed scores;
    it never asks the LLM again and therefore cannot change selection quality.
    """
    selected_ids = {candidate_identity(c) for c in chosen}
    target_seconds = float(settings.get("target_minutes", 30) or 30) * 60.0
    rows: list[dict[str, Any]] = []
    rejection_counts: dict[str, int] = {}
    for rank, c in enumerate(candidates, start=1):
        selected = candidate_identity(c) in selected_ids
        reason = "selected" if selected else _candidate_rejection_reason(c, settings, chosen)
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
        quarter = min(4, max(1, int((float(c.start) / max(1.0, float(duration or 0.0))) * 4) + 1)) if duration else 1
        rows.append({
            "rank": rank,
            "candidate_id": candidate_identity(c),
            "id": c.id,
            "start": round(float(c.start), 3),
            "end": round(float(c.end), 3),
            "duration_seconds": round(candidate_duration(c), 3),
            "quarter": quarter,
            "selected": selected,
            "selection_reason": reason,
            "title": c.title,
            "semantic_topic": c.semantic_topic,
            "content_class": c.content_class,
            "content_class_confidence": round(float(c.content_class_confidence or 0.0), 3),
            "content_evidence": c.content_evidence,
            "template_signature": c.template_signature,
            "score": round(float(c.score or 0.0), 3),
            "ai_score": round(float(c.ai_score or 0.0), 3),
            "transcript_score": round(float(c.transcript_score or 0.0), 3),
            "visual_score": round(float(c.visual_score or 0.0), 3),
            "ocr_score": round(float(c.ocr_score or 0.0), 3),
            "audio_score": round(float(c.audio_score or 0.0), 3),
            "irl_score": round(float(c.irl_score or 0.0), 3),
            "penalty_score": round(float(c.penalty_score or 0.0), 3),
            "confidence": round(float(c.confidence or 0.0), 3),
            "standalone_clarity": round(float(c.standalone_clarity or 0.0), 3),
            "decision": c.decision,
            "story_role": c.story_role,
            "moment_type": c.moment_type,
            "hook_potential": c.hook_potential,
            "reason": c.reason,
            "why_selected": c.why_selected,
            "ai_explanation": c.ai_explanation,
            "viewer_value": c.viewer_value,
            "risk": c.risk,
        })
    selected_seconds = sum(candidate_duration(c) for c in chosen)
    report = {
        "version": "11.2.5",
        "created_at": time.time(),
        "model": str(settings.get("text_model") or ""),
        "target_seconds": round(target_seconds, 3),
        "selected_seconds": round(selected_seconds, 3),
        "target_shortfall_seconds": round(max(0.0, target_seconds - selected_seconds), 3),
        "candidate_count": len(candidates),
        "selected_count": len(chosen),
        "rejection_counts": rejection_counts,
        "items": rows,
        "note": "Trace explains existing decisions; it never changes ranking or quality thresholds.",
    }
    write_json(project_dir / "candidate_decision_trace.json", report)
    return report


def build_quality_core_report(project_dir: Path, settings: dict[str, Any]) -> dict[str, Any]:
    p = project_paths(project_dir)
    segs = read_json(p["segments"], [])
    cands = read_json(p["candidates"], [])
    audio = read_json(project_dir / "audio_dynamics.json", {})
    report = {
        "target_minutes": settings.get("target_minutes"),
        "segments": len(segs),
        "candidates": len(cands),
        "duration": tc(sum(max(0, float(s.get("end", 0)) - float(s.get("start", 0))) for s in segs)),
        "audio_dynamics": {
            "enabled": bool(audio.get("enabled")),
            "p50": audio.get("p50"),
            "p90": audio.get("p90"),
            "p95": audio.get("p95"),
        },
        "refill_enabled": settings.get("refill_after_dedup_enabled", True),
        "density_enabled": True,
        "cache_version": QUALITY_CORE_VERSION,
    }
    write_json(project_dir / "quality_core_report.json", report)
    return report


# ---------------- v8.6.6 Adaptive Auto Mode helpers ----------------


def clamp_value(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def auto_target_minutes_for_duration(duration_sec: float, content_type: str = "Auto") -> int:
    """Pick a reasonable target duration when user wants Auto."""
    minutes = max(1.0, duration_sec / 60.0)
    ct = (content_type or "Auto").lower()

    if minutes <= 20:
        ratio = 0.35
        target = clamp_value(minutes * ratio, 3, 8)
    elif minutes <= 60:
        ratio = 0.28
        target = clamp_value(minutes * ratio, 5, 15)
    elif minutes <= 120:
        ratio = 0.22
        target = clamp_value(minutes * ratio, 10, 25)
    elif minutes <= 360:
        ratio = 0.16
        target = clamp_value(minutes * ratio, 20, 60)
    elif minutes <= 600:
        ratio = 0.12
        target = clamp_value(minutes * ratio, 30, 80)
    else:
        ratio = 0.10
        target = clamp_value(minutes * ratio, 45, 120)

    # Content-specific adjustments.
    if "лекц" in ct or "lecture" in ct:
        target *= 0.75
    elif "подкаст" in ct or "podcast" in ct or "интерв" in ct:
        target *= 0.90
    elif "short" in ct:
        target = min(target, 10)
    elif "irl" in ct or "ирл" in ct or "улиц" in ct or "travel" in ct or "прогул" in ct:
        target *= 1.05
    elif "игр" in ct or "game" in ct:
        target *= 0.85
    elif "стрим" in ct or "stream" in ct:
        target *= 1.0

    return int(round(clamp_value(target, 2, 120)))


def adaptive_auto_settings(project_dir: Path, current: dict[str, Any]) -> dict[str, Any]:
    """Return recommended settings for any input video length and content type."""
    p = project_paths(project_dir)
    try:
        duration_sec = video_duration(p["video"])
    except Exception:
        duration_sec = 0.0
    duration_min = max(0.0, duration_sec / 60.0)
    content_type = current.get("content_type", "Auto")
    edit_mode = str(current.get("edit_mode", "") or "")
    if content_type == "Auto" and edit_mode.startswith("IRL"):
        content_type = "IRL стрим"
    mode = current.get("auto_mode", "Auto")

    target = current.get("target_minutes")
    if current.get("auto_target_duration", True) or not target:
        target = auto_target_minutes_for_duration(duration_sec, content_type)

    # Base by length.
    if duration_min <= 20:
        rec = {
            "block_seconds": 60,
            "chunk_seconds": 600,
            "micro_window_seconds": 20,
            "micro_min_seconds": 8,
            "micro_max_seconds": 45,
            "min_final_segments": 6,
            "max_final_segments": 40,
            "top_blocks_for_micro": 12,
            "ai_batch_size": 4,
            "visual_mode": "Лёгкий",
        }
        bucket = "short"
    elif duration_min <= 60:
        rec = {
            "block_seconds": 90,
            "chunk_seconds": 900,
            "micro_window_seconds": 25,
            "micro_min_seconds": 10,
            "micro_max_seconds": 50,
            "min_final_segments": 10,
            "max_final_segments": 60,
            "top_blocks_for_micro": 20,
            "ai_batch_size": 4,
            "visual_mode": "Лёгкий",
        }
        bucket = "medium"
    elif duration_min <= 120:
        rec = {
            "block_seconds": 120,
            "chunk_seconds": 900,
            "micro_window_seconds": 30,
            "micro_min_seconds": 12,
            "micro_max_seconds": 60,
            "min_final_segments": 15,
            "max_final_segments": 80,
            "top_blocks_for_micro": 28,
            "ai_batch_size": 3,
            "visual_mode": "Лёгкий",
        }
        bucket = "long"
    elif duration_min <= 360:
        rec = {
            "block_seconds": 180,
            "chunk_seconds": 900,
            "micro_window_seconds": 35,
            "micro_min_seconds": 15,
            "micro_max_seconds": 70,
            "min_final_segments": 25,
            "max_final_segments": 120,
            "top_blocks_for_micro": 45,
            "ai_batch_size": 3,
            "visual_mode": "Лёгкий",
        }
        bucket = "very_long"
    elif duration_min <= 600:
        rec = {
            "block_seconds": 240,
            "chunk_seconds": 900,
            "micro_window_seconds": 45,
            "micro_min_seconds": 18,
            "micro_max_seconds": 80,
            "min_final_segments": 35,
            "max_final_segments": 150,
            "top_blocks_for_micro": 60,
            "ai_batch_size": 3,
            "visual_mode": "Лёгкий",
        }
        bucket = "huge"
    else:
        rec = {
            "block_seconds": 300,
            "chunk_seconds": 1200,
            "micro_window_seconds": 55,
            "micro_min_seconds": 20,
            "micro_max_seconds": 90,
            "min_final_segments": 45,
            "max_final_segments": 180,
            "top_blocks_for_micro": 80,
            "ai_batch_size": 2,
            "visual_mode": "Лёгкий",
        }
        bucket = "massive"

    ct = (content_type or "Auto").lower()

    # Content-type profiles.
    if "irl" in ct or "ирл" in ct or "улиц" in ct or "travel" in ct or "прогул" in ct:
        rec.update(
            {
                "visual_mode": "Лёгкий",
                "micro_window_seconds": max(35, rec["micro_window_seconds"]),
                "micro_min_seconds": max(12, rec["micro_min_seconds"]),
                "micro_max_seconds": max(80, rec["micro_max_seconds"]),
                "top_blocks_for_micro": int(rec["top_blocks_for_micro"] * 1.15),
                "audio_dynamics_enabled": True,
                "storyline_enabled": True,
                "dedup_enabled": True,
                "edit_mode": current.get("edit_mode")
                if current.get("edit_mode") and current.get("edit_mode") != "Сбалансированный"
                else "С историей",
            }
        )
        prompt_profile = "irl"
    elif "лекц" in ct or "lecture" in ct:
        rec.update(
            {
                "visual_mode": "Выкл",
                "micro_window_seconds": max(45, rec["micro_window_seconds"]),
                "micro_min_seconds": max(20, rec["micro_min_seconds"]),
                "micro_max_seconds": max(100, rec["micro_max_seconds"]),
                "dedup_enabled": True,
                "storyline_enabled": True,
                "audio_dynamics_enabled": False,
            }
        )
        prompt_profile = "lecture"
    elif "подкаст" in ct or "podcast" in ct:
        rec.update(
            {
                "visual_mode": "Выкл",
                "micro_window_seconds": max(40, rec["micro_window_seconds"]),
                "micro_min_seconds": max(18, rec["micro_min_seconds"]),
                "micro_max_seconds": max(90, rec["micro_max_seconds"]),
                "audio_dynamics_enabled": True,
            }
        )
        prompt_profile = "podcast"
    elif "интерв" in ct or "interview" in ct:
        rec.update(
            {
                "visual_mode": "Выкл",
                "micro_window_seconds": max(35, rec["micro_window_seconds"]),
                "micro_min_seconds": max(15, rec["micro_min_seconds"]),
                "micro_max_seconds": max(80, rec["micro_max_seconds"]),
                "audio_dynamics_enabled": True,
            }
        )
        prompt_profile = "interview"
    elif "игр" in ct or "game" in ct:
        rec.update(
            {
                "visual_mode": "Лёгкий",
                "micro_window_seconds": min(35, rec["micro_window_seconds"]),
                "micro_min_seconds": min(12, rec["micro_min_seconds"]),
                "micro_max_seconds": min(60, rec["micro_max_seconds"]),
                "audio_dynamics_enabled": True,
            }
        )
        prompt_profile = "gaming"
    elif "short" in ct:
        rec.update(
            {
                "visual_mode": "Лёгкий",
                "micro_window_seconds": 20,
                "micro_min_seconds": 8,
                "micro_max_seconds": 45,
                "min_final_segments": 5,
                "max_final_segments": 30,
                "audio_dynamics_enabled": True,
            }
        )
        prompt_profile = "shorts"
    elif "реакц" in ct or "reaction" in ct:
        rec.update(
            {
                "visual_mode": "Лёгкий",
                "micro_window_seconds": min(30, rec["micro_window_seconds"]),
                "micro_min_seconds": min(12, rec["micro_min_seconds"]),
                "micro_max_seconds": min(60, rec["micro_max_seconds"]),
                "audio_dynamics_enabled": True,
            }
        )
        prompt_profile = "reaction"
    else:
        # Stream/Auto/Vlog general.
        rec.update(
            {
                "audio_dynamics_enabled": True,
            }
        )
        prompt_profile = "stream"

    # Speed/quality mode.
    if mode == "Быстро":
        rec["visual_mode"] = "Выкл" if duration_min > 240 else rec["visual_mode"]
        rec["ai_batch_size"] = min(4, rec["ai_batch_size"])
        rec["top_blocks_for_micro"] = max(10, int(rec["top_blocks_for_micro"] * 0.75))
        rec["ollama_timeout"] = max(int(current.get("ollama_timeout", 900)), 900)
    elif mode == "Качество":
        rec["top_blocks_for_micro"] = int(rec["top_blocks_for_micro"] * 1.25)
        rec["max_final_segments"] = int(rec["max_final_segments"] * 1.15)
        rec["ollama_timeout"] = max(int(current.get("ollama_timeout", 900)), 1200)
        if duration_min <= 120 and prompt_profile in ("gaming", "reaction", "stream", "irl"):
            rec["visual_mode"] = "Средний"
    else:
        rec["ollama_timeout"] = max(int(current.get("ollama_timeout", 900)), 900)

    # Always useful for quality core.
    rec.update(
        {
            "target_minutes": int(target),
            "micro_cut_enabled": True,
            "refill_after_dedup_enabled": True,
            "target_fill_ratio": 0.94,
            "dedup_enabled": True,
            "storyline_enabled": bool(duration_min <= 360),  # for massive videos it can be too slow
            "remove_silence": False,
            "generate_metadata": False,
            "make_srt": False,
            "auto_mode": mode,
            "content_type": content_type,
            "auto_target_duration": bool(current.get("auto_target_duration", True)),
        }
    )

    warnings = []
    if target > duration_min * 0.55 and duration_min > 15:
        warnings.append("Целевая длительность слишком близка к исходнику: может остаться вода.")
    if target < max(2, duration_min * 0.04) and duration_min > 60:
        warnings.append("Целевая длительность очень маленькая: можно потерять важный контекст.")
    if duration_min >= 360 and rec.get("visual_mode") in ("Средний", "Полный"):
        warnings.append("Для 6+ часов лучше Visual mode Лёгкий, иначе анализ может быть очень долгим.")
    if duration_min >= 360 and rec.get("storyline_enabled"):
        warnings.append("Для 6+ часов Storyline может быть медленным; в Auto обычно выключается для 6+ часов.")

    explain = {
        "duration_sec": duration_sec,
        "duration_min": round(duration_min, 2),
        "bucket": bucket,
        "content_type": content_type,
        "auto_mode": mode,
        "target_minutes": int(target),
        "prompt_profile": prompt_profile,
        "warnings": warnings,
        "recommended": rec,
    }
    write_json(project_dir / "adaptive_recommendation.json", explain)
    return explain


def apply_adaptive_settings(project_dir: Path, current: dict[str, Any]) -> dict[str, Any]:
    rec = adaptive_auto_settings(project_dir, current)
    project_file = project_dir / "project.json"
    project = read_json(project_file, {})
    settings = project.get("settings", {})
    settings.update(rec["recommended"])
    project["settings"] = settings
    write_json(project_file, project)
    write_json(project_dir / "adaptive_recommendation.json", rec)
    return {"settings": settings, "recommendation": rec}


def visual_quality_report(project_dir: Path, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """A lightweight report explaining how visual analysis contributed."""
    settings = settings or read_json(project_dir / "project.json", {}).get("settings", {})
    scene_file = project_dir / "scene_changes.json"
    micro_file = project_dir / "micro_candidates.json"
    block_file = project_dir / "block_candidates.json"
    cands_file = project_dir / "candidates.json"
    scene_changes = read_json(scene_file, [])
    micro = read_json(micro_file, [])
    blocks = read_json(block_file, [])
    cands = read_json(cands_file, [])

    # Count vision failures from logs.
    logs = ""
    try:
        logs = (project_dir / "logs.txt").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass
    vision_failures = logs.count("Vision failed")

    rec = []
    visual_mode = settings.get("visual_mode", "Лёгкий")
    if visual_mode == "Выкл":
        rec.append("Visual mode выключен: выбор идёт почти полностью по тексту.")
    elif visual_mode == "Лёгкий":
        rec.append("Visual mode Лёгкий: учитываются резкие смены кадра без тяжёлого vision-AI. Это лучший режим для длинных стримов.")
    else:
        rec.append("Visual mode Средний/Полный: приложение пробует анализировать кадры через vision-модель, но это может быть медленно.")
    if vision_failures:
        rec.append(f"Vision-модель {vision_failures} раз вернула невалидный JSON. Это не ломает анализ, но визуальный вклад был частичным.")
    if not scene_changes and visual_mode != "Выкл":
        rec.append("Scene changes не найдены или не сохранены. Для статичного разговорного стрима это может быть нормально.")
    if len(micro) >= 30:
        rec.append(f"Micro-cut дал {len(micro)} микро-кандидатов — это хороший признак для динамичной нарезки.")
    elif micro:
        rec.append(f"Micro-cut дал только {len(micro)} микро-кандидатов. Можно увеличить Top blocks или уменьшить Micro window.")
    elif cands:
        rec.append("Micro-candidates не найдены, но обычные кандидаты есть. Проверь настройку Micro-cut.")

    report = {
        "visual_mode": visual_mode,
        "scene_changes": len(scene_changes) if isinstance(scene_changes, list) else 0,
        "vision_failures": vision_failures,
        "micro_candidates": len(micro) if isinstance(micro, list) else 0,
        "block_candidates": len(blocks) if isinstance(blocks, list) else 0,
        "candidates": len(cands) if isinstance(cands, list) else 0,
        "audio_dynamics": read_json(project_dir / "audio_dynamics.json", {}),
        "quality_core": read_json(project_dir / "quality_core_report.json", {}),
        "recommendations": rec,
    }
    write_json(project_dir / "visual_quality_report.json", report)
    return report


def preflight(project_dir: Path, settings: dict[str, Any]) -> dict[str, Any]:
    p = project_paths(project_dir)
    video = p["video"]
    checks = []

    def add(name: str, status: str, details: str = ""):
        checks.append({"name": name, "status": status, "details": details})

    ffmpeg = which("ffmpeg")
    ffprobe = which("ffprobe")
    add("FFmpeg", "OK" if ffmpeg else "FAIL", str(ffmpeg))
    add("FFprobe", "OK" if ffprobe else "FAIL", str(ffprobe))

    if video.exists():
        try:
            dur = video_duration(video)
            add("Видео читается", "OK", tc(dur))
        except Exception as exc:
            add("Видео читается", "FAIL", str(exc))
        streams = audio_streams(video)
        add("Аудиодорожки", "OK" if streams else "FAIL", f"найдено: {len(streams)}")
        free = shutil.disk_usage(project_dir).free / (1024**3)
        add("Свободное место", "OK" if free > 5 else "WARN", f"{free:.1f} GB")
    else:
        add("Видео", "FAIL", "Исходное видео не найдено. Если это Fast Import, проверь что файл не перемещён.")

    ai = make_ai_client(settings, cancel_file=cancel_path(project_dir))
    text_model = effective_text_model(settings)
    vision_model = effective_vision_model(settings)
    visual_mode = settings.get("visual_mode", "Лёгкий")
    check = ai.check(text_model, vision_model)
    strict_ai = strict_ai_enabled(settings)
    # Local AI is an enrichment layer in normal mode, not a prerequisite for
    # preserving hours of transcript/visual/audio work.  Strict/full-AI mode
    # keeps fail-closed semantics, while the normal desktop workflow may
    # continue through the deterministic degraded path and clearly records the
    # reduced AI coverage in ai_coverage_report.json / analysis_health.json.
    ai_failure_status = "FAIL" if strict_ai else "WARN"
    add("AI Engine", "OK" if check.get("ok") else ai_failure_status, json.dumps(check, ensure_ascii=False)[:700])
    if check.get("ok"):
        add("Text model", "OK" if check.get("text_model_installed") else ai_failure_status, text_model)
        if visual_mode in ("Средний", "Полный"):
            add("Vision model", "OK" if check.get("vision_model_installed") else ai_failure_status, vision_model)
        else:
            add("Vision model", "OK" if check.get("vision_model_installed") else "WARN", f"{vision_model}; нужен только для Средний/Полный")

    write_json(project_dir / "preflight.json", {"checks": checks})
    return {"checks": checks, "ok": not any(c["status"] == "FAIL" for c in checks)}


def extract_audio(video: Path, out_wav: Path, logger: JobLogger):
    ffmpeg = which("ffmpeg") or "ffmpeg"
    logger.heartbeat("extract_audio", 5, "Извлекаю аудио")
    cmd = [ffmpeg, "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(out_wav)]
    r = run_cmd(cmd, project_dir=out_wav.parent, cancel_file=cancel_path(out_wav.parent))
    if r.returncode != 0:
        raise RuntimeError(r.stdout)


def _validated_transcript_cache(data: Any, *, start: float = 0., end: float | None = None) -> list[TranscriptSegment] | None:
    """Reject a broken chunk as a unit so it can be recomputed from source.

    [] is a valid silent chunk. None, a malformed row, nonfinite timestamps or
    a wrong JSON root is not silence and must never count as completed work.
    """
    if not isinstance(data, list):
        return None
    result = []
    seen = set()
    for row in data:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            return None
        try:
            left, right = float(row["start"]), float(row["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if not (math.isfinite(left) and math.isfinite(right) and start <= left < right):
            return None
        if end is not None and right > end + .5:
            return None
        raw_words = row.get("words") or []
        if not isinstance(raw_words, list):
            return None
        words = []
        for word in raw_words:
            if not isinstance(word, dict) or not isinstance(word.get("word"), str):
                return None
            try:
                a, b = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError, OverflowError):
                return None
            if not (math.isfinite(a) and math.isfinite(b) and a <= b):
                return None
            words.append({"start": a, "end": b, "word": word["word"]})
        text = row["text"].strip()
        identity = (left, right, text)
        if text and identity not in seen:
            seen.add(identity)
            result.append(TranscriptSegment(left, right, text, words))
    return sorted(result, key=lambda row: (row.start, row.end))


def transcribe(project_dir: Path, settings: dict[str, Any], logger: JobLogger) -> list[TranscriptSegment]:
    p = project_paths(project_dir)
    fp = transcript_fingerprint(project_dir, settings)
    manifest_path = project_dir / "transcript_manifest.json"
    manifest = read_json(manifest_path, {}) or {}
    manifest = manifest if isinstance(manifest, dict) else {}
    cached = read_json(p["transcript"], None)
    manifest_fp = manifest.get("fingerprint")
    legacy_fp = transcript_fingerprint_legacy(project_dir, settings)
    cached_segments = _validated_transcript_cache(cached)
    if cached is not None and cached_segments is None:
        logger.log("Кэш транскрипта повреждён: восстанавливаю по проверенным chunks и исходнику.")
    if cached_segments is not None and manifest_fp in {fp, legacy_fp}:
        cached_segments, timing_report = normalize_transcript_timing(cached_segments)
        write_json(project_dir / "transcript_timing_report.json", timing_report)
        if manifest.get("timing_normalization_version") != TRANSCRIPT_TIMING_NORMALIZATION_VERSION:
            write_json(p["transcript"], [asdict(x) for x in cached_segments])
            p["transcript_txt"].write_text(
                "\n".join(f"[{tc(s.start)}-{tc(s.end)}] {s.text}" for s in cached_segments),
                encoding="utf-8",
            )
            manifest["timing_normalization_version"] = TRANSCRIPT_TIMING_NORMALIZATION_VERSION
            manifest["timing_normalization"] = timing_report
            write_json(manifest_path, manifest)
            logger.log(
                "Whisper timing repair: кэш нормализован без повторной транскрибации; "
                f"split={timing_report['split_input_segments']}, removed_silence={timing_report['removed_silence_seconds']}s"
            )
        if manifest_fp == legacy_fp and manifest_fp != fp:
            migrated = dict(manifest)
            migrated["fingerprint"] = fp
            migrated["content_signature"] = video_content_signature(project_dir)
            migrated["migrated_from_legacy_fingerprint_at"] = time.time()
            write_json(manifest_path, migrated)
            logger.log("Использую кэш транскрипта: legacy fingerprint проверен и мигрирован без повторной транскрибации")
        else:
            logger.log("Использую кэш транскрипта: fingerprint совпал")
        return cached_segments
    if cached:
        logger.log("Кэш транскрипта устарел: исходник или настройки Whisper изменились, использую новую generation chunks")

    from .whisper_worker import WhisperProcess

    audio = project_dir / "audio_16k.wav"
    extract_audio(p["video"], audio, logger)

    model_name = str(settings.get("whisper_model") or "base")
    requested_device = str(settings.get("whisper_device") or "auto").strip().lower()
    requested_compute = str(settings.get("whisper_compute") or "auto").strip().lower()
    language = settings.get("language", "ru")
    chunk_len = max(1, int(settings.get("chunk_seconds", 900)))
    capabilities = detect_hardware_capabilities()
    recommended = capabilities.get("recommended_settings") or {}
    effective_device = str(recommended.get("whisper_device") or "cpu") if requested_device == "auto" else requested_device
    if effective_device not in {"cpu", "cuda"}:
        effective_device = "cpu"
    if requested_compute == "auto":
        effective_compute = str(recommended.get("whisper_compute") or ("int8" if effective_device == "cpu" else "float16"))
    else:
        effective_compute = requested_compute

    ctranslate2_info = capabilities.get("ctranslate2") or {}
    supported_cuda = set(str(x) for x in (ctranslate2_info.get("compute_types") or []))
    if effective_device == "cuda" and not ctranslate2_info.get("cuda_ok"):
        logger.log(
            "Whisper GPU запрошен, но CTranslate2 CUDA не готов: "
            + str(ctranslate2_info.get("hint") or "CUDA device unavailable")
            + ". Использую CPU/int8."
        )
        effective_device, effective_compute = "cpu", "int8"
    elif effective_device == "cuda" and supported_cuda and effective_compute not in supported_cuda:
        replacement = str(recommended.get("whisper_compute") or "float16")
        logger.log(f"Whisper compute={effective_compute} не поддерживается текущим CUDA runtime; использую {replacement}.")
        effective_compute = replacement

    configured_threads = int(settings.get("cpu_worker_limit") or 0)
    logical_threads = max(1, int((capabilities.get("cpu") or {}).get("logical_threads") or os.cpu_count() or 1))
    cpu_threads = configured_threads if configured_threads > 0 else max(1, min(8, logical_threads // 2 or 1))
    runtime_path = project_dir / "whisper_runtime.json"

    def write_runtime(status: str, *, error: str = "") -> None:
        write_json(
            runtime_path,
            {
                "status": status,
                "requested_device": requested_device,
                "requested_compute": requested_compute,
                "effective_device": effective_device,
                "effective_compute": effective_compute,
                "model": model_name,
                "cpu_threads": cpu_threads,
                "ctranslate2": {
                    "version": ctranslate2_info.get("version"),
                    "cuda_device_count": ctranslate2_info.get("cuda_device_count"),
                    "compute_types": sorted(supported_cuda),
                    "hint": ctranslate2_info.get("hint"),
                },
                "gpu": ((capabilities.get("nvidia") or {}).get("gpus") or [None])[0],
                "error": error[:3000],
                "updated_at": time.time(),
            },
        )

    workers = []
    progress_base = max(8.0, float(getattr(logger, "_last_progress", 8.0)))
    progress_state = {"stage": "load_whisper", "progress": progress_base,
                      "message": f"Загружаю Whisper {model_name}"}

    def pulse():
        logger.heartbeat(progress_state["stage"], progress_state["progress"],
                         progress_state["message"], eta_seconds=None)

    def load_model(device: str, compute: str):
        progress_state.update(stage="load_whisper", message=f"Загружаю Whisper {model_name} · {device}/{compute}")
        pulse()
        worker = WhisperProcess(
            model_name, device=device, compute_type=compute, num_workers=1,
            cpu_threads=cpu_threads, cancel_check=lambda: check_cancel(project_dir), heartbeat=pulse,
            stall_timeout=300 if device == "cuda" else 900,
        )
        workers.append(worker)
        return worker

    try:
        pulse()
        try:
            model = load_model(effective_device, effective_compute)
            write_runtime("ready")
            logger.log(f"Whisper effective runtime: {effective_device}/{effective_compute}; CPU threads={cpu_threads}")
        except OperationCancelled:
            raise
        except Exception as exc:
            original_error = f"{type(exc).__name__}: {exc}"
            if effective_device != "cuda":
                write_runtime("failed", error=original_error)
                raise RuntimeError(f"Whisper не удалось запустить на {effective_device}/{effective_compute}: {original_error}") from exc
            logger.log(
                "Whisper CUDA initialization failed: " + original_error + ". Переключаюсь на CPU/int8, чтобы анализ продолжился."
            )
            effective_device, effective_compute = "cpu", "int8"
            try:
                model = load_model(effective_device, effective_compute)
            except OperationCancelled:
                raise
            except Exception as cpu_exc:
                write_runtime("failed", error=original_error + f"; CPU fallback failed: {type(cpu_exc).__name__}: {cpu_exc}")
                raise RuntimeError(
                    "Whisper не запустился ни на CUDA, ни на CPU. CUDA: " + original_error + "; CPU: " + str(cpu_exc)
                ) from cpu_exc
            write_runtime("cpu_fallback", error=original_error)

        progress_state.update(stage="transcription", message="Whisper загружен. Подготавливаю распознавание речи")
        pulse()
        duration = video_duration(audio)
        chunks_root = project_dir / "transcript_chunks"
        chunks_dir = chunks_root / fp
        chunks_dir.mkdir(parents=True, exist_ok=True)
        write_json(chunks_dir / "generation_manifest.json", {
            "fingerprint": fp, "video": video_signature(project_dir),
            "settings": settings_subset(settings, ["whisper_model", "whisper_device", "whisper_compute", "language", "chunk_seconds"]),
            "effective_whisper": {"device": effective_device, "compute_type": effective_compute, "cpu_threads": cpu_threads},
            "created_at": time.time(),
        })
        result: list[TranscriptSegment] = []
        num_chunks = max(1, math.ceil(duration / chunk_len))

        ffmpeg = which("ffmpeg") or "ffmpeg"
        for i in range(num_chunks):
            check_cancel(project_dir)
            start = i * chunk_len
            end = min(duration, start + chunk_len)
            cache_path = chunks_dir / f"transcript_{i + 1:04d}.json"
            chunk = _validated_transcript_cache(read_json(cache_path, None), start=start, end=end)
            if chunk is not None:
                chunk, chunk_timing = normalize_transcript_timing(chunk)
                if chunk_timing.get("split_input_segments") or chunk_timing.get("unresolved_long_inputs"):
                    write_json(cache_path, [asdict(x) for x in chunk])
                result.extend(chunk)
                logger.set_step(
                    "transcription", i + 1, num_chunks, None, f"Транскрибация chunk {i + 1}/{num_chunks}: кэш", global_start=progress_base, global_end=40
                )
                continue
            if cache_path.exists():
                logger.log(f"Whisper chunk {i + 1}: повреждённый кэш, повторное распознавание только этого интервала.")

            progress_state.update(stage="transcription", progress=progress_base + (40 - progress_base) * i / num_chunks,
                                  message=f"Подготавливаю аудиоблок {i + 1}/{num_chunks}")
            pulse()
            chunk_wav = chunks_dir / f"chunk_{i + 1:04d}.wav"
            rr = run_cmd(
                [ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(audio), "-t", f"{end - start:.3f}", "-ac", "1", "-ar", "16000", str(chunk_wav)],
                project_dir=project_dir,
                cancel_file=cancel_path(project_dir),
            )
            if rr.returncode != 0:
                raise RuntimeError(rr.stdout)

            def transcribe_chunk(active_model) -> list[TranscriptSegment]:
                # Heavy CUDA inference shares the same process-wide lease as Ollama
                # jobs. This prevents a background metadata request from fighting a
                # 4 GB card while faster-whisper is actively transcribing a chunk.
                wait_started = last_wait_pulse = time.monotonic()
                def check_gpu_wait():
                    nonlocal last_wait_pulse
                    now = time.monotonic()
                    if is_cancelled(project_dir):
                        return True
                    if now - wait_started > 300:
                        raise TimeoutError("GPU занят более 300 с; продолжаю распознавание на CPU")
                    if now - last_wait_pulse >= 5:
                        pulse()
                        last_wait_pulse = now
                    return False
                lease = AIResourceManager.lease(
                    "gpu_heavy",
                    capacity=max(1, int(settings.get("gpu_job_limit", 1) or 1)),
                    cancel_check=check_gpu_wait,
                ) if effective_device == "cuda" else None
                progress_state.update(stage="transcription", message=f"Блок {i + 1}/{num_chunks}: ожидание GPU" if lease else f"Распознаю блок {i + 1}/{num_chunks} на CPU")
                pulse()
                if lease is not None:
                    lease.__enter__()
                try:
                    progress_state.update(stage="transcription", message=f"Распознаю блок {i + 1}/{num_chunks} · {effective_device}/{effective_compute}")
                    pulse()
                    last_segment_pulse = time.monotonic()
                    segs_iter, _info = active_model.transcribe(
                        str(chunk_wav),
                        beam_size=1,
                        vad_filter=True,
                        word_timestamps=True,
                        language=None if language == "auto" else language,
                    )
                    local_segments: list[TranscriptSegment] = []
                    for seg in segs_iter:
                        check_cancel(project_dir)
                        processed = max(0.0, min(end - start, float(seg.end)))
                        progress_state.update(progress=progress_base + (40 - progress_base) * (start + processed) / max(duration, 1),
                                              message=f"Распознаю блок {i + 1}/{num_chunks}: {tc(processed)} из {tc(end - start)} · {effective_device}")
                        if time.monotonic() - last_segment_pulse >= 2:
                            pulse()
                            last_segment_pulse = time.monotonic()
                        txt = (seg.text or "").strip()
                        if not txt:
                            continue
                        words: list[dict[str, Any]] = []
                        for raw_word in getattr(seg, "words", None) or []:
                            word_text = str(getattr(raw_word, "word", "") or "").strip()
                            if not word_text:
                                continue
                            words.append(
                                {
                                    "start": round(start + float(getattr(raw_word, "start", seg.start)), 3),
                                    "end": round(start + float(getattr(raw_word, "end", seg.end)), 3),
                                    "word": word_text,
                                }
                            )
                        local_segments.append(
                            TranscriptSegment(
                                round(start + float(seg.start), 3),
                                round(start + float(seg.end), 3),
                                txt,
                                words,
                            )
                        )
                    return local_segments
                finally:
                    if lease is not None:
                        lease.__exit__(None, None, None)

            try:
                chunk_segments = transcribe_chunk(model)
            except OperationCancelled:
                raise
            except Exception as exc:
                if effective_device != "cuda":
                    write_runtime("failed", error=f"{type(exc).__name__}: {exc}")
                    raise
                gpu_error = f"{type(exc).__name__}: {exc}"
                logger.log(
                    f"Whisper CUDA runtime failed on chunk {i + 1}: {gpu_error}. Перезапускаю этот chunk на CPU/int8."
                )
                try:
                    model.close()
                except OperationCancelled:
                    raise
                except Exception:
                    pass
                effective_device, effective_compute = "cpu", "int8"
                model = load_model(effective_device, effective_compute)
                write_runtime("cpu_fallback", error=gpu_error)
                chunk_segments = transcribe_chunk(model)

            validated_chunk = _validated_transcript_cache([asdict(row) for row in chunk_segments], start=start, end=end)
            if validated_chunk is None:
                raise RuntimeError(f"Whisper chunk {i + 1}: некорректные timestamps. Chunk не сохранён; возможен повторный запуск.")
            chunk_segments, _chunk_timing = normalize_transcript_timing(validated_chunk)
            write_json(cache_path, [asdict(x) for x in chunk_segments])
            result.extend(chunk_segments)
            logger.set_step(
                "transcription",
                i + 1,
                num_chunks,
                None,
                f"Транскрибация chunk {i + 1}/{num_chunks}: {effective_device}/{effective_compute}, сегментов {len(chunk_segments)}",
                global_start=progress_base,
                global_end=40,
            )

        result, timing_report = normalize_transcript_timing(result)
        write_json(p["transcript"], [asdict(x) for x in result])
        write_json(project_dir / "transcript_timing_report.json", timing_report)
        write_json(
            manifest_path,
            {
                "fingerprint": fp,
                "version": QUALITY_CORE_VERSION,
                "segments": len(result),
                "settings": settings_subset(settings, ["whisper_model", "whisper_device", "whisper_compute", "language", "chunk_seconds"]),
                "effective_whisper": {"device": effective_device, "compute_type": effective_compute, "cpu_threads": cpu_threads},
                "timing_normalization_version": TRANSCRIPT_TIMING_NORMALIZATION_VERSION,
                "timing_normalization": timing_report,
                "video": video_signature(project_dir),
                "content_signature": video_content_signature(project_dir),
                "created_at": time.time(),
            },
        )
        p["transcript_txt"].write_text("\n".join(f"[{tc(s.start)}-{tc(s.end)}] {s.text}" for s in result), encoding="utf-8")
        # CTranslate2 releases its model allocation when the Python object is
        # destroyed.  Do this before Ollama/vision stages so low-VRAM GPUs (for
        # example 4 GB cards) do not keep two inference models resident at once.
        try:
            model.close()
        except OperationCancelled:
            raise
        except Exception:
            pass
        logger.log("Whisper model released before AI/visual stages to reduce GPU VRAM contention.")
        return result
    finally:
        for worker in workers:
            worker.close()


def build_blocks(segments: list[TranscriptSegment], block_seconds: int) -> list[dict[str, Any]]:
    if not segments:
        return []
    end = max(s.end for s in segments)
    blocks = []
    cursor = 0.0
    idx = 1
    while cursor < end:
        b_end = min(end, cursor + block_seconds)
        text = " ".join(s.text for s in segments if s.end > cursor and s.start < b_end).strip()
        if text:
            blocks.append({"id": idx, "start": cursor, "end": b_end, "text": text})
            idx += 1
        cursor = b_end
    return blocks


def scene_detection(project_dir: Path, logger: JobLogger) -> list[float]:
    p = project_paths(project_dir)
    threshold = 0.35
    fp = scene_detection_fingerprint(project_dir, threshold)
    cache_path = project_dir / "scene_times.json"
    manifest_path = project_dir / "scene_times_manifest.json"
    manifest = read_json(manifest_path, {}) or {}
    cached = read_json(cache_path, None)
    manifest_fp = manifest.get("fingerprint")
    legacy_fp = scene_detection_fingerprint_legacy(project_dir, threshold)
    if isinstance(cached, list) and manifest_fp in {fp, legacy_fp}:
        if manifest_fp == legacy_fp and manifest_fp != fp:
            migrated = dict(manifest)
            migrated["fingerprint"] = fp
            migrated["content_signature"] = video_content_signature(project_dir)
            migrated["migrated_from_legacy_fingerprint_at"] = time.time()
            write_json(manifest_path, migrated)
        logger.log(f"Scene detection: кэш fingerprint OK ({len(cached)} смен кадра)")
        return [float(x) for x in cached]
    ffmpeg = which("ffmpeg") or "ffmpeg"
    logger.heartbeat("scene_detection", 65, "Scene detection")
    r = run_cmd(
        [ffmpeg, "-i", str(p["video"]), "-filter:v", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
        project_dir=project_dir,
        cancel_file=cancel_path(project_dir),
    )
    if r.returncode != 0:
        raise RuntimeError(f"Scene detection FFmpeg failed ({r.returncode}): {(r.stdout or '')[-1200:]}")
    times = []
    for m in re.finditer(r"pts_time:([0-9.]+)", r.stdout or ""):
        try:
            times.append(float(m.group(1)))
        except Exception:
            pass
    times = sorted(set(round(x, 2) for x in times))
    write_json(cache_path, times)
    write_json(
        manifest_path,
        {
            "fingerprint": fp,
            "version": QUALITY_CORE_VERSION,
            "threshold": threshold,
            "count": len(times),
            "video": video_signature(project_dir),
            "content_signature": video_content_signature(project_dir),
            "created_at": time.time(),
        },
    )
    return times


def _normalize_content_class(value: Any) -> str:
    raw = str(value or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "live": "primary_live",
        "primary": "primary_live",
        "primary_stream": "primary_live",
        "live_primary": "primary_live",
        "reaction": "live_reaction",
        "reaction_live": "live_reaction",
        "technical": "intermission",
        "technical_pause": "intermission",
        "offline": "waiting",
        "best_of": "replay",
        "old_highlights": "replay",
        "promo": "advertisement",
        "ad": "advertisement",
    }
    return aliases.get(raw, raw if raw in PRIMARY_CONTENT_CLASSES | NON_PRIMARY_CONTENT_CLASSES else "unknown")


def _content_signature(text: str, cls: str) -> str:
    words = re.findall(r"[a-zа-яё0-9]+", (text or "").lower())
    stop = {"и", "в", "на", "с", "что", "это", "the", "a", "to", "of", "я", "мы", "он", "она"}
    compact = [w for w in words if len(w) >= 3 and w not in stop][:10]
    return f"{cls}:" + "_".join(compact)


def detect_live_interaction_evidence(text: str) -> dict[str, Any]:
    """Find transcript evidence that a coarse block is current live content.

    A local LLM can mistake captured gameplay for a ``prerecorded`` insert or
    controller trouble for a stream ``reconnect``.  Direct interaction with the
    audience and present-tense gameplay are stronger evidence for a coarse
    four-minute block.  This helper never overrides explicit replay/waiting text
    or repeated OCR; it is only used to reconcile an otherwise AI-only verdict.
    """
    low = re.sub(r"\s+", " ", str(text or "").lower())
    signal_patterns: list[tuple[str, tuple[str, ...]]] = [
        ("audience", ("чат", "зрител", "ребят", "пацан", "всем привет")),
        ("stream", ("стрим", "эфир", "рейд", "модер", "донат", "подпис")),
        ("live_request", ("скинул", "скинула", "кинул", "таймер", "алиса, сколько", "в чат")),
        ("gameplay", ("контроллер", "джойстик", "уровень", "карта", "играю", "пройду", "управление")),
        ("present_reaction", ("что происходит", "что это", "сейчас", "почему", "получается")),
    ]
    signals = [name for name, needles in signal_patterns if any(needle in low for needle in needles)]
    # Audience/stream interaction is decisive on its own when accompanied by
    # another present-tense cue. Gameplay needs at least one additional signal
    # so a voiced trailer or an old match recording is not automatically live.
    strong = len(signals) >= 2 and (
        any(name in signals for name in {"audience", "stream", "live_request"})
        or ("gameplay" in signals and "present_reaction" in signals)
    )
    return {
        "strong": strong,
        "signals": signals,
        "confidence": round(min(0.92, 0.72 + 0.05 * len(signals)), 3) if strong else 0.0,
    }


def detect_stream_content_class(
    text: str,
    *,
    ocr_texts: list[str] | tuple[str, ...] | None = None,
    ai_class: str = "unknown",
    ai_confidence: float = 0.0,
) -> dict[str, Any]:
    """Classify whether a moment belongs to the actual stream.

    Text-only evidence is intentionally conservative. OCR evidence is stronger:
    a big on-screen ``ПРОПАЛ ИНТЕРНЕТ / ПОДКЛЮЧАЮСЬ`` banner is a property of
    the whole scene, so laughter/chat inside the embedded replay must not turn
    it into primary live content.
    """
    source_text = str(text or "")
    ocr_join = " ".join(str(x or "") for x in (ocr_texts or []) if str(x or "").strip())
    low = source_text.lower()
    ocr_low = ocr_join.lower()
    combined = f"{low} {ocr_low}".strip()

    patterns: list[tuple[str, tuple[str, ...]]] = [
        ("reconnect", ("пропал интернет", "нет интернета", "подключаюсь", "переподключ", "reconnect", "connection lost", "соединение потер")),
        ("waiting", ("starting soon", "скоро нач", "стрим скоро", "ожидание", "ожидайте", "скоро верн", "be right back")),
        ("intermission", ("технический перерыв", "тех перерыв", "перерыв", "intermission", "technical break", "технические неполад")),
        ("replay", ("лучшие моменты", "лучшие момен", "best moments", "best of", "повтор стрима", "повтор трансляции", "replay", "прошлого стрима", "старого стрима", "нарезка стрима")),
        ("prerecorded", ("записанное видео", "запись эфира", "предзапис", "pre-recorded", "prerecorded", "запись стрима")),
        ("advertisement", ("рекламная пауза", "advertisement", "ad break", "рекламный блок")),
    ]

    # OCR is the strongest deterministic scene-level signal.
    for cls, needles in patterns:
        hits = [needle for needle in needles if needle in ocr_low]
        if hits:
            return {
                "class": cls,
                "confidence": 0.98 if cls in {"reconnect", "waiting", "intermission"} else 0.92,
                "evidence": "OCR: " + ", ".join(hits[:3]),
                "signature": _content_signature(ocr_join, cls),
                "source": "ocr",
            }

    normalized_ai = _normalize_content_class(ai_class)
    ai_conf = max(0.0, min(1.0, float(ai_confidence or 0.0)))

    # A confident model verdict that this is an active live reaction is allowed:
    # reacting to a video can be the actual stream. Strong technical OCR above
    # still overrides this exception.
    if normalized_ai == "live_reaction" and ai_conf >= 0.68:
        return {
            "class": normalized_ai,
            "confidence": ai_conf,
            "evidence": "AI: active live reaction",
            "signature": "",
            "source": "ai",
        }

    for cls, needles in patterns:
        hits = [needle for needle in needles if needle in combined]
        if hits:
            # Transcript/title evidence alone is useful but less decisive than
            # an on-screen label because a streamer may merely mention a past
            # disconnect/replay while already back live.
            confidence = 0.82 if normalized_ai == cls else 0.66
            if cls in {"reconnect", "waiting", "intermission"} and normalized_ai in NON_PRIMARY_CONTENT_CLASSES:
                confidence = max(confidence, ai_conf, 0.82)
            return {
                "class": cls,
                "confidence": confidence,
                "evidence": "text: " + ", ".join(hits[:3]),
                "signature": _content_signature(source_text, cls),
                "source": "text",
            }

    if normalized_ai != "unknown":
        return {
            "class": normalized_ai,
            "confidence": ai_conf,
            "evidence": f"AI: {normalized_ai}",
            "signature": "",
            "source": "ai",
        }
    return {"class": "unknown", "confidence": 0.0, "evidence": "", "signature": "", "source": "none"}


def water_penalty(text: str) -> float:
    """Penalty for technical/waiting/replay material.

    v10.15.8 reduced this penalty whenever the same block contained laughter or
    a donation. That made an embedded old-highlight reel on a reconnect screen
    look valuable. In v10.15.14 the scene-level technical meaning wins.
    """
    detected = detect_stream_content_class(text)
    penalties = {
        "reconnect": 4.8,
        "waiting": 4.4,
        "intermission": 4.2,
        "replay": 3.4,
        "prerecorded": 3.1,
        "advertisement": 3.4,
    }
    if detected["class"] in penalties:
        return penalties[detected["class"]]
    low = str(text or "").lower()
    words = ["подключ", "микрофон", "камера", "ссылка", "не слыш", "звук", "ожид", "перезайд", "настрой", "скинь"]
    return round(min(4.0, sum(0.8 for w in words if w in low)), 2)


def apply_stream_content_guard(
    project_dir: Path,
    candidates: list[Candidate],
    settings: dict[str, Any],
    logger: JobLogger,
    *,
    stage: str,
) -> list[Candidate]:
    """Attach stream-content semantics and reject technical/replay inserts.

    This pass does not reduce temporal coverage. It changes *what* a sampled
    scene means, not how many scenes are sampled.
    """
    if not settings.get("semantic_quality_guard_enabled", True) or not candidates:
        return candidates

    ocr_items: list[dict[str, Any]] = []
    if settings.get("ocr_enabled", True):
        try:
            ocr_report = ocr_scan_video(project_dir, settings, logger)
            ocr_items = list(ocr_report.get("items") or []) if isinstance(ocr_report, dict) else []
        except OperationCancelled:
            raise
        except Exception as exc:
            logger.log(f"Semantic content guard: OCR evidence unavailable: {exc}")

    reject_conf = float(settings.get("non_primary_reject_confidence", 0.72) or 0.72)
    caps = {"reconnect": 2.2, "waiting": 2.4, "intermission": 2.5, "replay": 3.6, "prerecorded": 3.8, "advertisement": 3.2}
    counts: dict[str, int] = {}
    report_rows: list[dict[str, Any]] = []
    for c in candidates:
        nearby_items = [
            item for item in ocr_items
            if float(c.start) - 4.0 <= float(item.get("time", -1) or -1) <= float(c.end) + 4.0
        ]
        nearby = [str(item.get("text") or "") for item in nearby_items if str(item.get("text") or "").strip()]

        # Coarse block candidates can span several minutes. A single chat line
        # or a 10-second disconnect inside a 4-minute otherwise-good block must
        # not veto the whole block. For the pre-micro coarse stage, scene-level
        # OCR has to repeat across samples before it becomes a hard signal. The
        # later micro guard operates on short windows and can be decisive.
        ocr_for_detection = nearby
        coarse_ocr = str(stage).startswith("block_") and len(nearby) > 1
        if coarse_ocr:
            per_sample = [detect_stream_content_class("", ocr_texts=[text]) for text in nearby]
            non_primary = [x for x in per_sample if str(x.get("class") or "unknown") in NON_PRIMARY_CONTENT_CLASSES]
            by_class: dict[str, int] = {}
            for x in non_primary:
                key = str(x.get("class") or "unknown")
                by_class[key] = by_class.get(key, 0) + 1
            dominant_count = max(by_class.values(), default=0)
            dominance = dominant_count / max(1, len(nearby))
            if dominant_count < 2 or dominance < 0.25:
                ocr_for_detection = []

        # Deterministic text rules must inspect the transcript, not the AI title
        # or reason.  Feeding the model's own word "reconnect" back into the
        # rule engine made an unsupported classification look independently
        # confirmed and hard-deleted normal gameplay.
        detected = detect_stream_content_class(
            c.text_preview,
            ocr_texts=ocr_for_detection,
            ai_class=c.content_class,
            ai_confidence=c.content_class_confidence,
        )
        cls = str(detected.get("class") or "unknown")
        confidence = float(detected.get("confidence") or 0.0)

        # At coarse block level an AI-only non-primary verdict is provisional.
        # Strong transcript interaction promotes it back to live content.  If
        # there is no live evidence, keep the non-primary label but below the
        # hard-veto threshold so Micro AI/OCR can verify the shorter scenes.
        # Explicit text/OCR evidence remains untouched and can still veto.
        if (
            str(stage).startswith("block_")
            and str(detected.get("source") or "") == "ai"
            and cls in NON_PRIMARY_CONTENT_CLASSES
        ):
            live_evidence = detect_live_interaction_evidence(c.text_preview)
            if live_evidence.get("strong"):
                detected = {
                    "class": "primary_live",
                    "confidence": float(live_evidence.get("confidence") or 0.78),
                    "evidence": "transcript live interaction: " + ", ".join(live_evidence.get("signals") or []),
                    "signature": "",
                    "source": "transcript_live_override",
                }
            else:
                detected = {
                    **detected,
                    "confidence": min(confidence, max(0.0, reject_conf - 0.03)),
                    "evidence": str(detected.get("evidence") or "AI non-primary")
                    + "; coarse AI-only verdict awaits Micro/OCR confirmation",
                    "source": "ai_provisional",
                }
        cls = str(detected.get("class") or "unknown")
        confidence = float(detected.get("confidence") or 0.0)
        c.content_class = cls
        c.content_class_confidence = round(confidence, 3)
        c.content_evidence = str(detected.get("evidence") or c.content_evidence or "")[:600]
        c.template_signature = str(detected.get("signature") or c.template_signature or "")[:240]
        counts[cls] = counts.get(cls, 0) + 1

        if cls in NON_PRIMARY_CONTENT_CLASSES:
            # Strong non-primary evidence is a semantic veto, not a soft bonus
            # contest. A funny clip inside a reconnect overlay is still a
            # reconnect overlay from the viewer's perspective.
            if confidence >= reject_conf:
                old_score = float(c.score or 0.0)
                c.score = round(min(old_score, caps.get(cls, 3.5)), 2)
                delta = max(0.0, old_score - float(c.score))
                c.penalty_score = round(float(c.penalty_score or 0.0) + delta, 2)
                c.decision = "remove"
                c.hook_potential = "low"
                c.reason += f" / semantic_guard={cls}:{confidence:.2f}"
            else:
                c.score = round(max(0.0, float(c.score or 0.0) - 1.25), 2)
                c.penalty_score = round(float(c.penalty_score or 0.0) + 1.25, 2)
                c.reason += f" / semantic_warning={cls}:{confidence:.2f}"

        report_rows.append(
            {
                "id": c.id,
                "start": round(float(c.start), 3),
                "end": round(float(c.end), 3),
                "class": cls,
                "confidence": round(confidence, 3),
                "evidence": c.content_evidence,
                "template_signature": c.template_signature,
                "ocr_samples_nearby": len(nearby),
                "ocr_samples_used_for_classification": len(ocr_for_detection),
                "score": c.score,
                "decision": c.decision,
            }
        )

    write_json(
        project_dir / f"stream_content_guard_{stage}.json",
        {"stage": stage, "counts": counts, "reject_confidence": reject_conf, "items": report_rows, "created_at": time.time()},
    )
    logger.log(f"Semantic content guard ({stage}): " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return candidates



def build_block_ai_degraded_items(batch_items: list[dict[str, Any]], reason: str = "AI unavailable") -> dict[int, dict[str, Any]]:
    """Create conservative transcript-only block scores when local AI is unavailable.

    Normal One-click mode should preserve the expensive transcript/visual work if
    Ollama becomes unhealthy mid-run.  These placeholders intentionally stay
    below a strong AI verdict: only transcript-rich blocks reach the quality
    floor, and later semantic/visual/audio guards can still demote them.  Strict
    AI modes never use this fallback.
    """
    out: dict[int, dict[str, Any]] = {}
    safe_reason = re.sub(r"\s+", " ", str(reason or "AI unavailable")).strip()[:260]
    for block in batch_items:
        bid = int(block["id"])
        text = str(block.get("text") or "")
        tq = transcript_quality_score(text)
        # 5.2..6.7: conservative enough not to imitate a strong LLM score, but
        # rich transcript blocks can still survive into Micro AI / visual/audio
        # refinement instead of disappearing from the project entirely.
        score = round(max(5.2, min(6.7, 5.2 + tq * 0.15)), 2)
        clarity = round(max(0.5, min(0.65, 0.5 + tq * 0.015)), 2)
        out[bid] = {
            "id": bid,
            "score": score,
            "decision": "maybe",
            "title": f"Фрагмент {bid}",
            "reason": f"Block AI degraded fallback: {safe_reason}",
            "hook_potential": "low",
            "context_before_seconds": 0,
            "context_after_seconds": 0,
            "moment_type": "general",
            "standalone_clarity": clarity,
            "content_class": "unknown",
            "content_class_confidence": 0.0,
            "semantic_topic": "",
            "_degraded_fallback": True,
        }
    return out


def _block_ai_item_fingerprint(
    project_dir: Path,
    settings: dict[str, Any],
    model: str,
    prompt_base: str,
    block: dict[str, Any],
) -> str:
    """Stable per-block identity independent of batch planning.

    A previous release cached only whole batches.  If prompt budgeting changed
    36 batches into 37, all following cache files shifted and hours of valid AI
    work became unusable.  Per-item cache survives batch-size / batch-boundary
    changes while still invalidating when the source, model, prompt or block
    content changes.
    """
    content_sig = video_content_signature(project_dir)
    return stable_hash(
        {
            "version": QUALITY_CORE_VERSION,
            "kind": "block_ai_item_v1",
            "video": content_sig,
            "model": model,
            "prompt_base": prompt_base,
            "ollama_num_ctx": settings.get("ollama_num_ctx"),
            "ollama_think": bool(settings.get("ollama_think", False)),
            "block_seconds": settings.get("block_seconds"),
            "language": settings.get("language"),
            "block": {
                "id": block.get("id"),
                "start": block.get("start"),
                "end": block.get("end"),
                "text_hash": stable_hash(block.get("text", "")),
            },
        }
    )


def _load_block_ai_item_cache(
    cache_dir: Path,
    project_dir: Path,
    settings: dict[str, Any],
    model: str,
    prompt_base: str,
    block: dict[str, Any],
) -> dict[str, Any] | None:
    bid = int(block["id"])
    fp = _block_ai_item_fingerprint(project_dir, settings, model, prompt_base, block)
    data = read_json(cache_dir / f"block_{bid:05d}.json", None)
    if not isinstance(data, dict) or data.get("fingerprint") != fp:
        return None
    item = data.get("item")
    if not isinstance(item, dict):
        return None
    try:
        score = float(item.get("score"))
    except Exception:
        return None
    if math.isnan(score) or math.isinf(score):
        return None
    out = dict(item)
    out["id"] = bid
    return out


def _save_block_ai_item_cache(
    cache_dir: Path,
    project_dir: Path,
    settings: dict[str, Any],
    model: str,
    prompt_base: str,
    block: dict[str, Any],
    item: dict[str, Any],
) -> None:
    """Persist only genuine AI output; degraded fallbacks never poison AI cache."""
    if not isinstance(item, dict) or item.get("_degraded_fallback"):
        return
    bid = int(block["id"])
    fp = _block_ai_item_fingerprint(project_dir, settings, model, prompt_base, block)
    clean = dict(item)
    clean["id"] = bid
    write_json(cache_dir / f"block_{bid:05d}.json", {"fingerprint": fp, "item": clean, "updated_at": time.time()})


def build_micro_ai_degraded_items(batch_items: list[dict[str, Any]], reason: str = "AI unavailable") -> dict[int, dict[str, Any]]:
    """Conservative micro-cut fallback used only in non-strict mode.

    It preserves dynamic windows when Ollama dies after expensive transcript /
    Visual / OCR work.  Scores are anchored to the already AI-scored parent and
    intentionally slightly reduced, so fallback windows cannot masquerade as a
    fresh strong Micro-AI verdict.
    """
    out: dict[int, dict[str, Any]] = {}
    safe_reason = re.sub(r"\s+", " ", str(reason or "AI unavailable")).strip()[:260]
    for local_id, window in enumerate(batch_items, start=1):
        parent_score = float(window.get("parent_score", 0) or 0)
        tq = transcript_quality_score(str(window.get("text") or ""))
        parent_cls = _normalize_content_class(window.get("parent_content_class"))
        try:
            parent_conf = max(0.0, min(1.0, float(window.get("parent_content_confidence", 0.0) or 0.0)))
        except Exception:
            parent_conf = 0.0
        # Transcript richness can restore at most 0.25, while the mandatory
        # degradation penalty remains >=0.35 versus the parent score.
        bonus = min(0.25, max(0.0, (tq - 5.0) * 0.04))
        score = round(max(0.0, min(9.0, parent_score - 0.60 + bonus)), 2)
        blocked = parent_cls in NON_PRIMARY_CONTENT_CLASSES and parent_conf >= 0.72
        keep = bool((score >= 6.7) and not blocked)
        out[local_id] = {
            "id": local_id,
            "score": score,
            "decision": "keep" if keep else "remove",
            "title": f"{str(window.get('parent_title') or 'Момент')[:90]} — часть",
            "reason": f"Micro AI degraded fallback: {safe_reason}",
            "keep": keep,
            "hook_potential": "medium" if score >= 7.5 else "low",
            "moment_type": "general",
            "standalone_clarity": 0.58 if keep else 0.5,
            "content_class": parent_cls,
            "content_class_confidence": parent_conf,
            "semantic_topic": str(window.get("parent_semantic_topic") or "")[:160],
            "extra_start_seconds": 0,
            "extra_end_seconds": 0,
            "_degraded_fallback": True,
        }
    return out


def _micro_ai_item_fingerprint(
    project_dir: Path,
    settings: dict[str, Any],
    model: str,
    window: dict[str, Any],
) -> str:
    content_sig = video_content_signature(project_dir)
    return stable_hash(
        {
            "version": MICRO_AI_POLICY_VERSION,
            "kind": "micro_ai_item_v1",
            "video": content_sig,
            "model": model,
            "ollama_num_ctx": settings.get("ollama_num_ctx"),
            "ollama_think": bool(settings.get("ollama_think", False)),
            "micro_window_seconds": settings.get("micro_window_seconds"),
            "micro_min_seconds": settings.get("micro_min_seconds"),
            "micro_max_seconds": settings.get("micro_max_seconds"),
            "window": {
                "start": window.get("start"),
                "end": window.get("end"),
                "parent_id": window.get("parent_id"),
                "parent_score": window.get("parent_score"),
                "text_hash": stable_hash(window.get("text", "")),
            },
        }
    )


def _load_micro_ai_item_cache(
    cache_dir: Path,
    project_dir: Path,
    settings: dict[str, Any],
    model: str,
    window: dict[str, Any],
    local_id: int,
) -> dict[str, Any] | None:
    fp = _micro_ai_item_fingerprint(project_dir, settings, model, window)
    data = read_json(cache_dir / f"item_{fp}.json", None)
    if not isinstance(data, dict) or data.get("fingerprint") != fp:
        return None
    item = data.get("item")
    if not isinstance(item, dict):
        return None
    try:
        score = float(item.get("score"))
    except Exception:
        return None
    if math.isnan(score) or math.isinf(score):
        return None
    out = dict(item)
    out["id"] = int(local_id)
    return out


def _save_micro_ai_item_cache(
    cache_dir: Path,
    project_dir: Path,
    settings: dict[str, Any],
    model: str,
    window: dict[str, Any],
    item: dict[str, Any],
) -> None:
    if not isinstance(item, dict) or item.get("_degraded_fallback"):
        return
    fp = _micro_ai_item_fingerprint(project_dir, settings, model, window)
    clean = dict(item)
    # local IDs are batch-relative and must not be persisted as identity.
    clean.pop("id", None)
    write_json(cache_dir / f"item_{fp}.json", {"fingerprint": fp, "item": clean, "updated_at": time.time()})

def analyze(project_dir: Path, settings: dict[str, Any], logger: JobLogger) -> dict[str, Any]:
    p = project_paths(project_dir)
    pre = preflight(project_dir, settings)
    if not pre["ok"]:
        failed = [
            f"{item.get('name')}: {item.get('details') or 'FAIL'}"
            for item in (pre.get("checks") or [])
            if str(item.get("status") or "").upper() == "FAIL"
        ]
        raise RuntimeError("Preflight не пройден: " + "; ".join(failed or ["source/runtime unavailable"]))

    segments = transcribe(project_dir, settings, logger)
    blocks = build_blocks(segments, int(settings.get("block_seconds", 180)))
    logger.heartbeat("prepare_blocks", 42, f"Блоков для AI: {len(blocks)}")

    ai = make_ai_client(settings, cancel_file=cancel_path(project_dir))
    text_model = effective_text_model(settings)
    vision_model = effective_vision_model(settings)
    ollama_timeout = int(settings.get("ollama_timeout", 600))
    prompt_base = (
        settings.get("prompt", "") + content_type_prompt(settings) + edit_mode_prompt(settings) + user_preferences_prompt(project_dir)
    )

    candidates: list[Candidate] = []
    batch_size = max(1, min(12, int(settings.get("ai_batch_size", 4))))
    strict_ai = strict_ai_enabled(settings)
    complete_ai = ai_batch_completeness_required(settings)
    # Transport stalls/connection errors are retried inside AIExecutionController.
    # These outer budgets are only for semantic recovery (malformed/incomplete
    # JSON) plus targeted repair/rescue, so transient sockets are not multiplied
    # into another N x 900s loop.
    retry_budget = ai_retry_count(settings)
    semantic_retries = max(1, min(2, retry_budget))
    repair_retries = max(1, min(2, retry_budget))
    rescue_retries = max(1, min(3, retry_budget))
    successful_batches = 0
    ai_analyzed_block_ids: set[int] = set()
    ai_failed_batches: list[dict[str, Any]] = []
    ai_cache_dir = project_dir / "ai_batches"
    ai_cache_dir.mkdir(exist_ok=True)

    def build_block_ai_prompt(batch_items: list[dict[str, Any]]) -> str:
        block_text = "\n\n".join(
            f"ID={b['id']}\nTIME={tc(b['start'])}-{tc(b['end'])}\nTEXT={b['text'][:3500]}"
            for b in batch_items
        )
        return f"""
Ты AI-монтажёр. Оцени блоки видео для хайлайта.

Что считать интересным:
{prompt_base}

Правила:
- Сначала определи, ЧТО зритель реально смотрит: основной live-контент или служебную/старую вставку.
- waiting/reconnect/intermission/replay/prerecorded/advertisement — score 0-3 и decision=remove, даже если внутри вставки есть смешной старый хайлайт, чат, донат или сильная эмоция.
- Исключение: если стример СЕЙЧАС в прямом эфире активно реагирует/комментирует чужое или записанное видео, это live_reaction и может быть хорошим моментом.
- primary_live/live_reaction: смешные реакции, конфликт, донат, чат, эмоции, сильная история — score 8-10, если момент сам по себе содержательный.
- обычный разговор без развития — score 3-6.
- Не оценивай отдельную яркую реплику выше смысла всей сцены: техническая заставка остаётся технической заставкой.
- ОБЯЗАТЕЛЬНО верни по одному элементу blocks для каждого входного ID: {[int(b['id']) for b in batch_items]}.
- Даже если блок нужно удалить, его ID должен присутствовать с decision="remove". Не пропускай и не дублируй ID.

Верни JSON:
{{
 "blocks": [
   {{"id": 1, "score": 8.5, "decision": "keep", "title": "коротко", "reason": "почему", "hook_potential": "low/medium/high", "context_before_seconds": 0, "context_after_seconds": 0, "moment_type": "reaction/conflict/donation/chat/story/visual/irl_event/general", "standalone_clarity": 0.8, "content_class": "primary_live/live_reaction/waiting/reconnect/intermission/replay/prerecorded/advertisement/unknown", "content_class_confidence": 0.9, "semantic_topic": "коротко: о чём именно эта сцена"}}
 ]
}}

Блоки:
{block_text}
""".strip()

    prompt_budget = ai_prompt_char_budget(settings)
    planned_ai_batches = plan_prompt_safe_batches(blocks, batch_size, build_block_ai_prompt, prompt_budget)
    total_ai_batches = max(1, len(planned_ai_batches))
    logger.set_step(
        "block_ai", 0, total_ai_batches, None,
        f"AI: прогреваю {text_model} перед {total_ai_batches} batch",
        global_start=45, global_end=60,
    )
    block_warmup = _ai_warmup_with_recovery(
        ai, text_model, settings, logger, "Block AI", timeout=min(180, max(60, ollama_timeout))
    )
    block_ai_available = bool(block_warmup.get("ok"))
    write_json(
        project_dir / "ollama_block_runtime.json",
        {
            "model": text_model,
            "configured_batch_size": batch_size,
            "planned_batch_sizes": [len(x) for x in planned_ai_batches],
            "prompt_char_budget": prompt_budget,
            "prompt_chars": [len(build_block_ai_prompt(x)) for x in planned_ai_batches],
            "total_blocks": len(blocks),
            "total_batches": total_ai_batches,
            "warmup": block_warmup,
            "completeness_required": complete_ai,
            "updated_at": time.time(),
        },
    )
    if block_warmup.get("ok"):
        logger.log(f"Block AI warm-up: {text_model} готов за {block_warmup.get('elapsed_seconds')}s")
    else:
        warmup_error = str(block_warmup.get("error") or "unknown error")
        if strict_ai:
            raise RuntimeError(
                f"Block AI warm-up не восстановился после {block_warmup.get('max_attempts', 1)} попыток: {warmup_error}"
            )
        logger.log(
            "Block AI недоступен после recovery. Продолжаю в degraded mode: "
            "валидный AI-cache будет использован, а отсутствующие блоки получат консервативный transcript fallback."
        )
    if total_ai_batches != max(1, math.ceil(len(blocks) / batch_size)):
        logger.log(
            f"Block AI: context-safe batching изменил число batch на {total_ai_batches}; "
            f"данные не урезаны, prompt budget={prompt_budget} chars"
        )

    block_item_cache_dir = project_dir / "ai_items"
    block_item_cache_dir.mkdir(exist_ok=True)
    runtime_recovery_used = False

    for batch_i, batch in enumerate(planned_ai_batches):
        check_cancel(project_dir)
        full_prompt = build_block_ai_prompt(batch)
        logger.set_step(
            "block_ai", batch_i, total_ai_batches, None,
            f"AI batch {batch_i + 1}/{total_ai_batches}",
            global_start=45, global_end=60,
        )
        by_id: dict[int, dict[str, Any]] = {}
        last_exc: Exception | None = None
        batch_degraded = False
        cache_path = ai_cache_dir / f"batch_{batch_i + 1:04d}.json"
        cache_fp = ai_batch_fingerprint(project_dir, settings, text_model, prompt_base, batch_size, batch_i, batch)
        cached = read_json(cache_path, None)
        expected_ids = {int(b["id"]) for b in batch}
        block_by_id = {int(b["id"]): b for b in batch}

        update_ai_batch_health(
            project_dir, stage="block_ai", batch=batch_i + 1, total=total_ai_batches, state="running",
            expected_ids=expected_ids, prompt_chars=len(full_prompt),
        )

        # V4 resume cache: recover every block independently of batch planning.
        item_cache_hits = 0
        for b in batch:
            cached_item = _load_block_ai_item_cache(
                block_item_cache_dir, project_dir, settings, text_model, prompt_base, b
            )
            if cached_item is not None:
                by_id[int(b["id"])] = cached_item
                item_cache_hits += 1
        if item_cache_hits:
            ai_analyzed_block_ids.update(set(by_id))
            logger.log(
                f"AI batch {batch_i + 1}: per-block cache {item_cache_hits}/{len(batch)}"
            )

        # Backward compatibility: accept the old whole-batch checkpoint and
        # immediately fan it out into per-block cache for future resumes.
        if len(by_id) < len(expected_ids) and cache_matches(cached, cache_fp, "blocks"):
            cached_items = extract_ai_items(cached, "blocks")
            legacy_by_id = normalize_ai_items_by_id(cached_items, expected_ids)
            for bid, item in legacy_by_id.items():
                if bid not in by_id:
                    by_id[bid] = item
                block_obj = block_by_id.get(bid)
                if block_obj is not None:
                    _save_block_ai_item_cache(
                        block_item_cache_dir, project_dir, settings, text_model, prompt_base, block_obj, item
                    )
            if legacy_by_id:
                ai_analyzed_block_ids.update(set(legacy_by_id))
                logger.log(f"AI batch {batch_i + 1}: legacy batch cache восстановлен")

        pending_ids = sorted(expected_ids - set(by_id))
        pending_batch = [b for b in batch if int(b["id"]) in set(pending_ids)]

        if not pending_ids:
            successful_batches += 1
            logger.log(f"AI batch {batch_i + 1}: cache complete")
        elif not block_ai_available and not strict_ai:
            warmup_error = str(block_warmup.get("error") or "Ollama unavailable")
            last_exc = AITransportError(warmup_error, transient=True)
            fallback = build_block_ai_degraded_items(pending_batch, warmup_error)
            by_id.update(fallback)
            batch_degraded = True
            ai_failed_batches.append(
                {"batch": batch_i + 1, "missing_ids": pending_ids, "error": f"circuit_breaker: {warmup_error}"}
            )
            logger.log(
                f"AI batch {batch_i + 1}/{total_ai_batches}: Ollama circuit breaker; "
                f"fallback только для ID {pending_ids}"
            )
        else:
            if cached and not cache_matches(cached, cache_fp, "blocks"):
                logger.log(f"AI batch {batch_i + 1}: legacy batch cache fingerprint устарел")

            # Query only blocks that are still missing. Successful per-item
            # checkpoints are never sent to Ollama again.
            query_batch = pending_batch
            query_ids = {int(b["id"]) for b in query_batch}
            query_prompt = build_block_ai_prompt(query_batch)

            for attempt in range(1, semantic_retries + 1):
                check_cancel(project_dir)
                try:
                    logger.log(
                        f"AI batch {batch_i + 1}: missing {len(query_batch)}/{len(batch)}, "
                        f"attempt {attempt}/{semantic_retries}, timeout={ollama_timeout}s"
                    )
                    data = ai.generate_json(
                        query_prompt,
                        model=text_model,
                        timeout=ollama_timeout,
                        operation="block_ai",
                        trace_context={
                            "batch": batch_i + 1,
                            "total_batches": total_ai_batches,
                            "expected_ids": sorted(query_ids),
                            "resume_cached_ids": sorted(expected_ids - query_ids),
                        },
                        collection_key="blocks",
                        expected_ids=query_ids,
                        required_item_fields={"id", "score"},
                        activity_callback=ai_activity_callback(
                            logger, stage="block_ai", batch_index=batch_i, total_batches=total_ai_batches,
                            global_start=45, global_end=60, label=f"AI batch {batch_i + 1}/{total_ai_batches}"
                        ),
                    )
                    fresh_items = extract_ai_items(data, "blocks")
                    fresh_by_id = normalize_ai_items_by_id(fresh_items, query_ids)
                    by_id.update(fresh_by_id)
                    for bid, item in fresh_by_id.items():
                        block_obj = block_by_id.get(bid)
                        if block_obj is not None:
                            _save_block_ai_item_cache(
                                block_item_cache_dir, project_dir, settings, text_model, prompt_base, block_obj, item
                            )
                    ai_analyzed_block_ids.update(set(fresh_by_id))

                    missing_ids = sorted(expected_ids - set(by_id))
                    if complete_ai and missing_ids:
                        logger.log(
                            f"AI batch {batch_i + 1}: primary ответ пропустил {missing_ids}; targeted repair"
                        )
                        missing_batch = [b for b in batch if int(b["id"]) in set(missing_ids)]
                        missing_text = "\n\n".join(
                            f"ID={b['id']}\nTIME={tc(b['start'])}-{tc(b['end'])}\nTEXT={b['text'][:3500]}"
                            for b in missing_batch
                        )
                        repair_prompt = f"""
Ты вернул неполный JSON. Нужно обработать только пропущенные ID.
Обязательно верни blocks для КАЖДОГО ID: {missing_ids}.
Не добавляй другие ID.

Формат:
{{"blocks":[{{"id":{missing_ids[0] if missing_ids else 1},"score":0,"decision":"remove","title":"коротко","reason":"почему","hook_potential":"low","context_before_seconds":0,"context_after_seconds":0,"moment_type":"general","standalone_clarity":0.5}}]}}

Критерии:
{prompt_base}

Блоки:
{missing_text}
""".strip()
                        for repair_attempt in range(1, repair_retries + 1):
                            try:
                                repair_data = ai.generate_json(
                                    repair_prompt, model=text_model, timeout=ollama_timeout,
                                    operation="block_ai_missing_ids",
                                    trace_context={"batch": batch_i + 1, "missing_ids": missing_ids},
                                    collection_key="blocks", expected_ids=set(missing_ids),
                                    required_item_fields={"id", "score"},
                                    activity_callback=ai_activity_callback(
                                        logger, stage="block_ai", batch_index=batch_i, total_batches=total_ai_batches,
                                        global_start=45, global_end=60,
                                        label=f"AI repair batch {batch_i + 1}/{total_ai_batches}"
                                    ),
                                )
                                repair_items = extract_ai_items(repair_data, "blocks")
                                repair_by_id = normalize_ai_items_by_id(repair_items, set(missing_ids))
                                by_id.update(repair_by_id)
                                for bid, item in repair_by_id.items():
                                    block_obj = block_by_id.get(bid)
                                    if block_obj is not None:
                                        _save_block_ai_item_cache(
                                            block_item_cache_dir, project_dir, settings, text_model, prompt_base, block_obj, item
                                        )
                                ai_analyzed_block_ids.update(set(repair_by_id))
                                missing_ids = sorted(expected_ids - set(by_id))
                                if not missing_ids:
                                    logger.log(f"AI batch {batch_i + 1}: repair-pass закрыл все ID")
                                    break
                            except OperationCancelled:
                                raise
                            except Exception as repair_exc:
                                last_exc = repair_exc
                                logger.log(
                                    f"AI batch {batch_i + 1}: repair-pass {repair_attempt}/{repair_retries} failed: {repair_exc}"
                                )
                                if isinstance(repair_exc, AITransportError):
                                    break

                    if not (expected_ids - set(by_id)):
                        successful_batches += 1
                    break
                except OperationCancelled:
                    raise
                except Exception as exc:
                    last_exc = exc
                    logger.log(
                        f"AI batch {batch_i + 1}: primary attempt {attempt}/{semantic_retries} failed: {exc}"
                    )
                    # Transport controller has already exhausted its own retry
                    # budget. Semantic retry is only useful for malformed JSON.
                    if isinstance(exc, AITransportError):
                        break
                    if attempt < semantic_retries:
                        time.sleep(min(8, 2 * attempt))

            missing_now = sorted(expected_ids - set(by_id))

            # After a hard timeout/stall, verify/reload Ollama once before tiny
            # rescue requests. This handles the real-world case where a long
            # qwen request leaves the model runtime wedged.
            if missing_now and isinstance(last_exc, AITransportError) and not runtime_recovery_used:
                runtime_recovery_used = True
                recovery = _ai_warmup_with_recovery(
                    ai, text_model, settings, logger, "Block AI runtime recovery",
                    timeout=min(180, max(60, ollama_timeout)), max_attempts=2,
                )
                block_ai_available = bool(recovery.get("ok"))
                if block_ai_available:
                    logger.log(
                        f"AI batch {batch_i + 1}: Ollama runtime восстановлен; запускаю targeted rescue"
                    )
                else:
                    logger.log(
                        f"AI batch {batch_i + 1}: Ollama runtime не восстановился; rescue пропущен"
                    )

            if complete_ai and missing_now and block_ai_available:
                logger.log(f"AI batch {batch_i + 1}: single-ID rescue для {missing_now}")
                for b in batch:
                    bid = int(b["id"])
                    if bid in by_id:
                        continue
                    single_prompt = f"""
Ты AI-монтажёр. Оцени ОДИН блок видео для хайлайта.
Критерии:
{prompt_base}
Верни строго JSON:
{{"blocks":[{{"id":{bid},"score":0,"decision":"remove","title":"коротко","reason":"почему","hook_potential":"low","context_before_seconds":0,"context_after_seconds":0,"moment_type":"general","standalone_clarity":0.5}}]}}
Блок:
ID={bid}
TIME={tc(b["start"])}-{tc(b["end"])}
TEXT={b["text"][:3000]}
""".strip()
                    for rescue_attempt in range(1, rescue_retries + 1):
                        try:
                            rescue_data = ai.generate_json(
                                single_prompt, model=text_model, timeout=ollama_timeout,
                                operation="block_ai_single_id",
                                trace_context={"batch": batch_i + 1, "block_id": bid},
                                collection_key="blocks", expected_ids={bid}, required_item_fields={"id", "score"},
                                activity_callback=ai_activity_callback(
                                    logger, stage="block_ai", batch_index=batch_i, total_batches=total_ai_batches,
                                    global_start=45, global_end=60, label=f"AI rescue ID={bid}"
                                ),
                            )
                            rescue_items = extract_ai_items(rescue_data, "blocks")
                            rescue_by_id = normalize_ai_items_by_id(rescue_items, {bid})
                            if bid in rescue_by_id:
                                item = rescue_by_id[bid]
                                by_id[bid] = item
                                ai_analyzed_block_ids.add(bid)
                                _save_block_ai_item_cache(
                                    block_item_cache_dir, project_dir, settings, text_model, prompt_base, b, item
                                )
                                logger.log(f"AI batch {batch_i + 1}: rescue закрыл ID={bid}")
                                break
                        except OperationCancelled:
                            raise
                        except Exception as rescue_exc:
                            last_exc = rescue_exc
                            logger.log(
                                f"AI batch {batch_i + 1}: rescue ID={bid} "
                                f"attempt {rescue_attempt}/{rescue_retries} failed: {rescue_exc}"
                            )
                            if isinstance(rescue_exc, AITransportError):
                                block_ai_available = False
                                break
                    if not block_ai_available:
                        break

            missing_ids = sorted(expected_ids - set(by_id))
            if missing_ids:
                failure = {
                    "batch": batch_i + 1,
                    "missing_ids": missing_ids,
                    "error": str(last_exc or "Ollama unavailable/incomplete response"),
                }
                ai_failed_batches.append(failure)
                if strict_ai:
                    update_ai_batch_health(
                        project_dir, stage="block_ai", batch=batch_i + 1, total=total_ai_batches, state="failed",
                        expected_ids=expected_ids, actual_ids=set(by_id), error=failure["error"], prompt_chars=len(full_prompt),
                    )
                    update_ai_coverage_report(
                        project_dir, stage="block_ai_failed", strict_ai=True,
                        block_failed_batches=ai_failed_batches,
                    )
                    raise RuntimeError(
                        f"AI batch {batch_i + 1}/{total_ai_batches} не обработан полностью. "
                        f"Последняя ошибка: {last_exc}. Checkpoints сохранены."
                    )

                fallback = build_block_ai_degraded_items(
                    [b for b in batch if int(b["id"]) in set(missing_ids)],
                    str(last_exc or "Ollama unavailable/incomplete response"),
                )
                by_id.update(fallback)
                batch_degraded = True
                if isinstance(last_exc, AITransportError) or not block_ai_available:
                    block_ai_available = False
                    logger.log(
                        "Block AI circuit breaker: следующие uncached ID будут обработаны "
                        "без ожидания зависшего Ollama."
                    )
                logger.log(
                    f"Block AI degraded: batch {batch_i + 1}/{total_ai_batches}; "
                    f"fallback только для ID {missing_ids}. Анализ продолжается."
                )

        # Keep the legacy whole-batch checkpoint for backward compatibility,
        # but only when every item came from real AI/cache. Never persist
        # degraded placeholders as authoritative AI output.
        if by_id and not any(bool((by_id.get(i) or {}).get("_degraded_fallback")) for i in expected_ids):
            ordered_blocks = [by_id[i] for i in sorted(expected_ids)]
            write_json(cache_path, {"fingerprint": cache_fp, "blocks": ordered_blocks})

        update_ai_batch_health(
            project_dir, stage="block_ai", batch=batch_i + 1, total=total_ai_batches,
            state="degraded" if batch_degraded else "done",
            expected_ids=expected_ids, actual_ids=set(by_id),
            error=str(last_exc or "") if batch_degraded else "", prompt_chars=len(full_prompt),
        )
        logger.set_step(
            "block_ai", batch_i + 1, total_ai_batches, None,
            f"AI batch {batch_i + 1}/{total_ai_batches} {'degraded' if batch_degraded else 'готов'}",
            global_start=45, global_end=60,
        )

        for b in batch:
            item = by_id.get(int(b["id"]), {})
            score = float(item.get("score", 0) or 0)
            # Water rules inspect source speech only.  The model title/reason
            # may repeat its own provisional label (for example "reconnect"),
            # which previously created circular evidence and a second penalty.
            pen = water_penalty(b["text"][:1200])
            scene_bonus = 0
            final = max(0, min(10, score - pen + scene_bonus))
            decision = str(item.get("decision") or ("keep" if final >= 7 else "maybe" if final >= 5 else "remove"))
            hook_potential = str(item.get("hook_potential") or ("high" if final >= 8.7 else "medium" if final >= 6.5 else "low"))
            moment_type = str(item.get("moment_type") or "general")
            content_class = _normalize_content_class(item.get("content_class"))
            try:
                content_class_confidence = max(0.0, min(1.0, float(item.get("content_class_confidence", 0.0) or 0.0)))
            except OperationCancelled:
                raise
            except Exception:
                content_class_confidence = 0.0
            semantic_topic = str(item.get("semantic_topic") or item.get("title") or "").strip()[:160]
            try:
                ctx_before = max(0.0, min(20.0, float(item.get("context_before_seconds", 0) or 0)))
                ctx_after = max(0.0, min(20.0, float(item.get("context_after_seconds", 0) or 0)))
                clarity = max(0.0, min(1.0, float(item.get("standalone_clarity", 0.5) or 0.5)))
            except OperationCancelled:
                raise
            except Exception:
                ctx_before = ctx_after = 0.0
                clarity = 0.5
            candidates.append(
                Candidate(
                    b["id"],
                    max(0, b["start"] - ctx_before),
                    b["end"] + ctx_after,
                    round(final, 2),
                    item.get("title", f"Фрагмент {b['id']}"),
                    item.get("reason", "") + f" / penalty={pen}",
                    b["text"][:900],
                    penalty_score=pen,
                    transcript_score=transcript_quality_score(b["text"]),
                    ai_score=round(score, 2),
                    decision=decision,
                    hook_potential=hook_potential,
                    context_before_seconds=ctx_before,
                    context_after_seconds=ctx_after,
                    moment_type=moment_type,
                    standalone_clarity=clarity,
                    content_class=content_class,
                    content_class_confidence=content_class_confidence,
                    semantic_topic=semantic_topic,
                    what_happens=str(item.get("what_happens", "") or ""),
                    why_selected=str(item.get("why_selected", "") or ""),
                    viewer_value=str(item.get("viewer_value", "") or ""),
                    risk=str(item.get("risk", "") or ""),
                )
            )

    update_ai_coverage_report(
        project_dir,
        strict_ai=strict_ai,
        full_ai_coverage=bool(settings.get("full_ai_coverage", False)),
        block_total=len(blocks),
        block_ai_analyzed=len(ai_analyzed_block_ids),
        block_coverage_percent=round(100 * len(ai_analyzed_block_ids) / max(1, len(blocks)), 2),
        block_failed_batches=ai_failed_batches,
    )
    if blocks and successful_batches == 0:
        if strict_ai:
            raise RuntimeError("AI не обработал ни одного batch. Проверь AI Engine, API key/Ollama, модель, timeout или уменьши AI batch size.")
        logger.log(
            "Block AI degraded: Ollama не завершил ни одного полного batch. "
            "Продолжаю с консервативными transcript-кандидатами; Visual/OCR/audio/Micro AI всё ещё могут улучшить ранжирование."
        )
    if complete_ai and len(ai_analyzed_block_ids) < len(blocks):
        if strict_ai:
            raise RuntimeError(
                f"AI анализ неполный: обработано {len(ai_analyzed_block_ids)}/{len(blocks)} крупных блоков. Результат не будет выдан как успешный."
            )
        logger.log(
            f"Block AI coverage degraded: AI обработал {len(ai_analyzed_block_ids)}/{len(blocks)} крупных блоков; "
            "недостающие блоки сохранены как transcript fallback вместо аварийного завершения One-click."
        )

    scene_times = []
    if settings.get("visual_mode", "Лёгкий") != "Выкл":
        check_cancel(project_dir)
        scene_times = scene_detection(project_dir, logger)
        check_cancel(project_dir)
        for c in candidates:
            count = sum(1 for t in scene_times if c.start <= t <= c.end)
            if count:
                c.visual_score = min(2.0, count * 0.3)
                c.score = round(min(10, c.score + c.visual_score), 2)
                c.reason += f" / scene_bonus={c.visual_score}"

    vision_runtime_ready = True
    if settings.get("visual_mode") in ("Средний", "Полный"):
        vision_warmup = _ai_warmup_with_recovery(
            ai, vision_model, settings, logger, "Vision AI",
            timeout=min(180, max(60, ollama_timeout)), max_attempts=2,
        )
        vision_runtime_ready = bool(vision_warmup.get("ok"))
        if not vision_runtime_ready:
            vision_error = str(vision_warmup.get("error") or "unknown error")
            if strict_ai:
                raise RuntimeError(f"AI 100% режим: Vision AI недоступен: {vision_error}")
            logger.log(
                "Vision AI skipped after failed recovery; scene detection/OCR/audio остаются активны: "
                + vision_error
            )

    if settings.get("visual_mode") in ("Средний", "Полный") and vision_runtime_ready:
        frames_root = p["frames"]
        top = sorted(candidates, key=lambda x: x.score, reverse=True)[: min(20, len(candidates))]
        for idx, c in enumerate(top):
            logger.set_step(
                "vision_candidates",
                idx + 1,
                max(1, len(top)),
                None,
                f"Vision анализ кандидатов {idx + 1}/{len(top)}",
                global_start=60,
                global_end=66,
            )
            check_cancel(project_dir)
            frame_dir = frames_root / f"candidate_{c.id:03d}"
            times = [c.start + (c.end - c.start) / 2]
            near_scenes = [t for t in scene_times if c.start <= t <= c.end][:2]
            for t in near_scenes:
                times += [max(c.start, t - 1), t, min(c.end, t + 1)]
            frames = []
            for j, t in enumerate(times[:6]):
                fp = frame_dir / f"frame_{j + 1:02d}.jpg"
                if extract_frame(p["video"], t, fp):
                    frames.append(fp)
            if frames:
                try:
                    data = ai.generate_json_with_images(
                        f"""
Оцени мини-сцену для хайлайта.
Фрагмент: {tc(c.start)}-{tc(c.end)}
Текст: {c.text_preview}

Верни JSON:
{{"visual_score": 0, "event_type": "donation/chat/reaction/meme/gameplay/none", "screen_text": "", "reason": "", "score_delta": 0}}
""",
                        frames,
                        model=vision_model,
                        timeout=ollama_timeout,
                    )
                    delta = max(-2, min(2, float(data.get("score_delta", 0) or 0)))
                    c.visual_score += delta
                    c.score = round(max(0, min(10, c.score + delta)), 2)
                    c.reason += f" / vision={data.get('event_type', '')} {data.get('reason', '')}"
                except OperationCancelled:
                    raise
                except Exception as exc:
                    logger.log(f"Vision failed for {c.id}: {exc}")
                    if strict_ai_enabled(settings):
                        raise RuntimeError(f"AI 100% режим: vision-анализ кандидата {c.id} не выполнен: {exc}")

    # v10.15.14: multimodal evidence must influence the *micro prefilter*, not
    # only candidates that already survived it. This removes the old circular
    # bias where a late VOD moment could be visually strong but never receive
    # Visual/OCR evidence because it was filtered before those stages.
    #
    # OCR/Visual can run for a long time on a two-hour VOD. Release the block-AI
    # Qwen model before that pass on low-memory machines; build_micro_candidates
    # performs a warm-up and reloads the exact same model afterwards. This is
    # performance/memory isolation only and does not change model or scoring.
    if settings.get("visual_scan_enabled", True) or settings.get("ocr_enabled", True):
        release_result = ai.unload(model=text_model, timeout=30)
        write_json(
            project_dir / "ollama_memory_release_pre_micro_visual.json",
            {"stage": "before_pre_micro_visual_ocr", "model": text_model, "result": release_result, "updated_at": time.time()},
        )
        if release_result.get("ok"):
            logger.log(f"Ollama memory: {text_model} выгружена перед pre-micro Visual/OCR")
        else:
            logger.log(f"Ollama pre-micro memory release warning: {release_result.get('error') or 'unknown error'}")
    candidates = apply_stream_content_guard(project_dir, candidates, settings, logger, stage="block_pre_micro")
    if is_irl_settings(settings):
        candidates = apply_irl_pipeline(project_dir, candidates, settings, logger)
        candidates = apply_stream_content_guard(project_dir, candidates, settings, logger, stage="block_after_irl")

    candidates = apply_quality_score_calibration(candidates, settings, logger)
    candidates = update_confidences(candidates)
    candidates = enrich_candidate_explanations(candidates, settings)
    candidates = sorted(candidates, key=lambda x: x.score, reverse=True)
    write_json(project_dir / "block_candidates.json", [asdict(x) for x in candidates])

    candidates_for_final = build_micro_candidates(project_dir, candidates, segments, settings, logger)
    # Never replace a good/previous montage with the tiny partial result of an
    # interrupted Micro AI pass. Per-window checkpoints make the next Analyze
    # action a true continuation instead of a restart.
    enforce_micro_ai_completion(project_dir, settings, logger)
    candidates_for_final = apply_scene_proximity_to_candidates(candidates_for_final, scene_times, settings, logger)
    candidates_for_final = apply_audio_dynamics_to_candidates(project_dir, candidates_for_final, settings, logger)
    # Visual/OCR can run for tens of minutes on long VODs. Release Qwen3 after
    # micro AI so its ~GBs of RAM/VRAM do not compete with FFmpeg/OCR. The exact
    # same model is transparently loaded again by later dedup/storyline AI.
    if settings.get("visual_scan_enabled", True) or settings.get("ocr_enabled", True):
        release_result = ai.unload(model=text_model, timeout=30)
        write_json(project_dir / "ollama_memory_release.json", {"stage": "before_visual_ocr", "model": text_model, "result": release_result, "updated_at": time.time()})
        if release_result.get("ok"):
            logger.log(f"Ollama memory: {text_model} выгружена перед Visual/OCR")
        else:
            logger.log(f"Ollama memory release warning: {release_result.get('error') or 'unknown error'}")
    candidates_for_final = apply_irl_pipeline(project_dir, candidates_for_final, settings, logger)
    candidates_for_final = apply_stream_content_guard(project_dir, candidates_for_final, settings, logger, stage="micro_final")
    candidates_for_final = apply_highlight_density_score(candidates_for_final, settings, logger)
    candidates_for_final = apply_editing_profile_to_candidates(project_dir, candidates_for_final, logger)
    candidates_for_final = update_confidences(candidates_for_final)
    candidates_for_final = enrich_candidate_explanations(candidates_for_final, settings)
    candidates_for_final = sorted(candidates_for_final, key=lambda x: candidate_selection_key(x, settings), reverse=True)
    write_json(p["candidates"], [asdict(x) for x in candidates_for_final])

    target_sec = float(settings.get("target_minutes", 30)) * 60
    chosen = []
    total = 0.0
    configured_max_final_segments = max(1, int(settings.get("max_final_segments", 80) or 80))
    max_final_segments = effective_max_final_segments(settings, target_sec)
    min_final_segments = max(1, int(settings.get("min_final_segments", 8)))
    strict_quality = bool(settings.get("strict_quality_mode", False))
    strict_min_score = float(settings.get("strict_quality_min_score", 7.2))
    strict_min_confidence = float(settings.get("strict_quality_min_confidence", 6.2))
    for c in candidates_for_final:
        if total >= target_sec and len(chosen) >= min_final_segments:
            break
        if len(chosen) >= max_final_segments:
            break
        dur = c.end - c.start
        if dur <= 0:
            continue
        if not candidate_is_selectable(c, settings):
            continue
        if strict_quality and (float(c.score) < strict_min_score or float(c.confidence or 0) < strict_min_confidence):
            continue
        # Avoid a single huge fragment dominating the montage.
        if settings.get("micro_cut_enabled", True) and dur > float(settings.get("micro_max_seconds", 75)) * 1.5:
            continue
        chosen.append(c)
        total += dur

    # Normal mode should finish with a reviewable draft even if the local AI
    # runtime died after expensive transcript/visual/audio work. Only invoke the
    # narrow recovery when the regular quality selector found *nothing* and AI
    # coverage is known to be degraded; normal successful analyses are unchanged.
    if not chosen and ai_failed_batches and not strict_ai:
        chosen = degraded_minimum_selection(candidates_for_final, settings, target_sec, logger)
        total = candidates_total_duration(chosen)

    duration = video_duration(p["video"])
    remember_source_duration(project_dir, duration, p["video"])
    check_cancel(project_dir)
    before_dedup_total = candidates_total_duration(chosen)
    chosen = dedup_pass(project_dir, candidates_for_final, chosen, settings, logger)
    if candidates_total_duration(chosen) < before_dedup_total:
        chosen = refill_after_dedup(project_dir, candidates_for_final, chosen, target_sec, settings, logger, stage="after_dedup")
    check_cancel(project_dir)
    chosen = storyline_pass(project_dir, chosen, segments, duration, settings, logger)
    chosen = resolve_segment_overlaps(chosen, duration, settings, logger)
    chosen = refill_after_dedup(project_dir, candidates_for_final, chosen, target_sec, settings, logger, stage="after_storyline")
    chosen = resolve_segment_overlaps(chosen, duration, settings, logger)
    chosen, context_candidates = refill_with_primary_live_context(
        project_dir,
        candidates,
        chosen,
        segments,
        duration,
        target_sec,
        settings,
        logger,
        rejected_ranges=read_json(project_dir / "micro_rejections.json", []) if settings.get("micro_cut_enabled", True) else [],
    )
    chosen = resolve_segment_overlaps(chosen, duration, settings, logger)
    if context_candidates:
        candidates_for_final = sorted([*candidates_for_final, *context_candidates], key=lambda x: candidate_selection_key(x, settings), reverse=True)
        # The Review Studio must expose every automatically added context piece
        # as a normal removable segment/candidate instead of hiding duration
        # padding from the user.
        write_json(p["candidates"], [asdict(x) for x in candidates_for_final])
    chosen = hook_first_story_order(project_dir, chosen, settings, logger)
    chosen = update_confidences(chosen)
    chosen = enrich_candidate_explanations(chosen, settings)
    temporal_report = build_temporal_quality_report(
        project_dir, segments, candidates, candidates_for_final, chosen, duration, settings
    )
    candidate_trace = build_candidate_decision_trace(project_dir, candidates_for_final, chosen, duration, settings)
    logger.log(
        f"Candidate trace: selected={candidate_trace.get('selected_count', 0)}/{candidate_trace.get('candidate_count', 0)}; "
        f"shortfall={candidate_trace.get('target_shortfall_seconds', 0)}s"
    )
    write_json(
        project_dir / "selection_report.json",
        {
            "strict_quality_mode": bool(settings.get("strict_quality_mode", False)),
            "target_seconds": round(target_sec, 2),
            "selected_seconds": round(candidates_total_duration(chosen), 2),
            "selected_segments": len(chosen),
            "candidate_segments": len(candidates_for_final),
            "configured_max_final_segments": configured_max_final_segments,
            "effective_max_final_segments": max_final_segments,
            "quality_first_selection_enabled": bool(settings.get("quality_first_selection_enabled", True)),
            "semantic_quality_guard_enabled": bool(settings.get("semantic_quality_guard_enabled", True)),
            "non_primary_selected": sum(
                1 for c in chosen
                if str(c.content_class or "unknown").lower() in NON_PRIMARY_CONTENT_CLASSES
                and float(c.content_class_confidence or 0.0) >= float(settings.get("non_primary_reject_confidence", 0.72) or 0.72)
            ),
            "target_shortfall_seconds": round(max(0.0, target_sec - candidates_total_duration(chosen)), 2),
            "target_fill_percent": round(100 * candidates_total_duration(chosen) / max(1.0, target_sec), 2),
            "candidate_pool_seconds": round(candidates_total_duration(candidates_for_final), 2),
            "normal_quality_pool_seconds": round(
                sum(candidate_duration(c) for c in candidates_for_final if candidate_is_selectable(c, settings)), 2
            ),
            "quality_recovery_pool_seconds": round(
                sum(
                    candidate_duration(c)
                    for c in candidates_for_final
                    if candidate_is_quality_recovery_selectable(c, settings)
                ),
                2,
            ),
            "longform_context_segments": len(context_candidates),
            "longform_context_seconds": round(candidates_total_duration(context_candidates), 2),
            "temporal_quarters": temporal_report.get("quarters", []),
            "message": (
                "Quality-first long-form mode: highlights are extended only with labelled primary-live context; non-primary filler remains forbidden"
                if context_candidates
                else "Quality-first mode: target duration is a ceiling; weak/non-primary scenes are not used as filler"
                if settings.get("quality_first_selection_enabled", True)
                else (
                    "Strict quality mode may intentionally return less than target duration"
                    if settings.get("strict_quality_mode", False)
                    else "Fill target mode"
                )
            ),
        },
    )

    coverage = read_json(project_dir / "ai_coverage_report.json", {}) or {}
    block_failures = coverage.get("block_failed_batches") if isinstance(coverage.get("block_failed_batches"), list) else ai_failed_batches
    micro_failures = coverage.get("micro_failed_batches") if isinstance(coverage.get("micro_failed_batches"), list) else []
    degraded_analysis = bool(block_failures or micro_failures)
    selected_seconds = candidates_total_duration(chosen)
    target_floor_seconds = target_sec * float(settings.get("target_fill_ratio", 0.94) or 0.94)
    quality_shortfall = selected_seconds + 0.01 < target_floor_seconds
    analysis_outcome = (
        "complete_with_fallback_shortfall" if degraded_analysis and chosen and quality_shortfall
        else "complete_with_fallback" if degraded_analysis and chosen
        else "complete_no_safe_segments" if degraded_analysis and not chosen
        else "complete_with_quality_shortfall" if quality_shortfall
        else "complete"
    )
    analysis_health = {
        "outcome": analysis_outcome,
        "strict_ai": strict_ai,
        "degraded": degraded_analysis,
        "block_ai_coverage_percent": coverage.get("block_coverage_percent", 100.0),
        "micro_ai_coverage_percent": coverage.get("micro_coverage_percent", 100.0),
        "block_failed_batches": block_failures,
        "micro_failed_batches": micro_failures,
        "selected_segments": len(chosen),
        "selected_seconds": round(selected_seconds, 2),
        "target_seconds": round(target_sec, 2),
        "target_fill_percent": round(100 * selected_seconds / max(1.0, target_sec), 2),
        "quality_shortfall": quality_shortfall,
        "longform_context_segments": len(context_candidates),
        "longform_context_seconds": round(candidates_total_duration(context_candidates), 2),
        "message": (
            "Анализ завершён с безопасным fallback, но качественных сцен недостаточно для заданной длительности."
            if degraded_analysis and chosen and quality_shortfall
            else "Анализ завершён с безопасным fallback для части AI-запросов; готовые AI checkpoints сохранены."
            if degraded_analysis and chosen
            else "Анализ завершён, но безопасных фрагментов для автоматического монтажа не найдено."
            if degraded_analysis
            else (
                f"Анализ завершён, но качественный монтаж короче ориентира: "
                f"{tc(selected_seconds)} из {tc(target_sec)}. Недобор показан явно и не считается нормальным результатом."
            )
            if quality_shortfall
            else (
                f"Анализ завершён: к сильным моментам добавлено {len(context_candidates)} "
                "проверенных связующих live-фрагментов; replay/ожидание/технические заставки не использованы."
            )
            if context_candidates
            else "Анализ завершён без degraded AI fallback."
        ),
        "updated_at": time.time(),
    }
    write_json(project_dir / "analysis_health.json", analysis_health)
    if degraded_analysis:
        logger.log(f"Analysis health: {analysis_health['outcome']}; {analysis_health['message']}")

    write_json(p["segments"], [asdict(x) for x in chosen])

    if settings.get("make_srt", True):
        generate_srt(project_dir)
    # Metadata is a separate product step. In older builds, AI metadata could
    # block the entire analysis job for many minutes. v8.9 only creates the
    # fast non-AI metadata during analysis; AI Metadata / AI Metadata 100% are
    # always background jobs via /metadata-ai and /metadata-ai-strict.
    if (
        settings.get("generate_metadata", True)
        and not settings.get("metadata_ai_enabled", False)
        and not settings.get("require_ai_metadata", False)
    ):
        generate_youtube_metadata(project_dir, settings)
    elif settings.get("generate_metadata", True):
        write_json(
            project_dir / "metadata_pending.json",
            {
                "pending": True,
                "reason": "AI metadata is intentionally separated from analyze; run AI Metadata 100% as a background job.",
                "created_at": time.time(),
            },
        )
        logger.log("Metadata: AI metadata skipped inside analyze; run separate background metadata job.")

    q = quality_report(project_dir)
    visual_quality_report(project_dir, settings)
    build_quality_core_report(project_dir, settings)
    mark_analysis_complete(project_dir, settings)
    # The full 16 kHz WAV may be very large. Remove it only after every audio
    # consumer has finished, never immediately after Whisper transcription.
    try:
        (project_dir / "audio_16k.wav").unlink(missing_ok=True)
    except OperationCancelled:
        raise
    except Exception as exc:
        logger.log(f"Temporary audio cleanup skipped: {exc}")
    if analysis_health.get("quality_shortfall"):
        final_message = (
            f"Анализ готов: качественных сцен {tc(analysis_health.get('selected_seconds', 0))} "
            f"из ориентира {tc(analysis_health.get('target_seconds', 0))}"
        )
    elif analysis_health.get("degraded"):
        final_message = "Анализ готов (degraded recovery)"
    else:
        final_message = "Анализ готов"
    logger.set_status("done", 100, final_message, analysis_health=analysis_health)
    return {
        "candidates": [asdict(x) for x in candidates_for_final],
        "segments": [asdict(x) for x in chosen],
        "quality": q,
        "analysis_health": analysis_health,
    }


def quality_report(project_dir: Path) -> dict[str, Any]:
    p = project_paths(project_dir)
    segs = read_json(p["segments"], [])
    cands = read_json(p["candidates"], [])
    project = read_json(project_dir / "project.json", {}) or {}
    settings = project.get("settings") if isinstance(project.get("settings"), dict) else {}
    target_seconds = max(0.0, float(settings.get("target_minutes", 0) or 0) * 60.0)
    if not segs:
        report = {
            "quality_score": 0,
            "segments": 0,
            "candidates": len(cands),
            "duration": tc(0),
            "avg_score": 0,
            "technical_risk": 0,
            "short_ids": [],
            "recommendations": ["Сегменты ещё не выбраны. Сначала запусти анализ или добавь AI-кандидаты в монтаж."],
        }
        write_json(p["quality"], report)
        return report

    total = sum(max(0, float(s["end"]) - float(s["start"])) for s in segs)
    avg = sum(float(s.get("score", 0)) for s in segs) / max(1, len(segs))
    technical = sum(
        1 for s in segs if any(w in (s.get("title", "") + s.get("reason", "")).lower() for w in ["подключ", "микрофон", "ссылка", "ожид"])
    )
    short = [s.get("id", i + 1) for i, s in enumerate(segs) if float(s["end"]) - float(s["start"]) < 12]
    quality = max(0, min(100, int(avg * 10 - technical * 5 - len(short) * 2 + 10)))
    fill_ratio = total / target_seconds if target_seconds > 0 else 1.0
    if fill_ratio < 0.30:
        quality = max(0, quality - 25)
    elif fill_ratio < 0.60:
        quality = max(0, quality - 15)
    elif fill_ratio < float(settings.get("target_fill_ratio", 0.94) or 0.94):
        quality = max(0, quality - 8)
    rec = []
    if technical:
        rec.append(f"Проверь технические моменты: {technical}")
    if short:
        rec.append(f"Есть короткие фрагменты: {short[:10]}")
    if avg < 7:
        rec.append("Средний score низкий — проверь AI-кандидаты вручную")
    if target_seconds > 0 and fill_ratio < float(settings.get("target_fill_ratio", 0.94) or 0.94):
        rec.append(
            f"Монтаж существенно короче ориентира: {tc(total)} из {tc(target_seconds)}. "
            "Проверь отчёт отбора и альтернативы; результат не считается полностью готовым."
        )
    if not rec:
        rec.append("Качество выглядит нормальным для чернового рендера")
    report = {
        "quality_score": quality,
        "segments": len(segs),
        "candidates": len(cands),
        "duration": tc(total),
        "target_duration": tc(target_seconds),
        "target_seconds": round(target_seconds, 2),
        "target_fill_percent": round(fill_ratio * 100, 2),
        "target_shortfall_seconds": round(max(0.0, target_seconds - total), 2),
        "avg_score": round(avg, 2),
        "technical_risk": technical,
        "short_ids": short,
        "recommendations": rec,
    }
    write_json(p["quality"], report)
    return report


def build_editing_profile_from_feedback(project_dir: Path) -> dict[str, Any]:
    prefs = read_json(project_dir / "user_preferences.json", {"feedback": []})
    fb = prefs.get("feedback", []) if isinstance(prefs, dict) else []
    profile = {"liked_types": {}, "disliked_types": {}, "liked_keywords": {}, "disliked_keywords": {}, "updated_at": time.time()}
    for item in fb[-200:]:
        label = item.get("label")
        text = f"{item.get('title', '')} {item.get('reason', '')} {item.get('text_preview', '')}".lower()
        moment_type = str(item.get("moment_type") or "general")
        bucket_types = profile["liked_types"] if label == "good" else profile["disliked_types"]
        bucket_words = profile["liked_keywords"] if label == "good" else profile["disliked_keywords"]
        bucket_types[moment_type] = bucket_types.get(moment_type, 0) + 1
        for w in re.findall(r"[a-zа-яё0-9]{4,}", text)[:80]:
            if w in {"этот", "когда", "потому", "фрагмент", "момент", "просто"}:
                continue
            bucket_words[w] = bucket_words.get(w, 0) + 1
    for key in ["liked_keywords", "disliked_keywords"]:
        profile[key] = dict(sorted(profile[key].items(), key=lambda x: x[1], reverse=True)[:40])
    write_json(project_dir / "editing_profile.json", profile)
    return profile


def apply_editing_profile_to_candidates(project_dir: Path, candidates: list[Candidate], logger: JobLogger) -> list[Candidate]:
    profile = read_json(project_dir / "editing_profile.json", None) or build_editing_profile_from_feedback(project_dir)
    liked = set((profile.get("liked_keywords") or {}).keys())
    disliked = set((profile.get("disliked_keywords") or {}).keys())
    liked_types = profile.get("liked_types") or {}
    disliked_types = profile.get("disliked_types") or {}
    if not liked and not disliked and not liked_types and not disliked_types:
        return candidates
    for c in candidates:
        text = f"{c.title} {c.reason} {c.text_preview}".lower()
        words = set(re.findall(r"[a-zа-яё0-9]{4,}", text))
        delta = 0.0
        if c.moment_type in liked_types:
            delta += min(0.5, 0.1 * liked_types[c.moment_type])
        if c.moment_type in disliked_types:
            delta -= min(0.7, 0.12 * disliked_types[c.moment_type])
        delta += min(0.5, 0.05 * len(words & liked))
        delta -= min(0.8, 0.06 * len(words & disliked))
        if delta:
            c.score = round(max(0, min(10, c.score + delta)), 2)
            c.reason += f" / personal_profile_delta={round(delta, 2)}"
    logger.log("Personal editing profile: применил обучение по feedback.")
    return candidates


def user_preferences_prompt(project_dir: Path) -> str:
    prefs = read_json(project_dir / "user_preferences.json", {"feedback": []})
    fb = prefs.get("feedback", [])
    if not fb:
        return ""
    good = [x for x in fb if x.get("label") == "good"][-8:]
    bad = [x for x in fb if x.get("label") == "bad"][-8:]
    lines = ["\nПредпочтения пользователя:"]
    if good:
        lines.append("Пользователь любит похожие моменты:")
        for x in good:
            lines.append(f"- {x.get('title', '')}: {str(x.get('reason', ''))[:180]}")
    if bad:
        lines.append("Пользователь НЕ хочет похожие моменты:")
        for x in bad:
            lines.append(f"- {x.get('title', '')}: {str(x.get('reason', ''))[:180]}")
    profile = read_json(project_dir / "editing_profile.json", {}) or {}
    if profile:
        liked_words = list((profile.get("liked_keywords") or {}).keys())[:10]
        bad_words = list((profile.get("disliked_keywords") or {}).keys())[:10]
        if liked_words:
            lines.append("Профиль монтажа: чаще оставлять темы/слова: " + ", ".join(liked_words))
        if bad_words:
            lines.append("Профиль монтажа: чаще убирать темы/слова: " + ", ".join(bad_words))
    return "\n".join(lines)


def save_feedback(project_dir: Path, item: dict[str, Any], label: str, note: str = "") -> dict[str, Any]:
    prefs_path = project_dir / "user_preferences.json"
    prefs = read_json(prefs_path, {"feedback": [], "summary": ""})
    prefs.setdefault("feedback", []).append(
        {
            "label": label,
            "note": note,
            "title": item.get("title", ""),
            "reason": item.get("reason", ""),
            "text_preview": item.get("text_preview", ""),
            "time": [item.get("start"), item.get("end")],
            "score": item.get("score"),
            "moment_type": item.get("moment_type", "general"),
            "hook_potential": item.get("hook_potential", "medium"),
        }
    )
    write_json(prefs_path, prefs)
    prefs["editing_profile"] = build_editing_profile_from_feedback(project_dir)
    return prefs


def _ai_warmup_with_recovery(
    ai: Any,
    model: str,
    settings: dict[str, Any],
    logger: JobLogger,
    stage: str,
    *,
    timeout: int,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    """Warm an Ollama model with a bounded unload/backoff recovery cycle.

    A failed warm-up is especially common after we intentionally unload Qwen
    before Visual/OCR on low-VRAM systems.  Do not immediately send the first
    scored request into an already unhealthy Ollama runtime: verify the server,
    release any half-loaded model, back off briefly, and try loading it once more.
    """
    attempts = max_attempts if max_attempts is not None else min(2, ai_retry_count(settings))
    attempts = max(1, min(3, int(attempts or 1)))
    timeout = max(30, min(240, int(timeout or 120)))
    history: list[dict[str, Any]] = []
    last_health: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        result = ai.warmup(model=model, timeout=timeout)
        history.append({"attempt": attempt, **dict(result or {})})
        if result.get("ok"):
            if attempt > 1:
                logger.log(f"{stage}: Ollama warmup восстановлен на попытке {attempt}/{attempts}")
            return {**dict(result), "attempt": attempt, "max_attempts": attempts, "attempts": history, "health": last_health}

        logger.log(
            f"{stage}: Ollama warmup attempt {attempt}/{attempts} failed: "
            f"{result.get('error') or 'unknown error'}"
        )
        if attempt >= attempts:
            break

        # /api/tags is cheap and tells diagnostics whether Ollama itself is alive
        # and whether the requested model still exists before a reload attempt.
        try:
            last_health = ai.check(model, effective_vision_model(settings)) or {}
        except Exception as health_exc:
            last_health = {"ok": False, "error": f"{type(health_exc).__name__}: {health_exc}"}

        # A failed load may leave a model allocation in a bad state. Unload is
        # best-effort only; the next warmup remains the authority.
        try:
            unload_result = ai.unload(model=model, timeout=min(30, timeout)) or {}
            if not unload_result.get("ok"):
                logger.log(f"{stage}: Ollama cleanup warning: {unload_result.get('error') or 'unknown error'}")
        except Exception as unload_exc:
            logger.log(f"{stage}: Ollama cleanup warning: {type(unload_exc).__name__}: {unload_exc}")

        time.sleep(min(5, 2 * attempt))

    last = history[-1] if history else {"ok": False, "error": "warmup not attempted"}
    return {
        **last,
        "ok": False,
        "attempt": len(history),
        "max_attempts": attempts,
        "attempts": history,
        "health": last_health,
    }


def _postprocess_ai_warmup(ai: Any, model: str, settings: dict[str, Any], logger: JobLogger, stage: str) -> bool:
    """Warm Qwen before a streamed post-processing request.

    Returning availability lets normal mode skip an entire retry storm when
    Ollama is already known to be unavailable. Strict/full-AI keeps fail-closed
    behavior and stops immediately with a precise stage error.
    """
    if not settings.get("postprocess_ai_warmup_enabled", True):
        return True
    timeout = max(60, min(240, int(settings.get("postprocess_ai_warmup_timeout", 180) or 180)))
    result = _ai_warmup_with_recovery(ai, model, settings, logger, stage, timeout=timeout)
    if result.get("ok"):
        logger.log(f"{stage}: Ollama warmup готов за {result.get('elapsed_seconds', 0)}с")
        return True
    message = str(result.get("error") or "unknown error")
    logger.log(f"{stage}: Ollama warmup warning after recovery: {message}")
    if strict_ai_enabled(settings):
        raise RuntimeError(f"AI 100% режим: {stage} warmup не восстановился: {message}")
    return False


def _compact_dedup_prompt(items: list[Candidate]) -> str:
    rows = []
    for c in items:
        rows.append(
            f"ID={c.id} | TIME={tc(c.start)}-{tc(c.end)} | PARENT={int(c.parent_id or 0)} | "
            f"SCORE={float(c.score):.2f} | CLASS={c.content_class}:{float(c.content_class_confidence or 0):.2f} | "
            f"TOPIC={str(c.semantic_topic or '')[:140]} | TEMPLATE={str(c.template_signature or '')[:100]} | "
            f"TITLE={str(c.title or '')[:160]} | TEXT={str(c.text_preview or '')[:260]}"
        )
    return """Ты финальный монтажёр. Найди только реальные смысловые/шаблонные дубли.
Не удаляй разные события только потому, что у них общая тема. Если TEMPLATE одинаковый — это сильный признак дубля.
Соседние непересекающиеся TIME-фрагменты с одинаковым PARENT — последовательные части одной сцены, а не дубли.
waiting/reconnect/intermission/replay/prerecorded/advertisement нельзя оставлять.
Верни короткий JSON без пояснений вокруг:
{"drop_ids":[2,7],"groups":[{"ids":[2,7],"keep":7,"reason":"одна сцена"}]}
Если дублей нет: {"drop_ids":[],"groups":[]}.

Фрагменты:
""" + "\n".join(rows)


def _parse_ai_drop_ids(data: dict[str, Any], allowed_ids: set[int]) -> set[int]:
    out: set[int] = set()
    for raw in data.get("drop_ids", []) if isinstance(data, dict) else []:
        try:
            value = int(raw)
        except Exception:
            continue
        if value in allowed_ids:
            out.add(value)
    # Backward-compatible shape if a model returns keep_ids despite the compact
    # contract. Only use it when at least one valid keep ID is present.
    if not out and isinstance(data, dict) and isinstance(data.get("keep_ids"), list):
        keep: set[int] = set()
        for raw in data.get("keep_ids", []):
            try:
                value = int(raw)
            except Exception:
                continue
            if value in allowed_ids:
                keep.add(value)
        if keep:
            out = allowed_ids - keep
    return out


def _dedup_ai_request(ai: Any, model: str, items: list[Candidate], settings: dict[str, Any], operation: str) -> set[int]:
    allowed = {int(c.id) for c in items}
    data = ai.generate_json(
        _compact_dedup_prompt(items),
        model=model,
        timeout=int(settings.get("ollama_timeout", 900)),
        operation=operation,
        trace_context={"candidate_count": len(items), "compact": True},
    )
    return _parse_ai_drop_ids(data, allowed)


def dedup_pass(
    project_dir: Path, candidates: list[Candidate], chosen: list[Candidate], settings: dict[str, Any], logger: JobLogger
) -> list[Candidate]:
    if not settings.get("dedup_enabled", True) or len(chosen) < 3:
        return chosen
    logger.set_status("running", 77, "Dedup pass: убираю повторы")
    ai = make_ai_client(settings, cancel_file=cancel_path(project_dir))
    model = effective_text_model(settings)
    ai_ready = _postprocess_ai_warmup(ai, model, settings, logger, "Dedup")
    if not ai_ready:
        logger.log("Dedup AI skipped: Ollama unavailable; применяю deterministic local duplicate guard.")
        return sorted(
            local_similarity_filter_keep_target(chosen, 0.0, {**settings, "target_fill_ratio": 0.0}, logger),
            key=lambda x: x.start,
        )

    drop_ids: set[int] = set()
    ai_succeeded = False
    try:
        # One compact global request keeps cross-stream duplicate awareness while
        # cutting prompt size dramatically versus 10.15.16.
        drop_ids = _dedup_ai_request(ai, model, chosen[:80], settings, "dedup_ai")
        ai_succeeded = True
        logger.log(f"Dedup AI: глобальная проверка готова, drop={len(drop_ids)}")
    except OperationCancelled:
        raise
    except Exception as exc:
        logger.log(f"Dedup AI global failed: {exc}; пробую компактные rescue-batches")
        # Rescue: smaller overlapping chronological windows. Most duplicate
        # fragments from one event are close in time; the deterministic local
        # filter below still catches obvious cross-batch repeats.
        rescue_size = max(6, min(16, int(settings.get("dedup_rescue_batch_size", 12) or 12)))
        overlap = min(4, max(1, rescue_size // 4))
        ordered = sorted(chosen[:80], key=lambda c: c.start)
        step = max(1, rescue_size - overlap)
        successes = 0
        for start_idx in range(0, len(ordered), step):
            batch = ordered[start_idx:start_idx + rescue_size]
            if len(batch) < 2:
                continue
            try:
                drop_ids.update(_dedup_ai_request(ai, model, batch, settings, "dedup_ai_rescue"))
                successes += 1
            except OperationCancelled:
                raise
            except Exception as batch_exc:
                logger.log(f"Dedup rescue {start_idx // step + 1}: пропущен ({batch_exc})")
        ai_succeeded = successes > 0
        if ai_succeeded:
            logger.log(f"Dedup rescue: успешных batch={successes}, drop={len(drop_ids)}")
        elif strict_ai_enabled(settings):
            raise RuntimeError(f"AI 100% режим: Dedup AI не выполнен, fallback запрещён: {exc}")

    out = []
    protected_ids = {
        int(item.id)
        for item in chosen
        if any(
            item is not other and _candidate_is_contiguous_sibling(item, other)
            for other in chosen
        )
    }
    blocked_protected = drop_ids & protected_ids
    if blocked_protected:
        drop_ids -= blocked_protected
        logger.log(
            f"Dedup continuity guard: сохранено последовательных частей сцены={len(blocked_protected)}"
        )
    for c in chosen:
        if int(c.id) in drop_ids:
            c.decision = "remove"
            c.reason += " / dedup_remove=ai_duplicate"
            continue
        out.append(c)
    if drop_ids and out:
        logger.log(f"Dedup: {len(chosen)} -> {len(out)}")

    # Always run the deterministic guard after AI/rescue. It is cheap, catches
    # obvious template/text repeats across rescue windows, and never inserts
    # weak content merely to reach the duration target.
    local_out = local_similarity_filter_keep_target(out, 0.0, {**settings, "target_fill_ratio": 0.0}, logger)
    if ai_succeeded or local_out:
        return sorted(local_out, key=lambda x: x.start)
    return sorted(out, key=lambda x: x.start)


def _storyline_prompt(items: list[tuple[Candidate, str, str]], *, rescue: bool = False) -> str:
    rows = []
    for c, before, after in items:
        rows.append(
            f"ID={c.id}\nTIME={tc(c.start)}-{tc(c.end)}\nSCORE={float(c.score):.2f}\n"
            f"CLASS={c.content_class}:{float(c.content_class_confidence or 0):.2f}\n"
            f"TOPIC={str(c.semantic_topic or '')[:140]}\nCLARITY={float(c.standalone_clarity or 0):.2f}\n"
            f"TITLE={str(c.title or '')[:180]}\nBEFORE={before[:360]}\nTEXT={str(c.text_preview or '')[:560]}\nAFTER={after[:360]}"
        )
    rescue_note = "Это rescue-запрос: обязательно верни строку для КАЖДОГО переданного ID." if rescue else "Верни строку для КАЖДОГО переданного ID."
    return f"""Ты финальный редактор YouTube-хайлайта. Проверяй смысл и законченность каждой сцены.
{rescue_note}
Правила:
- waiting/reconnect/intermission/replay/prerecorded/advertisement -> keep=false;
- live_reaction допустим, если смысл — текущая реакция стримера;
- если не хватает причины/развязки, можно добавить до 25 секунд до/после;
- не удаляй сильную понятную сцену ради целевой длительности;
- если даже с контекстом сцена бессмысленна, keep=false.

Верни СТРОГО объект:
{{"segments":[{{"id":1,"keep":true,"extra_start_seconds":0,"extra_end_seconds":0,"semantic_topic":"тема","reason":"почему связно"}}]}}

Фрагменты:
""" + "\n\n".join(rows)


def _storyline_ai_batch(
    ai: Any,
    model: str,
    batch: list[tuple[Candidate, str, str]],
    settings: dict[str, Any],
    *,
    operation: str,
    rescue: bool = False,
) -> dict[int, dict[str, Any]]:
    expected = {int(c.id) for c, _, _ in batch}
    data = ai.generate_json(
        _storyline_prompt(batch, rescue=rescue),
        model=model,
        timeout=int(settings.get("ollama_timeout", 900)),
        operation=operation,
        trace_context={"candidate_count": len(batch), "rescue": rescue},
        collection_key="segments",
        expected_ids=expected,
        required_item_fields={"id", "keep"},
    )
    by_id: dict[int, dict[str, Any]] = {}
    for item in data.get("segments", []):
        try:
            item_id = int(item.get("id"))
        except Exception:
            continue
        if item_id in expected:
            by_id[item_id] = item
    return by_id


def storyline_pass(
    project_dir: Path,
    chosen: list[Candidate],
    transcript: list[TranscriptSegment],
    duration: float,
    settings: dict[str, Any],
    logger: JobLogger,
) -> list[Candidate]:
    if not settings.get("storyline_enabled", True) or not chosen:
        return chosen
    logger.set_status("running", 80, "Storyline pass: проверяю контекст")
    ai = make_ai_client(settings, cancel_file=cancel_path(project_dir))
    model = effective_text_model(settings)
    ai_ready = _postprocess_ai_warmup(ai, model, settings, logger, "Storyline")
    if not ai_ready:
        reject_conf = float(settings.get("non_primary_reject_confidence", 0.72) or 0.72)
        out = [
            c for c in chosen
            if not (
                str(c.content_class or "unknown").lower() in NON_PRIMARY_CONTENT_CLASSES
                and float(c.content_class_confidence or 0.0) >= reject_conf
            )
        ]
        logger.log(
            f"Storyline AI skipped: Ollama unavailable; semantic guard сохранил {len(out)}/{len(chosen)} сегментов."
        )
        return out

    prepared: list[tuple[Candidate, str, str]] = []
    for c in chosen[:80]:
        before = " ".join(t.text for t in transcript if c.start - 25 <= t.start < c.start)[:360]
        after = " ".join(t.text for t in transcript if c.end < t.end <= c.end + 25)[:360]
        prepared.append((c, before, after))

    # 10.15.16 sent up to 80 context-rich scenes in one JSON request. On Qwen3
    # 8B that could exceed the practical context budget and return an error
    # object instead of the contract. Small batches keep each request bounded.
    batch_size = max(4, min(12, int(settings.get("storyline_batch_size", 8) or 8)))
    by_id: dict[int, dict[str, Any]] = {}
    failed_ids: set[int] = set()
    primary_batches = 0
    rescue_batches = 0
    for batch_idx in range(0, len(prepared), batch_size):
        batch = prepared[batch_idx:batch_idx + batch_size]
        try:
            result = _storyline_ai_batch(ai, model, batch, settings, operation="storyline_ai")
            primary_batches += 1
            by_id.update(result)
            missing = {int(c.id) for c, _, _ in batch} - set(result)
            failed_ids.update(missing)
            if missing:
                logger.log(f"Storyline batch {batch_idx // batch_size + 1}: partial, missing={sorted(missing)}")
        except OperationCancelled:
            raise
        except Exception as exc:
            ids = {int(c.id) for c, _, _ in batch}
            failed_ids.update(ids)
            logger.log(f"Storyline batch {batch_idx // batch_size + 1}: primary failed ({exc})")

    # Targeted rescue only for missing/failed IDs, in tiny batches. This avoids
    # repeating all successful inference and prevents retry budgets multiplying.
    if failed_ids:
        rescue_size = max(2, min(4, int(settings.get("storyline_rescue_batch_size", 4) or 4)))
        pending = [row for row in prepared if int(row[0].id) in failed_ids]
        recovered: set[int] = set()
        for batch_idx in range(0, len(pending), rescue_size):
            batch = pending[batch_idx:batch_idx + rescue_size]
            try:
                result = _storyline_ai_batch(ai, model, batch, settings, operation="storyline_ai_rescue", rescue=True)
                rescue_batches += 1
                by_id.update(result)
                recovered.update(result)
            except OperationCancelled:
                raise
            except Exception as exc:
                logger.log(f"Storyline rescue {batch_idx // rescue_size + 1}: пропущен ({exc})")
        failed_ids -= recovered

    write_json(
        project_dir / "storyline_runtime.json",
        {
            "version": "11.2.5",
            "requested": len(prepared),
            "resolved": len(by_id),
            "unresolved_ids": sorted(failed_ids),
            "primary_batches_ok": primary_batches,
            "rescue_batches_ok": rescue_batches,
            "batch_size": batch_size,
            "updated_at": time.time(),
        },
    )
    logger.log(
        f"Storyline AI: resolved={len(by_id)}/{len(prepared)}, primary_batches={primary_batches}, "
        f"rescue_batches={rescue_batches}, unresolved={len(failed_ids)}"
    )
    if failed_ids and strict_ai_enabled(settings):
        raise RuntimeError(f"AI 100% режим: Storyline AI не обработал ID {sorted(failed_ids)}")

    out: list[Candidate] = []
    reject_conf = float(settings.get("non_primary_reject_confidence", 0.72) or 0.72)
    for c in chosen:
        if (
            str(c.content_class or "unknown").lower() in NON_PRIMARY_CONTENT_CLASSES
            and float(c.content_class_confidence or 0.0) >= reject_conf
        ):
            c.decision = "remove"
            c.reason += " / storyline_remove=non_primary"
            logger.log(f"Storyline semantic guard: удалён non-primary segment {c.id} ({c.content_class})")
            continue
        x = by_id.get(int(c.id))
        if x:
            keep_raw = x.get("keep", True)
            keep = not (keep_raw is False or str(keep_raw).strip().lower() in {"false", "0", "no", "remove", "drop"})
            if not keep:
                c.decision = "remove"
                c.reason += " / storyline_remove=incoherent"
                logger.log(f"Storyline semantic guard: AI удалил несвязный segment {c.id}: {x.get('reason', '')}")
                continue
            try:
                extra_start = min(25.0, max(0.0, float(x.get("extra_start_seconds", 0) or 0)))
                extra_end = min(25.0, max(0.0, float(x.get("extra_end_seconds", 0) or 0)))
            except OperationCancelled:
                raise
            except Exception:
                extra_start = extra_end = 0
            if extra_start or extra_end:
                c.start = round(max(0, c.start - extra_start), 3)
                c.end = round(min(duration, c.end + extra_end), 3)
                c.reason += " / Storyline: " + str(x.get("reason", "добавлен контекст"))
            topic = str(x.get("semantic_topic") or "").strip()
            if topic:
                c.semantic_topic = topic[:160]
        # Non-strict mode deliberately preserves an unresolved high-quality
        # candidate rather than dropping it because the post-process AI failed.
        out.append(c)
    return sorted(out, key=lambda x: x.start)

def resolve_segment_overlaps(chosen: list[Candidate], duration: float, settings: dict[str, Any], logger: JobLogger) -> list[Candidate]:
    """Remove or trim overlaps introduced by Storyline/context expansion."""
    if not chosen:
        return chosen
    min_clip = max(3.0, float(settings.get("micro_min_seconds", 10)) * 0.35)
    out: list[Candidate] = []
    for c in sorted(chosen, key=lambda x: (x.start, -x.score)):
        c.start = round(max(0.0, min(float(duration), float(c.start))), 3)
        c.end = round(max(c.start + 0.1, min(float(duration), float(c.end))), 3)
        if not out:
            out.append(c)
            continue
        prev = out[-1]
        if c.start < prev.end:
            overlap_len = prev.end - c.start
            if overlap_len <= 0:
                out.append(c)
                continue
            # Prefer trimming small overlaps. For big overlaps keep the stronger segment.
            if overlap_len <= 8.0:
                if candidate_duration(c) - overlap_len >= min_clip:
                    c.start = round(prev.end, 3)
                    c.reason += " / overlap_resolved=trim_start"
                    out.append(c)
                elif candidate_duration(prev) - overlap_len >= min_clip and float(c.score) >= float(prev.score):
                    prev.end = round(c.start, 3)
                    prev.reason += " / overlap_resolved=trim_end"
                    out.append(c)
                else:
                    keep = prev if float(prev.score) >= float(c.score) else c
                    if keep is c:
                        out[-1] = c
                    logger.log(f"Overlap resolver: удалил пересечение {tc(c.start)}-{tc(c.end)}")
            else:
                if float(c.score) > float(prev.score) + 0.25:
                    out[-1] = c
                    logger.log(f"Overlap resolver: заменил более слабый фрагмент на {c.id}")
                else:
                    logger.log(f"Overlap resolver: пропустил overlapping фрагмент {c.id}")
        else:
            out.append(c)
    # Final pass: drop accidentally broken segments. Candidate identity is
    # immutable; display ordering is a separate field used only by UI/export.
    clean = [c for c in out if candidate_duration(c) >= min_clip]
    ordered = sorted(clean, key=lambda x: x.start)
    for i, c in enumerate(ordered, start=1):
        c.display_order = i
        update_candidate_confidence(c)
    logger.log(f"Overlap resolver: {len(chosen)} -> {len(clean)} фрагментов без пересечений")
    return ordered


def generate_srt(project_dir: Path) -> Path:
    p = project_paths(project_dir)
    transcript = [TranscriptSegment(**x) for x in read_json(p["transcript"], [])]
    segments = read_json(p["segments"], [])
    out = project_dir / "highlight_subtitles.srt"
    lines = []
    idx = 1
    offset = 0.0
    for seg in segments:
        s0 = float(seg["start"])
        s1 = float(seg["end"])
        for t in transcript:
            if t.end <= s0 or t.start >= s1:
                continue
            ns = offset + max(0, t.start - s0)
            ne = offset + min(s1 - s0, t.end - s0)
            lines += [str(idx), f"{srt_time(ns)} --> {srt_time(ne)}", t.text.strip(), ""]
            idx += 1
        offset += max(0, s1 - s0)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def srt_time(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


METADATA_CONTEXT_VERSION = "stream-context-v11.2.6-full-montage"

_METADATA_GENERIC_MARKERS = (
    "лучшие моменты",
    "самые интересные",
    "самые смешные",
    "реакции и чат",
    "стрим в нарезке",
    "без лишней воды",
    "без воды",
    "вышел из-под контроля",
    "пошёл не по плану",
    "пошел не по плану",
    "сильный момент",
    "неожиданный момент",
    "сбалансированный",
    "quality",
    "balanced",
    "highlight",
)

_METADATA_STOPWORDS = {
    "автоматическая", "автоматический", "больше", "будет", "были", "было", "быть", "весь", "видео",
    "вот", "время", "где", "говорит", "даже", "данный", "долго", "другой", "есть", "здесь", "из-за",
    "интересный", "интересные", "каждый", "когда", "который", "лучшие", "лучший", "между", "момент",
    "моменты", "монтаж", "нарезка", "наш", "него", "ничего", "общий", "очень", "потом", "почему",
    "просто", "реакция", "реакции", "самый", "сильный", "ситуация", "смотреть", "стрим", "стрима",
    "стример", "стримера", "такой", "теперь", "только", "этого", "этот", "эпизод", "эпизоды", "часть",
    "через", "чтобы", "highlights", "highlight", "shorts", "twitch", "general", "primary", "live",
    "сбалансированный", "смысл", "авто", "auto", "готовый", "финальный", "фрагмент", "фрагменты",
    "and", "for", "from", "that", "the", "this", "with", "или", "как", "что", "это", "она", "они",
    "его", "ему", "еще", "ещё", "при", "про", "над", "под", "без", "для", "после", "перед", "тоже",
    "будем", "будет", "делает", "делать", "должен", "которого", "которая", "которые", "может", "можно",
    "новая", "новое", "новую", "показывает", "получается", "сказал", "сказала", "сейчас", "снова", "сразу",
    "стал", "стала", "становится", "свою", "свои", "свой", "этой", "этом", "тот", "там", "тут", "уже",
    "выбран", "выбрана", "потому", "пресет", "режим", "высокая", "высокий", "низкий", "риск", "score",
    "audio", "visual", "динамика", "уверенность", "заметная", "эмоция", "эмоции", "понятен", "понятна",
}

_METADATA_BOILERPLATE_MARKERS = (
    "в моменте есть напряжение",
    "есть заметная эмоция",
    "выбран потому что",
    "зрителю интересно узнать",
    "реакцию легко понять",
    "низкий риск",
    "момент должен быть понятен",
    "пресет:",
    "режим:",
)


def _metadata_clean_phrase(value: Any, *, limit: int = 110) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n-—:;,.\"'«»")
    text = re.sub(r"\s*[—-]\s*связующий контекст\s*$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^фрагмент\s+[«\"']?", "", text, flags=re.IGNORECASE).strip("»\"' .:-")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return text


def _metadata_clean_evidence(value: Any, *, limit: int = 220) -> str:
    text = _metadata_clean_phrase(value, limit=max(limit, 320))
    text = re.split(
        r"\s*/\s*(?:parent_id|audio_delta|visual_scene|density_bonus|peak|silence)\s*=",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    if any(marker in text.lower().replace("ё", "е") for marker in _METADATA_BOILERPLATE_MARKERS):
        return ""
    return _metadata_clean_phrase(text, limit=limit)


def _metadata_words(value: Any) -> list[str]:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_+#.-]{2,}", str(value or ""))
    result: list[str] = []
    for raw in words:
        word = raw.strip("._-+").lower().replace("ё", "е")
        if len(word) < 3 or word.isdigit() or word in _METADATA_STOPWORDS:
            continue
        if any(marker == word for marker in _METADATA_GENERIC_MARKERS):
            continue
        result.append(word)
    return result


def _metadata_topic_is_specific(value: Any) -> bool:
    topic = _metadata_clean_phrase(value)
    low = topic.lower().replace("ё", "е")
    if any(marker in low for marker in _METADATA_BOILERPLATE_MARKERS):
        return False
    if len(topic) < 5 or low in {"общий", "разговор", "диалог", "история", "юмор", "хаос", "irl", "gameplay"}:
        return False
    words = _metadata_words(topic)
    if not words:
        return False
    # A phrase consisting only of the stock vocabulary is not evidence about
    # this particular stream even when it is several words long.
    generic_blob = " ".join(_METADATA_GENERIC_MARKERS)
    return any(word not in generic_blob for word in words)


def _metadata_transcript_for_range(
    transcript: list[dict[str, Any]], start: float, end: float, *, limit: int = 520
) -> str:
    parts: list[str] = []
    for row in transcript:
        if not isinstance(row, dict):
            continue
        try:
            row_start = float(row.get("start", 0) or 0)
            row_end = float(row.get("end", row_start) or row_start)
        except (TypeError, ValueError, OverflowError):
            continue
        if row_end <= start or row_start >= end:
            continue
        text = _metadata_clean_phrase(row.get("text"), limit=220)
        if text:
            parts.append(text)
        if sum(len(part) + 1 for part in parts) >= limit:
            break
    return _metadata_clean_phrase(" ".join(parts), limit=limit)


def _metadata_representative_items(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Sample both strong and timeline-distributed scenes.

    The old metadata path sent only the first N final segments to Ollama. A
    multi-hour stream could therefore receive a title describing only its
    opening. Half of this sample is score-driven and half covers the complete
    timeline, while stable range identities prevent duplicates.
    """
    valid = [item for item in items if isinstance(item, dict)]
    if not valid:
        return []
    limit = max(1, min(int(limit or 1), len(valid)))

    def key(item: dict[str, Any]) -> tuple[float, float]:
        try:
            return (round(float(item.get("start", 0) or 0), 3), round(float(item.get("end", 0) or 0), 3))
        except (TypeError, ValueError, OverflowError):
            return (0.0, 0.0)

    selected: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()

    def add(item: dict[str, Any]) -> None:
        identity = key(item)
        if identity not in seen and len(selected) < limit:
            seen.add(identity)
            selected.append(item)

    chronological = sorted(valid, key=lambda item: float(item.get("start", 0) or 0))
    # Reserve the edges first. With a small budget the old score-first approach
    # could consume every remaining slot before ever reaching the end of a VOD.
    if limit >= 2:
        add(chronological[0])
        add(chronological[-1])
    strong_count = max(1, int(math.ceil(limit * 0.6)))
    ranked = sorted(
        valid,
        key=lambda item: (
            float(item.get("score", item.get("ai_score", 0)) or 0),
            float(item.get("confidence", 0) or 0),
        ),
        reverse=True,
    )
    for item in ranked[:max(1, strong_count - len(selected))]:
        add(item)

    chronological = sorted(valid, key=lambda item: float(item.get("start", 0) or 0))
    remaining = max(0, limit - len(selected))
    if remaining:
        for offset in range(remaining):
            index = round(offset * (len(chronological) - 1) / max(1, remaining - 1))
            add(chronological[index])
    for item in chronological:
        add(item)
    return sorted(selected, key=lambda item: float(item.get("start", 0) or 0))


def build_stream_context(
    project_dir: Path,
    settings: dict[str, Any],
    *,
    max_items: int = 25,
) -> dict[str, Any]:
    """Build an evidence-only dossier for metadata about this exact stream."""
    p = project_paths(project_dir)
    segments = read_json(p["segments"], []) or []
    # An explicitly empty final montage is intentional, not a signal to revive
    # every discarded alternative. Candidate-only context must be requested as
    # a separate product flow, never advertised as the final edit.
    source_items = segments
    source_items = source_items if isinstance(source_items, list) else []
    source_items = [item for item in source_items if _metadata_valid_scene(item)]
    transcript = read_json(p["transcript"], []) or []
    transcript = transcript if isinstance(transcript, list) else []
    montage_time = 0.0
    timeline = []
    for item in source_items:
        entry = dict(item)
        entry["montage_start"] = round(montage_time, 3)
        montage_time += float(item["end"]) - float(item["start"])
        entry["montage_end"] = round(montage_time, 3)
        timeline.append(entry)
    source_items = timeline
    representative = _metadata_representative_items(source_items, max_items)

    scenes: list[dict[str, Any]] = []
    topic_rows: list[tuple[float, str]] = []
    word_counts: dict[str, float] = {}
    total_duration = 0.0
    for item in source_items:
        if not isinstance(item, dict):
            continue
        try:
            total_duration += max(0.0, float(item.get("end", 0) or 0) - float(item.get("start", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            pass

    for item in representative:
        try:
            start = max(0.0, float(item.get("start", 0) or 0))
            end = max(start, float(item.get("end", start) or start))
            score = float(item.get("score", item.get("ai_score", 0)) or 0)
        except (TypeError, ValueError, OverflowError):
            start, end, score = 0.0, 0.0, 0.0
        title = _metadata_clean_phrase(item.get("title"), limit=100)
        semantic_topic = _metadata_clean_phrase(item.get("semantic_topic"), limit=100)
        what = _metadata_clean_evidence(item.get("what_happens"), limit=180)
        reason = ""
        for evidence_value in (
            item.get("why_selected"),
            item.get("viewer_value"),
            item.get("reason"),
            item.get("ai_explanation"),
        ):
            cleaned_evidence = _metadata_clean_evidence(evidence_value, limit=220)
            if cleaned_evidence:
                reason = cleaned_evidence
                break
        transcript_text = _metadata_transcript_for_range(transcript, start, end)
        if not transcript and not transcript_text:
            transcript_text = _metadata_clean_phrase(item.get("text_preview"), limit=520)

        topic = ""
        for candidate_topic in (title, semantic_topic, what, reason):
            if _metadata_topic_is_specific(candidate_topic):
                topic = _metadata_clean_phrase(candidate_topic, limit=86)
                break
        if topic:
            topic_rows.append((score, topic))
        quality_weight = 1.0 + max(0.0, min(10.0, score)) / 20.0
        for word in _metadata_words(" ".join(part for part in (title, semantic_topic, what, reason) if part)):
            word_counts[word] = word_counts.get(word, 0.0) + quality_weight * 1.5
        for word in _metadata_words(transcript_text):
            word_counts[word] = word_counts.get(word, 0.0) + quality_weight * 0.35
        scenes.append(
            {
                "source_start": round(start, 3),
                "source_end": round(end, 3),
                "montage_start": item["montage_start"],
                "montage_end": item["montage_end"],
                "score": round(score, 3),
                "title": title,
                "semantic_topic": semantic_topic,
                "what_happens": what,
                "why_selected": reason,
                "transcript": transcript_text,
            }
        )

    topics: list[str] = []
    topic_keys: set[str] = set()
    for _, topic in sorted(topic_rows, key=lambda row: row[0], reverse=True):
        normalized = " ".join(_metadata_words(topic))
        if not normalized or normalized in topic_keys:
            continue
        # Near-identical Micro windows should not occupy the whole dossier.
        if any(normalized in existing or existing in normalized for existing in topic_keys):
            continue
        topic_keys.add(normalized)
        topics.append(topic)
        if len(topics) >= 10:
            break

    keywords = [
        word for word, _ in sorted(word_counts.items(), key=lambda row: (-row[1], row[0]))
        if word not in {"комментарий", "контекст", "сцена", "событие", "зритель", "зрителю"}
    ][:18]
    if not topics and keywords:
        topics.append(" и ".join(keywords[:2]))

    content_type = _metadata_clean_phrase(settings.get("content_type"), limit=60)
    edit_mode = _metadata_clean_phrase(settings.get("edit_mode"), limit=60)
    is_irl = "irl" in f"{content_type} {edit_mode}".lower()
    signature_source = json.dumps(
        {"version": METADATA_CONTEXT_VERSION, "segments": source_items,
         "transcript": transcript, "content_type": content_type, "edit_mode": edit_mode},
        ensure_ascii=False, sort_keys=True
    )
    return {
        "version": METADATA_CONTEXT_VERSION,
        "signature": hashlib.sha256(signature_source.encode("utf-8", "replace")).hexdigest()[:20],
        "stream_kind": "IRL" if is_irl else (content_type if content_type and content_type.lower() != "auto" else "stream"),
        "selected_segments": len(source_items),
        "selected_duration_seconds": round(total_duration, 3),
        "representative_scene_count": len(scenes),
        "topics": topics,
        "keywords": keywords,
        "scenes": scenes,
        "timeline": [{"montage_start": row["montage_start"], "montage_end": row["montage_end"],
                      "title": _metadata_clean_phrase(row.get("title"), limit=100),
                      "semantic_topic": _metadata_clean_phrase(row.get("semantic_topic"), limit=100)} for row in timeline],
    }


def _metadata_valid_scene(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    try:
        start, end = float(item["start"]), float(item["end"])
        return math.isfinite(start) and math.isfinite(end) and 0 <= start < end
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _metadata_chapters(context: dict[str, Any], limit: int = 20) -> list[str]:
    timeline = context.get("timeline") or []
    if not timeline:
        return []
    # Sample the whole *output* timeline; the first twenty clips are not a
    # chapter plan for a one-hour montage. Never let AI invent the timestamps.
    count = min(limit, len(timeline))
    indexes = sorted({round(i * (len(timeline) - 1) / max(1, count - 1)) for i in range(count)})
    result = []
    previous = -10
    for index in indexes:
        item = timeline[index]
        seconds = int(item["montage_start"])
        if seconds - previous < 10:
            continue
        previous = seconds
        timestamp = (f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
                     if seconds >= 3600 else f"{seconds // 60:02d}:{seconds % 60:02d}")
        title = item.get("semantic_topic") or item.get("title") or "Эпизод монтажа"
        result.append(f"{timestamp} {_metadata_clean_phrase(title, limit=65)}")
    return result


def _metadata_fit_title(value: Any, *, limit: int = 100) -> str:
    text = _metadata_clean_phrase(value, limit=limit + 20)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")


def _metadata_unique(values: list[Any], *, limit: int, title_limit: int | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _metadata_fit_title(value, limit=title_limit) if title_limit else _metadata_clean_phrase(value, limit=120)
        key = re.sub(r"\W+", "", text.lower(), flags=re.UNICODE)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def metadata_specificity_audit(data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"passed": False, "reason": "invalid_schema"}
    evidence_terms = set(context.get("keywords") or [])
    for topic in context.get("topics") or []:
        evidence_terms.update(_metadata_words(topic))
    evidence_terms = {term for term in evidence_terms if len(term) >= 4}
    payload = " ".join(
        [str(data.get("description") or ""), str(data.get("montage_summary") or "")]
        + [str(value) for key in ("titles", "short_titles", "tags", "thumbnail_ideas") for value in (data.get(key) or [])]
    ).lower().replace("ё", "е")
    matched = sorted(term for term in evidence_terms if term in payload)
    raw_titles = data.get("titles")
    titles = [value.strip() for value in raw_titles if isinstance(value, str) and value.strip()] if isinstance(raw_titles, list) else []
    def has_anchor(value: str) -> bool:
        words = set(_metadata_words(value))
        return bool(words & evidence_terms)
    specific_titles = sum(1 for title in titles if has_anchor(title))
    required_hits = min(3, max(1, len(evidence_terms))) if evidence_terms else 0
    passed = bool(evidence_terms) and bool(titles) and len(matched) >= required_hits and specific_titles == len(titles)
    return {
        "passed": passed,
        "matched_terms": matched[:15],
        "matched_count": len(matched),
        "evidence_term_count": len(evidence_terms),
        "specific_titles": specific_titles,
        "title_count": len(titles),
    }


def build_specific_metadata_fallback(
    project_dir: Path,
    settings: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create stream-specific metadata without pretending a template is AI."""
    context = context or build_stream_context(project_dir, settings)
    topics = [_metadata_clean_phrase(topic, limit=82) for topic in (context.get("topics") or []) if _metadata_topic_is_specific(topic)]
    keywords = list(context.get("keywords") or [])
    primary = topics[0] if topics else (" и ".join(keywords[:2]) if keywords else "")
    secondary = topics[1] if len(topics) > 1 else ""
    third = topics[2] if len(topics) > 2 else ""

    title_candidates = [
        f"{primary} — ЧТО ПРОИЗОШЛО НА СТРИМЕ",
        f"{primary} — эпизоды эфира",
        f"Главное за стрим: {primary}",
        f"{primary} — фрагменты стрима",
        f"На стриме: {primary}",
        f"Как всё происходило: {primary}",
    ]
    if secondary:
        title_candidates.extend(
            [
                f"{secondary} — эпизод стрима",
                f"На стриме: {secondary}",
            ]
        )
        if len(primary) + len(secondary) <= 64:
            title_candidates.append(f"{primary} и {secondary} — чем всё закончилось")
    if third:
        title_candidates.append(f"{third} — эпизод эфира")
    titles = _metadata_unique(title_candidates, limit=10, title_limit=100)

    listed_topics = topics[:5] or [primary]
    if len(listed_topics) == 1:
        topic_sentence = listed_topics[0]
    else:
        topic_sentence = "; ".join(listed_topics[:-1]) + "; а также " + listed_topics[-1]
    description = (
        f"В этой нарезке собраны конкретные события эфира: {topic_sentence}. "
        f"В монтаж вошло {int(context.get('selected_segments') or 0)} фрагментов."
    )
    montage_summary = f"Главные темы монтажа: {topic_sentence}. Отобрано {int(context.get('selected_segments') or 0)} сцен."

    chapters = _metadata_chapters(context)

    specific_tags = []
    specific_tags.extend(topic.lower() for topic in topics[:5] if len(topic) <= 60)
    specific_tags.extend(keywords[:14])
    if str(context.get("stream_kind") or "").upper() == "IRL":
        specific_tags.extend(["IRL", "ирл стрим"])
    specific_tags.extend(["стрим", "нарезка", "хайлайты"])
    tags = _metadata_unique(specific_tags, limit=30)
    hashtags = ["#" + re.sub(r"[^A-Za-zА-Яа-яЁё0-9_]", "", tag.replace(" ", "")) for tag in tags[:5]]
    hashtags = [tag for tag in hashtags if len(tag) > 1]

    thumb = [
        f"Кадр события «{primary}» + реальная эмоция участника + текст 2–4 слова",
    ]
    if secondary:
        thumb.append(f"Разделённый кадр: «{primary}» и «{secondary}», без выдуманных объектов")
    thumb.append("Использовать самый узнаваемый реальный кадр выбранной сцены, имена — только из анализа")

    data = {
        "ok": True,
        "ai_used": False,
        "strict_ai": False,
        "error": "",
        "warning": "Creator Pack собран локально из конкретных сцен. Для более выразительных формулировок доступно AI-обновление.",
        "titles": titles,
        "short_titles": _metadata_unique([primary, secondary, third] + titles, limit=5, title_limit=55),
        "description": description,
        "montage_summary": montage_summary,
        "short_summary": f"{primary}: главное событие этого эфира.",
        "chapters": chapters,
        "tags": tags,
        "hashtags": hashtags,
        "thumbnail_ideas": thumb,
        "hook_options": [f"Начать с развязки сцены «{primary}».", f"Показать реакцию на «{primary}», затем вернуть начало ситуации."],
        "metadata_context_version": METADATA_CONTEXT_VERSION,
        "context_signature": context.get("signature"),
        "stream_context_summary": {
            "stream_kind": context.get("stream_kind"),
            "selected_segments": context.get("selected_segments"),
            "selected_duration_seconds": context.get("selected_duration_seconds"),
            "topics": topics[:10],
            "keywords": keywords[:18],
        },
    }
    data["specificity_audit"] = metadata_specificity_audit(data, context)
    if not primary or not context.get("selected_segments"):
        data.update(ok=False, error="Нет финальных сцен с достаточным контекстом. Добавьте фрагменты или завершите анализ.",
                    titles=[], short_titles=[], description="", montage_summary="", short_summary="", tags=[],
                    hashtags=[], chapters=[], thumbnail_ideas=[], hook_options=[])
        data["specificity_audit"] = metadata_specificity_audit(data, context)
    return data


def generate_youtube_metadata(project_dir: Path, settings: dict[str, Any], logger: JobLogger | None = None) -> dict[str, Any]:
    """Create YouTube metadata.

    v8.7.6 adds AI 100% / Strict mode:
    - fast metadata is still available when strict is off;
    - if require_ai_metadata or ai_strict_mode is enabled, fallback is forbidden;
    - the function retries Ollama and fails loudly instead of pretending AI worked.
    """
    check_cancel(project_dir)
    p = project_paths(project_dir)
    segs = read_json(p["segments"], [])
    strict_metadata = bool(settings.get("require_ai_metadata", False) or strict_ai_enabled(settings))
    if strict_metadata:
        settings = dict(settings)
        settings["metadata_ai_enabled"] = True

    if not segs:
        data = {
            "ok": False,
            "ai_used": False,
            "strict_ai": strict_metadata,
            "error": "Нет финального монтажа. Сначала сделай Анализ или добавь фрагменты в Финальный монтаж.",
            "warning": "",
            "titles": [],
            "short_titles": [],
            "description": "",
            "montage_summary": "",
            "short_summary": "",
            "chapters": [],
            "tags": [],
            "hashtags": [],
            "thumbnail_ideas": [],
            "hook_options": [],
        }
        return data

    max_items = max(5, min(40, int(settings.get("metadata_max_segments", 25) or 25)))
    context = build_stream_context(project_dir, settings, max_items=max_items)
    fallback = build_specific_metadata_fallback(project_dir, settings, context=context)
    write_json(project_dir / "stream_context.json", context)

    def save_metadata(data: dict[str, Any]) -> dict[str, Any]:
        check_cancel(project_dir)
        if build_stream_context(project_dir, settings, max_items=1)["signature"] != context["signature"]:
            raise RuntimeError("Монтаж изменился во время генерации метаданных. Обновите Creator Pack для текущих сцен.")
        for key in ["titles", "short_titles", "chapters", "tags", "hashtags", "thumbnail_ideas", "hook_options"]:
            if not isinstance(data.get(key), list):
                data[key] = [] if strict_metadata else fallback[key]
        for key in ["description", "montage_summary", "short_summary", "error", "warning"]:
            if data.get(key) is None:
                data[key] = ""
        data.setdefault("ok", True)
        data.setdefault("ai_used", False)
        data["strict_ai"] = strict_metadata
        data["metadata_context_version"] = METADATA_CONTEXT_VERSION
        data["context_signature"] = context.get("signature")
        data.setdefault("stream_context_summary", fallback.get("stream_context_summary", {}))
        data["specificity_audit"] = metadata_specificity_audit(data, context)
        data["titles"] = _metadata_unique(list(data.get("titles") or []), limit=10, title_limit=100)
        data["short_titles"] = _metadata_unique(list(data.get("short_titles") or []), limit=5, title_limit=55)
        data["tags"] = _metadata_unique(list(data.get("tags") or []), limit=30)
        data["chapters"] = _metadata_chapters(context) if data.get("ok") else []
        data["field_sources"] = {"chapters": "montage_timeline", "text": "ai" if data.get("ai_used") else "scene_evidence"}
        data["specificity_audit"] = metadata_specificity_audit(data, context)
        write_json(project_dir / "youtube_metadata.json", data)
        (project_dir / "youtube_description.txt").write_text(str(data.get("description", "")), encoding="utf-8")
        (project_dir / "montage_summary.txt").write_text(str(data.get("montage_summary", "")), encoding="utf-8")
        (project_dir / "short_summary.txt").write_text(str(data.get("short_summary", "")), encoding="utf-8")
        (project_dir / "chapters.txt").write_text("\n".join(data.get("chapters", [])), encoding="utf-8")
        (project_dir / "title_suggestions.txt").write_text("\n".join(data.get("titles", [])), encoding="utf-8")
        (project_dir / "short_title_suggestions.txt").write_text("\n".join(data.get("short_titles", [])), encoding="utf-8")
        (project_dir / "tags.txt").write_text(", ".join(data.get("tags", [])), encoding="utf-8")
        (project_dir / "hashtags.txt").write_text(" ".join(data.get("hashtags", [])), encoding="utf-8")
        (project_dir / "thumbnail_ideas.txt").write_text("\n".join(data.get("thumbnail_ideas", [])), encoding="utf-8")
        return data

    # Fast/offline metadata remains available only when strict is off.
    if not bool(settings.get("metadata_ai_enabled", False)) and not strict_metadata:
        return save_metadata(dict(fallback))

    ai = make_ai_client(settings, cancel_file=cancel_path(project_dir))
    model = effective_text_model(settings)
    timeout = int(settings.get("metadata_timeout", 1800 if strict_metadata else 90))
    if strict_metadata:
        timeout = max(timeout, 1800)
    retries = max(1, min(10, int(settings.get("metadata_ai_retries", settings.get("ai_retry_count", 5 if strict_metadata else 2)) or 1)))
    compact_context = json.dumps(
        {
            "stream_kind": context.get("stream_kind"),
            "selected_segments": context.get("selected_segments"),
            "selected_duration_seconds": context.get("selected_duration_seconds"),
            "topics_found": context.get("topics"),
            "keywords_found": context.get("keywords"),
            "representative_scenes": context.get("scenes"),
        },
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""
Сделай YouTube metadata для итогового хайлайта именно этого стрима.

Правила:
- Используй только финальный монтаж.
- Сначала пойми центральную тему, участников и 3-7 реальных событий по паспорту стрима.
- Каждое название должно содержать конкретное имя, объект, занятие, место, тему разговора или событие из паспорта.
- Запрещены названия, состоящие только из общих слов: «лучшие моменты», «реакции», «стрим в хаосе», «без воды».
- Никогда не используй названия настроек и пресетов («Сбалансированный», «Качество», «Auto») как тему ролика.
- Не добавляй людей, игры, места и события, которых нет в паспорте или расшифровке.
- Если имя распознано неуверенно, используй подтверждённое событие без имени.
- Описание должно перечислить реальные эпизоды этого стрима, а не универсальные достоинства нарезки.
- Сначала ставь конкретные теги: имена, игра, место, занятие и событие; общие теги оставь в конце.
- Главы считай по времени итогового ролика, начиная с 00:00.
- Пиши на русском.
- Верни только JSON.
- Не делай слишком длинное описание.
- Не копируй служебные поля FINAL, SOURCE, score и confidence в текст для зрителя.

Верни JSON с ключами:
{{
 "ok": true,
 "titles": ["10 разных названий"],
 "short_titles": ["5 коротких названий до 55 символов"],
 "description": "готовое описание для YouTube",
 "montage_summary": "что происходит в монтаже, 3-5 предложений",
 "short_summary": "1 предложение",
 "chapters": ["00:00 Название главы"],
 "tags": ["tag1", "tag2"],
 "hashtags": ["#стрим"],
 "thumbnail_ideas": ["идея обложки"],
 "hook_options": ["вариант хука"]
}}

Паспорт конкретного стрима, построенный только из выбранных сцен и их расшифровки:
{compact_context}
""".strip()

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        check_cancel(project_dir)
        if logger:
            logger.set_step(
                "metadata_ai", attempt, retries, None, f"AI Metadata попытка {attempt}/{retries}", global_start=10, global_end=90
            )
        try:
            data = ai.generate_json(prompt, model=model, timeout=timeout, operation="metadata_ai")
            if not isinstance(data, dict):
                raise ValueError("Ollama вернула не JSON-объект")
            for field in ("titles", "short_titles", "tags", "hashtags", "chapters", "thumbnail_ideas", "hook_options"):
                if field in data and (not isinstance(data[field], list) or any(not isinstance(x, str) for x in data[field])):
                    raise ValueError(f"AI metadata: поле {field} должно быть списком строк")
            for field in ("description", "montage_summary", "short_summary"):
                if field in data and not isinstance(data[field], str):
                    raise ValueError(f"AI metadata: поле {field} должно быть строкой")
            if data.get("ok") is False or data.get("error"):
                raise ValueError("AI metadata сообщила об ошибке: " + str(data.get("error") or "ok=false"))
            required = [
                "titles",
                "short_titles",
                "description",
                "montage_summary",
                "short_summary",
                "chapters",
                "tags",
                "hashtags",
                "thumbnail_ideas",
                "hook_options",
            ]
            missing = [k for k in required if data.get(k) in (None, "", [])]
            if strict_metadata and missing:
                raise ValueError(f"AI metadata неполная, пустые поля: {', '.join(missing)}")
            specificity = metadata_specificity_audit(data, context)
            if context.get("keywords") and not specificity.get("passed"):
                raise ValueError(
                    "AI metadata получилась слишком общей: "
                    f"конкретных названий {specificity.get('specific_titles', 0)}, "
                    f"совпадений с паспортом {specificity.get('matched_count', 0)}"
                )
            data.setdefault("ok", True)
            data["ai_used"] = True
            data.setdefault("error", "")
            data.setdefault("warning", "")
            if not strict_metadata:
                for key, value in fallback.items():
                    if key not in data or data.get(key) in (None, "", []):
                        data[key] = value
            context_terms = set(context.get("keywords") or [])
            for topic in context.get("topics") or []:
                context_terms.update(_metadata_words(topic))
            description_low = str(data.get("description") or "").lower().replace("ё", "е")
            if context_terms and not any(term in description_low for term in context_terms):
                data["description"] = fallback["description"]
                data["warning"] = (
                    str(data.get("warning") or "") + " Описание AI было общим и заменено доказательным вариантом."
                ).strip()
            # Specific stream terms must appear before universal YouTube tags.
            data["tags"] = _metadata_unique(
                list(fallback.get("tags") or []) + list(data.get("tags") or []),
                limit=30,
            )
            data["specificity_audit"] = specificity
            return save_metadata(data)
        except OperationCancelled:
            raise
        except Exception as exc:
            from ..infrastructure.support_bundle import redact_text
            last_exc = RuntimeError(redact_text(str(exc)))
            with (project_dir / "logs.txt").open("a", encoding="utf-8") as log:
                log.write(redact_text(f"AI Metadata attempt {attempt}/{retries} failed: {exc}") + "\n")
            if attempt < retries:
                for _ in range(min(20, 2 * attempt) * 5):
                    check_cancel(project_dir)
                    time.sleep(0.2)

    if strict_metadata:
        data = {
            "ok": False,
            "ai_used": False,
            "strict_ai": True,
            "error": f"AI 100% режим: metadata не сгенерирована после {retries} попыток. Fallback запрещён. Последняя ошибка: {last_exc}",
            "warning": "",
            "titles": [],
            "short_titles": [],
            "description": "",
            "montage_summary": "",
            "short_summary": "",
            "chapters": [],
            "tags": [],
            "hashtags": [],
            "thumbnail_ideas": [],
            "hook_options": [],
        }
        raise RuntimeError(data["error"])

    check_cancel(project_dir)
    previous = read_json(project_dir / "youtube_metadata.json", {})
    if isinstance(previous, dict) and previous.get("titles") and previous.get("ok", True):
        # A failed regeneration must never replace a user's saved result. Return
        # the failure separately; creator_pack checks its context before display.
        raise RuntimeError(f"AI Metadata не получена после {retries} попыток. Предыдущий результат сохранён. {last_exc}")

    data = dict(fallback)
    data["warning"] = (
        f"AI Metadata не получена или не прошла проверку после {retries} попыток. Сохранён локальный вариант из сцен. Детали: {last_exc}"
    )
    data["error"] = ""
    return save_metadata(data)


def result_check(
    project_dir: Path,
    final_path: Path | None = None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    p = project_paths(project_dir)
    if final_path is None:
        final_path = p["outputs"] / "highlight_final.mp4"
    report = {"file": str(final_path), "ok": True, "warnings": []}
    if not final_path.exists():
        report["ok"] = False
        report["warnings"].append("Итоговый файл не найден")
        if persist:
            write_json(project_dir / "result_check.json", report)
        return report
    try:
        report["size_mb"] = round(final_path.stat().st_size / 1024 / 1024, 2)
        if report["size_mb"] < 1:
            report["warnings"].append("Файл слишком маленький")
    except Exception as exc:
        report["warnings"].append(f"Не удалось проверить размер: {exc}")
    try:
        duration_sec = video_duration(final_path)
        report["duration"] = tc(duration_sec)
        report["duration_seconds"] = round(duration_sec, 3)
        if duration_sec <= 0.5:
            report["ok"] = False
            report["warnings"].append("Длительность итогового файла слишком мала")
    except Exception as exc:
        report["ok"] = False
        report["warnings"].append(f"Не удалось определить длительность: {exc}")
    info = video_info(final_path)
    report["video"] = info
    if info.get("error"):
        report["ok"] = False
        report["warnings"].append(f"Не удалось прочитать видеопоток: {info.get('error')}")
    if int(info.get("width") or 0) <= 0 or int(info.get("height") or 0) <= 0:
        report["ok"] = False
        report["warnings"].append("В итоговом файле не найден корректный видеопоток")
    streams = audio_streams(final_path)
    report["audio_streams"] = len(streams)
    if not streams:
        report["ok"] = False
        report["warnings"].append("В итоговом файле нет аудио")
    srt = project_dir / "highlight_subtitles.srt"
    report["srt_exists"] = srt.exists()
    if persist:
        write_json(project_dir / "result_check.json", report)
    return report


def model_benchmark(project_dir: Path, settings: dict[str, Any], logger: JobLogger) -> list[dict[str, Any]]:
    p = project_paths(project_dir)
    ffmpeg = which("ffmpeg") or "ffmpeg"
    temp = project_dir / "benchmark_tmp"
    temp.mkdir(exist_ok=True)
    sample = temp / "sample_5min.mp4"
    logger.set_status("running", 5, "Benchmark: делаю 5-минутный sample")
    r = run_cmd(
        [ffmpeg, "-y", "-i", str(p["video"]), "-t", "300", "-c", "copy", str(sample)],
        project_dir=project_dir,
        cancel_file=cancel_path(project_dir),
    )
    if r.returncode != 0:
        raise RuntimeError(r.stdout)

    # Extract audio/transcribe sample directly using a small temporary project.
    sample_project = temp / "project"
    sample_project.mkdir(exist_ok=True)
    shutil.copy2(sample, sample_project / "input.mp4")
    sample_logger = JobLogger(sample_project)
    sample_settings = dict(settings)
    sample_settings["chunk_seconds"] = 300
    transcript = transcribe(sample_project, sample_settings, sample_logger)
    text = "\n".join(f"[{tc(x.start)}-{tc(x.end)}] {x.text}" for x in transcript)[:12000]

    ai = make_ai_client(settings, cancel_file=cancel_path(project_dir))
    models = settings.get("benchmark_models", ["qwen3:8b", "qwen2.5:7b", "qwen2.5:3b"])
    results = []
    for model in models:
        logger.set_status("running", 20, f"Benchmark: {model}")
        prompt = f"""
Оцени первые 5 минут как AI-монтажёр.
Верни JSON:
{{"model_quality": 0, "summary": "", "best_clips": [{{"start":"00:00:10","end":"00:00:50","score":8,"title":"","reason":""}}], "problems":[]}}

Транскрипт:
{text}
""".strip()
        t0 = time.time()
        try:
            data = ai.generate_json(prompt, model=model, timeout=240, operation="model_benchmark")
            elapsed = round(time.time() - t0, 1)
            scores = [float(x.get("score", 0)) for x in data.get("best_clips", []) if isinstance(x, dict)]
            quality = int(data.get("model_quality", 0) or (sum(scores) / max(1, len(scores)) * 10))
            results.append({"model": model, "ok": True, "seconds": elapsed, "quality": quality, "result": data})
        except Exception as exc:
            results.append({"model": model, "ok": False, "error": str(exc)})
    write_json(project_dir / "model_benchmark_5min.json", results)
    logger.set_status("done", 100, "Benchmark готов")
    return results


USER_OUTPUT_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".srt", ".vtt", ".edl", ".fcpxml", ".jpg", ".jpeg", ".png", ".json", ".txt"}
ROOT_USER_OUTPUT_FILES = {
    "highlight_subtitles.srt",
    "youtube_metadata.json",
    "thumbnail_ideas.json",
    "selection_report.json",
}
INTERNAL_OUTPUT_PARTS = {
    "render_parts",
    "transcript_chunks",
    "ai_batches",
    "micro_batches",
    "preview",
    "hls",
    "frames",
    "benchmark_tmp",
    "exports",
}
INTERNAL_OUTPUT_NAMES = {
    "project.json",
    "status.json",
    "status_history.json",
    "cache_manifest.json",
    "result_check.json",
    "pre_render_check.json",
    "quality_report.json",
    "visual_quality_report.json",
    "quality_core_report.json",
    "logs.txt",
    "concat.txt",
    "content_factory_manifest.json",
}


def _output_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    lowered = path.as_posix().lower()
    if suffix in {".mp4", ".mkv", ".mov", ".webm"}:
        return "short" if "short" in lowered else "video"
    if suffix in {".srt", ".vtt"}:
        return "subtitles"
    if suffix in {".edl", ".fcpxml"}:
        return "timeline"
    if suffix in {".jpg", ".jpeg", ".png"}:
        return "thumbnail"
    if "metadata" in lowered:
        return "metadata"
    if "segments" in lowered or "candidate" in lowered:
        return "edit_data"
    return "document"


def list_output_files(project_dir: Path) -> list[dict[str, Any]]:
    """Return only user-facing deliverables, without duplicates or internals."""
    candidates: list[Path] = []
    for directory in (project_dir / "outputs", project_dir / "content_factory"):
        if directory.exists():
            candidates.extend(path for path in directory.rglob("*") if path.is_file())
    candidates.extend(project_dir / name for name in ROOT_USER_OUTPUT_FILES if (project_dir / name).is_file())

    manifest = read_json(project_dir / "shorts_render_manifest.json", {}) or {}
    rows = manifest.get("rendered") if isinstance(manifest, dict) else []
    reports = {str(row.get("path")): row.get("reframe_report")
               for row in rows if isinstance(row, dict)} if isinstance(rows, list) else {}
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(candidates, key=lambda item: item.relative_to(project_dir).as_posix().lower()):
        rel_path = path.relative_to(project_dir)
        rel = rel_path.as_posix()
        if rel in seen or path.suffix.lower() not in USER_OUTPUT_EXTENSIONS:
            continue
        if set(rel_path.parts) & INTERNAL_OUTPUT_PARTS or path.name in INTERNAL_OUTPUT_NAMES:
            continue
        seen.add(rel)
        try:
            stat = path.stat()
            size_bytes = stat.st_size
        except OSError:
            continue
        files.append(
            {
                "name": path.name,
                "path": rel,
                "url": f"/api/projects/{project_dir.name}/file/{rel}",
                "size_mb": round(size_bytes / 1024 / 1024, 2),
                "size_bytes": size_bytes,
                "modified_at": stat.st_mtime,
                "revision": str(stat.st_mtime_ns),
                "kind": _output_kind(rel_path),
                **({"reframe_report": reports[rel]} if isinstance(reports.get(rel), dict) else {}),
            }
        )
    return files


def apply_quality_score_calibration(candidates: list[Candidate], settings: dict[str, Any], logger: JobLogger) -> list[Candidate]:
    """Final score calibration: make selection less random and more highlight-oriented."""
    calibrated = []
    for c in candidates:
        text = f"{c.title} {c.reason} {c.text_preview}".lower()
        bonus = 0.0
        penalty = 0.0

        strong_words = [
            "смеш",
            "смех",
            "реакц",
            "донат",
            "конфликт",
            "спор",
            "крич",
            "удив",
            "хаос",
            "мем",
            "шут",
            "эмоц",
            "неожидан",
            "важн",
            "истори",
            "личн",
        ]
        weak_words = ["техничес", "подключ", "микрофон", "камера", "ссылка", "ожид", "настрой", "пауза", "повтор", "реклама", "вода"]
        if any(w in text for w in strong_words):
            bonus += 0.35
        if any(w in text for w in weak_words):
            penalty += 0.45
        dur = max(0.0, c.end - c.start)
        if dur > 180 and settings.get("micro_cut_enabled", True):
            penalty += 0.25
        if 12 <= dur <= 75:
            bonus += 0.15

        c.score = round(max(0, min(10, float(c.score) + bonus - penalty)), 2)
        calibrated.append(c)
    logger.log("Quality calibration: применил бонусы/штрафы к score кандидатов.")
    return calibrated


def build_micro_windows_for_candidate(c: Candidate, transcript: list[TranscriptSegment], settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Split a block into speech-aware windows with a real duration ceiling.

    The previous implementation accumulated transcript segments across silent
    gaps and merged tiny tails even when the result exceeded ``micro_max``.
    Those candidates were later rejected by final selection, producing a large
    and unexplained target shortfall.  This implementation closes a window at
    silence, never merges across a gap, and applies a final hard cap.
    """
    micro_window = max(8.0, float(settings.get("micro_window_seconds", 45) or 45))
    micro_min = max(6.0, float(settings.get("micro_min_seconds", 20) or 20))
    micro_max = max(micro_window, float(settings.get("micro_max_seconds", 75) or 75))
    speech_gap = max(2.5, min(6.0, float(settings.get("micro_speech_gap_seconds", 4.0) or 4.0)))
    minimum_window = max(8.0, micro_min * 0.5)

    inside = sorted(
        (t for t in transcript if float(t.end) > float(c.start) and float(t.start) < float(c.end)),
        key=lambda item: (float(item.start), float(item.end)),
    )
    pieces: list[dict[str, Any]] = []
    for t in inside:
        piece_start = max(float(c.start), float(t.start))
        piece_end = min(float(c.end), float(t.end))
        if piece_end <= piece_start:
            continue
        # A legacy transcript without word timestamps can still contain a very
        # long segment. Split its timing so it can never bypass the montage
        # duration guard. Text is retained for AI review rather than discarded.
        cursor = piece_start
        while cursor < piece_end - 0.001:
            end = min(piece_end, cursor + micro_max)
            pieces.append({"start": cursor, "end": end, "text": str(t.text or "").strip()})
            cursor = end

    windows: list[dict[str, Any]] = []
    current_start: float | None = None
    current_end = 0.0
    current_text: list[str] = []

    def flush() -> None:
        nonlocal current_start, current_end, current_text
        if current_start is not None and current_end - current_start >= minimum_window:
            windows.append(
                {
                    "start": round(current_start, 3),
                    "end": round(current_end, 3),
                    "text": " ".join(part for part in current_text if part).strip(),
                    "parent_id": c.id,
                }
            )
        current_start = None
        current_end = 0.0
        current_text = []

    for piece in pieces:
        piece_start = float(piece["start"])
        piece_end = float(piece["end"])
        if current_start is None:
            current_start, current_end = piece_start, piece_end
            current_text = [str(piece.get("text", ""))]
            continue
        gap = piece_start - current_end
        projected = max(current_end, piece_end) - current_start
        if gap > speech_gap or projected > micro_max or (current_end - current_start >= micro_window and gap >= 0.8):
            flush()
            current_start, current_end = piece_start, piece_end
            current_text = [str(piece.get("text", ""))]
            continue
        current_end = max(current_end, piece_end)
        current_text.append(str(piece.get("text", "")))
    flush()

    if not windows:
        cursor = float(c.start)
        while cursor < float(c.end) - 0.001:
            end = min(float(c.end), cursor + min(micro_window, micro_max))
            if end - cursor >= minimum_window:
                windows.append(
                    {
                        "start": round(cursor, 3),
                        "end": round(end, 3),
                        "text": c.text_preview,
                        "parent_id": c.id,
                    }
                )
            cursor = end

    # Merge a short tail only when it belongs to the same continuous speech
    # region and the merged result remains inside the configured hard limit.
    merged: list[dict[str, Any]] = []
    for window in windows:
        duration = float(window["end"]) - float(window["start"])
        if merged and duration < micro_min:
            previous = merged[-1]
            gap = float(window["start"]) - float(previous["end"])
            combined = float(window["end"]) - float(previous["start"])
            if gap <= speech_gap and combined <= micro_max:
                previous["end"] = window["end"]
                previous["text"] = (str(previous.get("text", "")) + " " + str(window.get("text", ""))).strip()
                continue
        merged.append(window)

    return [
        window
        for window in merged
        if 0 < float(window["end"]) - float(window["start"]) <= micro_max + 0.001
    ]


def partition_micro_windows_for_context(
    windows: list[dict[str, Any]], *, max_items: int, num_ctx: int
) -> tuple[list[list[dict[str, Any]]], int]:
    """Group every micro window without risking context truncation.

    `max_items` is a throughput ceiling, not a quality knob.  The character
    budget is intentionally conservative for Russian/mixed Twitch text and
    leaves context space for instructions plus the JSON response.  No window
    is dropped; an unusually long single window is kept as its own batch.
    """
    max_items = max(1, min(20, int(max_items or 1)))
    num_ctx = max(2048, min(16384, int(num_ctx or 4096)))
    # Roughly 1.5 input characters per context token is conservative enough for
    # Cyrillic/mixed chat after reserving substantial room for prompt + output.
    char_budget = max(3200, min(24000, int(num_ctx * 1.5)))

    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for w in windows:
        text = str(w.get("text", ""))[:1800]
        # Include metadata/punctuation overhead used by the real prompt.
        estimated_chars = len(text) + 220
        if current and (len(current) >= max_items or current_chars + estimated_chars > char_budget):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(w)
        current_chars += estimated_chars
    if current:
        batches.append(current)
    return batches, char_budget


def effective_micro_batch_size(settings: dict[str, Any]) -> int:
    """Choose a latency-safe Micro AI batch without dropping any windows.

    Qwen3:8b on the common 16 GB RAM / 4 GB VRAM configuration can keep a
    healthy eight-item stream alive for 10-15 minutes and then hit a socket
    timeout. This is a prompt-latency limit, not GPU concurrency, so the
    quality-preserving fix is to split the same windows into smaller sequential
    requests. Explicit manual hardware mode remains untouched.
    """
    configured = max(1, min(20, int(settings.get("micro_batch_size", 8) or 8)))
    if bool(settings.get("hardware_manual_override_enabled", False)):
        return configured

    profile = str(settings.get("hardware_detected_profile") or "").upper()
    vram_match = re.search(r"VRAM(\d+)GB", profile)
    ram_match = re.search(r"(?:^|_)RAM(\d+)GB(?:_|$)", profile)
    vram_gb = int(vram_match.group(1)) if vram_match else 0
    ram_gb = int(ram_match.group(1)) if ram_match else 0
    if (vram_gb and vram_gb <= 6) or (ram_gb and ram_gb <= 18):
        return min(configured, 4)
    return configured


def enforce_micro_ai_completion(
    project_dir: Path,
    settings: dict[str, Any],
    logger: JobLogger,
) -> dict[str, Any]:
    """Fail honestly when Micro AI coverage is too low for a final montage.

    Normal mode may safely tolerate an isolated failed batch, but it must not
    publish a severely partial montage as a completed analysis. Existing
    segments are deliberately left untouched so pressing Analyze/Continue can
    resume from per-window checkpoints.
    """
    if not bool(settings.get("micro_cut_enabled", True)):
        return {
            "total": 0,
            "analyzed": 0,
            "percent": 100.0,
            "required_percent": 0.0,
            "complete": True,
            "skipped": True,
        }

    coverage = read_json(project_dir / "ai_coverage_report.json", {}) or {}
    total = max(0, int(coverage.get("micro_windows_total", 0) or 0))
    analyzed = max(0, min(total, int(coverage.get("micro_ai_analyzed", 0) or 0)))
    percent = round(100.0 * analyzed / max(1, total), 2) if total else 100.0
    required_percent = 100.0 if strict_ai_enabled(settings) else 90.0
    result = {
        "total": total,
        "analyzed": analyzed,
        "percent": percent,
        "required_percent": required_percent,
        "complete": not total or percent + 0.001 >= required_percent,
    }
    if result["complete"]:
        return result

    previous_segments = read_json(project_paths(project_dir)["segments"], []) or []
    previous_seconds = 0.0
    for item in previous_segments if isinstance(previous_segments, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            previous_seconds += max(
                0.0,
                float(item.get("end", 0) or 0) - float(item.get("start", 0) or 0),
            )
        except (TypeError, ValueError):
            continue
    message = (
        f"Micro AI не завершён: обработано {analyzed}/{total} окон ({percent:.2f}%), "
        f"для честного результата требуется не менее {required_percent:.0f}%. "
        "Предыдущий монтаж не заменён. Нажмите «Анализировать/Продолжить» — "
        "готовые окна будут взяты из checkpoints."
    )
    write_json(
        project_dir / "analysis_health.json",
        {
            "outcome": "incomplete_micro_ai",
            "analysis_complete": False,
            "retryable": True,
            "degraded": True,
            "strict_ai": strict_ai_enabled(settings),
            "micro_ai_coverage_percent": percent,
            "micro_ai_analyzed": analyzed,
            "micro_windows_total": total,
            "required_micro_ai_coverage_percent": required_percent,
            "micro_failed_batches": coverage.get("micro_failed_batches", []),
            "preserved_previous_segments": len(previous_segments) if isinstance(previous_segments, list) else 0,
            "preserved_previous_seconds": round(previous_seconds, 2),
            "message": message,
            "updated_at": time.time(),
        },
    )
    logger.log(f"Analysis health: incomplete_micro_ai; {message}")
    raise RuntimeError(message)


def select_micro_source_blocks(
    candidates: list[Candidate], target_sec: float, settings: dict[str, Any]
) -> tuple[list[Candidate], dict[str, Any]]:
    """Choose blocks for micro analysis without giving early timestamps an advantage.

    The final montage is *not* forced to be temporally uniform.  Temporal
    stratification is used only to make sure a strong late block gets a fair
    second-pass evaluation before global quality ranking.
    """
    if not candidates:
        return [], {"selected": 0, "buckets": []}

    reject_conf = float(settings.get("non_primary_reject_confidence", 0.72) or 0.72)
    eligible = [
        c for c in candidates
        if not (
            settings.get("semantic_quality_guard_enabled", True)
            and str(c.content_class or "unknown").lower() in NON_PRIMARY_CONTENT_CLASSES
            and float(c.content_class_confidence or 0.0) >= reject_conf
        )
    ]
    if settings.get("full_ai_coverage", False):
        selected = sorted(eligible, key=lambda x: candidate_selection_key(x, settings), reverse=True)
        return selected, {"mode": "full", "selected": len(selected), "eligible": len(eligible), "buckets": []}

    ranked = sorted(eligible, key=lambda x: candidate_selection_key(x, settings), reverse=True)
    top_n = max(3, int(settings.get("top_blocks_for_micro", 24) or 24))
    min_final = max(1, int(settings.get("min_final_segments", 8) or 8))
    source_duration_multiplier = max(
        2.0, min(8.0, float(settings.get("micro_source_duration_multiplier", 4.0) or 4.0))
    )
    duration_budget = max(target_sec * source_duration_multiplier, target_sec + 900.0)
    selected: list[Candidate] = []
    selected_ids: set[str] = set()
    selected_duration = 0.0

    def add(c: Candidate) -> None:
        nonlocal selected_duration
        identity = candidate_identity(c)
        if identity in selected_ids:
            return
        selected_ids.add(identity)
        selected.append(c)
        selected_duration += candidate_duration(c)

    # Global quality leaders. This remains the dominant source of blocks.
    for c in ranked[:top_n]:
        add(c)
        if selected_duration >= duration_budget and len(selected) >= min_final:
            break

    # Strong blocks are always preserved regardless of which time bucket they
    # came from.  This avoids a top_n cliff around close scores.
    strong_floor = float(settings.get("micro_global_score_floor", 7.6) or 7.6)
    for c in ranked:
        if float(c.score or 0.0) >= strong_floor:
            add(c)

    bucket_seconds = max(300.0, float(settings.get("temporal_fairness_bucket_seconds", 900) or 900))
    per_bucket = max(1, min(5, int(settings.get("temporal_fairness_blocks_per_bucket", 2) or 2)))
    fairness_floor = float(settings.get("temporal_fairness_min_score", 4.5) or 4.5)
    max_end = max(float(c.end) for c in eligible) if eligible else 0.0
    bucket_count = max(1, int(math.ceil(max_end / bucket_seconds)))
    bucket_report: list[dict[str, Any]] = []
    if settings.get("temporal_fairness_enabled", True):
        for bucket in range(bucket_count):
            start = bucket * bucket_seconds
            end = min(max_end, (bucket + 1) * bucket_seconds)
            pool = [c for c in eligible if start <= float(c.start) < max(start + 0.001, end)]
            pool = sorted(pool, key=lambda x: candidate_selection_key(x, settings), reverse=True)
            picked = []
            for c in pool:
                if float(c.score or 0.0) < fairness_floor:
                    continue
                before = len(selected)
                add(c)
                if len(selected) > before:
                    picked.append(c.id)
                if len(picked) >= per_bucket:
                    break
            bucket_report.append({
                "start": round(start, 2), "end": round(end, 2),
                "eligible": len(pool), "added_ids": picked,
                "best_score": round(float(pool[0].score), 2) if pool else None,
            })

    selected = sorted(selected, key=lambda x: candidate_selection_key(x, settings), reverse=True)
    report = {
        "mode": "quality_plus_temporal_fairness",
        "eligible": len(eligible),
        "selected": len(selected),
        "selected_seconds": round(sum(candidate_duration(c) for c in selected), 2),
        "eligible_seconds": round(sum(candidate_duration(c) for c in eligible), 2),
        "source_duration_multiplier": source_duration_multiplier,
        "duration_budget_seconds": round(duration_budget, 2),
        "eligible_coverage_percent": round(
            100 * sum(candidate_duration(c) for c in selected) / max(1.0, sum(candidate_duration(c) for c in eligible)),
            2,
        ),
        "source_max_timestamp": round(max_end, 2),
        "strong_floor": strong_floor,
        "fairness_floor": fairness_floor,
        "bucket_seconds": bucket_seconds,
        "buckets": bucket_report,
        "selected_timestamps": [round(float(c.start), 2) for c in sorted(selected, key=lambda x: x.start)],
    }
    return selected, report


def build_micro_candidates(
    project_dir: Path, candidates: list[Candidate], transcript: list[TranscriptSegment], settings: dict[str, Any], logger: JobLogger
) -> list[Candidate]:
    """Second pass: split high-score blocks into many smaller dynamic clips."""
    if not settings.get("micro_cut_enabled", True):
        return candidates
    micro_rejections: list[dict[str, Any]] = []
    write_json(project_dir / "micro_rejections.json", micro_rejections)

    target_sec = float(settings.get("target_minutes", 30)) * 60
    max_final = max(5, effective_max_final_segments(settings, target_sec))

    # Analyze global quality leaders + temporal champions from the whole VOD.
    # Temporal fairness only decides who gets a second-pass chance; the final
    # selection is still a global quality competition.
    selected_blocks, micro_source_report = select_micro_source_blocks(candidates, target_sec, settings)
    write_json(project_dir / "micro_source_selection.json", micro_source_report)
    if settings.get("full_ai_coverage", False):
        logger.log(f"AI 100% coverage: micro-cut будет анализировать все допустимые крупные блоки: {len(selected_blocks)}")
    else:
        logger.log(
            f"Micro temporal fairness: {len(selected_blocks)}/{len(candidates)} blocks, "
            f"last={tc(max((c.end for c in selected_blocks), default=0.0))}"
        )

    logger.set_status("running", 72, f"Micro-cut: дроблю {len(selected_blocks)} блоков на короткие куски")

    windows = []
    for c in selected_blocks:
        for w in build_micro_windows_for_candidate(c, transcript, settings):
            w["parent_score"] = c.score
            w["parent_title"] = c.title
            w["parent_reason"] = c.reason
            w["parent_content_class"] = c.content_class
            w["parent_content_confidence"] = c.content_class_confidence
            w["parent_semantic_topic"] = c.semantic_topic
            w["parent_template_signature"] = c.template_signature
            windows.append(w)

    if not windows:
        update_ai_coverage_report(
            project_dir,
            micro_windows_total=0,
            micro_ai_analyzed=0,
            micro_coverage_percent=100.0,
            micro_failed_batches=[],
        )
        return candidates

    write_json(project_dir / "micro_windows_raw.json", windows)

    ai = make_ai_client(settings, cancel_file=cancel_path(project_dir))
    model = effective_text_model(settings)
    timeout = int(settings.get("ollama_timeout", 600))
    configured_batch_size = max(1, min(20, int(settings.get("micro_batch_size", 8) or 8)))
    batch_size = effective_micro_batch_size(settings)
    strict_ai = strict_ai_enabled(settings)
    complete_ai = ai_batch_completeness_required(settings)
    retry_budget = ai_retry_count(settings)
    # Transport stalls/connection errors are already retried inside
    # AIExecutionController. These are semantic retry budgets only, so we do not
    # multiply a 45s transport stall into an unbounded retry storm.
    semantic_retries = max(1, min(2, retry_budget))
    rescue_retries = max(1, min(3, retry_budget))
    source_span = max((float(c.end) for c in candidates), default=0.0)
    retention_ratio = min(1.0, target_sec / max(1.0, source_span))
    longform_instruction = (
        "Это ДЛИННАЯ НАРЕЗКА с высоким коэффициентом сохранения материала. "
        "Оставляй не только вирусные пики, но и понятные связующие части текущего live-геймплея/разговора, "
        "если они развивают ту же сцену. Не путай захват игры с prerecorded-вставкой."
        if retention_ratio >= 0.25
        else "Это хайлайт-нарезка: отдавай приоритет наиболее сильным законченным сценам."
    )

    def build_micro_ai_prompt(batch_items: list[dict[str, Any]]) -> str:
        batch_text = "\n\n".join(
            f"ID={i + 1}\nTIME={tc(w['start'])}-{tc(w['end'])}\nPARENT_SCORE={w.get('parent_score')}\nPARENT_TITLE={w.get('parent_title', '')}\nPARENT_CONTENT_CLASS={w.get('parent_content_class', 'unknown')}\nPARENT_TOPIC={w.get('parent_semantic_topic', '')}\nTEXT={w.get('text', '')[:1800]}"
            for i, w in enumerate(batch_items)
        )
        return f"""
Ты монтажёр хайлайтов. Перед тобой короткие микро-фрагменты из уже найденных сильных блоков.
Нужно выбрать, какие микро-фрагменты реально стоит оставить в динамичном монтаже.

{longform_instruction}
Целевая доля исходника: примерно {retention_ratio:.0%}. Это ориентир композиции, но не разрешение добавлять техническую воду.

Оцени каждый микро-фрагмент 0-10:
- 9-10: сильный СМЫСЛОВОЙ момент из основного live-контента: реакция, шутка, конфликт, донат, история, важный поворот с понятным payoff.
- 7-8: хороший самостоятельный кусок основного стрима.
- 5-6: средний; обычно НЕ оставлять только ради длительности.
- 0-4: вода, техника, ожидание, reconnect, intermission, replay/старые хайлайты, реклама, обычный разговор без развития.

Важно:
- Сначала классифицируй content_class. waiting/reconnect/intermission/replay/prerecorded/advertisement нужно удалять, даже если внутри старой вставки есть яркая реплика или смех.
- live_reaction разрешён только когда стример СЕЙЧАС активно реагирует/комментирует показанный материал и именно его реакция является содержанием текущего стрима.
- НЕ выбирай длинную воду только потому что родительский блок был сильный.
- Не разрезай сцену так, чтобы осталась только реакция без причины или только punchline без setup. Фрагмент должен быть понятен по смыслу.
- Лучше короткая законченная мини-сцена, чем случайный яркий кусок без контекста.
- Если микро-фрагмент начинается/заканчивается резко, укажи extra_start_seconds/extra_end_seconds.
- ОБЯЗАТЕЛЬНО верни ровно {len(batch_items)} элементов clips: по одному для каждого ID 1..{len(batch_items)}.
- Даже если фрагмент нужно удалить, всё равно верни его ID с keep=false. Не пропускай и не дублируй ID.

Верни JSON:
{{
 "clips": [
   {{"id": 1, "score": 8.5, "decision": "keep", "title": "короткое название", "reason": "почему оставить", "keep": true, "hook_potential": "low/medium/high", "moment_type": "reaction/conflict/donation/chat/story/visual/irl_event/general", "standalone_clarity": 0.8, "content_class": "primary_live/live_reaction/waiting/reconnect/intermission/replay/prerecorded/advertisement/unknown", "content_class_confidence": 0.9, "semantic_topic": "о чём сцена", "extra_start_seconds": 0, "extra_end_seconds": 0}}
 ]
}}

Микро-фрагменты:
{batch_text}
""".strip()

    micro_prompt_budget = ai_prompt_char_budget(settings)
    micro_batches = plan_prompt_safe_batches(windows, batch_size, build_micro_ai_prompt, micro_prompt_budget)
    micro_char_budget = micro_prompt_budget

    micro: list[Candidate] = []
    next_id = 1
    total_batches = max(1, len(micro_batches))
    micro_analyzed_ids: set[str] = set()
    micro_failed_batches: list[dict[str, Any]] = []

    micro_cache_dir = project_dir / "micro_batches"
    micro_cache_dir.mkdir(exist_ok=True)

    micro_item_cache_dir = project_dir / "micro_items"
    micro_item_cache_dir.mkdir(exist_ok=True)

    # Load/recover the model before Micro AI, but a local runtime failure is not
    # fatal in normal mode. The expensive block/visual work must remain usable.
    logger.set_step("micro_ai", 0, total_batches, 72, f"Micro AI: прогреваю {model} перед {total_batches} batch")
    warmup_result = _ai_warmup_with_recovery(
        ai, model, settings, logger, "Micro AI", timeout=min(180, max(60, timeout))
    )
    micro_ai_available = bool(warmup_result.get("ok"))
    write_json(
        project_dir / "ollama_micro_runtime.json",
        {
            "model": model,
            "configured_batch_size": configured_batch_size,
            "initial_effective_batch_size": batch_size,
            "effective_batch_sizes": [len(item) for item in micro_batches],
            "prompt_char_budget": micro_char_budget,
            "prompt_chars": [len(build_micro_ai_prompt(item)) for item in micro_batches],
            "num_ctx": int(settings.get("ollama_num_ctx", 4096) or 4096),
            "total_windows": len(windows),
            "total_batches": total_batches,
            "quality_guard": {"all_windows_preserved": sum(len(item) for item in micro_batches) == len(windows)},
            "warmup": warmup_result,
            "updated_at": time.time(),
        },
    )
    if micro_ai_available:
        logger.log(
            f"Micro AI warm-up: {model} готов за {warmup_result.get('elapsed_seconds')}s; "
            f"batch_size={batch_size}, batches={total_batches}"
        )
    else:
        warmup_error = str(warmup_result.get("error") or "unknown error")
        if strict_ai:
            raise RuntimeError(
                f"Micro AI warm-up не восстановился после {warmup_result.get('max_attempts', 1)} попыток: {warmup_error}"
            )
        logger.log(
            "Micro AI runtime недоступен. Не прерываю анализ: micro windows будут "
            "оценены консервативным fallback на основе уже AI-оценённых parent blocks."
        )

    # A transport stall switches the rest of this run to targeted single-window
    # rescue. This keeps every successful checkpoint and avoids replaying the
    # same large prompt that already proved too slow for the local machine.
    micro_adaptive_single_mode = False
    micro_runtime_recovery_count = 0
    micro_runtime_recovery_limit = max(2, min(6, retry_budget))
    micro_runtime_recoveries: list[dict[str, Any]] = []

    def recover_micro_runtime(reason: str, batch_number: int) -> bool:
        nonlocal micro_ai_available, micro_runtime_recovery_count, micro_adaptive_single_mode
        micro_adaptive_single_mode = True
        if micro_runtime_recovery_count >= micro_runtime_recovery_limit:
            micro_ai_available = False
            logger.log(
                f"Micro AI recovery limit reached ({micro_runtime_recovery_limit}); "
                "checkpoints сохранены для следующего запуска."
            )
            return False
        micro_runtime_recovery_count += 1
        recovery = _ai_warmup_with_recovery(
            ai,
            model,
            settings,
            logger,
            f"Micro AI recovery batch {batch_number}",
            timeout=min(180, max(60, timeout)),
            max_attempts=2,
        )
        micro_ai_available = bool(recovery.get("ok"))
        micro_runtime_recoveries.append(
            {
                "batch": batch_number,
                "reason": str(reason)[:1200],
                "ok": micro_ai_available,
                "attempt": micro_runtime_recovery_count,
                "result": recovery,
            }
        )
        if micro_ai_available:
            logger.log(
                f"Micro AI batch {batch_number}: Ollama восстановлен; "
                "перехожу на надёжные запросы по одному окну."
            )
        return micro_ai_available

    for batch_i in range(total_batches):
        check_cancel(project_dir)
        batch = micro_batches[batch_i]
        prompt = build_micro_ai_prompt(batch)
        expected_local_ids = set(range(1, len(batch) + 1))
        by_id: dict[int, dict[str, Any]] = {}
        last_exc: Exception | None = None
        batch_degraded = False
        cache_path = micro_cache_dir / f"micro_{batch_i + 1:04d}.json"
        cache_fp = micro_batch_fingerprint(project_dir, settings, model, batch_i, batch)
        cached = read_json(cache_path, None)

        update_ai_batch_health(
            project_dir, stage="micro_ai", batch=batch_i + 1, total=total_batches, state="running",
            expected_ids=expected_local_ids, prompt_chars=len(prompt),
        )

        # Per-window cache survives batch-boundary changes. Local IDs are
        # remapped to the current batch on load.
        item_hits = 0
        for local_id, window in enumerate(batch, start=1):
            item = _load_micro_ai_item_cache(
                micro_item_cache_dir, project_dir, settings, model, window, local_id
            )
            if item is not None:
                by_id[local_id] = item
                micro_analyzed_ids.add(f"{batch_i + 1}:{local_id}")
                item_hits += 1
        if item_hits:
            logger.log(
                f"Micro AI batch {batch_i + 1}/{total_batches}: per-window cache {item_hits}/{len(batch)}"
            )

        # Backward-compatible whole-batch checkpoints are promoted into the new
        # per-window cache immediately.
        if len(by_id) < len(expected_local_ids) and cache_matches(cached, cache_fp, "clips"):
            legacy_items = extract_ai_items(cached, "clips")
            legacy_by_id = normalize_ai_items_by_id(legacy_items, expected_local_ids)
            for local_id, item in legacy_by_id.items():
                by_id.setdefault(local_id, item)
                _save_micro_ai_item_cache(
                    micro_item_cache_dir, project_dir, settings, model, batch[local_id - 1], item
                )
                micro_analyzed_ids.add(f"{batch_i + 1}:{local_id}")
            if legacy_by_id:
                logger.log(f"Micro AI batch {batch_i + 1}/{total_batches}: legacy cache восстановлен")

        pending_ids = sorted(expected_local_ids - set(by_id))

        if pending_ids and not micro_ai_available:
            recover_micro_runtime(
                str(warmup_result.get("error") or "Ollama unavailable"), batch_i + 1
            )

        if pending_ids and micro_ai_available:
            # If the batch is entirely uncached, one normal batch request is the
            # most efficient path. If only a few windows are missing after a
            # resume, skip reprocessing cached windows and go directly to tiny
            # targeted requests below.
            should_primary = (
                not micro_adaptive_single_mode
                and (len(pending_ids) == len(batch) or len(pending_ids) >= max(4, len(batch) // 2))
            )
            if should_primary:
                for attempt in range(1, semantic_retries + 1):
                    check_cancel(project_dir)
                    try:
                        logger.set_step(
                            "micro_ai", batch_i, total_batches,
                            72 + 10 * (batch_i / total_batches),
                            f"Micro AI batch {batch_i + 1}/{total_batches}, attempt {attempt}/{semantic_retries}",
                        )
                        data = ai.generate_json(
                            prompt, model=model, timeout=timeout, operation="micro_ai",
                            trace_context={
                                "batch": batch_i + 1,
                                "total_batches": total_batches,
                                "expected_ids": sorted(expected_local_ids),
                            },
                            collection_key="clips", expected_ids=expected_local_ids,
                            required_item_fields={"id", "score"},
                            activity_callback=ai_activity_callback(
                                logger, stage="micro_ai", batch_index=batch_i, total_batches=total_batches,
                                global_start=72, global_end=82,
                                label=f"Micro AI batch {batch_i + 1}/{total_batches}"
                            ),
                        )
                        fresh_items = extract_ai_items(data, "clips")
                        fresh_by_id = normalize_ai_items_by_id(fresh_items, expected_local_ids)
                        for local_id, item in fresh_by_id.items():
                            by_id[local_id] = item
                            micro_analyzed_ids.add(f"{batch_i + 1}:{local_id}")
                            _save_micro_ai_item_cache(
                                micro_item_cache_dir, project_dir, settings, model, batch[local_id - 1], item
                            )
                        break
                    except OperationCancelled:
                        raise
                    except Exception as exc:
                        last_exc = exc
                        logger.log(
                            f"Micro AI batch {batch_i + 1}: primary attempt {attempt}/{semantic_retries} failed: {exc}"
                        )
                        if isinstance(exc, AITransportError):
                            break
                        if attempt < semantic_retries:
                            time.sleep(min(8, 2 * attempt))

            missing_now = sorted(expected_local_ids - set(by_id))

            if missing_now and isinstance(last_exc, AITransportError):
                recover_micro_runtime(str(last_exc), batch_i + 1)

            # Targeted single-window rescue is both more reliable and cheaper
            # than replaying a large batch after a partial response/cache hit.
            if missing_now and micro_ai_available:
                logger.log(f"Micro AI batch {batch_i + 1}: targeted rescue ID {missing_now}")
                for local_id in missing_now:
                    w = batch[local_id - 1]
                    single_prompt = f"""
Ты AI-монтажёр. Оцени ОДИН микро-фрагмент.
{longform_instruction}
Верни строго JSON:
{{"clips":[{{"id":{local_id},"score":0,"decision":"remove","title":"коротко","reason":"почему","keep":false,"hook_potential":"low","moment_type":"general","standalone_clarity":0.5,"extra_start_seconds":0,"extra_end_seconds":0}}]}}
Фрагмент:
ID={local_id}
TIME={tc(w["start"])}-{tc(w["end"])}
PARENT_SCORE={w.get("parent_score")}
PARENT_TITLE={w.get("parent_title", "")}
PARENT_CONTENT_CLASS={w.get("parent_content_class", "unknown")}
TEXT={w.get("text", "")[:1800]}
""".strip()
                    for rescue_attempt in range(1, rescue_retries + 1):
                        try:
                            rescue_data = ai.generate_json(
                                single_prompt, model=model, timeout=timeout, operation="micro_ai_single_id",
                                trace_context={"batch": batch_i + 1, "clip_id": local_id},
                                collection_key="clips", expected_ids={local_id}, required_item_fields={"id", "score"},
                                activity_callback=ai_activity_callback(
                                    logger, stage="micro_ai", batch_index=batch_i, total_batches=total_batches,
                                    global_start=72, global_end=82, label=f"Micro AI rescue ID={local_id}"
                                ),
                            )
                            rescue_items = extract_ai_items(rescue_data, "clips")
                            rescue_by_id = normalize_ai_items_by_id(rescue_items, {local_id})
                            if local_id in rescue_by_id:
                                item = rescue_by_id[local_id]
                                by_id[local_id] = item
                                micro_analyzed_ids.add(f"{batch_i + 1}:{local_id}")
                                _save_micro_ai_item_cache(
                                    micro_item_cache_dir, project_dir, settings, model, w, item
                                )
                                break
                        except OperationCancelled:
                            raise
                        except Exception as rescue_exc:
                            last_exc = rescue_exc
                            logger.log(
                                f"Micro AI batch {batch_i + 1}: rescue ID={local_id} "
                                f"attempt {rescue_attempt}/{rescue_retries} failed: {rescue_exc}"
                            )
                            if isinstance(rescue_exc, AITransportError):
                                micro_ai_available = False
                                if rescue_attempt < rescue_retries and recover_micro_runtime(
                                    str(rescue_exc), batch_i + 1
                                ):
                                    continue
                                break
                    if not micro_ai_available:
                        break

            missing_ids = sorted(expected_local_ids - set(by_id))
            if missing_ids:
                failure = {
                    "batch": batch_i + 1,
                    "missing_ids": missing_ids,
                    "error": str(last_exc or "Ollama unavailable/incomplete response"),
                }
                micro_failed_batches.append(failure)
                if strict_ai:
                    update_ai_batch_health(
                        project_dir, stage="micro_ai", batch=batch_i + 1, total=total_batches, state="failed",
                        expected_ids=expected_local_ids, actual_ids=set(by_id), error=failure["error"], prompt_chars=len(prompt),
                    )
                    raise RuntimeError(
                        f"Micro AI batch {batch_i + 1}/{total_batches} не обработан полностью. "
                        f"Последняя ошибка: {last_exc}. Checkpoints сохранены."
                    )

                fallback = build_micro_ai_degraded_items(
                    [batch[i - 1] for i in missing_ids],
                    str(last_exc or "Ollama unavailable/incomplete response"),
                )
                for subset_id, original_id in enumerate(missing_ids, start=1):
                    item = dict(fallback.get(subset_id) or {})
                    item["id"] = original_id
                    by_id[original_id] = item
                batch_degraded = True
                if isinstance(last_exc, AITransportError) or not micro_ai_available:
                    micro_adaptive_single_mode = True
                    logger.log(
                        "Micro AI recovery: необработанные окна отмечены как fallback; "
                        "следующий batch попробует восстановить Ollama, а не отключит весь остаток анализа."
                    )
                logger.log(
                    f"Micro AI degraded: batch {batch_i + 1}/{total_batches}; "
                    f"fallback только для ID {missing_ids}. Анализ продолжается."
                )

        # Recovery can remain unavailable before the AI branch above starts.
        # Record those windows truthfully and keep a conservative in-memory
        # placeholder; the completion gate later prevents a severely partial
        # pass from replacing the user's existing montage.
        unavailable_ids = sorted(expected_local_ids - set(by_id))
        if unavailable_ids:
            unavailable_error = str(last_exc or "Ollama unavailable after bounded recovery")
            micro_failed_batches.append(
                {
                    "batch": batch_i + 1,
                    "missing_ids": unavailable_ids,
                    "error": unavailable_error,
                }
            )
            if strict_ai:
                update_ai_batch_health(
                    project_dir,
                    stage="micro_ai",
                    batch=batch_i + 1,
                    total=total_batches,
                    state="failed",
                    expected_ids=expected_local_ids,
                    actual_ids=set(by_id),
                    error=unavailable_error,
                    prompt_chars=len(prompt),
                )
                raise RuntimeError(
                    f"Micro AI batch {batch_i + 1}/{total_batches} не обработан полностью. "
                    f"Последняя ошибка: {unavailable_error}. Checkpoints сохранены."
                )
            fallback = build_micro_ai_degraded_items(
                [batch[i - 1] for i in unavailable_ids], unavailable_error
            )
            for subset_id, original_id in enumerate(unavailable_ids, start=1):
                item = dict(fallback.get(subset_id) or {})
                item["id"] = original_id
                by_id[original_id] = item
            batch_degraded = True
            micro_adaptive_single_mode = True
            logger.log(
                f"Micro AI degraded: batch {batch_i + 1}/{total_batches}; "
                f"Ollama не восстановился для ID {unavailable_ids}. Checkpoints сохранены."
            )

        if by_id and not any(bool((by_id.get(i) or {}).get("_degraded_fallback")) for i in expected_local_ids):
            ordered_clips = [by_id[i] for i in sorted(expected_local_ids)]
            write_json(cache_path, {"fingerprint": cache_fp, "clips": ordered_clips})

        update_ai_batch_health(
            project_dir, stage="micro_ai", batch=batch_i + 1, total=total_batches,
            state="degraded" if batch_degraded else "done",
            expected_ids=expected_local_ids, actual_ids=set(by_id),
            error=str(last_exc or "") if batch_degraded else "", prompt_chars=len(prompt),
        )
        logger.set_step(
            "micro_ai", batch_i + 1, total_batches,
            72 + 10 * ((batch_i + 1) / total_batches),
            f"Micro AI batch {batch_i + 1}/{total_batches} {'degraded' if batch_degraded else 'готов'}",
        )

        for local_id, w in enumerate(batch, start=1):
            item = by_id.get(local_id, {})
            parent_score = float(w.get("parent_score", 0) or 0)
            raw_score = item.get("score")
            try:
                score = float(raw_score) if raw_score is not None else parent_score
                if not math.isfinite(score):
                    score = 0.0
            except (TypeError, ValueError, OverflowError):
                score = 0.0
            raw_keep = item.get("keep")
            keep = (raw_keep is True or raw_keep == 1 or
                    (isinstance(raw_keep, str) and raw_keep.strip().lower() == "true")) if raw_keep is not None else score >= 6.5
            explicit_reject = str(item.get("decision") or "").lower() in {"remove", "reject", "rejected", "delete", "drop"}
            if not keep or explicit_reject:
                if not item.get("_degraded_fallback"):
                    micro_rejections.append({"start": w["start"], "end": w["end"], "parent_id": w.get("parent_id"),
                                             "score": score, "reason": str(item.get("reason") or "Micro AI rejected"),
                                             "content_class": item.get("content_class")})
                continue

            try:
                extra_start = max(0, min(8, float(item.get("extra_start_seconds", 0) or 0)))
                extra_end = max(0, min(8, float(item.get("extra_end_seconds", 0) or 0)))
            except OperationCancelled:
                raise
            except Exception:
                extra_start = extra_end = 0

            start = max(0, float(w["start"]) - extra_start)
            end = float(w["end"]) + extra_end
            if end - start < max(8, float(settings.get("micro_min_seconds", 20)) * 0.5):
                continue
            try:
                micro_clarity = max(0.0, min(1.0, float(item.get("standalone_clarity", 0.5) or 0.5)))
            except OperationCancelled:
                raise
            except Exception:
                micro_clarity = 0.5
            try:
                micro_content_confidence = max(
                    0.0,
                    min(1.0, float(item.get("content_class_confidence", w.get("parent_content_confidence", 0.0)) or 0.0)),
                )
            except OperationCancelled:
                raise
            except Exception:
                micro_content_confidence = max(0.0, min(1.0, float(w.get("parent_content_confidence", 0.0) or 0.0)))

            micro.append(
                Candidate(
                    id=next_id,
                    start=round(start, 3),
                    end=round(end, 3),
                    score=round(max(0, min(10, score)), 2),
                    title=item.get("title") or f"{w.get('parent_title', 'Момент')} — часть",
                    reason=(item.get("reason") or "micro-cut из сильного блока") + f" / parent_id={w.get('parent_id')}",
                    text_preview=w.get("text", "")[:900],
                    visual_score=0,
                    audio_score=0,
                    penalty_score=0,
                    transcript_score=transcript_quality_score(w.get("text", "")),
                    ai_score=round(max(0, min(10, score)), 2),
                    decision=str(item.get("decision") or ("keep" if keep else "remove")),
                    hook_potential=str(item.get("hook_potential") or ("high" if score >= 8.7 else "medium" if score >= 6.5 else "low")),
                    context_before_seconds=extra_start,
                    context_after_seconds=extra_end,
                    moment_type=str(item.get("moment_type") or "general"),
                    standalone_clarity=micro_clarity,
                    content_class=_normalize_content_class(item.get("content_class") or w.get("parent_content_class")),
                    content_class_confidence=micro_content_confidence,
                    content_evidence=(
                        f"micro {'fallback' if item.get('_degraded_fallback') else 'AI'}; "
                        f"parent={w.get('parent_content_class', 'unknown')}"
                    ),
                    semantic_topic=str(item.get("semantic_topic") or w.get("parent_semantic_topic") or item.get("title") or "")[:160],
                    parent_id=int(w.get("parent_id") or 0),
                    template_signature=str(w.get("parent_template_signature") or "")[:240],
                )
            )
            next_id += 1

    update_ai_coverage_report(
        project_dir,
        micro_windows_total=len(windows),
        micro_ai_analyzed=len(micro_analyzed_ids),
        micro_coverage_percent=round(100 * len(micro_analyzed_ids) / max(1, len(windows)), 2),
        micro_failed_batches=micro_failed_batches,
    )
    micro_runtime_report = read_json(project_dir / "ollama_micro_runtime.json", {}) or {}
    micro_runtime_report.update(
        {
            "adaptive_single_mode_used": micro_adaptive_single_mode,
            "runtime_recovery_count": micro_runtime_recovery_count,
            "runtime_recovery_limit": micro_runtime_recovery_limit,
            "runtime_recoveries": micro_runtime_recoveries,
            "final_ai_available": micro_ai_available,
            "final_ai_analyzed": len(micro_analyzed_ids),
            "final_ai_coverage_percent": round(
                100 * len(micro_analyzed_ids) / max(1, len(windows)), 2
            ),
            "updated_at": time.time(),
        }
    )
    write_json(project_dir / "ollama_micro_runtime.json", micro_runtime_report)
    if strict_ai and len(micro_analyzed_ids) < len(windows):
        raise RuntimeError(
            f"Micro AI анализ неполный: обработано {len(micro_analyzed_ids)}/{len(windows)} окон. "
            "Highlight Studio не будет выдавать неполный результат как успешный."
        )
    write_json(project_dir / "micro_rejections.json", micro_rejections)
    if not micro:
        write_json(project_dir / "micro_candidates.json", [])
        if micro_failed_batches and not strict_ai:
            logger.log(
                "Micro AI degraded не дал безопасных микро-фрагментов; использую качественные block-кандидаты вместо пустого монтажа."
            )
            return candidates
        if complete_ai or micro_rejections:
            logger.log("Micro AI полностью обработал материал, но не выбрал ни одного достаточно сильного микро-фрагмента.")
            return []
        logger.log("Micro-cut не дал фрагментов, использую старые кандидаты.")
        return candidates

    # Remove near-duplicates / heavy overlaps. Keep best score first.
    micro_sorted = sorted(micro, key=lambda x: x.score, reverse=True)
    filtered: list[Candidate] = []
    for c in micro_sorted:
        if any(overlaps(c.start, c.end, x.start, x.end) for x in filtered):
            continue
        filtered.append(c)
        if len(filtered) >= max_final * 2:
            break

    filtered = sorted(filtered, key=lambda x: x.score, reverse=True)
    write_json(project_dir / "micro_candidates.json", [asdict(x) for x in filtered])
    logger.log(f"Micro-cut: {len(candidates)} крупных кандидатов -> {len(filtered)} микро-кандидатов")
    return filtered


def update_segments(
    project_dir: Path,
    segments: list[dict[str, Any]],
    *,
    source_duration: float | None = None,
    revision_context: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    p = project_paths(project_dir)

    def finite_float(value: Any, default: float | None = None) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return number if math.isfinite(number) else default

    if source_duration is None:
        source_duration_value = 0.0
        if p["video"].exists():
            probed = finite_float(video_duration(p["video"]), 0.0)
            source_duration_value = max(0.0, probed or 0.0)
    else:
        source_duration_value = max(0.0, finite_float(source_duration, 0.0) or 0.0)
    source_duration = source_duration_value

    clean: list[dict[str, Any]] = []
    for item in segments:
        if not isinstance(item, dict):
            continue
        start_value = finite_float(item.get("start", 0), None)
        if start_value is None:
            continue
        start = max(0.0, start_value)
        if source_duration > 0 and start >= source_duration:
            continue

        end = finite_float(item.get("end", start + 0.1), start + 0.1)
        if end is None:
            continue
        if source_duration > 0:
            end = min(end, source_duration)
        if end <= start:
            continue
        if end - start < 0.1:
            if source_duration > 0:
                end = min(source_duration, start + 0.1)
                if end - start < 0.1:
                    start = max(0.0, end - 0.1)
            else:
                end = start + 0.1
        if end <= start:
            continue

        text_preview = str(item.get("text_preview", "") or "")
        score = finite_float(item.get("score", 0), 0.0) or 0.0
        clean_item = {
            **item,
            "start": round(start, 3),
            "end": round(end, 3),
            "score": score,
            "title": str(item.get("title", "") or "Фрагмент"),
            "reason": str(item.get("reason", "") or ""),
            "text_preview": text_preview,
            "transcript_score": finite_float(item.get("transcript_score"), None),
            "ai_score": finite_float(item.get("ai_score"), score),
            "visual_score": finite_float(item.get("visual_score"), 0.0),
            "audio_score": finite_float(item.get("audio_score"), 0.0),
            "penalty_score": finite_float(item.get("penalty_score"), 0.0),
            "decision": str(item.get("decision", "maybe") or "maybe"),
            "hook_potential": str(item.get("hook_potential", "medium") or "medium"),
            "context_before_seconds": finite_float(item.get("context_before_seconds"), 0.0),
            "context_after_seconds": finite_float(item.get("context_after_seconds"), 0.0),
            "moment_type": str(item.get("moment_type", "general") or "general"),
            "standalone_clarity": finite_float(item.get("standalone_clarity"), 0.5),
            "ocr_score": finite_float(item.get("ocr_score"), 0.0),
            "irl_score": finite_float(item.get("irl_score"), 0.0),
            "story_role": str(item.get("story_role", "body") or "body"),
            "ai_explanation": str(item.get("ai_explanation", "") or ""),
            "what_happens": str(item.get("what_happens", "") or ""),
            "why_selected": str(item.get("why_selected", "") or ""),
            "viewer_value": str(item.get("viewer_value", "") or ""),
            "risk": str(item.get("risk", "") or ""),
            "candidate_id": str(item.get("candidate_id") or ""),
        }
        if clean_item["transcript_score"] is None:
            clean_item["transcript_score"] = transcript_quality_score(text_preview)
        candidate_id = finite_float(item.get("id", 0), 0.0) or 0.0
        c = Candidate(
            id=int(candidate_id),
            start=clean_item["start"],
            end=clean_item["end"],
            score=clean_item["score"],
            title=clean_item["title"],
            reason=clean_item["reason"],
            text_preview=clean_item["text_preview"],
            visual_score=clean_item["visual_score"],
            audio_score=clean_item["audio_score"],
            penalty_score=clean_item["penalty_score"],
            transcript_score=clean_item["transcript_score"],
            ai_score=clean_item["ai_score"],
            decision=clean_item["decision"],
            hook_potential=clean_item["hook_potential"],
            context_before_seconds=clean_item["context_before_seconds"],
            context_after_seconds=clean_item["context_after_seconds"],
            moment_type=clean_item["moment_type"],
            standalone_clarity=clean_item["standalone_clarity"],
            ocr_score=clean_item["ocr_score"],
            irl_score=clean_item["irl_score"],
            story_role=clean_item["story_role"],
            ai_explanation=clean_item["ai_explanation"],
            what_happens=clean_item["what_happens"],
            why_selected=clean_item["why_selected"],
            viewer_value=clean_item["viewer_value"],
            risk=clean_item["risk"],
            candidate_id=clean_item["candidate_id"],
        )
        update_candidate_confidence(c)
        clean_item["candidate_id"] = c.candidate_id
        clean_item["confidence"] = c.confidence
        clean_item["confidence_reason"] = c.confidence_reason
        clean.append(clean_item)
    clean.sort(key=lambda x: x["start"])
    for i, item in enumerate(clean, start=1):
        item["id"] = i
    write_json(p["segments"], clean)
    project = read_json(project_dir / "project.json", {}) or {}
    settings = project.get("settings") or {}
    context = revision_context if isinstance(revision_context, dict) else {}
    source_rev = str(context.get("source_revision") or "")
    analysis_rev = str(context.get("analysis_revision") or "")
    if source_rev and analysis_rev:
        new_segments_rev = segments_revision_from_items(
            clean,
            settings,
            analysis_rev=analysis_rev,
            source_rev=source_rev,
        )
        mark_segments_updated(
            project_dir,
            settings,
            source_rev=source_rev,
            analysis_rev=analysis_rev,
            segments_rev=new_segments_rev,
        )
    else:
        mark_segments_updated(project_dir, settings)
    quality_report(project_dir)
    return clean


def _render_impl(
    project_dir: Path,
    settings: dict[str, Any],
    logger: JobLogger,
    *,
    segments_override: list[dict[str, Any]] | None = None,
    final_output: Path | None = None,
    authoritative: bool = True,
    finalize_job_status: bool = True,
) -> dict[str, Any]:
    """Render a montage without letting derivative exports corrupt project state.

    The normal final render is authoritative and updates the project's render
    revision.  Content Factory variants pass ``segments_override`` and
    ``authoritative=False`` so they never rewrite ``segments.json`` or claim to
    be the current canonical final.
    """
    p = project_paths(project_dir)
    video = p["video"]
    segs = list(segments_override) if segments_override is not None else read_json(p["segments"], [])
    if not segs:
        raise RuntimeError("Нет сегментов для рендера")

    # Pin the exact generation consumed by this render.  A user may edit the
    # montage or render settings while FFmpeg is working; the resulting bytes
    # must never be committed as if they represented the newer generation.
    pinned_render_state: dict[str, str] | None = None
    if authoritative:
        pinned_source_rev = source_revision(project_dir)
        pinned_analysis_rev = analysis_revision(project_dir, settings, source_rev=pinned_source_rev)
        pinned_segments_rev = segments_revision_from_items(
            segs, settings, analysis_rev=pinned_analysis_rev, source_rev=pinned_source_rev
        )
        pinned_render_rev = render_revision(
            project_dir, settings, source_rev=pinned_source_rev,
            segments_rev=pinned_segments_rev, analysis_rev=pinned_analysis_rev,
        )
        pinned_render_state = {
            "source_revision": pinned_source_rev,
            "analysis_revision": pinned_analysis_rev,
            "segments_revision": pinned_segments_rev,
            "render_revision": pinned_render_rev,
        }
        write_json(project_dir / "render_inflight.json", {**pinned_render_state, "started_at": time.time()})
    out_dir = p["outputs"]
    out_dir.mkdir(exist_ok=True)
    parts_dir = project_dir / "render_parts"
    parts_dir.mkdir(exist_ok=True)

    ffmpeg = which("ffmpeg") or "ffmpeg"
    video_len = video_duration(video)
    parts = []
    for i, s in enumerate(segs, start=1):
        check_cancel(project_dir)
        try:
            raw_start = float(s.get("start", 0))
            raw_end = float(s.get("end", raw_start + 0.1))
        except (AttributeError, TypeError, ValueError, OverflowError):
            logger.log(f"Пропускаю фрагмент {i}: некорректные границы.")
            continue
        if not math.isfinite(raw_start) or not math.isfinite(raw_end):
            logger.log(f"Пропускаю фрагмент {i}: границы не являются конечными числами.")
            continue
        start = max(0.0, raw_start)
        if start >= video_len:
            logger.log(f"Пропускаю фрагмент {i}: начало {start:.3f} находится за концом видео {video_len:.3f}.")
            continue
        end = min(video_len, raw_end)
        if end <= start:
            logger.log(f"Пропускаю фрагмент {i}: конец должен быть позже начала.")
            continue
        dur = end - start
        part = parts_dir / f"part_{i:03d}.mp4"
        part_meta = parts_dir / f"part_{i:03d}.json"
        part_fp = render_part_fingerprint(project_dir, settings, start, end)
        cached_meta = read_json(part_meta, None)
        if (part.exists() and part.stat().st_size > 1024 and isinstance(cached_meta, dict)
                and cached_meta.get("fingerprint") == part_fp
                and cached_meta.get("output_signature") == source_file_signature(part)
                and validate_media_file(part).get("ok")):
            logger.set_step(
                "render_parts", i, len(segs), None, f"Рендер части {i}/{len(segs)}: кэш fingerprint OK", global_start=10, global_end=80
            )
            parts.append(part)
            continue
        if part.exists():
            logger.log(f"Рендер части {i}: кэш устарел или без fingerprint, пересчитываю")
        pending_part = parts_dir / f"part_{i:03d}.pending.mp4"
        pending_part.unlink(missing_ok=True)
        logger.set_step(
            "render_parts", i - 1, len(segs), None, f"Рендер части {i}/{len(segs)}: FFmpeg старт", global_start=10, global_end=80
        )
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(video),
            "-t",
            f"{dur:.3f}",
        ]
        if settings.get("remove_silence", False) and not effective_remove_silence(settings):
            logger.log("Remove silence отключён для этого рендера, потому что включены SRT-субтитры: иначе тайминги субтитров съедут.")
        if effective_remove_silence(settings):
            cmd += ["-af", "silenceremove=stop_periods=-1:stop_duration=1.2:stop_threshold=-35dB"]
        base_cmd = list(cmd)
        enc_args, used_encoder = video_encode_args(ffmpeg, settings, logger)
        cmd = base_cmd + enc_args + ["-c:a", "aac", "-b:a", "192k", "-shortest", str(pending_part)]
        r = run_cmd(cmd, project_dir=project_dir, cancel_file=cancel_path(project_dir))
        if r.returncode != 0 and used_encoder != "libx264":
            logger.log(
                f"Hardware encoder {used_encoder} объявлен FFmpeg, но не запустился на этом GPU/driver. Повторяю часть {i} через libx264."
            )
            pending_part.unlink(missing_ok=True)
            fallback_settings = dict(settings)
            fallback_settings["video_encoder"] = "libx264"
            fallback_args, used_encoder = video_encode_args(ffmpeg, fallback_settings, logger)
            fallback_cmd = base_cmd + fallback_args + ["-c:a", "aac", "-b:a", "192k", "-shortest", str(pending_part)]
            r = run_cmd(fallback_cmd, project_dir=project_dir, cancel_file=cancel_path(project_dir))
        if r.returncode != 0:
            raise RuntimeError(r.stdout)
        part_check = validate_media_file(pending_part)
        if not part_check.get("ok"):
            raise RuntimeError(f"Рендер части {i}: повреждённый результат: {part_check.get('message', 'invalid media')}")
        if not effective_remove_silence(settings) and abs(float(part_check['duration_seconds']) - dur) > max(1.25, dur * 0.12):
            raise RuntimeError(f"Рендер части {i}: длительность не соответствует выбранному фрагменту")
        check_cancel(project_dir)
        pending_part.replace(part)
        write_json(
            part_meta,
            {
                "fingerprint": part_fp,
                "output_signature": source_file_signature(part),
                "start": start,
                "end": end,
                "encoder_used": used_encoder,
                "remove_silence_applied": effective_remove_silence(settings),
                "settings_hash": stable_hash(
                    settings_subset(settings, ["video_encoder", "render_preset", "crf", "remove_silence", "make_srt"])
                ),
            },
        )
        parts.append(part)
        logger.set_step("render_parts", i, len(segs), None, f"Рендер части {i}/{len(segs)}: готово", global_start=10, global_end=80)

    if not parts:
        raise RuntimeError("Нет корректных сегментов внутри длительности исходного видео")

    concat = parts_dir / "concat.txt"
    concat.write_text(
        "\n".join(f"file '{str(x.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for x in parts), encoding="utf-8"
    )
    final = Path(final_output) if final_output is not None else out_dir / "highlight_final.mp4"
    if not final.is_absolute():
        final = project_dir / final
    final.parent.mkdir(parents=True, exist_ok=True)
    pending_final = final.with_name(f"{final.stem}.rendering{final.suffix}")
    pending_final.unlink(missing_ok=True)
    logger.heartbeat("concat_render", 90, "Склейка итогового файла")
    r = run_cmd(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(pending_final)],
        project_dir=project_dir,
        cancel_file=cancel_path(project_dir),
    )
    if r.returncode != 0 or not pending_final.exists() or pending_final.stat().st_size < 1024:
        logger.log("Concat copy failed or produced empty file. Пробую fallback re-encode concat.")
        r2 = run_cmd(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(pending_final),
            ],
            project_dir=project_dir,
            cancel_file=cancel_path(project_dir),
        )
        if r2.returncode != 0:
            raise RuntimeError("Concat failed:\n" + (r.stdout or "") + "\nFallback failed:\n" + (r2.stdout or ""))
    if not pending_final.exists() or pending_final.stat().st_size < 1024:
        raise RuntimeError(f"Рендер завершился, но временный итоговый файл не создан или пустой: {pending_final}")

    if authoritative and settings.get("make_srt", True):
        generate_srt(project_dir)
    check = result_check(project_dir, pending_final) if authoritative else result_check(project_dir, pending_final, persist=False)
    if authoritative:
        write_json(project_dir / "last_result_check.json", check)
    if not check.get("ok"):
        if authoritative:
            mark_render_failed(project_dir, settings, str(check.get("message") or check.get("errors") or "post-render validation failed"))
        pending_final.unlink(missing_ok=True)
        raise RuntimeError("Финальный файл создан, но не прошёл post-render validation: " + str(check.get("message") or check.get("errors") or check))
    # Never overwrite a validated deliverable until its replacement has passed
    # post-render validation.  Before committing an authoritative render, compare
    # the *current* project generation with the immutable generation captured at
    # job start.  If the montage/settings/source changed during FFmpeg work, keep
    # the bytes as a historical stale output and leave the current canonical
    # deliverable/revision untouched.
    stale_due_to_project_change = False
    committed_final = final
    current_render_state: dict[str, str] | None = None
    if authoritative and pinned_render_state is not None:
        current_project = read_json(project_dir / "project.json", {}) or {}
        current_settings = current_project.get("settings") if isinstance(current_project.get("settings"), dict) else settings
        current_source_rev = source_revision(project_dir)
        current_analysis_rev = analysis_revision(project_dir, current_settings, source_rev=current_source_rev)
        current_segments_rev = segments_revision(
            project_dir, current_settings, analysis_rev=current_analysis_rev, source_rev=current_source_rev
        )
        current_render_rev = render_revision(
            project_dir, current_settings, source_rev=current_source_rev,
            segments_rev=current_segments_rev, analysis_rev=current_analysis_rev,
        )
        current_render_state = {
            "source_revision": current_source_rev,
            "analysis_revision": current_analysis_rev,
            "segments_revision": current_segments_rev,
            "render_revision": current_render_rev,
        }
        stale_due_to_project_change = current_render_rev != pinned_render_state["render_revision"]

    if stale_due_to_project_change:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        stale_final = final.with_name(
            f"{final.stem}.stale_{pinned_render_state['render_revision'][:10]}_{stamp}{final.suffix}"
        )
        replace_file_atomic(pending_final, stale_final)
        committed_final = stale_final
        check["file"] = str(stale_final)
        check["stale_due_to_project_change"] = True
        stale_report = {
            "stale": True,
            "reason": "project_generation_changed_during_render",
            "rendered_generation": pinned_render_state,
            "current_generation": current_render_state,
            "output": str(stale_final),
            "created_at": time.time(),
        }
        write_json(project_dir / "render_stale_result.json", stale_report)
        write_json(project_dir / "last_result_check.json", {**check, **stale_report})
        logger.log(
            "Рендер завершён, но проект изменился во время FFmpeg. "
            f"Старая generation сохранена как {stale_final.name}; canonical final не перезаписан."
        )
    else:
        replace_file_atomic(pending_final, final)
        committed_final = final
        check["file"] = str(final)
        if authoritative:
            write_json(project_dir / "last_result_check.json", check)

    root_url = None
    authoritative_committed = bool(authoritative and not stale_due_to_project_change)
    if authoritative_committed:
        assert pinned_render_state is not None
        mark_render_complete(
            project_dir, settings, committed_final, check,
            render_rev=pinned_render_state["render_revision"],
            segments_rev=pinned_render_state["segments_revision"],
            source_rev=pinned_render_state["source_revision"],
        )
        # Only the canonical final is copied to the convenient project-root alias.
        root_copy = project_dir / "highlight_final.mp4"
        try:
            shutil.copy2(committed_final, root_copy)
            root_url = f"/api/projects/{project_dir.name}/file/highlight_final.mp4"
        except Exception as exc:
            logger.log(f"Не удалось скопировать итог в корень проекта: {exc}")

    check["outputs"] = list_output_files(project_dir)
    if authoritative:
        write_json(project_dir / "outputs_manifest.json", check["outputs"])
    try:
        (project_dir / "render_inflight.json").unlink(missing_ok=True)
    except Exception:
        pass
    if finalize_job_status:
        message = (
            f"Рендер готов, но монтаж изменился; сохранена устаревшая версия: {committed_final.name}"
            if stale_due_to_project_change
            else f"Рендер готов: {committed_final}"
        )
        logger.set_status("done", 100, message, stale_due_to_project_change=stale_due_to_project_change)
    rel_final = committed_final.relative_to(project_dir).as_posix() if committed_final.is_relative_to(project_dir) else str(committed_final)
    return {
        "output": str(committed_final),
        "url": f"/api/projects/{project_dir.name}/file/{rel_final}" if committed_final.is_relative_to(project_dir) else None,
        "root_url": root_url,
        "result_check": check,
        "outputs": check["outputs"],
        "authoritative": authoritative_committed,
        "authoritative_requested": authoritative,
        "stale_due_to_project_change": stale_due_to_project_change,
        "rendered_generation": pinned_render_state,
        "current_generation": current_render_state,
    }


def render(
    project_dir: Path,
    settings: dict[str, Any],
    logger: JobLogger,
    *,
    segments_override: list[dict[str, Any]] | None = None,
    final_output: Path | None = None,
    authoritative: bool = True,
    finalize_job_status: bool = True,
) -> dict[str, Any]:
    """Serialize GPU-heavy renders with Whisper and local AI across projects."""
    logger.checkpoint("render", state="running", message="Ожидание общего GPU-ресурса")
    with AIResourceManager.lease(
        "gpu_heavy",
        capacity=max(1, int(settings.get("gpu_job_limit", 1) or 1)),
        cancel_check=lambda: is_cancelled(project_dir),
    ):
        result = _render_impl(
            project_dir,
            settings,
            logger,
            segments_override=segments_override,
            final_output=final_output,
            authoritative=authoritative,
            finalize_job_status=finalize_job_status,
        )
    logger.checkpoint("render", state="completed", artifacts=[str(result.get("output") or "")])
    return result


def render_rough_cut_preview(project_dir: Path, settings: dict[str, Any], logger: JobLogger) -> dict[str, Any]:
    """Fast low-resolution preview using the best verified encoder for this PC."""
    p = project_paths(project_dir)
    video = p["video"]
    segs = read_json(p["segments"], [])
    if not segs:
        raise RuntimeError("Нет сегментов для rough preview")

    preview_dir = project_dir / "preview"
    parts_dir = preview_dir / "rough_parts"
    preview_dir.mkdir(exist_ok=True)
    parts_dir.mkdir(exist_ok=True)
    ffmpeg = which("ffmpeg") or "ffmpeg"
    video_len = video_duration(video)
    parts: list[Path] = []
    preview_settings = dict(settings)
    preview_settings.update({"render_preset": "ultrafast", "crf": 30})
    enc_args, used_encoder = video_encode_args(ffmpeg, preview_settings, logger)

    for i, s in enumerate(segs, start=1):
        check_cancel(project_dir)
        start = max(0.0, min(video_len, float(s.get("start", 0))))
        end = max(start + 0.1, min(video_len, float(s.get("end", start + 1))))
        dur = max(0.1, end - start)
        part = parts_dir / f"rough_{i:03d}.mp4"
        logger.set_step("rough_preview_parts", i, len(segs), None, f"Rough preview часть {i}/{len(segs)} · {used_encoder}", global_start=5, global_end=85)
        base = [
            ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{dur:.3f}", "-vf", "scale=-2:480"
        ]
        cmd = base + enc_args + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest", str(part)]
        r = run_cmd(cmd, project_dir=project_dir, cancel_file=cancel_path(project_dir))
        if r.returncode != 0 and used_encoder != "libx264":
            logger.log(f"Rough preview: {used_encoder} не запустился, повторяю часть {i} через libx264.")
            sw = dict(preview_settings)
            sw["video_encoder"] = "libx264"
            sw_args, _ = video_encode_args(ffmpeg, sw, logger)
            r = run_cmd(base + sw_args + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest", str(part)], project_dir=project_dir, cancel_file=cancel_path(project_dir))
        if r.returncode != 0:
            raise RuntimeError(r.stdout[-2000:])
        parts.append(part)

    concat = parts_dir / "concat.txt"
    concat.write_text(
        "\n".join(f"file '{str(x.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for x in parts), encoding="utf-8"
    )
    out = preview_dir / "rough_cut_preview.mp4"
    logger.heartbeat("concat_rough_preview", 90, "Склейка rough preview")
    r = run_cmd(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(out)],
        project_dir=project_dir,
        cancel_file=cancel_path(project_dir),
    )
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 1024:
        sw = dict(preview_settings)
        sw["video_encoder"] = "libx264"
        sw_args, _ = video_encode_args(ffmpeg, sw, logger)
        r2 = run_cmd(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat)]
            + sw_args
            + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)],
            project_dir=project_dir,
            cancel_file=cancel_path(project_dir),
        )
        if r2.returncode != 0:
            raise RuntimeError("Rough preview concat failed:\n" + (r.stdout or "") + "\nFallback failed:\n" + (r2.stdout or ""))
    result = {
        "output": str(out),
        "url": f"/api/projects/{project_dir.name}/file/preview/rough_cut_preview.mp4",
        "size_mb": round(out.stat().st_size / 1024 / 1024, 2),
        "segments": len(segs),
        "encoder": used_encoder,
    }
    write_json(project_dir / "rough_preview.json", result)
    logger.set_status("done", 100, "Rough-cut preview готов")
    return result


def render_factory_versions(project_dir: Path, settings: dict[str, Any], logger: JobLogger) -> dict[str, Any]:
    """Render Content Factory variants without mutating canonical montage state."""
    fresh = freshness_report(project_dir, settings)
    if not fresh.get("segments_current"):
        raise RuntimeError("Content Factory render заблокирован: текущий монтаж отсутствует или устарел.")

    manifest = content_factory(project_dir, settings)
    p = project_paths(project_dir)
    out_dir = p["outputs"]
    out_dir.mkdir(exist_ok=True)
    rendered: list[dict[str, Any]] = []
    base_segments_revision = str(fresh.get("segments_revision") or segments_revision(project_dir, settings))
    base_source_revision = str(fresh.get("source_revision") or source_revision(project_dir))
    base_analysis_revision = str(fresh.get("analysis_revision") or analysis_revision(project_dir, settings))

    for item in manifest.get("outputs", []):
        check_cancel(project_dir)
        minutes = int(float(item.get("minutes", 0)))
        seg_path = Path(item.get("json", ""))
        if not seg_path.exists():
            seg_path = p["factory"] / f"montage_{minutes}min_segments.json"
        segs = read_json(seg_path, []) or []
        if not segs:
            continue
        target = out_dir / f"highlight_{minutes}min.mp4"
        logger.set_status("running", 1, f"Рендер версии {minutes} минут")
        result = render(
            project_dir,
            settings,
            logger,
            segments_override=segs,
            final_output=target,
            authoritative=False,
            finalize_job_status=False,
        )
        final = Path(str(result.get("output") or target))
        if final.exists():
            rendered.append(
                {
                    "minutes": minutes,
                    "path": target.relative_to(project_dir).as_posix(),
                    "url": f"/api/projects/{project_dir.name}/file/{target.relative_to(project_dir).as_posix()}",
                    "size_mb": round(target.stat().st_size / 1024 / 1024, 2),
                    "base_segments_revision": base_segments_revision,
                    "base_source_revision": base_source_revision,
                    "base_analysis_revision": base_analysis_revision,
                }
            )

    factory_render_manifest = {
        "rendered": rendered,
        "base_segments_revision": base_segments_revision,
        "base_source_revision": base_source_revision,
        "base_analysis_revision": base_analysis_revision,
        "created_at": time.time(),
    }
    write_json(project_dir / "factory_render_manifest.json", factory_render_manifest)
    logger.set_status("done", 100, f"Content Factory: готово версий {len(rendered)}")
    return {"rendered": rendered, "outputs": list_output_files(project_dir), "revision": base_segments_revision}


def _parse_tc_to_seconds(value: str | float | int) -> float:
    if isinstance(value, (int, float)):
        try:
            result = float(value)
            return result if math.isfinite(result) else 0.0
        except Exception:
            return 0.0
    parts = str(value).strip().replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            result = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            result = float(parts[0]) * 60 + float(parts[1])
        else:
            result = float(parts[0])
        return result if math.isfinite(result) else 0.0
    except Exception:
        return 0.0


def _parse_tc_to_seconds_strict(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except Exception:
            return None
    parts = str(value).strip().replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            result = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            result = float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 1 and parts[0] != "":
            result = float(parts[0])
        else:
            return None
        return result if math.isfinite(result) else None
    except Exception:
        return None


def _short_candidate_strength(item: dict[str, Any]) -> float:
    """Stable ranking for Shorts candidates without trusting a single score field."""
    text = " ".join(
        str(item.get(key) or "") for key in ("title", "reason", "what_happens", "why_selected", "viewer_value", "moment_type")
    ).lower()
    hook_words = ("шок", "жесть", "смеш", "реакц", "конфликт", "донат", "мем", "угар", "неожидан", "поворот", "payoff")
    hook_bonus = min(1.2, sum(0.2 for word in hook_words if word in text))

    def finite_number(value: Any) -> float:
        try:
            result = float(value or 0)
            return result if math.isfinite(result) else 0.0
        except Exception:
            return 0.0

    base = finite_number(item.get("score") or item.get("ai_score"))
    confidence = finite_number(item.get("confidence"))
    clarity = finite_number(item.get("standalone_clarity"))
    return round(base + confidence * 0.08 + clarity * 0.5 + hook_bonus, 4)


def _short_overlap_ratio(a: dict[str, Any], b: dict[str, Any]) -> float:
    start = max(float(a["start"]), float(b["start"]))
    end = min(float(a["end"]), float(b["end"]))
    intersection = max(0.0, end - start)
    shortest = max(0.001, min(float(a["end"]) - float(a["start"]), float(b["end"]) - float(b["start"])))
    return intersection / shortest




# ---------------- Highlight Studio V5: Shorts Quality Engine ----------------

SHORTS_V5_VERSION = "5.1"
_SHORTS_FUNNY_WORDS = (
    "смеш", "угар", "рж", "лол", "мем", "прикол", "шут", "ахах", "хаха", "ор", "кек",
    "funny", "laugh", "joke", "meme",
)
_SHORTS_REACTION_WORDS = (
    "реакц", "офиг", "шок", "жесть", "что?!", "неожидан", "удив", "крич", "орал", "rage",
    "reaction", "surprise", "wtf", "донат", "clutch", "фейл", "fail",
)
_SHORTS_PAYOFF_WORDS = (
    "развяз", "итог", "получилось", "побед", "проиграл", "выиграл", "сделал", "ответил", "payoff",
    "clutch", "финал", "концов",
)
_SHORTS_HOOK_WORDS = (
    "сейчас", "смотри", "подожди", "что", "как", "почему", "вдруг", "реально", "никогда", "лучший",
    "шок", "жесть", "мем", "донат", "конфликт", "неожидан",
)


def _shorts_num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value if value is not None else default)
        return result if math.isfinite(result) else default
    except Exception:
        return default


def _shorts_text(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(k) or "") for k in (
        "title", "reason", "text_preview", "what_happens", "why_selected", "viewer_value",
        "moment_type", "semantic_topic", "ai_explanation", "content_evidence",
    )).lower()


def _shorts_semantic_rejected(item: dict[str, Any], settings: dict[str, Any]) -> bool:
    decision = str(item.get("decision") or "").strip().lower()
    if decision in {"remove", "reject", "rejected", "delete", "drop"}:
        return True
    cls = str(item.get("content_class") or "unknown").strip().lower()
    confidence = _shorts_num(item.get("content_class_confidence"), 0.0)
    reject_conf = _shorts_num(settings.get("non_primary_reject_confidence"), 0.72)
    return cls in NON_PRIMARY_CONTENT_CLASSES and confidence >= reject_conf


def _shorts_candidate_pool(project_dir: Path) -> list[dict[str, Any]]:
    """Build a Shorts-only pool from micro/block/general/final candidates.

    V4 selected Shorts mostly from final montage segments. V5 keeps final
    segments as evidence, but also searches the denser micro/block pools so a
    20-second punchline inside a 2-minute montage scene can win independently.
    """
    sources = [
        ("micro", project_dir / "micro_candidates.json", 0.35),
        ("block", project_dir / "block_candidates.json", 0.18),
        ("candidate", project_paths(project_dir)["candidates"], 0.10),
        ("segment", project_paths(project_dir)["segments"], 0.0),
    ]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_name, path, source_bonus in sources:
        rows = read_json(path, []) or []
        if not isinstance(rows, list):
            continue
        for position, raw in enumerate(rows):
            if not isinstance(raw, dict):
                continue
            start = _parse_tc_to_seconds_strict(raw.get("start", raw.get("source_start")))
            end = _parse_tc_to_seconds_strict(raw.get("end", raw.get("source_end")))
            if start is None or end is None or end <= start:
                continue
            identity = str(raw.get("candidate_id") or raw.get("id") or "")
            temporal_key = f"{round(start,1)}:{round(end,1)}:{str(raw.get('title') or '')[:80]}"
            key = identity if identity else temporal_key
            dedupe_key = f"{source_name}:{key}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            item = dict(raw)
            item.update({
                "start": float(start), "end": float(end), "shorts_source": source_name,
                "shorts_source_bonus": source_bonus, "shorts_pool_index": position + 1,
                "source_segment_id": raw.get("source_segment_id", raw.get("id")),
            })
            out.append(item)
    return out


def _shorts_audio_features(project_dir: Path, start: float, end: float) -> dict[str, float]:
    report = read_json(project_dir / "audio_events.json", {}) or {}
    events = report.get("events") if isinstance(report, dict) else []
    if not isinstance(events, list):
        events = []
    loud = reaction = silence = 0.0
    for event in events:
        if not isinstance(event, dict):
            continue
        t0 = _shorts_num(event.get("time"), -1)
        t1 = _shorts_num(event.get("end_time"), t0)
        if t1 < start or t0 > end:
            continue
        typ = str(event.get("type") or "").lower()
        score = max(0.0, min(1.0, _shorts_num(event.get("score"), 0.0)))
        if typ == "sudden_reaction":
            reaction += max(0.4, score)
        elif typ == "loud_peak":
            loud += max(0.35, score)
        elif typ == "sudden_silence":
            silence += max(0.2, score)
    return {
        "reaction": min(1.0, reaction / 2.0),
        "loud": min(1.0, loud / 3.0),
        "silence": min(1.0, silence / 2.0),
    }


def _shorts_keyword_score(text: str, words: tuple[str, ...]) -> float:
    hits = sum(1 for word in words if word in text)
    return min(10.0, hits * 2.25)


def shorts_candidate_score(project_dir: Path, item: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Score a candidate for vertical short-form viewing, independently of montage score."""
    text = _shorts_text(item)
    start = _shorts_num(item.get("start"))
    end = _shorts_num(item.get("end"))
    duration = max(0.1, end - start)
    base = max(0.0, min(10.0, _shorts_num(item.get("score", item.get("ai_score")), 0.0)))
    confidence = max(0.0, min(10.0, _shorts_num(item.get("confidence"), 0.0)))
    clarity_raw = _shorts_num(item.get("standalone_clarity"), 0.5)
    standalone = max(0.0, min(10.0, clarity_raw * 10.0 if clarity_raw <= 1.0 else clarity_raw))
    audio = _shorts_audio_features(project_dir, start, end)

    funny = max(_shorts_keyword_score(text, _SHORTS_FUNNY_WORDS), base * 0.25)
    reaction = max(_shorts_keyword_score(text, _SHORTS_REACTION_WORDS), audio["reaction"] * 10.0, base * 0.20)
    surprise = max(_shorts_keyword_score(text, ("неожидан", "шок", "вдруг", "офиг", "surprise", "wtf")), reaction * 0.65)
    payoff = max(_shorts_keyword_score(text, _SHORTS_PAYOFF_WORDS), base * 0.45)
    hook = max(_shorts_keyword_score(text, _SHORTS_HOOK_WORDS), max(funny, reaction, surprise) * 0.65)
    quotability = max(_shorts_keyword_score(text, ("сказал", "фраз", "цитат", "ответ", "говорит")), funny * 0.35)
    energy = min(10.0, max(base * 0.45, audio["loud"] * 10.0, reaction * 0.65))
    visual_reaction = max(0.0, min(10.0, _shorts_num(item.get("visual_score"), 0.0) * 1.35))
    laughter = min(10.0, funny * 0.45 + audio["reaction"] * 3.0 + audio["loud"] * 2.0)

    # Long scenes are useful pool parents, but standalone Shorts should not need
    # a minute of setup. This is a penalty, not an unconditional rejection: the
    # boundary optimizer can still extract a tighter child window.
    context_required = max(0.0, min(10.0, (duration - 28.0) / 5.0))
    if standalone >= 7.0:
        context_required *= 0.55
    dead_air = min(10.0, audio["silence"] * 8.0 + max(0.0, duration - 55.0) / 8.0)
    repetition = 2.5 if "повтор" in text or "duplicate" in text else 0.0

    funny_search_enabled = bool(settings.get("shorts_funny_search_enabled", True))
    funny_weight = 1.5 if funny_search_enabled else 0.35
    laughter_weight = 1.0 if funny_search_enabled else 0.25
    weighted = (
        hook * 1.4 + funny * funny_weight + reaction * 1.2 + surprise * 1.1 + payoff * 1.5
        + standalone * 1.2 + quotability * 0.8 + energy * 0.6 + laughter * laughter_weight
        + visual_reaction * 0.7 + base * 0.65 + confidence * 0.25
        - context_required * 1.4 - dead_air * 1.0 - repetition * 0.8
    )
    # Keep the normalized 0-10 scale stable when the user disables the
    # funny/laughter preference instead of silently leaving the toggle inert.
    positive_weight_mass = 9.4 + funny_weight + laughter_weight
    short_score = max(0.0, min(10.0, weighted / positive_weight_mass + _shorts_num(item.get("shorts_source_bonus"), 0.0)))
    moment_type = str(item.get("moment_type") or "general").strip().lower()
    if funny >= 6.0 and moment_type in {"", "general", "unknown"}:
        moment_type = "joke"
    elif reaction >= 6.0 and moment_type in {"", "general", "unknown"}:
        moment_type = "reaction"

    reasons: list[str] = []
    for name, value in (("funny", funny), ("reaction", reaction), ("hook", hook), ("payoff", payoff), ("standalone", standalone), ("energy", energy)):
        if value >= 6.0:
            reasons.append(f"{name}={value:.1f}")
    if audio["reaction"] >= 0.5:
        reasons.append("audio_reaction")
    if audio["loud"] >= 0.5:
        reasons.append("audio_peak")

    return {
        "short_score": round(short_score, 3), "hook_score": round(hook, 2), "funny_score": round(funny, 2),
        "reaction_score": round(reaction, 2), "surprise_score": round(surprise, 2), "payoff_score": round(payoff, 2),
        "standalone_score": round(standalone, 2), "quotability_score": round(quotability, 2), "energy_score": round(energy, 2),
        "visual_reaction_score": round(visual_reaction, 2), "laughter_score": round(laughter, 2),
        "context_required": round(context_required, 2), "dead_air": round(dead_air, 2), "repetition": round(repetition, 2),
        "moment_type": moment_type or "general", "short_score_reasons": reasons,
    }


def _shorts_transcript_rows(project_dir: Path, start: float, end: float) -> list[dict[str, Any]]:
    rows = read_json(project_paths(project_dir)["transcript"], []) or []
    result: list[dict[str, Any]] = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        s0 = _shorts_num(raw.get("start"), -1)
        e0 = _shorts_num(raw.get("end"), s0)
        if e0 > start and s0 < end and _clean_caption_text(raw.get("text")):
            result.append(raw)
    return result


def optimize_short_boundaries(project_dir: Path, item: dict[str, Any], settings: dict[str, Any], source_duration: float) -> dict[str, Any]:
    """Optimize a Short around a detected setup -> payoff -> reaction arc.

    V5.0 stored fake 66%/84% timestamps. V5.1 prefers explicit LLM anchors,
    audio reactions and transcript semantics, and records the evidence source so
    downstream UI/tests can distinguish a real detected payoff from fallback.
    """
    original_start = max(0.0, _shorts_num(item.get("start")))
    original_end = min(source_duration or 1e12, _shorts_num(item.get("end"), original_start))
    min_seconds = max(3.0, _shorts_num(settings.get("shorts_min_seconds"), 3.0))
    max_seconds = max(min_seconds, min(180.0, _shorts_num(settings.get("shorts_max_seconds"), 45.0)))
    if original_end <= original_start:
        return dict(item)

    search_start = max(0.0, original_start - 2.0)
    search_end = min(source_duration or original_end + 2.0, original_end + 2.0)
    rows = _shorts_transcript_rows(project_dir, search_start, search_end)
    report = read_json(project_dir / "audio_events.json", {}) or {}
    audio_events = report.get("events") if isinstance(report, dict) else []
    audio_events = audio_events if isinstance(audio_events, list) else []

    def in_range(value: Any) -> float | None:
        raw = _shorts_num(value, -1.0)
        return raw if original_start <= raw <= original_end else None

    llm_hook = in_range(item.get("llm_hook_timestamp"))
    llm_payoff = in_range(item.get("llm_payoff_timestamp"))
    llm_reaction_end = in_range(item.get("llm_reaction_end_timestamp"))

    row_scores: list[tuple[float, float, float, str]] = []
    for row in rows:
        text = _clean_caption_text(row.get("text")).lower()
        if not text:
            continue
        rs = max(original_start, _shorts_num(row.get("start"), original_start))
        re_ = min(original_end, _shorts_num(row.get("end"), rs))
        if re_ <= original_start or rs >= original_end:
            continue
        keyword = (
            _shorts_keyword_score(text, _SHORTS_PAYOFF_WORDS) * 0.8
            + _shorts_keyword_score(text, _SHORTS_REACTION_WORDS) * 0.55
            + _shorts_keyword_score(text, _SHORTS_FUNNY_WORDS) * 0.65
        )
        punctuation = min(2.0, text.count("!") * 0.7 + text.count("?") * 0.35)
        row_scores.append((keyword + punctuation, rs, re_, text))

    peaks: list[tuple[float, float, float, str]] = []
    for event in audio_events:
        if not isinstance(event, dict):
            continue
        typ = str(event.get("type") or "").lower()
        t0 = _shorts_num(event.get("time"), -1.0)
        t1 = _shorts_num(event.get("end_time"), t0 + 0.6)
        if original_start <= t0 <= original_end and typ in {"sudden_reaction", "loud_peak", "laughter", "applause"}:
            weight = max(0.1, _shorts_num(event.get("score"), 0.5))
            if typ in {"laughter", "applause"}:
                weight += 0.35
            peaks.append((weight, t0, min(original_end, max(t0, t1)), typ))

    payoff_source = "fallback_center"
    if llm_payoff is not None:
        payoff = llm_payoff
        payoff_source = "llm_semantic"
    else:
        semantic_rows = [row for row in row_scores if row[0] > 0.2]
        best_row = max(semantic_rows, default=None, key=lambda row: (row[0], row[1]))
        best_peak = max(peaks, default=None, key=lambda row: (row[0], row[1]))
        if best_row and (not best_peak or best_row[0] >= best_peak[0] * 4.0):
            payoff = min(original_end, max(original_start, best_row[2]))
            payoff_source = "transcript_semantic"
        elif best_peak:
            payoff = best_peak[1]
            payoff_source = f"audio_{best_peak[3]}"
        else:
            payoff = original_start + (original_end - original_start) * 0.62

    reaction_source = "speech_tail"
    if llm_reaction_end is not None and llm_reaction_end >= payoff:
        reaction_end = llm_reaction_end
        reaction_source = "llm_semantic"
    else:
        later_peaks = [peak for peak in peaks if payoff - 0.4 <= peak[1] <= min(original_end, payoff + 8.0)]
        if later_peaks:
            peak = max(later_peaks, key=lambda row: (row[0], row[2]))
            reaction_end = min(original_end, peak[2] + 0.8)
            reaction_source = f"audio_{peak[3]}"
        else:
            later_rows = [row for row in rows if _shorts_num(row.get("end"), 0) >= payoff and _shorts_num(row.get("start"), 0) <= payoff + 8.0]
            if later_rows:
                reaction_end = min(original_end, max(_shorts_num(row.get("end"), payoff) for row in later_rows) + 0.65)
            else:
                reaction_end = min(original_end, payoff + min(4.0, max(1.2, (original_end-original_start)*0.16)))
                reaction_source = "bounded_tail"

    # Select setup. A semantic hook from the LLM wins; otherwise walk backwards
    # through nearby spoken rows and retain enough context without dragging the
    # entire parent scene into a Short.
    if llm_hook is not None and llm_hook < payoff:
        start = max(original_start, llm_hook - 0.25)
        start_reason = "llm_hook"
    else:
        target_setup = max(6.0, min(18.0, max_seconds * 0.38))
        desired_start = max(original_start, payoff - target_setup)
        candidate_rows = [row for row in rows if _shorts_num(row.get("end"), 0) > desired_start and _shorts_num(row.get("start"), 0) < payoff]
        if candidate_rows:
            start = max(original_start, _shorts_num(candidate_rows[0].get("start"), desired_start) - 0.35)
            start_reason = "spoken_setup"
        else:
            start = desired_start
            start_reason = "bounded_setup"

    end = min(original_end, max(reaction_end, payoff + 0.65))
    end_reason = reaction_source

    # If the arc is too long, keep the detected payoff/reaction and trim setup,
    # then snap back to speech boundaries where possible.
    if end - start > max_seconds:
        start = max(original_start, end - max_seconds)
        start_reason = "max_duration_setup_trim"
        local_rows = _shorts_transcript_rows(project_dir, start, min(original_end, start + 3.0))
        if local_rows:
            snapped = _shorts_num(local_rows[0].get("start"), start) - 0.25
            if original_start <= snapped <= start + 2.0 and end - snapped <= max_seconds + 0.25:
                start = max(original_start, snapped)

    if end - start < min_seconds:
        missing = min_seconds - (end - start)
        start = max(original_start, start - missing * 0.55)
        end = min(original_end, end + missing * 0.45)
        if end - start < min_seconds:
            start = max(0.0, min(start, end - min_seconds))
            end = min(source_duration or original_end, max(end, start + min_seconds))

    # Final speech-safe shoulders.
    local_rows = _shorts_transcript_rows(project_dir, start, end)
    if local_rows:
        first_s = _shorts_num(local_rows[0].get("start"), start)
        last_e = _shorts_num(local_rows[-1].get("end"), end)
        if 0 <= first_s - start <= 1.8:
            start = max(0.0, first_s - 0.30)
        if 0 <= end - last_e <= 2.2 and last_e >= payoff:
            end = min(source_duration or original_end, last_e + 0.65)
    if end - start > max_seconds:
        start = max(0.0, end - max_seconds)

    payoff = max(start, min(end, payoff))
    reaction_timestamp = max(payoff, min(end, reaction_end))
    out = dict(item)
    out.update({
        "start": round(start, 3),
        "end": round(end, 3),
        "duration_seconds": round(max(0.0, end-start), 3),
        "short_start_reason": start_reason,
        "short_end_reason": end_reason,
        "payoff_timestamp": round(payoff, 3),
        "reaction_timestamp": round(reaction_timestamp, 3),
        "payoff_source": payoff_source,
        "reaction_source": reaction_source,
    })
    return out


def _shorts_llm_rerank(project_dir: Path, items: list[dict[str, Any]], settings: dict[str, Any], logger: "JobLogger | None" = None) -> list[dict[str, Any]]:
    """Optional bounded LLM rerank. Failure always falls back to deterministic V5 scoring."""
    if not items or not settings.get("shorts_llm_rerank_enabled", False):
        return items
    top_n = max(3, min(16, int(settings.get("shorts_llm_rerank_top", 10) or 10)))
    batch = items[:top_n]
    rows = []
    for idx, item in enumerate(batch, start=1):
        transcript = " ".join(_clean_caption_text(row.get("text")) for row in _shorts_transcript_rows(project_dir, float(item["start"]), float(item["end"])))
        rows.append(
            f"ID={idx} TIME={tc(float(item['start']))}-{tc(float(item['end']))} SCORE={float(item.get('short_score',0)):.2f} "
            f"TITLE={str(item.get('title') or '')[:120]} TYPE={str(item.get('moment_type') or '')[:50]} TEXT={transcript[:650]}"
        )
    prompt = """Ты редактор YouTube Shorts для Twitch/IRL. Оцени каждый момент независимо. Главный вопрос: поймёт ли зритель без контекста, почему это смешно/ярко? Верни только JSON:\n{"items":[{"id":1,"hook":0,"funny":0,"reaction":0,"surprise":0,"payoff":0,"standalone":0,"context_cost":0,"moment_type":"joke","hook_offset":0.0,"payoff_offset":12.0,"reaction_end_offset":16.0,"reason":"..."}]}\nШкала score 0-10. offset — секунды от начала указанного момента. Определи реальный hook/setup/payoff/reaction по репликам, а не по фиксированному проценту длительности. waiting/replay/intermission/advertisement должны получать standalone=0.\n\n""" + "\n".join(rows)
    try:
        ai = make_ai_client(settings, cancel_file=cancel_path(project_dir))
        data = ai.generate_json(
            prompt, model=effective_text_model(settings), timeout=min(240, int(settings.get("ollama_timeout", 900) or 900)),
            operation="shorts_rerank", collection_key="items", expected_ids=set(range(1, len(batch)+1)), required_item_fields={"id"},
        )
        by_id = {int(row.get("id")): row for row in data.get("items", []) if isinstance(row, dict) and str(row.get("id","")).isdigit()}
        for idx, item in enumerate(batch, start=1):
            ai_row = by_id.get(idx)
            if not ai_row:
                continue
            for field, key in (("hook_score","hook"),("funny_score","funny"),("reaction_score","reaction"),("surprise_score","surprise"),("payoff_score","payoff"),("standalone_score","standalone")):
                if key in ai_row:
                    item[field] = max(0.0, min(10.0, _shorts_num(ai_row.get(key), item.get(field,0))))
            context = max(0.0, min(10.0, _shorts_num(ai_row.get("context_cost"), item.get("context_required",0))))
            ai_quality = (
                item.get("hook_score",0)*1.4 + item.get("funny_score",0)*1.5 + item.get("reaction_score",0)*1.2
                + item.get("surprise_score",0)*1.1 + item.get("payoff_score",0)*1.5 + item.get("standalone_score",0)*1.2
                - context*1.4
            ) / 6.5
            item["short_score"] = round(max(0.0, min(10.0, float(item.get("short_score",0))*0.45 + ai_quality*0.55)), 3)
            item["shorts_llm_reason"] = str(ai_row.get("reason") or "")[:400]
            item["moment_type"] = str(ai_row.get("moment_type") or item.get("moment_type") or "general")[:60]
            item_start = float(item.get("start") or 0.0)
            item_end = float(item.get("end") or item_start)
            item_duration = max(0.0, item_end - item_start)
            for source_key, target_key in (("hook_offset", "llm_hook_timestamp"), ("payoff_offset", "llm_payoff_timestamp"), ("reaction_end_offset", "llm_reaction_end_timestamp")):
                if source_key not in ai_row:
                    continue
                raw_offset = _shorts_num(ai_row.get(source_key), -1.0)
                if raw_offset < 0:
                    continue
                offset = max(0.0, min(item_duration, raw_offset))
                item[target_key] = round(item_start + offset, 3)
        if logger:
            logger.log(f"Shorts V5: LLM rerank обработал {len(by_id)}/{len(batch)} кандидатов")
    except OperationCancelled:
        raise
    except Exception as exc:
        if logger:
            logger.log(f"Shorts V5: LLM rerank недоступен ({exc}); использую deterministic scoring")
    return sorted(items, key=lambda row: float(row.get("short_score") or 0), reverse=True)



def _sensevoice_parse_features(raw_text: str) -> dict[str, Any]:
    tags = [tag.strip().upper() for tag in re.findall(r"<\|([^|>]+)\|>", str(raw_text or ""))]
    tag_set = set(tags)
    emotion = next((name for name in ("HAPPY", "SAD", "ANGRY", "NEUTRAL", "FEARFUL", "DISGUSTED", "SURPRISED") if name in tag_set), "")
    features = {
        "laughter": 1.0 if any(tag in tag_set for tag in {"LAUGHTER", "LAUGH"}) else 0.0,
        "applause": 1.0 if any(tag in tag_set for tag in {"APPLAUSE", "CLAPPING"}) else 0.0,
        "surprise": 1.0 if "SURPRISED" in tag_set else 0.0,
        "happy": 1.0 if "HAPPY" in tag_set else 0.0,
        "angry": 1.0 if "ANGRY" in tag_set else 0.0,
        "cry": 1.0 if any(tag in tag_set for tag in {"CRY", "CRYING"}) else 0.0,
        "emotion": emotion,
        "tags": tags[:24],
    }
    return features


def _apply_sensevoice_features(item: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    """Fuse event/emotion evidence without using SenseVoice as Russian ASR."""
    out = dict(item)
    laughter = max(0.0, min(1.0, _shorts_num(features.get("laughter"), 0.0)))
    applause = max(0.0, min(1.0, _shorts_num(features.get("applause"), 0.0)))
    surprise = max(0.0, min(1.0, _shorts_num(features.get("surprise"), 0.0)))
    happy = max(0.0, min(1.0, _shorts_num(features.get("happy"), 0.0)))
    angry = max(0.0, min(1.0, _shorts_num(features.get("angry"), 0.0)))
    if not any((laughter, applause, surprise, happy, angry)):
        out["sensevoice_features"] = features
        return out
    out["laughter_score"] = round(min(10.0, max(_shorts_num(out.get("laughter_score")), laughter * 10.0)), 2)
    out["funny_score"] = round(min(10.0, _shorts_num(out.get("funny_score")) + laughter * 2.4 + happy * 0.6), 2)
    out["reaction_score"] = round(min(10.0, _shorts_num(out.get("reaction_score")) + laughter * 1.2 + applause * 1.0 + angry * 0.8 + surprise * 1.4), 2)
    out["surprise_score"] = round(min(10.0, _shorts_num(out.get("surprise_score")) + surprise * 2.2), 2)
    out["payoff_score"] = round(min(10.0, _shorts_num(out.get("payoff_score")) + applause * 1.4 + laughter * 0.7), 2)
    bonus = laughter * 0.55 + applause * 0.30 + surprise * 0.35 + happy * 0.12 + angry * 0.10
    out["short_score"] = round(min(10.0, _shorts_num(out.get("short_score")) + bonus), 3)
    reasons = list(out.get("short_score_reasons") or [])
    if laughter:
        reasons.append("sensevoice_laughter")
    if applause:
        reasons.append("sensevoice_applause")
    if surprise:
        reasons.append("sensevoice_surprise")
    if happy:
        reasons.append("sensevoice_happy")
    if angry:
        reasons.append("sensevoice_angry")
    out["short_score_reasons"] = list(dict.fromkeys(reasons))[-16:]
    out["sensevoice_features"] = features
    return out


def _shorts_sensevoice_enrich(
    project_dir: Path,
    items: list[dict[str, Any]],
    settings: dict[str, Any],
    logger: "JobLogger | None" = None,
) -> list[dict[str, Any]]:
    """Optional shortlist-only SenseVoice SER/AED pass.

    It deliberately ignores SenseVoice transcription output: the app keeps
    faster-whisper as the authoritative Russian ASR and uses SenseVoice only as
    extra laughter/emotion/event evidence. Missing package/model/network is a
    single degraded warning, never a fatal Shorts error.
    """
    if not items or not settings.get("shorts_emotion_events_enabled", True):
        return items
    try:
        import importlib.util
        if importlib.util.find_spec("funasr") is None:
            if logger:
                logger.log("Shorts V5.1: SenseVoice не установлен; смех/эмоции оцениваются по локальным audio events")
            return items
        from funasr import AutoModel  # type: ignore
    except Exception as exc:
        if logger:
            logger.log(f"Shorts V5.1: SenseVoice недоступен ({exc}); продолжаю без него")
        return items

    top_n = max(1, min(40, int(settings.get("shorts_emotion_top_n", 20) or 20)))
    shortlist = items[:top_n]
    cache_dir = project_dir / "shorts_cache" / "sensevoice"
    cache_dir.mkdir(parents=True, exist_ok=True)
    video = project_paths(project_dir)["video"]
    model_name = str(settings.get("shorts_sensevoice_model") or "iic/SenseVoiceSmall")
    caps = detect_hardware_capabilities()
    ct2 = caps.get("ctranslate2") or {}
    device = "cuda:0" if ct2.get("cuda_ok") else "cpu"
    model = None
    model_error: Exception | None = None
    enriched: list[dict[str, Any]] = []
    ffmpeg = which("ffmpeg") or "ffmpeg"

    for position, source in enumerate(shortlist, start=1):
        item = dict(source)
        start = float(item.get("start") or 0.0)
        end = float(item.get("end") or start)
        fp = stable_hash({
            "engine": SHORTS_V5_VERSION,
            "video": video_content_signature(project_dir),
            "start": round(start, 3),
            "end": round(end, 3),
            "model": model_name,
            "purpose": "ser_aed_only",
        })[:24]
        cache_path = cache_dir / f"{fp}.json"
        features = read_json(cache_path, None)
        if not isinstance(features, dict):
            if model_error is not None:
                enriched.append(item)
                continue
            if model is None:
                try:
                    # Official SenseVoice examples use FunASR AutoModel. For
                    # short shortlisted clips the base model is enough; no
                    # diarization/VAD models are made mandatory.
                    model = AutoModel(model=model_name, trust_remote_code=True, device=device)
                except Exception as exc:
                    model_error = exc
                    if logger:
                        logger.log(f"Shorts V5.1: SenseVoice model не загрузился ({exc}); продолжаю без optional SER/AED")
                    enriched.append(item)
                    continue
            wav = cache_dir / f"{fp}.wav"
            try:
                rr = run_cmd([
                    ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{max(0.2,end-start):.3f}",
                    "-vn", "-ac", "1", "-ar", "16000", str(wav),
                ], project_dir=project_dir, cancel_file=cancel_path(project_dir))
                if rr.returncode != 0:
                    raise RuntimeError(rr.stdout[-500:])
                result = model.generate(input=str(wav), cache={}, language="auto", use_itn=False, batch_size_s=60)
                raw_text = ""
                if isinstance(result, list) and result and isinstance(result[0], dict):
                    raw_text = str(result[0].get("text") or "")
                features = _sensevoice_parse_features(raw_text)
                features.update({"model": model_name, "device": device, "cached": False})
                write_json(cache_path, features)
            except Exception as exc:
                features = {"error": str(exc)[:400], "model": model_name, "device": device}
                # Cache only a short-lived diagnostic artifact in memory; do not
                # make a transient model/runtime outage sticky on disk.
                if logger:
                    logger.log(f"Short {position}: SenseVoice optional pass пропущен ({exc})")
            finally:
                try:
                    wav.unlink(missing_ok=True)
                except OSError:
                    pass
        if isinstance(features, dict) and not features.get("error"):
            item = _apply_sensevoice_features(item, features)
        enriched.append(item)

    # Keep candidates outside the expensive shortlist unchanged.
    enriched.extend(dict(item) for item in items[top_n:])
    try:
        del model
        import gc
        gc.collect()
    except Exception:
        pass
    if logger:
        used = sum(1 for item in enriched[:top_n] if item.get("sensevoice_features"))
        logger.log(f"Shorts V5.1: SenseVoice SER/AED применён к {used}/{len(shortlist)} shortlisted моментов")
    return enriched


def _shorts_artifact_digest(path: Path) -> str:
    """Content digest for small/medium analysis artifacts that drive Shorts selection.

    Selection-plan freshness must follow artifact content, not mtimes or the old
    batch layout. Missing artifacts have an explicit sentinel so an artifact
    appearing later invalidates the plan exactly once.
    """
    if not path.exists() or not path.is_file():
        return "missing"
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"


def _shorts_selection_fingerprint(project_dir: Path, settings: dict[str, Any]) -> str:
    """Fingerprint only inputs that can change *which moments* are selected.

    Caption font/framing/render settings deliberately do not belong here. They
    have their own per-Short render fingerprints and must not trigger expensive
    LLM/SenseVoice selection work.
    """
    p = project_paths(project_dir)
    artifacts = {
        "micro": project_dir / "micro_candidates.json",
        "blocks": project_dir / "block_candidates.json",
        "candidates": p["candidates"],
        "segments": p["segments"],
        "audio_events": project_dir / "audio_events.json",
        "transcript": p["transcript"],
    }
    payload = {
        "engine": SHORTS_V5_VERSION,
        "video": video_content_signature(project_dir),
        "artifacts": {name: _shorts_artifact_digest(path) for name, path in artifacts.items()},
        "settings": settings_subset(settings, [
            "shorts_count",
            "shorts_min_seconds",
            "shorts_max_seconds",
            "shorts_candidate_pool_limit",
            "shorts_min_quality_score",
            "shorts_funny_search_enabled",
            "shorts_emotion_events_enabled",
            "shorts_sensevoice_model",
            "shorts_emotion_top_n",
            "shorts_llm_rerank_enabled",
            "shorts_llm_rerank_top",
            "non_primary_reject_confidence",
            "language",
            "ai_engine",
            "text_model",
            "ollama_model",
        ]),
    }
    return stable_hash(payload)


def _shorts_edit_identity(item: dict[str, Any]) -> str:
    """Stable-enough identity used only to carry manual editor fields forward."""
    if item.get("editor_identity"):
        return str(item["editor_identity"])
    explicit = item.get("candidate_id")
    if explicit not in (None, ""):
        return f"candidate:{explicit}"
    source = str(item.get("shorts_source") or item.get("source") or "")
    source_id = item.get("source_segment_id", item.get("id"))
    if source_id not in (None, ""):
        return f"source:{source}:{source_id}"
    return stable_hash({
        "source": source,
        "start": round(_shorts_num(item.get("start"), 0.0), 2),
        "end": round(_shorts_num(item.get("end"), 0.0), 2),
        "title": str(item.get("title") or "")[:160],
    })[:28]


def _ensure_shorts_candidates_current(
    project_dir: Path,
    settings: dict[str, Any],
    logger: "JobLogger | None" = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return a selection plan matching the current analysis/settings.

    11.2.0 previously reused ``content_factory/shorts_candidates.json`` forever.
    Changing count/funny/emotion/LLM selection settings therefore could render
    an old shortlist. This helper makes candidate selection independently
    resumable/fresh while preserving user-edited caption/hook/reframe fields.
    """
    p = project_paths(project_dir)
    factory = p["factory"]
    factory.mkdir(parents=True, exist_ok=True)
    candidate_path = factory / "shorts_candidates.json"
    plan_path = project_dir / "shorts_quality_plan.json"
    current_fp = _shorts_selection_fingerprint(project_dir, settings)
    existing = read_json(candidate_path, []) or []
    plan = read_json(plan_path, {}) or {}
    if (
        isinstance(existing, list)
        and existing
        and isinstance(plan, dict)
        and str(plan.get("fingerprint") or "") == current_fp
    ):
        rejected = plan.get("rejected") if isinstance(plan.get("rejected"), list) else []
        return [dict(row) for row in existing if isinstance(row, dict)], list(rejected)

    # Legacy/recovered projects can contain a perfectly usable factory shortlist
    # while the upstream candidate artifacts were intentionally not copied. Do
    # not destroy that only copy merely because pre-5.1 plans had no fingerprint.
    # Bootstrap freshness once; if upstream artifacts appear later their digests
    # will change and the plan will rebuild normally.
    upstream_paths = [
        project_dir / "micro_candidates.json",
        project_dir / "block_candidates.json",
        p["candidates"],
        p["segments"],
    ]
    upstream_has_rows = False
    for upstream_path in upstream_paths:
        rows = read_json(upstream_path, []) or []
        if isinstance(rows, list) and any(isinstance(row, dict) for row in rows):
            upstream_has_rows = True
            break
    if isinstance(existing, list) and existing and not upstream_has_rows and not str(plan.get("fingerprint") or ""):
        bootstrap_plan = dict(plan) if isinstance(plan, dict) else {}
        bootstrap_plan.update({
            "version": SHORTS_V5_VERSION,
            "fingerprint": current_fp,
            "selected": existing,
            "selected_count": len(existing),
            "requested_count": max(1, int(settings.get("shorts_count", len(existing)) or len(existing))),
            "bootstrap_legacy_factory": True,
        })
        write_json(plan_path, bootstrap_plan)
        if logger:
            logger.log("Shorts V5.1: восстановлен fingerprint существующего legacy shortlist без потери кандидатов")
        rejected = bootstrap_plan.get("rejected") if isinstance(bootstrap_plan.get("rejected"), list) else []
        return [dict(row) for row in existing if isinstance(row, dict)], list(rejected)

    manual_by_identity: dict[str, dict[str, Any]] = {}
    for raw in existing if isinstance(existing, list) else []:
        if not isinstance(raw, dict):
            continue
        preserved: dict[str, Any] = {}
        # Presence matters for caption_text: an intentionally empty caption is
        # different from "no edit" and is handled downstream by the editor.
        for key in ("caption_text", "hook_text", "reframe_mode"):
            if key in raw:
                preserved[key] = raw.get(key)
        if raw.get("bounds_edited"):
            for key in ("title", "start", "end", "duration_seconds", "bounds_edited", "editor_identity"):
                if key in raw:
                    preserved[key] = raw[key]
            preserved["caption_text"] = raw.get("caption_text")
        if preserved:
            manual_by_identity[_shorts_edit_identity(raw)] = preserved

    if logger:
        why = "отсутствует" if not existing else "устарел"
        logger.log(f"Shorts V5.1: план выбора {why}; обновляю только shortlist без повторного Whisper")
    selected, rejected = build_shorts_quality_candidates(project_dir, settings, logger=logger)
    merged: list[dict[str, Any]] = []
    for source in selected:
        item = dict(source)
        edits = manual_by_identity.get(_shorts_edit_identity(item))
        if edits:
            item.update(edits)
            if item.get("caption_text") is None:
                item.pop("caption_text", None)
        item["render_dirty"] = True
        merged.append(item)
    write_json(candidate_path, merged)

    # Keep Content Factory manifest consistent for dashboard/API consumers, but
    # do not rebuild its unrelated montage exports.
    manifest_path = factory / "content_factory_manifest.json"
    manifest = read_json(manifest_path, {}) or {}
    if not isinstance(manifest, dict):
        manifest = {}
    manifest["shorts"] = merged
    manifest["shorts_rejected"] = rejected
    write_json(manifest_path, manifest)
    return merged, rejected


def build_shorts_quality_candidates(project_dir: Path, settings: dict[str, Any], *, logger: "JobLogger | None" = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_duration = video_duration(project_paths(project_dir)["video"])
    requested = max(1, int(settings.get("shorts_count", 5) or 5))
    pool_limit = max(requested * 4, min(120, int(settings.get("shorts_candidate_pool_limit", 48) or 48)))
    minimum_score = max(0.0, min(10.0, _shorts_num(settings.get("shorts_min_quality_score"), 4.8)))
    rejected: list[dict[str, Any]] = []
    ranked: list[dict[str, Any]] = []
    for item in _shorts_candidate_pool(project_dir):
        if _shorts_semantic_rejected(item, settings):
            rejected.append({"title": item.get("title"), "start": item.get("start"), "reason": "semantic_non_primary"})
            continue
        enriched = dict(item)
        enriched.update(shorts_candidate_score(project_dir, enriched, settings))
        # First-pass boundaries keep the expensive shortlist compact. A second
        # pass after LLM/SenseVoice uses their stronger payoff/reaction anchors.
        enriched = optimize_short_boundaries(project_dir, enriched, settings, source_duration)
        ranked.append(enriched)
    ranked.sort(key=lambda row: (float(row.get("short_score") or 0), float(row.get("score") or 0)), reverse=True)
    working_limit = max(pool_limit, min(len(ranked), pool_limit * 2))
    ranked = _shorts_sensevoice_enrich(project_dir, ranked[:working_limit], settings, logger)
    ranked.sort(key=lambda row: (float(row.get("short_score") or 0), float(row.get("score") or 0)), reverse=True)
    ranked = _shorts_llm_rerank(project_dir, ranked[:pool_limit], settings, logger)
    ranked = [optimize_short_boundaries(project_dir, item, settings, source_duration) for item in ranked]
    kept: list[dict[str, Any]] = []
    for item in ranked:
        if float(item.get("short_score") or 0) < minimum_score:
            rejected.append({"title": item.get("title"), "start": item.get("start"), "reason": "quality_below_threshold", "short_score": item.get("short_score")})
        else:
            kept.append(item)
    ranked = kept

    # Normalize intervals and suppress near-duplicates. Keep a wider candidate
    # pool than requested first, then enforce temporal/moment diversity.
    normalized, norm_rejected = normalize_shorts_candidates(
        ranked, source_duration, limit=pool_limit,
        min_seconds=float(settings.get("shorts_min_seconds", 3.0)), max_seconds=float(settings.get("shorts_max_seconds", 45.0)),
    )
    rejected.extend(norm_rejected)
    selected: list[dict[str, Any]] = []
    for item in normalized:
        duplicate = any(_short_overlap_ratio(item, kept) >= 0.52 for kept in selected)
        if duplicate:
            rejected.append({"title": item.get("title"), "start": item.get("start"), "reason": "v5_overlap_duplicate"})
            continue
        # Mild diversity preference: do not take an almost identical moment type
        # within 20 seconds while alternatives remain. It is a preference, not a
        # hard global spacing rule.
        same_near = any(
            str(item.get("moment_type")) == str(kept.get("moment_type"))
            and abs(float(item.get("start",0))-float(kept.get("start",0))) < 20.0
            for kept in selected
        )
        if same_near and len(normalized) > requested:
            continue
        selected.append(item)
        if len(selected) >= requested:
            break
    for item in selected:
        item["start_tc"] = tc(float(item["start"]))
        item["end_tc"] = tc(float(item["end"]))
        item["shorts_engine_version"] = SHORTS_V5_VERSION
    plan = {
        "version": SHORTS_V5_VERSION, "created_at": time.time(), "requested_count": requested,
        "selected_count": len(selected), "pool_count": len(ranked), "selected": selected, "rejected": rejected,
        "quality_threshold": minimum_score,
        "fingerprint": _shorts_selection_fingerprint(project_dir, settings),
    }
    write_json(project_dir / "shorts_quality_plan.json", plan)
    if logger:
        logger.log(f"Shorts V5: candidate pool={len(ranked)}, quality-selected={len(selected)}/{requested}")
    return selected, rejected



def _caption_token_key(value: str) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", str(value or "").lower(), flags=re.IGNORECASE)


def _manual_caption_transcript(
    manual_text: str,
    *,
    start: float,
    end: float,
    timing_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Apply editor text while preserving as much existing word timing as possible.

    11.2.0 persisted ``caption_text`` but rendered the old ASR transcript.  This
    function makes the editor authoritative for visible text. Matching words
    keep their original timestamps; inserted/replaced words are interpolated
    between neighbouring anchors so a one-word correction does not destroy the
    whole word-level timing plan.
    """
    text = _clean_caption_text(manual_text)
    if not text:
        return None
    tokens = [part for part in re.findall(r"\S+", text) if part.strip()]
    if not tokens:
        return None

    source_words: list[dict[str, Any]] = []
    for row in timing_rows or []:
        if not isinstance(row, dict):
            continue
        words = row.get("words") if isinstance(row.get("words"), list) else []
        for raw in words:
            if not isinstance(raw, dict):
                continue
            word = _clean_caption_text(raw.get("word"))
            try:
                w_start = float(raw.get("start"))
                w_end = float(raw.get("end"))
            except Exception:
                continue
            if word and math.isfinite(w_start) and math.isfinite(w_end) and w_end > w_start:
                source_words.append({"word": word, "start": max(start, w_start), "end": min(end, w_end)})
    source_words.sort(key=lambda row: (row["start"], row["end"]))

    duration = max(0.35, end - start)
    target: list[dict[str, Any] | None] = [None] * len(tokens)
    if source_words:
        source_keys = [_caption_token_key(row["word"]) for row in source_words]
        target_keys = [_caption_token_key(word) for word in tokens]
        matcher = difflib.SequenceMatcher(a=source_keys, b=target_keys, autojunk=False)
        for block in matcher.get_matching_blocks():
            for offset in range(block.size):
                source = source_words[block.a + offset]
                target[block.b + offset] = {
                    "word": tokens[block.b + offset],
                    "start": float(source["start"]),
                    "end": float(source["end"]),
                }

    # Interpolate unmatched runs. Weighted duration makes long words slightly
    # wider while still respecting exact timing anchors around unchanged text.
    index = 0
    while index < len(tokens):
        if target[index] is not None:
            index += 1
            continue
        run_start = index
        while index < len(tokens) and target[index] is None:
            index += 1
        run_end = index
        left_time = float(target[run_start - 1]["end"]) if run_start > 0 and target[run_start - 1] else start
        right_time = float(target[run_end]["start"]) if run_end < len(tokens) and target[run_end] else end
        if right_time <= left_time + 0.08:
            # If ASR anchors collide, allocate a safe local interval instead of
            # emitting invalid negative/zero subtitle timings.
            left_time = max(start, left_time - 0.04)
            right_time = min(end, max(left_time + 0.12 * (run_end - run_start), right_time + 0.12))
        weights = [max(1.0, min(12.0, float(len(_caption_token_key(tokens[i])) or 1))) for i in range(run_start, run_end)]
        total_weight = max(1.0, sum(weights))
        cursor = left_time
        for local, token_index in enumerate(range(run_start, run_end)):
            fraction = weights[local] / total_weight
            token_end = right_time if token_index == run_end - 1 else cursor + (right_time - left_time) * fraction
            token_end = max(cursor + 0.04, min(end, token_end))
            target[token_index] = {"word": tokens[token_index], "start": cursor, "end": token_end}
            cursor = token_end
            total_weight -= weights[local]
            left_time = cursor

    words: list[dict[str, Any]] = []
    previous_end = start
    for token, row in zip(tokens, target):
        row = dict(row or {})
        w_start = max(start, min(end, float(row.get("start", previous_end))))
        w_end = max(w_start + 0.04, min(end, float(row.get("end", w_start + 0.12))))
        if w_start < previous_end:
            w_start = previous_end
            w_end = max(w_start + 0.04, w_end)
        if w_start >= end:
            break
        w_end = min(end, w_end)
        if w_end <= w_start:
            continue
        words.append({"word": token, "start": round(w_start, 3), "end": round(w_end, 3)})
        previous_end = w_end

    if not words:
        # Absolute fallback: evenly distribute text over the Short.
        step = duration / max(1, len(tokens))
        words = [
            {
                "word": token,
                "start": round(start + step * idx, 3),
                "end": round(min(end, start + step * (idx + 1)), 3),
            }
            for idx, token in enumerate(tokens)
        ]
    return [{"start": round(start, 3), "end": round(end, 3), "text": text, "words": words, "source": "manual_editor"}]


def _shorts_refined_transcript(
    project_dir: Path, video: Path, start: float, end: float, settings: dict[str, Any], logger: "JobLogger | None", short_index: int,
) -> list[dict[str, Any]] | None:
    """Re-transcribe only the final Short and layer optional alignment safely.

    V5.1 uses two independent caches:
    - ``shorts_cache/asr`` stores authoritative faster-whisper word timestamps;
    - ``shorts_cache/alignment`` stores WhisperX output only after a successful
      forced-alignment pass.

    This prevents a temporary/missing WhisperX install from becoming a sticky
    "MAX quality" cache entry. Installing/fixing WhisperX later will retry only
    alignment and will never rerun the expensive short ASR unnecessarily.
    """
    quality = str(settings.get("shorts_caption_quality") or "high").strip().lower()
    if quality in {"fast", "быстро", "off", "existing"}:
        return None

    model_name = str(settings.get("shorts_whisper_model") or "small")
    dictionary = str(settings.get("shorts_recognition_dictionary") or "").strip()
    language = str(settings.get("language") or "ru")
    source_signature = video_content_signature(project_dir)
    asr_payload = {
        "version": SHORTS_V5_VERSION,
        "engine": "faster_whisper_short_asr_v2",
        "source": source_signature,
        "start": round(start, 3),
        "end": round(end, 3),
        "model": model_name,
        "language": language,
        "dictionary": dictionary,
        "beam_size": 5,
        "word_timestamps": True,
        "condition_on_previous_text": False,
    }
    asr_fp = hashlib.sha256(json.dumps(asr_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
    asr_dir = project_dir / "shorts_cache" / "asr"
    align_dir = project_dir / "shorts_cache" / "alignment"
    asr_dir.mkdir(parents=True, exist_ok=True)
    align_dir.mkdir(parents=True, exist_ok=True)
    asr_cache = asr_dir / f"{asr_fp}.json"
    wav = asr_dir / f"{asr_fp}.wav"
    ffmpeg = which("ffmpeg") or "ffmpeg"

    rows = read_json(asr_cache, None)
    if isinstance(rows, list) and rows:
        if logger:
            logger.log(f"Short {short_index}: использую V5 short-ASR cache")
    else:
        rows = None

    def ensure_wav() -> Path:
        if wav.exists() and wav.stat().st_size > 1024:
            return wav
        rr = run_cmd(
            [
                ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(video),
                "-t", f"{max(0.1, end-start):.3f}", "-vn", "-ac", "1", "-ar", "16000", str(wav),
            ],
            project_dir=project_dir,
            cancel_file=cancel_path(project_dir),
            timeout=60,
        )
        if rr.returncode != 0 or not wav.exists() or wav.stat().st_size <= 1024:
            raise RuntimeError((rr.stdout or "FFmpeg short audio extraction failed")[-600:])
        return wav

    try:
        if rows is None:
            ensure_wav()
            prepare_nvidia_dll_paths()
            from faster_whisper import WhisperModel

            capabilities = detect_hardware_capabilities()
            rec = capabilities.get("recommended_settings") or {}
            ct2 = capabilities.get("ctranslate2") or {}
            device = "cuda" if ct2.get("cuda_ok") and str(rec.get("whisper_device") or "cpu") == "cuda" else "cpu"
            compute = str(rec.get("whisper_compute") or ("int8_float32" if device == "cuda" else "int8"))
            kwargs: dict[str, Any] = {"device": device, "compute_type": compute, "num_workers": 1, "local_files_only": True}
            if device == "cpu":
                kwargs["cpu_threads"] = max(1, min(6, int((capabilities.get("cpu") or {}).get("logical_threads") or os.cpu_count() or 2) // 2))
            try:
                if logger:
                    logger.log(f"Short {short_index}: загружаю локальную Whisper {model_name} ({device}/{compute}); без скрытой загрузки модели из сети")
                model = WhisperModel(model_name, **kwargs)
            except OperationCancelled:
                raise
            except Exception as first_exc:
                if logger and device == "cuda":
                    logger.log(f"Short {short_index}: Whisper CUDA не запустился ({first_exc}); повторяю ASR на CPU")
                model = WhisperModel(
                    model_name, device="cpu", compute_type="int8", num_workers=1, local_files_only=True,
                    cpu_threads=max(1, min(6, (os.cpu_count() or 2) // 2)),
                )
                device = "cpu"

            transcribe_kwargs: dict[str, Any] = {
                "beam_size": 5,
                "vad_filter": True,
                "word_timestamps": True,
                "language": None if language == "auto" else language,
                "condition_on_previous_text": False,
            }
            if dictionary:
                hints = ", ".join(part.strip() for part in re.split(r"[,;\n]+", dictionary) if part.strip())[:500]
                if hints:
                    transcribe_kwargs["hotwords"] = hints
                    transcribe_kwargs["initial_prompt"] = "Имена, ники и термины: " + hints
            try:
                iterator, _ = model.transcribe(str(wav), **transcribe_kwargs)
            except TypeError:
                # Backward compatibility with faster-whisper builds that predate
                # the hotwords keyword. ``initial_prompt`` still carries hints.
                transcribe_kwargs.pop("hotwords", None)
                iterator, _ = model.transcribe(str(wav), **transcribe_kwargs)

            fresh_rows: list[dict[str, Any]] = []
            for seg in iterator:
                check_cancel(project_dir)
                text = _clean_caption_text(getattr(seg, "text", ""))
                if not text:
                    continue
                words: list[dict[str, Any]] = []
                for word in getattr(seg, "words", None) or []:
                    w = _clean_caption_text(getattr(word, "word", ""))
                    if not w:
                        continue
                    w_start = max(start, min(end, start + float(getattr(word, "start", 0.0))))
                    w_end = max(w_start, min(end, start + float(getattr(word, "end", 0.0))))
                    if w_end <= w_start:
                        continue
                    words.append({"start": round(w_start, 3), "end": round(w_end, 3), "word": w})
                seg_start = max(start, min(end, start + float(seg.start)))
                seg_end = max(seg_start, min(end, start + float(seg.end)))
                if seg_end > seg_start:
                    fresh_rows.append({"start": round(seg_start, 3), "end": round(seg_end, 3), "text": text, "words": words})
            if not fresh_rows:
                return None
            rows = fresh_rows
            write_json(asr_cache, rows)
            if logger:
                logger.log(f"Short {short_index}: V5 captions распознаны заново ({model_name}, beam=5, {device})")
            try:
                del model
                import gc
                gc.collect()
            except Exception:
                pass

        wants_alignment = quality in {"max", "maximum", "максимум"} and bool(settings.get("shorts_precise_alignment", True))
        if not wants_alignment:
            return rows

        import importlib.util
        if importlib.util.find_spec("whisperx") is None:
            if logger:
                logger.log(f"Short {short_index}: WhisperX не установлен; использую faster-whisper word timestamps")
            return rows

        # Alignment is a separate successful-only cache. A transient alignment
        # crash therefore retries next time without invalidating short ASR.
        align_payload = {
            "version": SHORTS_V5_VERSION,
            "engine": "whisperx_alignment_v2",
            "asr_fingerprint": asr_fp,
            "language": language,
        }
        align_fp = hashlib.sha256(json.dumps(align_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
        align_cache = align_dir / f"{align_fp}.json"
        aligned_cached = read_json(align_cache, None)
        if isinstance(aligned_cached, list) and aligned_cached:
            if logger:
                logger.log(f"Short {short_index}: использую WhisperX alignment cache")
            return aligned_cached

        try:
            ensure_wav()
            import whisperx  # type: ignore

            capabilities = detect_hardware_capabilities()
            ct2 = capabilities.get("ctranslate2") or {}
            align_device = "cuda" if ct2.get("cuda_ok") else "cpu"
            align_language = "ru" if language == "auto" else language
            model_a, metadata = whisperx.load_align_model(language_code=align_language, device=align_device)
            relative = [
                {"start": float(r["start"]) - start, "end": float(r["end"]) - start, "text": r["text"]}
                for r in rows if isinstance(r, dict)
            ]
            aligned = whisperx.align(relative, model_a, metadata, str(wav), align_device, return_char_alignments=False)
            aligned_rows: list[dict[str, Any]] = []
            for row in aligned.get("segments", []) if isinstance(aligned, dict) else []:
                text = _clean_caption_text(row.get("text"))
                if not text:
                    continue
                words: list[dict[str, Any]] = []
                for raw_word in row.get("words", []) or []:
                    if raw_word.get("start") is None or raw_word.get("end") is None:
                        continue
                    word = _clean_caption_text(raw_word.get("word"))
                    if not word:
                        continue
                    w_start = max(start, min(end, start + float(raw_word["start"])))
                    w_end = max(w_start, min(end, start + float(raw_word["end"])))
                    if w_end > w_start:
                        words.append({"start": round(w_start, 3), "end": round(w_end, 3), "word": word})
                row_start = max(start, min(end, start + float(row.get("start", 0))))
                row_end = max(row_start, min(end, start + float(row.get("end", 0))))
                if row_end > row_start:
                    aligned_rows.append({"start": round(row_start, 3), "end": round(row_end, 3), "text": text, "words": words})
            if aligned_rows:
                write_json(align_cache, aligned_rows)
                if logger:
                    logger.log(f"Short {short_index}: WhisperX precise alignment готов")
                return aligned_rows
        except OperationCancelled:
            raise
        except Exception as exc:
            if logger:
                logger.log(f"Short {short_index}: WhisperX alignment недоступен ({exc}); использую faster-whisper word timestamps")
        return rows
    except OperationCancelled:
        raise
    except Exception as exc:
        if logger:
            logger.log(f"Short {short_index}: high-quality ASR недоступен ({exc}); использую основной transcript")
        return None
    finally:
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            pass

def _shorts_caption_worker(request_path: str, result_path: str) -> None:
    """Fixed desktop/Python entry point; native ASR runs outside the server."""
    request = read_json(Path(request_path), {})
    project_dir = Path(request["project_dir"])
    class LogOnly:
        def log(self, message):
            with (project_dir / "logs.txt").open("a", encoding="utf-8") as stream:
                stream.write(str(message) + "\n")
    rows = _shorts_refined_transcript(
        project_dir, Path(request["video"]), float(request["start"]), float(request["end"]),
        request["settings"], LogOnly(), int(request["index"]),
    )
    write_json(Path(result_path), {"rows": rows})


def _shorts_refined_transcript_bounded(project_dir, video, start, end, settings, logger, short_index):
    quality = str(settings.get("shorts_caption_quality") or "high").lower()
    if quality in {"fast", "быстро", "off", "existing"} or settings.get("_shorts_asr_unavailable"):
        return None
    root = project_dir / "shorts_cache" / "workers"
    root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    request_path, result_path = root / f"{token}.request.json", root / f"{token}.result.json"
    write_json(request_path, {"project_dir": str(project_dir.resolve()), "video": str(video.resolve()),
                             "start": start, "end": end, "index": short_index, "settings": settings})
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--shorts-caption-worker", str(request_path), str(result_path)]
    else:
        script = ("import sys;sys.path.insert(0," + repr(str(Path(__file__).resolve().parents[2])) + ");"
                  "from highlight_studio.services.pipeline import _shorts_caption_worker;"
                  "_shorts_caption_worker(sys.argv[1],sys.argv[2])")
        cmd = [sys.executable, "-c", script, str(request_path), str(result_path)]
    try:
        check_cancel(project_dir)
        result = run_cmd(cmd, timeout=300, project_dir=project_dir, cancel_file=cancel_path(project_dir))
        check_cancel(project_dir)
        rows = (read_json(result_path, {}) or {}).get("rows")
        if result.returncode == 0 and isinstance(rows, list) and rows:
            return rows
        raise RuntimeError(f"ASR worker code={result.returncode}; уточнённая расшифровка не получена")
    except OperationCancelled:
        raise
    except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
        # One failure must not cost another five minutes for every subsequent clip.
        settings["_shorts_asr_unavailable"] = True
        logger.log(f"Short {short_index}: уточнение субтитров недоступно ({exc}). "
                   "Для этого экспорта используется сохранённая расшифровка; тайминги могут быть менее точными.")
        write_json(project_dir / "shorts_caption_warning.json", {"fallback": True,
                   "reason": str(exc)[:500], "at": time.time(), "source": "existing_transcript"})
        return None
    finally:
        request_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)


def shorts_quality_components() -> dict[str, Any]:
    import importlib.util
    from .face_tracking import MODEL_PATH
    return {
        "version": SHORTS_V5_VERSION,
        "faster_whisper": {"available": importlib.util.find_spec("faster_whisper") is not None, "required": True},
        "whisperx": {"available": importlib.util.find_spec("whisperx") is not None, "required": False},
        "sensevoice": {"available": importlib.util.find_spec("funasr") is not None, "required": False},
        "face_tracking": {
            "available": importlib.util.find_spec("mediapipe") is not None and MODEL_PATH.is_file(),
            "model_present": MODEL_PATH.is_file(), "required": False,
            "engine": "MediaPipe Tasks / BlazeFace", "device": "CPU",
        },
    }

def normalize_shorts_candidates(
    items: list[dict[str, Any]],
    source_duration: float,
    *,
    limit: int,
    min_seconds: float = 3.0,
    max_seconds: float = 60.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate, clamp, rank and deduplicate Shorts candidates.

    Invalid intervals are rejected instead of silently inventing five seconds of
    unrelated footage.  Overlapping candidates keep only the stronger version.
    """
    duration = max(0.0, float(source_duration or 0))
    min_seconds = max(1.0, float(min_seconds or 3.0))
    max_seconds = max(min_seconds, min(180.0, float(max_seconds or 60.0)))
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for position, raw in enumerate(items or [], start=1):
        item = dict(raw or {})
        raw_start = item.get("start", item.get("source_start", 0))
        raw_end = item.get("end", item.get("source_end", None))
        start = _parse_tc_to_seconds_strict(raw_start)
        end = _parse_tc_to_seconds_strict(raw_end if raw_end is not None else ((start or 0.0) + max_seconds))
        if start is None or end is None:
            rejected.append({"index": position, "title": item.get("title"), "reason": "invalid_timecode"})
            continue
        if item.get("bounds_edited"):
            try:
                validate_short_editor_bounds(start, end, {"shorts_max_seconds": max_seconds}, duration)
            except ValueError as exc:
                rejected.append({"index": position, "title": item.get("title"), "reason": "invalid_edited_bounds", "message": str(exc)})
                continue
        if duration > 0:
            start = max(0.0, min(start, duration))
            end = max(0.0, min(end, duration))
        else:
            start = max(0.0, start)
            end = max(0.0, end)
        if end <= start:
            rejected.append({"index": position, "title": item.get("title"), "reason": "end_not_after_start"})
            continue
        if end - start > max_seconds:
            end = start + max_seconds
        clip_duration = end - start
        if clip_duration < (1.0 if item.get("bounds_edited") else min_seconds):
            rejected.append(
                {
                    "index": position,
                    "title": item.get("title"),
                    "reason": "clip_too_short",
                    "duration_seconds": round(clip_duration, 3),
                }
            )
            continue
        item.update(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "duration_seconds": round(clip_duration, 3),
                "short_strength": _short_candidate_strength(item),
                "source_index": position,
            }
        )
        accepted.append(item)

    accepted.sort(key=lambda item: (float(item.get("short_strength") or 0), float(item.get("score") or 0)), reverse=True)
    deduped: list[dict[str, Any]] = []
    for item in accepted:
        duplicate = next((kept for kept in deduped if _short_overlap_ratio(item, kept) >= 0.65), None)
        if duplicate is not None:
            rejected.append(
                {
                    "index": item.get("source_index"),
                    "title": item.get("title"),
                    "reason": "overlap_duplicate",
                    "kept_title": duplicate.get("title"),
                }
            )
            continue
        deduped.append(item)
        if len(deduped) >= max(1, int(limit or 1)):
            break
    return deduped, rejected


_CAPTION_END_RE = re.compile(r"[.!?…,:;][\"'»)]*$")


def _clean_caption_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"([!?.,…])\1{2,}", r"\1\1", text)
    return text


def _split_caption_text(text: str, *, max_words: int = 5, max_chars: int = 34) -> list[str]:
    """Split long Whisper phrases into fast, readable Shorts caption beats."""
    words = _clean_caption_text(text).split()
    if not words:
        return []
    max_words = max(2, min(9, int(max_words or 5)))
    max_chars = max(18, min(48, int(max_chars or 34)))
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        candidate = " ".join(current)
        punctuation_break = len(current) >= 2 and bool(_CAPTION_END_RE.search(word))
        if len(current) >= max_words or len(candidate) >= max_chars or punctuation_break:
            chunks.append(candidate)
            current = []
    if current:
        chunks.append(" ".join(current))
    if len(chunks) >= 2 and len(chunks[-1].split()) == 1 and len((chunks[-2] + " " + chunks[-1]).split()) <= max_words + 1:
        chunks[-2] = f"{chunks[-2]} {chunks[-1]}"
        chunks.pop()
    return chunks


def _balanced_caption_lines(text: str, *, ass: bool = False, max_line_chars: int = 27) -> str:
    words = _clean_caption_text(text).split()
    if len(words) < 3 or len(" ".join(words)) <= max_line_chars:
        return " ".join(words)
    best_index = 1
    best_score = float("inf")
    for index in range(1, len(words)):
        left = " ".join(words[:index])
        right = " ".join(words[index:])
        score = abs(len(left) - len(right)) + max(0, len(left) - max_line_chars) * 3 + max(0, len(right) - max_line_chars) * 3
        if score < best_score:
            best_score = score
            best_index = index
    separator = r"\N" if ass else "\n"
    return f"{' '.join(words[:best_index])}{separator}{' '.join(words[best_index:])}"


def _caption_cues(project_dir: Path, start: float, end: float, *, max_words: int = 4, transcript_override: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    transcript = transcript_override if isinstance(transcript_override, list) else (read_json(project_paths(project_dir)["transcript"], []) or [])
    cues: list[dict[str, Any]] = []
    clip_duration = max(0.0, end - start)
    for raw in transcript:
        try:
            seg_start = float(raw.get("start", 0))
            seg_end = float(raw.get("end", 0))
        except Exception:
            continue
        text = _clean_caption_text(raw.get("text"))
        if not text or seg_end <= start or seg_start >= end:
            continue

        raw_words = raw.get("words") if isinstance(raw.get("words"), list) else []
        timed_words: list[dict[str, Any]] = []
        for word in raw_words:
            try:
                word_start = float(word.get("start", seg_start))
                word_end = float(word.get("end", word_start))
            except Exception:
                continue
            word_text = _clean_caption_text(word.get("word", word.get("text", "")))
            if word_text and word_end > start and word_start < end:
                timed_words.append(
                    {
                        "start": max(0.0, word_start - start),
                        "end": min(clip_duration, word_end - start),
                        "word": word_text,
                    }
                )

        if timed_words:
            group: list[dict[str, Any]] = []
            for word in timed_words:
                group.append(word)
                group_duration = float(group[-1]["end"]) - float(group[0]["start"])
                punctuation_break = len(group) >= 2 and bool(_CAPTION_END_RE.search(str(word["word"])))
                if len(group) >= max_words or group_duration >= 2.4 or punctuation_break:
                    cues.append(
                        {
                            "start": float(group[0]["start"]),
                            "end": max(float(group[-1]["end"]), float(group[0]["start"]) + 0.35),
                            "text": " ".join(str(item["word"]) for item in group),
                            "words": [dict(item) for item in group],
                        }
                    )
                    group = []
            if group:
                cues.append(
                    {
                        "start": float(group[0]["start"]),
                        "end": max(float(group[-1]["end"]), float(group[0]["start"]) + 0.35),
                        "text": " ".join(str(item["word"]) for item in group),
                        "words": [dict(item) for item in group],
                    }
                )
            continue

        relative_start = max(0.0, seg_start - start)
        relative_end = min(clip_duration, seg_end - start)
        if relative_end <= relative_start:
            continue
        chunks = _split_caption_text(text, max_words=max_words)
        if not chunks:
            continue
        weights = [max(1, len(chunk.replace(" ", ""))) for chunk in chunks]
        total_weight = max(1, sum(weights))
        cursor = relative_start
        for index, chunk in enumerate(chunks):
            if index == len(chunks) - 1:
                cue_end = relative_end
            else:
                cue_end = cursor + (relative_end - relative_start) * (weights[index] / total_weight)
            cues.append({"start": cursor, "end": max(cursor + 0.35, cue_end), "text": chunk, "words": []})
            cursor = cue_end

    cues.sort(key=lambda cue: (float(cue["start"]), float(cue["end"])))
    for index, cue in enumerate(cues):
        cue["start"] = max(0.0, min(clip_duration, float(cue["start"])))
        cue["end"] = min(clip_duration, max(float(cue["start"]) + 0.25, float(cue["end"])))
        if index + 1 < len(cues):
            next_start = float(cues[index + 1]["start"])
            if cue["end"] > next_start and next_start - cue["start"] >= 0.25:
                cue["end"] = next_start
    return [cue for cue in cues if float(cue["end"]) > float(cue["start"])]


def _write_short_srt(project_dir: Path, start: float, end: float, output: Path, *, max_words: int = 4, transcript_override: list[dict[str, Any]] | None = None) -> int:
    cues = _caption_cues(project_dir, start, end, max_words=max_words, transcript_override=transcript_override)
    lines: list[str] = []
    for index, cue in enumerate(cues, start=1):
        lines.extend(
            [
                str(index),
                f"{srt_time(float(cue['start']))} --> {srt_time(float(cue['end']))}",
                _balanced_caption_lines(str(cue["text"]), ass=False),
                "",
            ]
        )
    if lines:
        output.write_text("\n".join(lines), encoding="utf-8")
    return len(cues)


def _ass_time(seconds: float) -> str:
    value = max(0.0, float(seconds or 0))
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    secs = value % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _ass_escape(text: str) -> str:
    return str(text or "").replace("\r", "").replace("\n", r"\N").replace("{", r"\{").replace("}", r"\}")


def _short_hook_title(item: dict[str, Any]) -> str:
    original = _clean_caption_text(item.get("hook_text") or item.get("title") or "")
    value = original
    if not value or value.lower() in {"short", "shorts", "клип", "момент", "без названия"}:
        return ""
    words = value.split()
    while len(" ".join(words)) > 72 and len(words) > 3:
        words.pop()
    value = " ".join(words)
    if len(value) < 5:
        return ""
    return value + ("…" if value != original else "")


def _karaoke_ass_text(cue: dict[str, Any]) -> str:
    words = cue.get("words") if isinstance(cue.get("words"), list) else []
    if not words:
        return _ass_escape(_balanced_caption_lines(str(cue.get("text") or ""), ass=True))
    raw_words = [str(item.get("word") or "").strip() for item in words if str(item.get("word") or "").strip()]
    if not raw_words:
        return _ass_escape(_balanced_caption_lines(str(cue.get("text") or ""), ass=True))
    split_at = None
    formatted = _balanced_caption_lines(" ".join(raw_words), ass=True)
    if r"\N" in formatted:
        split_at = len(formatted.split(r"\N", 1)[0].split())
    parts: list[str] = []
    cue_end = float(cue.get("end") or 0)
    visible_word_index = 0
    for index, item in enumerate(words):
        word = str(item.get("word") or "").strip()
        if not word:
            continue
        current_start = float(item.get("start") or cue.get("start") or 0)
        next_start = float(words[index + 1].get("start")) if index + 1 < len(words) else cue_end
        centiseconds = max(4, int(round(max(0.04, next_start - current_start) * 100)))
        if split_at is not None and visible_word_index == split_at:
            parts.append(r"\N")
        cue_start = float(cue.get("start") or 0.0)
        pop_start = max(0, int(round((current_start - cue_start) * 1000)))
        pop_peak = pop_start + 85
        pop_end = pop_start + 175
        # Karaoke supplies the colour sweep; a small per-word transform adds a
        # modern 8% pop without moving the whole caption block.
        parts.append(
            rf"{{\k{centiseconds}\fscx100\fscy100\t({pop_start},{pop_peak},\fscx108\fscy108)"
            rf"\t({pop_peak},{pop_end},\fscx100\fscy100)}}{_ass_escape(word)}{{\fscx100\fscy100}}"
        )
        visible_word_index += 1
        if index + 1 < len(words):
            parts.append(" ")
    return "".join(parts)


def _write_short_ass(
    project_dir: Path,
    start: float,
    end: float,
    output: Path,
    *,
    hook_title: str = "",
    max_words: int = 4,
    karaoke_enabled: bool = True,
    transcript_override: list[dict[str, Any]] | None = None,
    caption_font_size: int = 72,
    hook_font_size: int = 82,
    outline_size: int = 5,
) -> int:
    cues = _caption_cues(project_dir, start, end, max_words=max_words, transcript_override=transcript_override)
    caption_font_size = max(48, min(96, int(caption_font_size or 72)))
    hook_font_size = max(caption_font_size, min(108, int(hook_font_size or 82)))
    outline_size = max(3, min(8, int(outline_size or 5)))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,{caption_font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H70000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,82,180,380,1
Style: Karaoke,Arial,{caption_font_size},&H0000D7FF,&H00FFFFFF,&H00000000,&H70000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,2,82,180,380,1
Style: Hook,Arial,{hook_font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H70000000,-1,0,0,0,100,100,0,0,1,{outline_size},0,8,95,210,170,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    clip_duration = max(0.0, end - start)
    if hook_title:
        hook_end = min(2.7, max(1.5, clip_duration))
        hook_text = _ass_escape(_balanced_caption_lines(hook_title, ass=True, max_line_chars=24))
        events.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(hook_end)},Hook,,0,0,0,,{{\\fad(100,180)}}{hook_text}")
    for cue in cues:
        use_karaoke = bool(karaoke_enabled and cue.get("words"))
        style = "Karaoke" if use_karaoke else "Caption"
        text = _karaoke_ass_text(cue) if use_karaoke else _ass_escape(_balanced_caption_lines(str(cue["text"]), ass=True))
        events.append(f"Dialogue: 2,{_ass_time(float(cue['start']))},{_ass_time(float(cue['end']))},{style},,0,0,0,,{{\\fad(45,65)}}{text}")
    if events:
        output.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
    return len(cues)


def _ffmpeg_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return value.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")



def _smooth_face_trajectory(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not points:
        return []
    points = sorted((dict(point) for point in points), key=lambda row: float(row.get("t") or 0.0))
    out: list[dict[str, Any]] = []
    sx = float(points[0].get("cx", 0.5))
    sy = float(points[0].get("cy", 0.5))
    previous_t = float(points[0].get("t") or 0.0)
    for point in points:
        t = float(point.get("t") or 0.0)
        cx = max(0.0, min(1.0, float(point.get("cx", 0.5))))
        cy = max(0.0, min(1.0, float(point.get("cy", 0.5))))
        dt = max(0.1, t - previous_t)
        # Dead-zone prevents caption-sized face jitter. Large changes are
        # clamped to a human-looking pan speed and then exponentially smoothed.
        if abs(cx - sx) < 0.025:
            cx = sx
        max_dx = 0.16 * dt
        cx = max(sx - max_dx, min(sx + max_dx, cx))
        alpha = 0.38 if abs(cx - sx) > 0.08 else 0.26
        sx = sx * (1.0 - alpha) + cx * alpha
        sy = sy * (1.0 - alpha) + cy * alpha
        row = dict(point)
        row.update({"t": round(t, 3), "cx": round(sx, 5), "cy": round(sy, 5)})
        out.append(row)
        previous_t = t
    # Keep the first and last sample. Comparing against a replaced last sample
    # collapses dense input to ONE keyframe. Distribute the bounded keyframes
    # across the entire clip, including 180-second Shorts.
    compact = [out[0]]
    for row in out[1:-1]:
        if float(row["t"]) - float(compact[-1]["t"]) >= 1.8:
            compact.append(row)
    if len(out) > 1 and out[-1]["t"] > compact[-1]["t"]:
        compact.append(out[-1])
    if len(compact) > 36:
        compact = [compact[round(i * (len(compact) - 1) / 35)] for i in range(36)]
    return compact


def _primary_face_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Choose one temporally coherent primary face per sampled frame.

    MediaPipe can return several faces (game character, guest, streamer). Smart
    Face should follow a stable human subject rather than jumping to whichever
    detection happens to be largest on one frame. The rank combines detector
    confidence, visible area and continuity with the previous chosen centre.
    """
    if not points:
        return []
    grouped: dict[float, list[dict[str, Any]]] = {}
    for raw in points:
        if not isinstance(raw, dict):
            continue
        t = round(float(raw.get("t") or 0.0), 3)
        grouped.setdefault(t, []).append(dict(raw))
    chosen: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for t in sorted(grouped):
        best: dict[str, Any] | None = None
        best_rank = -1e9
        for candidate in grouped[t]:
            cx = max(0.0, min(1.0, float(candidate.get("cx") or 0.5)))
            cy = max(0.0, min(1.0, float(candidate.get("cy") or 0.5)))
            area = max(0.0, float(candidate.get("w") or 0.0) * float(candidate.get("h") or 0.0))
            confidence = max(0.0, min(1.0, float(candidate.get("score") or 0.5)))
            area_score = min(1.0, area / 0.09)
            continuity = 0.5
            if previous is not None:
                dx = cx - float(previous.get("cx") or 0.5)
                dy = cy - float(previous.get("cy") or 0.5)
                distance = math.sqrt(dx * dx + dy * dy)
                continuity = max(0.0, 1.0 - distance / 0.42)
            # A slight centre preference helps IRL footage, but continuity has
            # enough weight to keep a moving speaker locked across samples.
            centre = max(0.0, 1.0 - abs(cx - 0.5) / 0.55)
            rank = confidence * 0.42 + area_score * 0.30 + continuity * 0.23 + centre * 0.05
            if rank > best_rank:
                best_rank = rank
                best = candidate
        if best is not None:
            chosen.append(best)
            previous = best
    return chosen


def _facecam_layout(points: list[dict[str, Any]], *, sample_count: int | None = None) -> dict[str, Any] | None:
    """Detect a stable small corner facecam even when other faces are present.

    Earlier V5.1 averaged all detections together, so a game character or guest
    could erase the stable corner signal. We cluster detections by screen corner
    and score temporal coverage/stability independently.
    """
    if len(points) < 5:
        return None
    clusters: dict[str, list[dict[str, Any]]] = {"tl": [], "tr": [], "bl": [], "br": []}
    for point in points:
        if not isinstance(point, dict):
            continue
        cx = float(point.get("cx") or 0.5)
        cy = float(point.get("cy") or 0.5)
        area = max(0.0, float(point.get("w") or 0.0) * float(point.get("h") or 0.0))
        # Facecam face boxes are normally small. Ignore implausibly tiny noise
        # and large IRL/full-screen faces before corner clustering.
        if not (0.0015 <= area <= 0.14):
            continue
        horizontal = "l" if cx < 0.38 else ("r" if cx > 0.62 else "")
        vertical = "t" if cy < 0.44 else ("b" if cy > 0.56 else "")
        key = (vertical + horizontal) if horizontal and vertical else ""
        if key in clusters:
            clusters[key].append(point)

    best_layout: dict[str, Any] | None = None
    best_confidence = -1.0
    for key, cluster in clusters.items():
        unique_times = sorted({round(float(p.get("t") or 0.0), 3) for p in cluster})
        if len(unique_times) < 5:
            continue
        xs = [float(p.get("cx") or 0.5) for p in cluster]
        ys = [float(p.get("cy") or 0.5) for p in cluster]
        areas = [max(0.0, float(p.get("w") or 0.0) * float(p.get("h") or 0.0)) for p in cluster]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        mean_area = sum(areas) / len(areas)
        var_x = sum((x - mean_x) ** 2 for x in xs) / len(xs)
        var_y = sum((y - mean_y) ** 2 for y in ys) / len(ys)
        spread = math.sqrt(var_x + var_y)
        if spread >= 0.075:
            continue
        coverage = min(1.0, len(unique_times) / max(1, int(sample_count or len(unique_times))))
        if sample_count and coverage < 0.20:
            continue
        stability = max(0.0, 1.0 - spread / 0.075)
        small_face_bonus = max(0.0, min(1.0, (0.14 - mean_area) / 0.12))
        confidence = coverage * 0.58 + stability * 0.29 + small_face_bonus * 0.13
        if confidence <= best_confidence:
            continue

        median = sorted(
            cluster,
            key=lambda p: abs(float(p.get("cx") or 0.5) - mean_x) + abs(float(p.get("cy") or 0.5) - mean_y),
        )[0]
        # Expand the face bounding box to include the actual webcam tile/body.
        bw = max(0.18, min(0.46, float(median.get("w") or 0.18) * 2.25))
        bh = max(0.18, min(0.46, float(median.get("h") or 0.18) * 2.45))
        x = max(0.0, min(1.0 - bw, mean_x - bw / 2.0))
        y = max(0.0, min(1.0 - bh, mean_y - bh / 2.0))
        best_layout = {
            "x": round(x, 5), "y": round(y, 5), "w": round(bw, 5), "h": round(bh, 5),
            "position": "top" if mean_y < 0.5 else "bottom",
            "corner": key,
            "confidence": round(min(1.0, confidence), 3),
            "coverage": round(coverage, 3),
        }
        best_confidence = confidence
    return best_layout


def _detect_faces_mediapipe_from_raw(raw_path: Path, *, width: int, height: int, fps: float,
                                     project_dir: Path | None = None) -> list[dict[str, Any]]:
    from .face_tracking import detect_faces
    return detect_faces(raw_path, width=width, height=height, fps=fps,
                        cancelled=(lambda: check_cancel(project_dir)) if project_dir else None)


def _prepare_short_reframe_plan(
    project_dir: Path,
    video: Path,
    start: float,
    end: float,
    mode: str,
    settings: dict[str, Any],
    logger: "JobLogger | None" = None,
    short_index: int = 0,
) -> dict[str, Any]:
    requested = str(mode or "auto").strip().lower().replace("-", "_")
    if requested in {"gameplay+facecam", "gameplay_facecam"}:
        requested = "gameplay_facecam"
    if requested not in {"auto", "smart_face", "gameplay_facecam"}:
        return {"requested_mode": requested, "resolved_mode": requested, "tracking": False}

    import importlib.util
    from importlib.metadata import version, PackageNotFoundError
    from .face_tracking import MODEL_PATH
    mediapipe_available = importlib.util.find_spec("mediapipe") is not None
    try:
        detector_version = version("mediapipe") if mediapipe_available else None
    except PackageNotFoundError:
        detector_version = "unknown"
    fp = stable_hash({
        "version": SHORTS_V5_VERSION,
        "video": video_content_signature(project_dir),
        "start": round(start, 3), "end": round(end, 3), "mode": requested,
        "sample_fps": float(settings.get("shorts_face_sample_fps", 1.0) or 1.0),
        "detector": "mediapipe_tasks_blazeface_v3",
        "detector_version": detector_version,
        "model_present": MODEL_PATH.is_file(),
        # Installing/removing the optional detector must invalidate a previous
        # fallback plan instead of making "MediaPipe missing" sticky forever.
        "mediapipe_available": mediapipe_available,
    })[:24]
    cache_dir = project_dir / "shorts_cache" / "reframe"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{fp}.json"
    raw = cache_dir / f"{fp}.rgb"
    cached = read_json(cache_path, None)
    if isinstance(cached, dict) and cached.get("resolved_mode"):
        return cached

    try:
        if not mediapipe_available:
            raise RuntimeError("MediaPipe not installed")
        duration = max(0.2, end - start)
        sample_fps = max(0.35, min(2.0, float(settings.get("shorts_face_sample_fps", 1.0) or 1.0)))
        # Keep source proportions instead of stretching a portrait into 16:9.
        source_info = video_info(video)
        source_w, source_h = max(1, source_info["width"]), max(1, source_info["height"])
        sample_w = max(2, round(512 * source_w / max(source_w, source_h) / 2) * 2)
        sample_h = max(2, round(512 * source_h / max(source_w, source_h) / 2) * 2)
        ffmpeg = which("ffmpeg") or "ffmpeg"
        rr = run_cmd([
            ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{duration:.3f}",
            "-vf", f"fps={sample_fps:.3f},scale={sample_w}:{sample_h}", "-an", "-sn", "-dn",
            "-pix_fmt", "rgb24", "-f", "rawvideo", str(raw),
        ], project_dir=project_dir, cancel_file=cancel_path(project_dir))
        if rr.returncode != 0:
            raise RuntimeError(rr.stdout[-500:])
        detections = _detect_faces_mediapipe_from_raw(raw, width=sample_w, height=sample_h, fps=sample_fps, project_dir=project_dir)
        raw.unlink(missing_ok=True)
        primary_points = _primary_face_points(detections)
        trajectory = _smooth_face_trajectory(primary_points)
        sample_count = max(1, int(math.ceil(duration * sample_fps)))
        facecam = _facecam_layout(detections, sample_count=sample_count)
        detection_ratio = min(1.0, len({round(float(p.get("t") or 0.0), 3) for p in primary_points}) / sample_count)
        if requested == "gameplay_facecam":
            if facecam:
                resolved = "gameplay_facecam"
                reason = "stable_corner_facecam"
            elif trajectory:
                resolved = "smart_face"
                reason = "facecam_not_stable_using_face_tracking"
            else:
                resolved = "smart_zoom"
                reason = "no_face_fallback"
        elif requested == "smart_face":
            resolved = "smart_face" if trajectory else "smart_zoom"
            reason = "face_tracking" if trajectory else "no_face_fallback"
        else:  # auto
            if facecam and float(facecam.get("coverage") or 0.0) >= 0.25:
                resolved = "gameplay_facecam"
                reason = "auto_gameplay_facecam"
            elif trajectory and detection_ratio >= 0.20:
                resolved = "smart_face"
                reason = "auto_face_tracking"
            else:
                resolved = "smart_zoom"
                reason = "auto_safe_fallback"
        plan = {
            "version": 3, "requested_mode": requested, "resolved_mode": resolved, "tracking": resolved == "smart_face" and len(trajectory) > 1,
            "trajectory": trajectory, "facecam": facecam, "detection_ratio": round(detection_ratio, 3), "reason": reason,
        }
        write_json(cache_path, plan)
        if logger:
            logger.log(f"Short {short_index}: reframe {requested} -> {resolved} ({reason}, face_samples={len(primary_points)}, detections={len(detections)})")
        return plan
    except OperationCancelled:
        raw.unlink(missing_ok=True)
        raise
    except Exception as exc:
        try:
            raw.unlink(missing_ok=True)
        except OSError:
            pass
        plan = {
            "version": 3, "requested_mode": requested, "resolved_mode": "smart_zoom", "tracking": False,
            "trajectory": [], "facecam": None, "reason": "optional_face_tracking_unavailable", "warning": str(exc)[:300],
        }
        # Never persist an operational fallback. A missing package is already
        # represented in the fingerprint, while transient FFmpeg/MediaPipe
        # failures should be retried on the next render instead of becoming a
        # permanent smart_zoom decision.
        if logger:
            logger.log(f"Short {short_index}: Smart Face optional fallback -> smart_zoom ({exc})")
        return plan


def _face_x_expression(points: list[dict[str, Any]]) -> str:
    if not points:
        return "0.5"
    pts = sorted(points, key=lambda row: float(row.get("t") or 0.0))
    if len(pts) > 36:
        pts = [pts[round(i * (len(pts) - 1) / 35)] for i in range(36)]
    if len(pts) == 1:
        return f"{max(0.0,min(1.0,float(pts[0].get('cx', 0.5)))):.5f}"
    tail = f"{max(0.0,min(1.0,float(pts[-1].get('cx', 0.5)))):.5f}"
    for index in range(len(pts) - 2, -1, -1):
        a = pts[index]
        b = pts[index + 1]
        t0 = float(a.get("t") or 0.0)
        t1 = max(t0 + 0.05, float(b.get("t") or t0 + 0.05))
        x0 = max(0.0, min(1.0, float(a.get("cx", 0.5))))
        x1 = max(0.0, min(1.0, float(b.get("cx", 0.5))))
        linear = f"({x0:.5f}+({x1-x0:.5f})*max(0,t-{t0:.3f})/{t1-t0:.3f})"
        tail = f"if(lt(t,{t1:.3f}),{linear},{tail})"
    return tail


def _shorts_filter_complex(
    mode: str,
    subtitle_filename: str | None = None,
    *,
    subtitle_is_ass: bool = False,
    reframe_plan: dict[str, Any] | None = None,
) -> str:
    requested = str(mode or "auto").strip().lower().replace("-", "_")
    if requested in {"full_crop", "full-crop"}:
        requested = "center_crop"
    plan = reframe_plan or {}
    mode = str(plan.get("resolved_mode") or requested).strip().lower().replace("-", "_")

    if mode == "smart_face" and plan.get("trajectory"):
        x_expr = _face_x_expression(list(plan.get("trajectory") or []))
        # Scale to full vertical height and dynamically crop around the smoothed
        # face centre. x is evaluated per frame, so this is real tracking rather
        # than the old static center-crop alias.
        chain = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase[facebase];"
            f"[facebase]crop=1080:1920:x='clip(iw*({x_expr})-540,0,max(0,iw-1080))':y='(ih-1920)/2',setsar=1"
        )
    elif mode == "gameplay_facecam" and isinstance(plan.get("facecam"), dict):
        face = plan["facecam"]
        x = max(0.0, min(0.95, _shorts_num(face.get("x"), 0.0)))
        y = max(0.0, min(0.95, _shorts_num(face.get("y"), 0.0)))
        w = max(0.08, min(1.0 - x, _shorts_num(face.get("w"), 0.30)))
        h = max(0.08, min(1.0 - y, _shorts_num(face.get("h"), 0.30)))
        face_filter = (
            f"[face]crop='max(2,iw*{w:.6f})':'max(2,ih*{h:.6f})':'iw*{x:.6f}':'ih*{y:.6f}',"
            "scale=1080:640:force_original_aspect_ratio=decrease,"
            "pad=1080:640:(ow-iw)/2:(oh-ih)/2:color=black[facev]"
        )
        game_filter = (
            "[game]scale=1080:1280:force_original_aspect_ratio=increase,"
            "crop=1080:1280,setsar=1[gamev]"
        )
        if str(face.get("position") or "bottom") == "top":
            stack = "[facev][gamev]vstack=inputs=2,setsar=1"
        else:
            stack = "[gamev][facev]vstack=inputs=2,setsar=1"
        chain = "[0:v]split=2[game][face];" + game_filter + ";" + face_filter + ";" + stack
    elif mode == "center_crop":
        chain = "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
    elif mode == "fit":
        chain = "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    elif mode == "smart_zoom":
        chain = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2,eq=brightness=-0.22[bgv];"
            "[fg]scale=1280:1920:force_original_aspect_ratio=decrease,"
            "crop='min(iw,1080)':'min(ih,1920)':'max(0,(iw-1080)/2)':'max(0,(ih-1920)/2)'[fgv];"
            "[bgv][fgv]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    else:
        # Full source frame over a blurred fill. This is the safest fallback for
        # explicit blur_background and unknown future modes.
        chain = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2,eq=brightness=-0.18[bgv];"
            "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgv];"
            "[bgv][fgv]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    if subtitle_filename:
        if subtitle_is_ass:
            chain += f",subtitles=filename='{subtitle_filename}'"
        else:
            style = "FontName=Arial,FontSize=72,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=5,Shadow=0,Alignment=2,MarginL=90,MarginR=205,MarginV=380"
            chain += f",subtitles=filename='{subtitle_filename}':force_style='{style}'"
    return chain + "[vout]"


def _validate_short_output(
    output: Path,
    *,
    expected_duration: float,
    vertical: bool,
    source_has_audio: bool,
) -> tuple[bool, list[str], dict[str, Any]]:
    warnings: list[str] = []
    details: dict[str, Any] = {}
    if not output.exists() or output.stat().st_size < 10_000:
        return False, ["output_missing_or_too_small"], details
    actual_duration = video_duration(output)
    info = video_info(output)
    details.update(
        {
            "duration_seconds": round(actual_duration, 3),
            "width": int(info.get("width") or 0),
            "height": int(info.get("height") or 0),
            "audio_streams": len(audio_streams(output)),
        }
    )
    if actual_duration <= 0.5 or abs(actual_duration - expected_duration) > max(1.25, expected_duration * 0.12):
        warnings.append("unexpected_duration")
    if vertical and (details["width"], details["height"]) != (1080, 1920):
        warnings.append("unexpected_resolution")
    if source_has_audio and details["audio_streams"] < 1:
        warnings.append("audio_missing")
    return not warnings, warnings, details


def _tighten_short_bounds_to_speech(
    project_dir: Path,
    start: float,
    end: float,
    *,
    min_seconds: float,
) -> tuple[float, float, bool]:
    """Conservatively remove long silent shoulders without cutting spoken words."""
    transcript = read_json(project_paths(project_dir)["transcript"], []) or []
    spoken: list[tuple[float, float]] = []
    for raw in transcript:
        try:
            seg_start = float(raw.get("start", 0))
            seg_end = float(raw.get("end", 0))
        except Exception:
            continue
        if _clean_caption_text(raw.get("text")) and seg_end > start and seg_start < end:
            spoken.append((seg_start, seg_end))
    if not spoken:
        return start, end, False

    first_speech = max(start, min(item[0] for item in spoken))
    last_speech = min(end, max(item[1] for item in spoken))
    tightened_start = max(start, first_speech - 0.45)
    tightened_end = min(end, last_speech + 0.65)
    if tightened_start - start < 0.9:
        tightened_start = start
    if end - tightened_end < 1.1:
        tightened_end = end
    if tightened_end - tightened_start < max(1.0, min_seconds):
        return start, end, False
    changed = abs(tightened_start - start) > 0.05 or abs(tightened_end - end) > 0.05
    return round(tightened_start, 3), round(tightened_end, 3), changed


def _short_render_fingerprint(
    project_dir: Path, settings: dict[str, Any], item: dict[str, Any], start: float, end: float,
    reframe_plan: dict[str, Any] | None = None,
) -> str:
    return stable_hash(
        {
            "engine": SHORTS_V5_VERSION,
            "reframe_engine": 3,
            "video": video_content_signature(project_dir),
            "transcript": transcript_fingerprint(project_dir, settings),
            "start": round(start, 3),
            "end": round(end, 3),
            "content": {key: item.get(key) for key in (
                "title", "hook", "hook_text", "caption_text", "moment_type", "text_preview", "reframe_mode"
            )},
            "settings": settings_subset(settings, [
                "shorts_vertical_reframe", "shorts_reframe_mode", "shorts_burn_subtitles",
                "shorts_dynamic_captions", "shorts_hook_title_enabled", "shorts_trim_silence",
                "shorts_caption_max_words", "shorts_caption_font_size", "shorts_hook_font_size",
                "shorts_caption_outline", "shorts_render_preset", "shorts_crf", "shorts_normalize_audio",
                "shorts_caption_quality", "shorts_whisper_model", "shorts_recognition_dictionary",
                "shorts_precise_alignment", "language", "shorts_emotion_events_enabled",
                "shorts_sensevoice_model", "shorts_face_sample_fps",
            ]),
            "reframe_plan": {
                "resolved_mode": (reframe_plan or {}).get("resolved_mode"),
                "reason": (reframe_plan or {}).get("reason"),
                "trajectory": (reframe_plan or {}).get("trajectory"),
                "facecam": (reframe_plan or {}).get("facecam"),
            },
        }
    )


def validate_short_editor_bounds(start: float, end: float, settings: dict[str, Any], source_duration: float = 0) -> None:
    """Validate explicit edits without silently clamping or extending footage."""
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise ValueError("Укажите корректные начало и конец Short")
    if end - start < 1:
        raise ValueError("Длительность Short должна быть не меньше 1 секунды")
    configured_max = float(settings.get("shorts_max_seconds", 60) or 60)
    max_seconds = min(180.0, max(1.0, configured_max)) if math.isfinite(configured_max) else 60.0
    if end - start > max_seconds:
        raise ValueError(f"Длительность Short не должна превышать {max_seconds:g} секунд")
    if source_duration > 0 and end > source_duration:
        raise ValueError("Конец Short выходит за длительность исходного видео")


def _render_shorts_candidates_impl(
    project_dir: Path,
    settings: dict[str, Any],
    logger: JobLogger,
    *,
    only_indexes: set[int] | None = None,
) -> dict[str, Any]:
    """Render validated Shorts with safe reframing, optional captions and output checks."""
    settings = dict(settings)
    settings.pop("_shorts_asr_unavailable", None)
    logger.log("Shorts preparation fix 2026-09-07: ограничение ожидания ASR и подробные этапы включены")
    (project_dir / "shorts_caption_warning.json").unlink(missing_ok=True)
    p = project_paths(project_dir)
    video = p["video"]
    if not video.exists():
        raise RuntimeError(f"Исходное видео не найдено: {video}")
    logger.heartbeat("shorts_select", 2, "Shorts: отбор кандидатов", eta_seconds=None)
    if only_indexes is None:
        raw_shorts, selection_rejected = _ensure_shorts_candidates_current(project_dir, settings, logger)
    else:
        # A card index identifies the persisted editor shortlist. Rebuilding or
        # ranking that shortlist here can render a different moment at its path.
        raw_shorts = read_json(p["factory"] / "shorts_candidates.json", []) or []
        selection_rejected = []
    if not raw_shorts:
        raise RuntimeError("Нет качественных Shorts-кандидатов. Анализ завершён, но моменты не прошли Shorts Quality Gate.")

    requested_count = max(1, int(settings.get("shorts_count", 5)))
    logger.heartbeat("shorts_probe", 5, "Shorts: проверка исходного видео и звука", eta_seconds=None)
    source_duration = video_duration(video)
    if only_indexes is None:
        candidates, rejected = normalize_shorts_candidates(
            raw_shorts,
            source_duration,
            limit=requested_count,
            min_seconds=float(settings.get("shorts_min_seconds", 3.0)),
            max_seconds=float(settings.get("shorts_max_seconds", 60.0)),
        )
    else:
        candidates, rejected = [], []
        for index in sorted(only_indexes):
            if index < 1 or index > len(raw_shorts) or not isinstance(raw_shorts[index - 1], dict):
                raise ValueError(f"Shorts-кандидат {index} не найден")
            item = dict(raw_shorts[index - 1])
            start, end = float(item["start"]), float(item["end"])
            validate_short_editor_bounds(start, end, settings, source_duration)
            candidates.append({**item, "start": start, "end": end, "source_index": index})
    rejected = list(selection_rejected or []) + list(rejected or [])
    if not candidates:
        write_json(project_dir / "shorts_render_manifest.json", {"rendered": [], "rejected": rejected})
        raise RuntimeError("Все Shorts-кандидаты имеют некорректные или дублирующиеся границы.")

    out_dir = p["outputs"] / "shorts"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = project_dir / "shorts_render_manifest.json"
    previous_manifest = read_json(manifest_path, {}) or {}
    previous_by_index = {
        int(item.get("index")): item
        for item in previous_manifest.get("rendered", [])
        if isinstance(item, dict) and str(item.get("index", "")).isdigit()
    }
    selected_indexes = {int(item["source_index"]) for item in candidates}

    ffmpeg = which("ffmpeg") or "ffmpeg"
    source_has_audio = bool(audio_streams(video))
    vertical = bool(settings.get("shorts_vertical_reframe", True))
    reframe_mode = str(settings.get("shorts_reframe_mode", "auto")) if vertical else "horizontal"
    burn_subtitles = bool(settings.get("shorts_burn_subtitles", True))
    normalize_audio = bool(settings.get("shorts_normalize_audio", True)) and source_has_audio
    dynamic_captions = bool(settings.get("shorts_dynamic_captions", True))
    hook_title_enabled = bool(settings.get("shorts_hook_title_enabled", True))
    trim_silence = bool(settings.get("shorts_trim_silence", True))
    caption_max_words = max(3, min(8, int(settings.get("shorts_caption_max_words", 4))))
    caption_font_size = max(48, min(96, int(settings.get("shorts_caption_font_size", 72) or 72)))
    hook_font_size = max(caption_font_size, min(108, int(settings.get("shorts_hook_font_size", 82) or 82)))
    caption_outline = max(3, min(8, int(settings.get("shorts_caption_outline", 5) or 5)))
    render_preset = str(settings.get("shorts_render_preset", "veryfast"))
    shorts_crf = max(15, min(35, int(settings.get("shorts_crf", 22))))
    short_encoder_settings = dict(settings)
    short_encoder_settings.update({"render_preset": render_preset, "crf": shorts_crf})
    short_enc_args, short_encoder = video_encode_args(ffmpeg, short_encoder_settings, logger)
    rendered: list[dict[str, Any]] = [
        item for index, item in previous_by_index.items()
        if only_indexes is not None and index not in selected_indexes and (project_dir / str(item.get("path") or "")).exists()
    ]
    failed: list[dict[str, Any]] = []
    successful_indexes: set[int] = set()
    limit = len(candidates)

    def preserve_previous_output(
        index: int,
        item: dict[str, Any],
        output: Path,
        previous: dict[str, Any],
        expected_duration: float,
    ) -> dict[str, Any] | None:
        """Return a complete manifest row for a known-good canonical output.

        A recovered project can contain the MP4 while its old manifest is absent
        or damaged. Keeping only ``{"stale_preserved": true}`` made completion
        bookkeeping lose the output path and produced a corrupt manifest.
        """
        valid, warnings, details = _validate_short_output(
            output,
            expected_duration=expected_duration,
            vertical=vertical,
            source_has_audio=source_has_audio,
        )
        if not valid:
            logger.log(f"Short {index}: предыдущий canonical output тоже не прошёл проверку: {warnings}")
            return None
        relative = output.relative_to(project_dir).as_posix()
        row = dict(previous)
        row.update({
            "index": index,
            "path": relative,
            "url": f"/api/projects/{project_dir.name}/file/{relative}",
            "size_mb": round(output.stat().st_size / 1024 / 1024, 2),
            "title": row.get("title") or item.get("title") or f"Short {index}",
            "start": row.get("start", item.get("start")),
            "end": row.get("end", item.get("end")),
            "duration_seconds": row.get("duration_seconds", details.get("duration_seconds")),
            "width": row.get("width", details.get("width")),
            "height": row.get("height", details.get("height")),
            "stale_preserved": True,
        })
        return row

    for ordinal, item in enumerate(candidates, start=1):
        i = int(item["source_index"])
        check_cancel(project_dir)
        start = float(item["start"])
        end = float(item["end"])
        bounds_trimmed = False
        if trim_silence and only_indexes is None and not item.get("bounds_edited"):
            start, end, bounds_trimmed = _tighten_short_bounds_to_speech(
                project_dir,
                start,
                end,
                min_seconds=float(settings.get("shorts_min_seconds", 3.0)),
            )
        duration = end - start
        check_cancel(project_dir)
        logger.heartbeat("shorts_reframe", 10 + 80 * (ordinal - 1) / limit,
                         f"Shorts {ordinal}/{limit} (№{i}): подготовка кадра", eta_seconds=None)
        item_reframe_mode = str(item.get("reframe_mode") or reframe_mode)
        reframe_plan = (
            _prepare_short_reframe_plan(project_dir, video, start, end, item_reframe_mode, settings, logger, i)
            if vertical else {"requested_mode": "horizontal", "resolved_mode": "horizontal", "tracking": False}
        )
        out = out_dir / f"short_{i:02d}.mp4"
        fingerprint = _short_render_fingerprint(project_dir, settings, item, start, end, reframe_plan)
        previous = previous_by_index.get(i) or {}
        if previous.get("fingerprint") == fingerprint and out.exists() and out.stat().st_size > 1024:
            rendered.append({**previous, "reused": True})
            successful_indexes.add(i)
            logger.set_step("shorts_render", ordinal, limit, None, f"Shorts {ordinal}/{limit}: кеш актуален", global_start=10, global_end=90)
            continue
        pending_out = out_dir / f"short_{i:02d}.{fingerprint[:10]}.pending.mp4"
        pending_out.unlink(missing_ok=True)
        sidecar_srt = out_dir / f"short_{i:02d}.srt"
        sidecar_ass = out_dir / f"short_{i:02d}.ass"
        temp_srt = project_dir / f".short_{i:02d}.srt"
        temp_ass = project_dir / f".short_{i:02d}.ass"
        # An interrupted render may leave captions from the previous edit.
        temp_srt.unlink(missing_ok=True)
        temp_ass.unlink(missing_ok=True)
        hook_title = _short_hook_title(item) if hook_title_enabled else ""
        subtitle_count = 0
        manual_caption_present = item.get("caption_text") is not None
        if burn_subtitles and manual_caption_present:
            # A manual edit must be immediate and local: never rerun Whisper/LLM
            # just because the user fixed one word in one Short.
            timing_rows = _shorts_transcript_rows(project_dir, start, end)
            caption_transcript = _manual_caption_transcript(
                str(item.get("caption_text") or ""), start=start, end=end, timing_rows=timing_rows
            ) or []
            logger.log(f"Short {i}: применяю ручной текст субтитров без повторного ASR")
        else:
            if burn_subtitles:
                logger.heartbeat("shorts_captions", 10 + 80 * (ordinal - 1) / limit,
                                 f"Shorts {ordinal}/{limit}: подготовка субтитров (до 5 минут на уточнение)", eta_seconds=None)
            caption_transcript = _shorts_refined_transcript_bounded(project_dir, video, start, end, settings, logger, i) if burn_subtitles else None
        if burn_subtitles:
            subtitle_count = _write_short_srt(project_dir, start, end, temp_srt, max_words=caption_max_words, transcript_override=caption_transcript)
            if dynamic_captions or hook_title:
                _write_short_ass(
                    project_dir,
                    start,
                    end,
                    temp_ass,
                    hook_title=hook_title,
                    max_words=caption_max_words,
                    karaoke_enabled=dynamic_captions,
                    transcript_override=caption_transcript,
                    caption_font_size=caption_font_size,
                    hook_font_size=hook_font_size,
                    outline_size=caption_outline,
                )

        logger.set_step(
            "shorts_render",
            ordinal - 1,
            limit,
            None,
            f"Рендер Shorts {ordinal}/{limit} (№{i}): FFmpeg старт",
            global_start=10,
            global_end=90,
        )

        def build_cmd(subtitle_path: Path | None = None, *, subtitle_is_ass: bool = False, force_software: bool = False) -> list[str]:
            cmd = [ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{duration:.3f}"]
            if vertical:
                subtitle_name = _ffmpeg_filter_path(subtitle_path) if subtitle_path and subtitle_path.exists() else None
                cmd += [
                    "-filter_complex",
                    _shorts_filter_complex(item_reframe_mode, subtitle_name, subtitle_is_ass=subtitle_is_ass, reframe_plan=reframe_plan),
                    "-map",
                    "[vout]",
                ]
            else:
                vf = "scale=-2:720"
                if subtitle_path and subtitle_path.exists():
                    vf += f",subtitles=filename='{_ffmpeg_filter_path(subtitle_path)}'"
                cmd += ["-vf", vf, "-map", "0:v:0"]
            cmd += ["-map", "0:a:0?", "-sn", "-dn"]
            if normalize_audio:
                cmd += ["-af", "loudnorm=I=-14:LRA=11:TP=-1.5"]
            if force_software:
                sw_settings = dict(short_encoder_settings)
                sw_settings["video_encoder"] = "libx264"
                active_enc_args, _ = video_encode_args(ffmpeg, sw_settings, logger)
            else:
                active_enc_args = short_enc_args
            cmd += active_enc_args + [
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-write_tmcd",
                "0",
                "-movflags",
                "+faststart",
                str(pending_out),
            ]
            return cmd

        ass_ready = bool(burn_subtitles and temp_ass.exists())
        srt_ready = bool(burn_subtitles and subtitle_count and temp_srt.exists())
        selected_subtitle = temp_ass if ass_ready else (temp_srt if srt_ready else None)
        selected_is_ass = bool(ass_ready)
        actual_short_encoder = short_encoder
        result = run_cmd(
            build_cmd(selected_subtitle, subtitle_is_ass=selected_is_ass),
            project_dir=project_dir,
            cancel_file=cancel_path(project_dir),
        )
        subtitle_burned = selected_subtitle is not None
        subtitle_format = "ass" if selected_is_ass else ("srt" if selected_subtitle else "none")
        if result.returncode != 0 and ass_ready and srt_ready:
            logger.log(f"Short {i}: динамические ASS-субтитры недоступны, повторяю с SRT: {result.stdout[-500:]}")
            result = run_cmd(build_cmd(temp_srt, subtitle_is_ass=False), project_dir=project_dir, cancel_file=cancel_path(project_dir))
            subtitle_burned = result.returncode == 0
            subtitle_format = "srt" if subtitle_burned else "none"
        if result.returncode != 0 and (ass_ready or srt_ready):
            logger.log(f"Short {i}: burn-in субтитров недоступен, повторяю без вшивания: {result.stdout[-500:]}")
            subtitle_burned = False
            subtitle_format = "none"
            result = run_cmd(build_cmd(None), project_dir=project_dir, cancel_file=cancel_path(project_dir))
        if result.returncode != 0 and short_encoder != "libx264":
            logger.log(f"Short {i}: hardware encoder {short_encoder} не запустился в реальном filter graph; повторяю через libx264.")
            fallback_subtitle = selected_subtitle if subtitle_burned else None
            result = run_cmd(
                build_cmd(fallback_subtitle, subtitle_is_ass=selected_is_ass and subtitle_burned, force_software=True),
                project_dir=project_dir,
                cancel_file=cancel_path(project_dir),
            )
            if result.returncode == 0:
                actual_short_encoder = "libx264"
        if result.returncode != 0:
            failed.append({"index": i, "title": item.get("title"), "reason": "ffmpeg_failed", "details": result.stdout[-800:]})
            logger.log(f"Short {i} failed: {result.stdout[-800:]}")
            pending_out.unlink(missing_ok=True)
            if out.exists():
                preserved = preserve_previous_output(i, item, out, previous, duration)
                failed[-1]["stale_preserved"] = preserved is not None
                if preserved is not None:
                    rendered.append(preserved)
            temp_srt.unlink(missing_ok=True)
            temp_ass.unlink(missing_ok=True)
            continue

        valid, validation_warnings, details = _validate_short_output(
            pending_out,
            expected_duration=duration,
            vertical=vertical,
            source_has_audio=source_has_audio,
        )
        if not valid:
            failed.append(
                {
                    "index": i,
                    "title": item.get("title"),
                    "reason": "output_validation_failed",
                    "warnings": validation_warnings,
                    "details": details,
                }
            )
            logger.log(f"Short {i} rejected after validation: {validation_warnings}")
            pending_out.unlink(missing_ok=True)
            if out.exists():
                preserved = preserve_previous_output(i, item, out, previous, duration)
                failed[-1]["stale_preserved"] = preserved is not None
                if preserved is not None:
                    rendered.append(preserved)
            temp_srt.unlink(missing_ok=True)
            temp_ass.unlink(missing_ok=True)
            continue

        replace_file_atomic(pending_out, out)
        successful_indexes.add(i)
        if subtitle_count and temp_srt.exists():
            pending_srt = sidecar_srt.with_suffix(".pending.srt")
            shutil.copy2(temp_srt, pending_srt)
            replace_file_atomic(pending_srt, sidecar_srt)
        else:
            sidecar_srt.unlink(missing_ok=True)
        if temp_ass.exists():
            pending_ass = sidecar_ass.with_suffix(".pending.ass")
            shutil.copy2(temp_ass, pending_ass)
            replace_file_atomic(pending_ass, sidecar_ass)
        else:
            sidecar_ass.unlink(missing_ok=True)
        temp_srt.unlink(missing_ok=True)
        temp_ass.unlink(missing_ok=True)

        logger.set_step(
            "shorts_render",
            ordinal,
            limit,
            None,
            f"Рендер Shorts {ordinal}/{limit} (№{i}): готово",
            global_start=10,
            global_end=90,
        )
        rendered.append(
            {
                "index": i,
                "path": out.relative_to(project_dir).as_posix(),
                "url": f"/api/projects/{project_dir.name}/file/{out.relative_to(project_dir).as_posix()}",
                "size_mb": round(out.stat().st_size / 1024 / 1024, 2),
                "title": item.get("title", f"Short {i}"),
                "start": start,
                "end": end,
                "duration_seconds": details.get("duration_seconds"),
                "width": details.get("width"),
                "height": details.get("height"),
                "vertical_reframe": vertical,
                "reframe_mode": item_reframe_mode,
                "reframe_report": {key: reframe_plan.get(key) for key in (
                    "requested_mode", "resolved_mode", "tracking", "reason", "detection_ratio", "warning")},
                "subtitle_cues": subtitle_count,
                "subtitle_burned": subtitle_burned,
                "subtitle_format": subtitle_format,
                "dynamic_captions": dynamic_captions,
                "encoder": actual_short_encoder,
                "hook_title": hook_title,
                "bounds_trimmed": bounds_trimmed,
                "audio_normalized": normalize_audio,
                "fingerprint": fingerprint,
                "reused": False,
            }
        )

    rendered.sort(key=lambda row: int(row.get("index") or 0))
    if only_indexes is None:
        valid_names = {Path(str(item.get("path") or "")).name for item in rendered}
        for stale in list(out_dir.glob("short_*.mp4")) + list(out_dir.glob("short_*.srt")) + list(out_dir.glob("short_*.ass")):
            canonical_mp4 = stale.with_suffix(".mp4").name
            if canonical_mp4 not in valid_names and ".pending." not in stale.name:
                try:
                    stale.unlink()
                except OSError as exc:
                    logger.log(f"Не удалось удалить устаревший Shorts-файл {stale.name}: {exc}")

    manifest = {
        "rendered": rendered,
        "rejected": rejected,
        "failed": failed,
        "requested_count": requested_count,
        "source_duration_seconds": round(source_duration, 3),
        "partial_regeneration": sorted(selected_indexes) if only_indexes is not None else [],
        "caption_warning": read_json(project_dir / "shorts_caption_warning.json", None),
        "settings": {
            "vertical": vertical,
            "reframe_mode": reframe_mode,
            "burn_subtitles": burn_subtitles,
            "dynamic_captions": dynamic_captions,
            "hook_title_enabled": hook_title_enabled,
            "trim_silence": trim_silence,
            "caption_max_words": caption_max_words,
            "caption_font_size": caption_font_size,
            "caption_quality": str(settings.get("shorts_caption_quality", "high")),
            "normalize_audio": normalize_audio,
            "engine_version": SHORTS_V5_VERSION,
            "max_seconds": float(settings.get("shorts_max_seconds", 60.0)),
        },
        "created_at": time.time(),
    }
    write_json(project_dir / "shorts_render_manifest.json", manifest)
    with project_metadata_lock(project_dir):
        candidate_path = p["factory"] / "shorts_candidates.json"
        current = read_json(candidate_path, []) or []
        if isinstance(current, list):
            for index in selected_indexes:
                # Never clear a newer edit if a caller changed metadata outside
                # the API lifecycle admission while FFmpeg was running.
                if index <= len(current) and current[index - 1] == raw_shorts[index - 1]:
                    current[index - 1]["render_dirty"] = index not in successful_indexes
            write_json(candidate_path, current)
            factory_path = p["factory"] / "content_factory_manifest.json"
            factory = read_json(factory_path, {}) or {}
            factory = factory if isinstance(factory, dict) else {}
            factory["shorts"] = current
            write_json(factory_path, factory)
    if not rendered:
        raise RuntimeError(f"Shorts export не создал ни одного корректного клипа. Ошибок: {len(failed)}")
    if failed:
        logger.set_status("error", 100,
                          f"Не удалось пересобрать {len(failed)} ролик(ов). Предыдущие готовые файлы сохранены; проверь журнал и повтори сборку.")
        return {**manifest, "outputs": list_output_files(project_dir)}
    mark_shorts_complete(project_dir, settings, [str(item.get("path") or "") for item in rendered])
    caption_notice = " · Субтитры из основной расшифровки: уточняющая модель недоступна" if manifest.get("caption_warning") else ""
    logger.set_status("done", 100, f"Shorts export готов: {len(rendered)} клипов{caption_notice}")
    return {**manifest, "outputs": list_output_files(project_dir)}


def render_shorts_candidates(
    project_dir: Path,
    settings: dict[str, Any],
    logger: JobLogger,
    *,
    only_indexes: set[int] | None = None,
) -> dict[str, Any]:
    logger.checkpoint("shorts_render", state="running", message="Ожидание общего GPU-ресурса")
    with AIResourceManager.lease(
        "gpu_heavy",
        capacity=max(1, int(settings.get("gpu_job_limit", 1) or 1)),
        cancel_check=lambda: is_cancelled(project_dir),
    ):
        result = _render_shorts_candidates_impl(
            project_dir, settings, logger, only_indexes=only_indexes
        )
    logger.checkpoint(
        "shorts_render", state="failed" if result.get("failed") else "completed",
        artifacts=[str(item.get("path") or "") for item in result.get("rendered", [])],
    )
    return result


def content_factory(project_dir: Path, settings: dict[str, Any]) -> dict[str, Any]:
    p = project_paths(project_dir)
    segs = read_json(p["segments"], [])
    factory = p["factory"]
    factory.mkdir(exist_ok=True)

    minutes = []
    for x in str(settings.get("batch_export_minutes", "10,30,60")).split(","):
        try:
            minutes.append(float(x.strip()))
        except Exception:
            pass
    if not minutes:
        minutes = [10, 30, 60]

    outputs = []
    hook = None

    for m in minutes:
        target = m * 60
        chosen = []
        total = 0
        if hook:
            chosen.append(hook)
            total += float(hook["end"]) - float(hook["start"])
        for s in sorted(segs, key=lambda x: x["start"]):
            if hook and s["id"] == hook["id"]:
                continue
            if total >= target:
                break
            d = float(s["end"]) - float(s["start"])
            if total + d <= target or target - total > 20:
                chosen.append(s)
                total += d
        path = factory / f"montage_{int(m)}min_segments.json"
        write_json(path, chosen)
        outputs.append({"minutes": m, "segments": len(chosen), "duration": tc(total), "json": str(path)})

    shorts_data, shorts_rejected = build_shorts_quality_candidates(project_dir, settings)
    write_json(factory / "shorts_candidates.json", shorts_data)

    manifest = {"outputs": outputs, "shorts": shorts_data, "shorts_rejected": shorts_rejected, "hook": hook}
    write_json(factory / "content_factory_manifest.json", manifest)
    return manifest


def create_hls_proxy_preview(project_dir: Path, settings: dict[str, Any], logger: JobLogger) -> dict[str, Any]:
    """Create HLS/proxy preview using verified NVENC when available."""
    p = project_paths(project_dir)
    out_dir = project_dir / "preview" / "hls"
    out_dir.mkdir(parents=True, exist_ok=True)
    playlist = out_dir / "index.m3u8"
    if playlist.exists():
        return {
            "ok": True,
            "playlist": playlist.relative_to(project_dir).as_posix(),
            "url": f"/api/projects/{project_dir.name}/file/{playlist.relative_to(project_dir).as_posix()}",
        }
    ffmpeg = which("ffmpeg") or "ffmpeg"
    proxy_settings = dict(settings)
    proxy_settings.update({"render_preset": "ultrafast", "crf": 32})
    enc_args, used_encoder = video_encode_args(ffmpeg, proxy_settings, logger)
    logger.heartbeat("hls_preview", 3, f"Создаю HLS/proxy preview 480p · {used_encoder}")

    def build(active_args: list[str]) -> list[str]:
        return [
            ffmpeg, "-y", "-i", str(p["video"]), "-vf", "scale=-2:480",
        ] + active_args + [
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
            "-f", "hls", "-hls_time", "6", "-hls_playlist_type", "vod", str(playlist),
        ]

    r = run_cmd(build(enc_args), project_dir=project_dir, cancel_file=cancel_path(project_dir))
    if (r.returncode != 0 or not playlist.exists()) and used_encoder != "libx264":
        logger.log(f"HLS preview: {used_encoder} не запустился, повторяю через libx264.")
        sw = dict(proxy_settings)
        sw["video_encoder"] = "libx264"
        sw_args, _ = video_encode_args(ffmpeg, sw, logger)
        r = run_cmd(build(sw_args), project_dir=project_dir, cancel_file=cancel_path(project_dir))
        used_encoder = "libx264"
    if r.returncode != 0 or not playlist.exists():
        raise RuntimeError("HLS preview failed:\n" + (r.stdout or ""))
    data = {
        "ok": True,
        "playlist": playlist.relative_to(project_dir).as_posix(),
        "url": f"/api/projects/{project_dir.name}/file/{playlist.relative_to(project_dir).as_posix()}",
        "folder": str(out_dir),
        "encoder": used_encoder,
    }
    write_json(project_dir / "hls_preview.json", data)
    logger.set_status("done", 100, "HLS/proxy preview готов")
    return data


def export_edit_timelines(project_dir: Path, settings: dict[str, Any]) -> dict[str, Any]:
    """Export timeline for Premiere/DaVinci: EDL + simple FCPXML."""
    p = project_paths(project_dir)
    segs = read_json(p["segments"], [])
    if not segs:
        raise RuntimeError("Нет сегментов для экспорта timeline")
    out_dir = p["outputs"] / "timeline_exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    info = video_info(p["video"])
    fps = max(1, int(round(float(info.get("fps", 25.0) or 25.0))))
    width = int(info.get("width") or 1920)
    height = int(info.get("height") or 1080)

    def frames(sec: float) -> int:
        return max(0, int(round(float(sec) * fps)))

    def tc_frames(sec: float) -> str:
        total = frames(sec)
        hh = total // (3600 * fps)
        total %= 3600 * fps
        mm = total // (60 * fps)
        total %= 60 * fps
        ss = total // fps
        ff = total % fps
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    edl = ["TITLE: Highlight Studio Export", "FCM: NON-DROP FRAME", ""]
    rec_cursor = 0.0
    for i, s in enumerate(segs, start=1):
        src_in = float(s.get("start", 0))
        src_out = float(s.get("end", src_in + 1))
        dur = max(0.1, src_out - src_in)
        rec_in = rec_cursor
        rec_out = rec_cursor + dur
        edl.append(f"{i:03d}  AX       V     C        {tc_frames(src_in)} {tc_frames(src_out)} {tc_frames(rec_in)} {tc_frames(rec_out)}")
        edl.append(f"* FROM CLIP NAME: {p['video'].name}")
        edl.append(f"* COMMENT: {s.get('title', '')}")
        edl.append("")
        rec_cursor = rec_out
    edl_path = out_dir / "highlight_timeline.edl"
    edl_path.write_text("\n".join(edl), encoding="utf-8")

    asset_path = str(p["video"].resolve()).replace("&", "&amp;")
    resources = f'<resources><format id="r1" name="FFVideoFormat{width}x{height}p{fps}" frameDuration="1/{fps}s" width="{width}" height="{height}"/><asset id="r2" name="{p["video"].name}" src="file://{asset_path}" hasVideo="1" hasAudio="1"/></resources>'
    spine = []
    offset_frames = 0
    for s in segs:
        src_frames = frames(float(s.get("start", 0)))
        dur_frames = max(1, frames(float(s.get("end", 0)) - float(s.get("start", 0))))
        title = str(s.get("title", "clip")).replace("&", "&amp;").replace('"', "&quot;")
        spine.append(
            f'<asset-clip name="{title}" ref="r2" offset="{offset_frames}/{fps}s" start="{src_frames}/{fps}s" duration="{dur_frames}/{fps}s"/>'
        )
        offset_frames += dur_frames
    fcpxml = f'<?xml version="1.0" encoding="UTF-8"?><fcpxml version="1.10">{resources}<library><event name="Highlight Studio"><project name="AI Highlight"><sequence format="r1"><spine>{"".join(spine)}</spine></sequence></project></event></library></fcpxml>'
    fcpxml_path = out_dir / "highlight_timeline.fcpxml"
    fcpxml_path.write_text(fcpxml, encoding="utf-8")

    result = {
        "ok": True,
        "source_fps": fps,
        "source_width": width,
        "source_height": height,
        "exports": [
            {
                "type": "edl",
                "path": edl_path.relative_to(project_dir).as_posix(),
                "url": f"/api/projects/{project_dir.name}/file/{edl_path.relative_to(project_dir).as_posix()}",
            },
            {
                "type": "fcpxml",
                "path": fcpxml_path.relative_to(project_dir).as_posix(),
                "url": f"/api/projects/{project_dir.name}/file/{fcpxml_path.relative_to(project_dir).as_posix()}",
            },
        ],
    }
    write_json(project_dir / "timeline_export_report.json", result)
    return result


def generate_thumbnail_ideas(project_dir: Path, settings: dict[str, Any], logger: JobLogger | None = None) -> dict[str, Any]:
    p = project_paths(project_dir)
    segs = read_json(p["segments"], [])
    candidates = read_json(p["candidates"], [])
    items = sorted(segs or candidates, key=lambda x: float(x.get("score", 0) or 0), reverse=True)[:10]
    out_dir = p["outputs"] / "thumbnails"
    out_dir.mkdir(parents=True, exist_ok=True)
    ideas = []
    for i, s in enumerate(items, start=1):
        t = (float(s.get("start", 0)) + float(s.get("end", 0))) / 2
        frame = out_dir / f"thumb_idea_{i:02d}.jpg"
        ok = frame.exists() and frame.stat().st_size > 0
        if not ok:
            ok = extract_frame(p["video"], t, frame, width=1280)
        title = str(s.get("title") or f"Идея {i}")
        hook = str(s.get("hook_potential", "medium"))
        ideas.append(
            {
                "index": i,
                "time": round(t, 2),
                "title": title,
                "thumbnail_text": title[:42],
                "hook_potential": hook,
                "path": frame.relative_to(project_dir).as_posix() if ok else "",
                "url": f"/api/projects/{project_dir.name}/file/{frame.relative_to(project_dir).as_posix()}" if ok else "",
                "reason": s.get("reason", ""),
            }
        )
    result = {"ok": True, "ideas": ideas, "note": "Выбери кадр с эмоцией/конфликтом, добавь 2-4 слова крупным текстом."}
    write_json(project_dir / "thumbnail_ideas.json", result)
    if logger:
        logger.log(f"Thumbnail ideas: {len(ideas)} кадров.")
    return result


def build_compare_mode(project_dir: Path, settings: dict[str, Any]) -> dict[str, Any]:
    p = project_paths(project_dir)
    candidates = read_json(p["candidates"], [])
    if not candidates:
        raise RuntimeError("Нет кандидатов для Compare mode")
    target_sec = float(settings.get("target_minutes", 30) or 30) * 60
    max_items = int(settings.get("max_final_segments", 100) or 100)

    def pick(items: list[dict[str, Any]], dense: bool) -> list[dict[str, Any]]:
        chosen = []
        total = 0.0
        if dense:
            ordered = sorted(
                items,
                key=lambda x: (float(x.get("score", 0) or 0), -max(1, float(x.get("end", 0)) - float(x.get("start", 0)))),
                reverse=True,
            )
        else:
            ordered = sorted(items, key=lambda x: (x.get("story_role") == "hook", float(x.get("start", 0))))
        for c in ordered:
            dur = max(0.0, float(c.get("end", 0)) - float(c.get("start", 0)))
            if dur <= 0 or len(chosen) >= max_items:
                continue
            if dense and dur > 90:
                continue
            if any(
                overlaps(float(c.get("start", 0)), float(c.get("end", 0)), float(x.get("start", 0)), float(x.get("end", 0))) for x in chosen
            ):
                continue
            chosen.append(c)
            total += dur
            if total >= target_sec:
                break
        return sorted(chosen, key=lambda x: x.get("start", 0))

    dense = pick(candidates, True)
    story = pick(candidates, False)
    story.sort(key=lambda item: float(item.get("start", 0)))
    out_dir = p["factory"]
    out_dir.mkdir(exist_ok=True)
    a_path = out_dir / "compare_A_dense_segments.json"
    b_path = out_dir / "compare_B_story_segments.json"
    write_json(a_path, dense)
    write_json(b_path, story)
    result = {
        "ok": True,
        "A_dense": {"segments": len(dense), "json": str(a_path)},
        "B_story": {"segments": len(story), "json": str(b_path)},
        "note": "A = плотная версия, B = история. Обе идут по времени исходника.",
    }
    write_json(project_dir / "compare_mode.json", result)
    return result
