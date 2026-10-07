from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from .artifacts import ARTIFACTS, resolve_project_source, source_file_signature, source_readiness
from .utils import read_json, write_json

REVISION_SCHEMA_VERSION = 1
# Settings that change the semantic analysis result.  Device/precision choices
# such as whisper_device/whisper_compute are intentionally NOT part of this
# identity: Auto hardware resolution may switch CPU -> CUDA (or int8 ->
# int8_float32) at job start without changing the user's analysis request.
# Including those execution-only values caused a completed v10.15.15 analysis
# to become immediately "stale", hiding candidates/segments and locking Review.
ANALYSIS_SETTING_KEYS = (
    # Source-to-text / AI generation
    "ai_engine", "text_model", "vision_model", "prompt", "ollama_num_ctx",
    "ai_batch_size", "ai_retry_count", "ai_strict_mode", "full_ai_coverage",
    "whisper_model", "language", "chunk_seconds",
    # Candidate generation / selection
    "content_type", "edit_mode", "block_seconds", "micro_batch_size", "micro_cut_enabled",
    "micro_window_seconds", "micro_min_seconds", "micro_max_seconds", "micro_speech_gap_seconds",
    "micro_source_duration_multiplier", "top_blocks_for_micro",
    "target_minutes", "auto_target_duration", "min_final_segments", "max_final_segments",
    "refill_after_dedup_enabled", "target_fill_ratio", "dedup_enabled", "storyline_enabled",
    "hook_first_enabled", "hook_min_score", "strict_quality_mode", "strict_quality_min_score",
    "strict_quality_min_confidence", "quality_recovery_score_relaxation",
    # Visual / OCR / audio features that can alter candidate scores and selection
    "visual_mode", "visual_scan_enabled", "visual_scan_interval_seconds", "visual_scan_max_samples",
    "ocr_enabled", "ocr_languages", "ocr_every_n_visual_samples", "ocr_roi_enabled",
    "ocr_roi_x", "ocr_roi_y", "ocr_roi_w", "ocr_roi_h", "ocr_upscale",
    "audio_dynamics_enabled", "audio_events_enabled",
    # High-level profile/preset selectors
    "analysis_profile", "task_preset",
)


# Exact v10.15.15 analysis identity, retained only for safe migration of projects
# that were completed with runtime-resolved Whisper hardware fields.
LEGACY_ANALYSIS_SETTING_KEYS_V101515 = (
    "ai_engine", "text_model", "vision_model", "prompt", "ollama_num_ctx",
    "ai_batch_size", "ai_retry_count", "ai_strict_mode", "full_ai_coverage",
    "whisper_model", "whisper_device", "whisper_compute", "language", "chunk_seconds",
    "content_type", "edit_mode", "block_seconds", "micro_batch_size", "micro_cut_enabled",
    "micro_window_seconds", "micro_min_seconds", "micro_max_seconds", "top_blocks_for_micro",
    "target_minutes", "auto_target_duration", "min_final_segments", "max_final_segments",
    "refill_after_dedup_enabled", "target_fill_ratio", "dedup_enabled", "storyline_enabled",
    "hook_first_enabled", "hook_min_score", "strict_quality_mode", "strict_quality_min_score",
    "strict_quality_min_confidence",
    "visual_mode", "visual_scan_enabled", "visual_scan_interval_seconds", "visual_scan_max_samples",
    "ocr_enabled", "ocr_languages", "ocr_every_n_visual_samples", "ocr_roi_enabled",
    "ocr_roi_x", "ocr_roi_y", "ocr_roi_w", "ocr_roi_h", "ocr_upscale",
    "audio_dynamics_enabled", "audio_events_enabled",
    "analysis_profile", "task_preset",
)

RENDER_SETTING_KEYS = ("render_preset","render_crf","crf","render_fps","output_width","output_height","normalize_audio","target_lufs","make_srt",
                       "burn_subtitles","video_codec","video_encoder","audio_codec","audio_bitrate","render_hwaccel","render_container")
SHORTS_SETTING_KEYS = ("shorts_count","shorts_vertical_reframe","shorts_reframe_mode","shorts_min_seconds","shorts_max_seconds","shorts_crf","shorts_width","shorts_height","shorts_fps",
                       "shorts_burn_subtitles","shorts_dynamic_captions","shorts_normalize_audio","shorts_trim_silence","shorts_hook_title_enabled",
                       "shorts_caption_max_words","shorts_caption_quality","shorts_caption_font_size","shorts_hook_font_size","shorts_caption_outline",
                       "shorts_precise_alignment","shorts_whisper_model","shorts_recognition_dictionary","shorts_funny_search_enabled",
                       "shorts_emotion_events_enabled","shorts_sensevoice_model","shorts_emotion_top_n","shorts_face_sample_fps",
                       "shorts_llm_rerank_enabled","shorts_llm_rerank_top","shorts_candidate_pool_limit","shorts_min_quality_score",
                       "shorts_render_preset")

def _canonicalize(value: Any) -> Any:
    """Normalize semantically equivalent persisted values before hashing.

    JSON round-trips can turn an integer setting into a float (for example
    ``30`` -> ``30.0`` through Pydantic validation).  Revision identity must
    reflect the setting's meaning, not its incidental Python numeric type.
    """
    if isinstance(value, dict):
        return {str(k): _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    if isinstance(value, float):
        if value == 0:
            return 0
        if value.is_integer():
            return int(value)
    return value

def _hash(payload: Any) -> str:
    canonical = _canonicalize(payload)
    return hashlib.sha256(json.dumps(canonical,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def _project(d: Path)->dict[str,Any]: return read_json(d/ARTIFACTS["project"],{}) or {}
def source_revision(d: Path)->str:
    p=_project(d)
    raw=resolve_project_source(d)
    tw=p.get("twitch") or {}
    sig=source_file_signature(raw)
    content={"size":sig.get("size"),"partial_sha256":sig.get("partial_sha256"),"signature_version":sig.get("signature_version"),"exists":sig.get("exists")}
    return _hash({"source_type":p.get("source_type") or "local","source_url":p.get("source_url") or tw.get("url"),"vod_id":tw.get("vod_id"),
                  "start_seconds":tw.get("start_seconds"),"end_seconds":tw.get("end_seconds"),"range_section":tw.get("range_section"),"file":content})
def analysis_revision(d: Path, s: dict[str, Any], *, source_rev: str | None = None) -> str:
    return _hash({"source_revision": source_rev or source_revision(d), "settings": {k: s.get(k) for k in ANALYSIS_SETTING_KEYS}})
def normalized_segment_items(items: Any)->list[dict[str,Any]]:
    out=[]
    for x in items or []:
        if isinstance(x,dict):
            out.append({"candidate_id":x.get("candidate_id",x.get("id")),"start":x.get("start"),"end":x.get("end"),"title":x.get("title"),"decision":x.get("decision"),"best_moment":x.get("best_moment"),"story_role":x.get("story_role"),"shorts_candidate":x.get("shorts_candidate")})
    return out
def normalized_segments(d:Path)->list[dict[str,Any]]:
    return normalized_segment_items(read_json(d/ARTIFACTS["segments"],[]) or [])
def segments_revision_from_items(items: Any, s: dict[str, Any], *, analysis_rev: str, source_rev: str) -> str:
    # Hash the immutable snapshot actually consumed by a render job instead of
    # re-reading segments.json later, after a user may have edited the montage.
    return _hash({"analysis_revision": analysis_rev, "segments": normalized_segment_items(items)})
def segments_revision(d: Path, s: dict[str, Any] | None = None, *, analysis_rev: str | None = None, source_rev: str | None = None) -> str:
    settings = s or (_project(d).get("settings") or {})
    ar = analysis_rev or analysis_revision(d, settings, source_rev=source_rev)
    return _hash({"analysis_revision": ar, "segments": normalized_segments(d)})

def render_revision(d: Path, s: dict[str, Any], *, source_rev: str | None = None, segments_rev: str | None = None, analysis_rev: str | None = None) -> str:
    src = source_rev or source_revision(d)
    seg = segments_rev or segments_revision(d, s, analysis_rev=analysis_rev, source_rev=src)
    return _hash({"source_revision": src, "segments_revision": seg, "settings": {k: s.get(k) for k in RENDER_SETTING_KEYS}})

def shorts_revision(d: Path, s: dict[str, Any], *, source_rev: str | None = None, segments_rev: str | None = None, analysis_rev: str | None = None) -> str:
    src = source_rev or source_revision(d)
    seg = segments_rev or segments_revision(d, s, analysis_rev=analysis_rev, source_rev=src)
    return _hash({"source_revision": src, "segments_revision": seg, "settings": {k: s.get(k) for k in SHORTS_SETTING_KEYS}})
def read_revision_state(d:Path)->dict[str,Any]:
    st=read_json(d/ARTIFACTS["revision_state"],{}) or {}
    st=st if isinstance(st,dict) else {}
    st.setdefault("schema_version",REVISION_SCHEMA_VERSION)
    return st
def _save(d:Path,st:dict[str,Any])->dict[str,Any]:
    st["schema_version"]=REVISION_SCHEMA_VERSION
    st["updated_at"]=time.time()
    write_json(d/ARTIFACTS["revision_state"],st)
    return st
def ensure_revision_state(d:Path,s:dict[str,Any])->dict[str,Any]:
    st=read_revision_state(d)
    if not (d/ARTIFACTS["revision_state"]).exists():
        st.update({"migrated_legacy":True,"source_revision":source_revision(d),"candidates_analysis_revision":None,"segments_analysis_revision":None,"segments_revision":None,"rendered_revision":None,"shorts_revision":None})
        _save(d,st)
    return st
def mark_source_changed(d:Path,s:dict[str,Any])->dict[str,Any]:
    st=ensure_revision_state(d,s)
    rev=source_revision(d)
    if st.get("source_revision")!=rev:
        st.update({"source_revision":rev,"source_changed_at":time.time(),"candidates_analysis_revision":None,"segments_analysis_revision":None,"segments_revision":None,"rendered_revision":None,"shorts_revision":None})
    return _save(d,st)
def mark_analysis_complete(d:Path,s:dict[str,Any])->dict[str,Any]:
    st=ensure_revision_state(d,s)
    ar=analysis_revision(d,s)
    st.update({"source_revision":source_revision(d),"candidates_analysis_revision":ar,"segments_analysis_revision":ar,"segments_revision":segments_revision(d,s),"analysis_completed_at":time.time(),"rendered_revision":None,"shorts_revision":None})
    return _save(d,st)
def mark_segments_updated(
    d: Path,
    s: dict[str, Any],
    *,
    source_rev: str | None = None,
    analysis_rev: str | None = None,
    segments_rev: str | None = None,
) -> dict[str, Any]:
    """Record a timeline mutation, optionally reusing a verified revision context.

    Review Studio already sends the exact segments revision it loaded.  Once
    that revision has matched the persisted state, callers may pass the source
    and analysis revisions from the same state.  This avoids repeatedly
    sampling a multi-gigabyte VOD for one atomic ``add clip`` operation while
    preserving the normal deep verification path for every other caller.
    """
    st = ensure_revision_state(d, s)
    src = source_rev or source_revision(d)
    ar = analysis_rev or analysis_revision(d, s, source_rev=src)
    st["segments_revision"] = segments_rev or segments_revision(d, s, analysis_rev=ar, source_rev=src)
    if st.get("candidates_analysis_revision")==ar:
        st["segments_analysis_revision"]=ar
    st.update({"segments_updated_at":time.time(),"rendered_revision":None,"shorts_revision":None})
    return _save(d,st)
def mark_render_complete(
    d:Path,s:dict[str,Any],out:Path,check:dict[str,Any],*,
    render_rev: str | None = None, segments_rev: str | None = None, source_rev: str | None = None
)->dict[str,Any]:
    if not check.get("ok"):
        raise RuntimeError("Финальный файл не прошёл post-render validation.")
    src = source_rev or source_revision(d)
    seg = segments_rev or segments_revision(d,s,source_rev=src)
    rr = render_rev or render_revision(d,s,source_rev=src,segments_rev=seg)
    st=ensure_revision_state(d,s)
    st.update({"rendered_revision":rr,"rendered_at":time.time(),"render_output":str(out)})
    write_json(d/ARTIFACTS["render_manifest"],{"revision":rr,"segments_revision":seg,"source_revision":src,"output":str(out),"output_signature":source_file_signature(out),"result_check":check,"created_at":time.time()})
    return _save(d,st)
def mark_render_failed(d:Path,s:dict[str,Any],error:str)->dict[str,Any]:
    st=ensure_revision_state(d,s)
    st.update({"last_render_failed_at":time.time(),"last_render_error":str(error)[:1000]})
    return _save(d,st)
def mark_shorts_complete(d:Path,s:dict[str,Any],outputs:list[str])->dict[str,Any]:
    st=ensure_revision_state(d,s)
    sr=shorts_revision(d,s)
    st["shorts_revision"]=sr
    write_json(d/ARTIFACTS["shorts_manifest"],{"revision":sr,"outputs":outputs,"created_at":time.time()})
    return _save(d,st)

def _legacy_runtime_analysis_revision_v101515(d: Path, s: dict[str, Any], *, source_rev: str) -> str | None:
    """Rebuild the buggy v10.15.15 runtime hash when it can be proven exactly.

    v10.15.15 passed runtime_optimized_settings() into mark_analysis_complete().
    On Auto hardware this replaced persisted Whisper CPU/int8 with CUDA-specific
    execution values.  Later freshness checks used project.json and therefore
    produced a different hash.  hardware_runtime.json records the exact effective
    values, which lets us recognize only that known drift without accepting a
    genuinely changed prompt/model/preset.
    """
    runtime = read_json(d / "hardware_runtime.json", {}) or {}
    effective = runtime.get("effective_settings") if isinstance(runtime, dict) else None
    if not isinstance(effective, dict) or not effective:
        return None
    legacy = dict(s)
    for key in LEGACY_ANALYSIS_SETTING_KEYS_V101515:
        if key in effective:
            legacy[key] = effective.get(key)
    return _hash({
        "source_revision": source_rev,
        "settings": {k: legacy.get(k) for k in LEGACY_ANALYSIS_SETTING_KEYS_V101515},
    })

def _repair_v101515_runtime_revision_drift(
    d: Path,
    s: dict[str, Any],
    st: dict[str, Any],
    *,
    source_rev: str,
    analysis_rev: str,
) -> dict[str, Any]:
    """Migrate only the exact v10.15.15 Auto-hardware false-stale state."""
    stored = st.get("candidates_analysis_revision")
    if not stored or stored == analysis_rev:
        return st
    legacy_runtime_rev = _legacy_runtime_analysis_revision_v101515(d, s, source_rev=source_rev)
    if not legacy_runtime_rev or stored != legacy_runtime_rev:
        return st

    # Require a terminal analysis marker and the saved candidate artifact.  This
    # prevents a partial/incomplete job from being promoted by migration.
    status = read_json(d / "status.json", {}) or {}
    if not bool(status.get("analysis_complete")) or not (d / ARTIFACTS["candidates"]).exists():
        return st

    old_segments_match = st.get("segments_analysis_revision") == legacy_runtime_rev
    st["candidates_analysis_revision"] = analysis_rev
    if old_segments_match:
        st["segments_analysis_revision"] = analysis_rev
        st["segments_revision"] = segments_revision(d, s, analysis_rev=analysis_rev, source_rev=source_rev)
    st["runtime_revision_migrated_from"] = "v10.15.15"
    st["runtime_revision_migrated_at"] = time.time()
    return _save(d, st)

def freshness_report(d: Path, s: dict[str, Any]) -> dict[str, Any]:
    st = ensure_revision_state(d, s)
    ready = source_readiness(d)
    # Source signatures can read a few MiB from a very large local/external VOD.
    # Calculate the source generation exactly once per freshness snapshot, then
    # thread it through downstream revision hashes instead of re-reading the VOD.
    src = source_revision(d)
    ar = analysis_revision(d, s, source_rev=src)
    st = _repair_v101515_runtime_revision_drift(d, s, st, source_rev=src, analysis_rev=ar)
    sr = segments_revision(d, s, analysis_rev=ar, source_rev=src)
    rr = render_revision(d, s, source_rev=src, segments_rev=sr, analysis_rev=ar)
    cp = d / ARTIFACTS["candidates"]
    sp = d / ARTIFACTS["segments"]
    fp = d / ARTIFACTS["final_render"]
    ce = cp.exists()
    se = sp.exists() and bool(read_json(sp, []) or [])
    cc = bool(ce and st.get("candidates_analysis_revision") == ar)
    sc = bool(se and st.get("segments_analysis_revision") == ar and st.get("segments_revision") == sr)
    manifest = read_json(d / ARTIFACTS["render_manifest"], {}) or {}
    rc = bool(fp.exists() and sc and st.get("rendered_revision") == rr and manifest.get("revision") == rr)
    return {"source_ready": ready["ok"], "source": ready, "source_revision": src, "analysis_revision": ar, "analysis_current": cc, "candidates_current": cc, "segments_current": sc, "segments_revision": sr, "render_current": rc, "render_revision": rr, "rendered_revision": st.get("rendered_revision"), "analysis_stale": bool(ce and not cc), "segments_stale": bool(se and not sc), "render_stale": bool(fp.exists() and not rc), "legacy_migration": bool(st.get("migrated_legacy"))}

def output_is_publishable(d: Path, s: dict[str, Any], relpath: str, *, freshness: dict[str, Any] | None = None) -> tuple[bool, str]:
    rel = relpath.replace("\\", "/").lstrip("/")
    fresh = freshness if freshness is not None else freshness_report(d, s)
    if rel in {ARTIFACTS["final_render"],"highlight_final.mp4"}:
        return bool(fresh["render_current"]), "" if fresh["render_current"] else "Монтаж или настройки изменены после последнего рендера. Выполните новый рендер перед публикацией."
    if "/short" in rel.lower() or rel.lower().startswith("short"):
        st=read_revision_state(d)
        cur=shorts_revision(d,s)
        mf=read_json(d/ARTIFACTS["shorts_manifest"],{}) or {}
        ok=st.get("shorts_revision")==cur and mf.get("revision")==cur
        return bool(ok), "" if ok else "Shorts устарели относительно текущего монтажа/настроек. Выполните экспорт Shorts заново."
    # Content Factory videos are derivative deliverables. They are publishable
    # only while the canonical montage generation they were built from remains
    # current. Unknown video files are not silently trusted.
    factory_manifest = read_json(d / "factory_render_manifest.json", {}) or {}
    rendered = factory_manifest.get("rendered") if isinstance(factory_manifest, dict) else []
    if isinstance(rendered, list):
        entry = next((x for x in rendered if isinstance(x, dict) and str(x.get("path") or "").replace("\\", "/").lstrip("/") == rel), None)
        if entry is not None:
            ok = bool(
                fresh.get("segments_current")
                and str(factory_manifest.get("base_segments_revision") or "") == str(fresh.get("segments_revision") or "")
                and (d / rel).exists()
            )
            return ok, "" if ok else "Версия Content Factory устарела относительно текущего монтажа. Пересоберите эту версию перед публикацией."
    if rel.lower().endswith((".mp4", ".mkv", ".mov", ".webm")):
        return False, "Видео не связано с подтверждённой текущей revision проекта. Пересоберите output перед публикацией."
    return True,""
