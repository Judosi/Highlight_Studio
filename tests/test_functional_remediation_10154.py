from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from highlight_studio.core.artifacts import ARTIFACTS, RECOMPUTABLE_CACHE_PATHS, source_file_signature, source_readiness, validate_media_file
from highlight_studio.core.revisions import (
    analysis_revision,
    freshness_report,
    mark_analysis_complete,
    mark_render_complete,
    mark_segments_updated,
    output_is_publishable,
)
from highlight_studio.core.utils import OperationCancelled, read_json, run_cmd, write_json
from highlight_studio.integrations.ai import client as ai_client
from highlight_studio.integrations.ai.ollama import OllamaClient
from highlight_studio.integrations.twitch import source as twitch_source
from highlight_studio.integrations.youtube import publisher as youtube
from highlight_studio.services import pipeline


def auth_headers() -> dict[str, str]:
    return {"X-Local-Token": main.LOCAL_AUTH_TOKEN}


def make_project(root: Path, pid: str = "remediation", *, with_source: bool = True) -> Path:
    d = root / pid
    d.mkdir(parents=True, exist_ok=True)
    settings = main.default_settings()
    project = {"id": pid, "name": pid, "settings": settings}
    if with_source:
        src = d / "input.mp4"
        src.write_bytes(b"synthetic-source" * 100)
        project["source_video_path"] = str(src)
    write_json(d / "project.json", project)
    return d


def seed_analysis(d: Path) -> dict:
    p = read_json(d / "project.json", {})
    settings = main.validate_settings(p.get("settings", {}))
    write_json(d / "candidates.json", [{"id": 10, "candidate_id": "cand-10", "start": 0, "end": 20, "score": 9, "decision": "keep", "title": "A"}])
    write_json(d / "segments.json", [{"id": 1, "candidate_id": "cand-10", "start": 0, "end": 20, "score": 9, "decision": "keep", "title": "A"}])
    mark_analysis_complete(d, settings)
    return settings


# HS-032 + a remediation-discovered regression: semantically identical persisted
# numeric settings must not change generation identity just because int -> float.
def test_revision_identity_survives_json_pydantic_numeric_roundtrip(tmp_path):
    d = make_project(tmp_path)
    raw = read_json(d / "project.json", {})["settings"]
    before = analysis_revision(d, raw)
    after = analysis_revision(d, main.load_project(d)["settings"])
    assert before == after


def test_hs032_analysis_becomes_stale_after_sensitive_setting_change(tmp_path):
    d = make_project(tmp_path)
    settings = seed_analysis(d)
    assert freshness_report(d, settings)["candidates_current"] is True
    changed = dict(settings)
    changed["text_model"] = str(settings.get("text_model") or "model") + "-changed"
    assert freshness_report(d, changed)["candidates_current"] is False
    assert freshness_report(d, changed)["segments_current"] is False


def test_hs032_selection_settings_also_invalidate_analysis_generation(tmp_path):
    d = make_project(tmp_path)
    settings = seed_analysis(d)
    assert freshness_report(d, settings)["candidates_current"] is True
    changed = dict(settings)
    changed["min_final_segments"] = int(settings.get("min_final_segments") or 8) + 1
    assert freshness_report(d, changed)["candidates_current"] is False
    changed = dict(settings)
    changed["visual_mode"] = "Полный" if settings.get("visual_mode") != "Полный" else "Лёгкий"
    assert freshness_report(d, changed)["candidates_current"] is False


def test_hs031_render_becomes_stale_after_segment_mutation_and_cannot_publish(tmp_path):
    d = make_project(tmp_path)
    settings = seed_analysis(d)
    out = d / ARTIFACTS["final_render"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"render-A" * 300)
    mark_render_complete(d, settings, out, {"ok": True, "duration_seconds": 20})
    assert freshness_report(d, settings)["render_current"] is True
    segs = read_json(d / "segments.json", [])
    segs[0]["end"] = 18
    write_json(d / "segments.json", segs)
    mark_segments_updated(d, settings)
    fresh = freshness_report(d, settings)
    assert fresh["render_current"] is False and fresh["render_stale"] is True
    ok, reason = output_is_publishable(d, settings, ARTIFACTS["final_render"])
    assert ok is False and "рендер" in reason.lower()


def test_hs001_twitch_current_attempt_cannot_select_larger_stale_parent_file(tmp_path):
    cache = tmp_path / "twitch_cache"
    stale = cache / "old_finished.mp4"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x" * 5000)
    attempt = cache / "attempts" / "run-current"
    attempt.mkdir(parents=True)
    current = attempt / "current.mp4"
    current.write_bytes(b"y" * 2000)
    assert twitch_source._find_downloaded_video(attempt) == current.resolve()


def test_hs003_transcript_chunk_generation_changes_with_source_or_settings(tmp_path):
    d = make_project(tmp_path)
    settings = main.load_project(d)["settings"]
    fp_a = pipeline.transcript_fingerprint(d, settings)
    chunks_a = d / "transcript_chunks" / fp_a
    chunks_a.mkdir(parents=True)
    write_json(chunks_a / "transcript_0001.json", [{"start": 0, "end": 1, "text": "old"}])
    changed = dict(settings)
    changed["whisper_model"] = "medium"
    fp_b = pipeline.transcript_fingerprint(d, changed)
    assert fp_a != fp_b
    assert (d / "transcript_chunks" / fp_b / "transcript_0001.json").exists() is False


def test_hs027_candidate_identity_survives_overlap_and_refill_can_use_unused_ids(tmp_path):
    logger = pipeline.JobLogger(tmp_path)
    chosen = [
        pipeline.Candidate(10, 0, 10, 9, "A", "", candidate_id="stable-10"),
        pipeline.Candidate(20, 20, 30, 9, "B", "", candidate_id="stable-20"),
    ]
    unused = [
        pipeline.Candidate(1, 40, 50, 8, "C", "", candidate_id="stable-1"),
        pipeline.Candidate(2, 60, 70, 8, "D", "", candidate_id="stable-2"),
    ]
    resolved = pipeline.resolve_segment_overlaps(chosen, 100, {"micro_min_seconds": 3}, logger)
    assert [c.candidate_id for c in resolved] == ["stable-10", "stable-20"]
    assert [c.id for c in resolved] == [10, 20]
    refilled = pipeline.refill_after_dedup(tmp_path, resolved + unused, resolved, 60, {"target_fill_ratio": 0.8, "min_final_segments": 4, "max_final_segments": 10, "micro_cut_enabled": False}, logger)
    ids = {c.candidate_id for c in refilled}
    assert {"stable-1", "stable-2"}.issubset(ids)


def test_hs004_post_render_validation_failure_never_marks_done(tmp_path, monkeypatch):
    d = make_project(tmp_path)
    settings = seed_analysis(d)
    monkeypatch.setattr(pipeline, "video_duration", lambda path: 100.0)
    monkeypatch.setattr(pipeline, "video_encode_args", lambda *a, **k: (["-c:v", "libx264"], "libx264"))

    def fake_run(cmd, **kwargs):
        target = Path(str(cmd[-1]))
        if target.suffix == ".mp4":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"z" * 4096)
        return subprocess.CompletedProcess(cmd, 0, "", None)

    monkeypatch.setattr(pipeline, "run_cmd", fake_run)
    monkeypatch.setattr(pipeline, "result_check", lambda *a, **k: {"ok": False, "errors": ["invalid stream"]})
    # Unit fixture uses synthetic bytes; isolate the new part check so this
    # regression continues to exercise final-output validation specifically.
    monkeypatch.setattr(pipeline, "validate_media_file", lambda *a, **k: {"ok": True, "duration_seconds": 20.0})
    logger = pipeline.JobLogger(d)
    with pytest.raises(RuntimeError, match="post-render validation"):
        pipeline.render(d, settings, logger)
    assert read_json(d / "status.json", {}).get("state") != "done"
    assert freshness_report(d, settings)["render_current"] is False


def test_hs004_failed_rerender_does_not_overwrite_last_validated_final(tmp_path, monkeypatch):
    d = make_project(tmp_path)
    settings = seed_analysis(d)
    final = d / ARTIFACTS["final_render"]
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"VALID-OLD" * 700)
    mark_render_complete(d, settings, final, {"ok": True, "duration_seconds": 20})
    old_bytes = final.read_bytes()
    monkeypatch.setattr(pipeline, "video_duration", lambda path: 100.0)
    monkeypatch.setattr(pipeline, "video_encode_args", lambda *a, **k: (["-c:v", "libx264"], "libx264"))
    def fake_run(cmd, **kwargs):
        target = Path(str(cmd[-1]))
        if target.suffix == ".mp4":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"INVALID-NEW" * 600)
        return subprocess.CompletedProcess(cmd, 0, "", None)
    monkeypatch.setattr(pipeline, "run_cmd", fake_run)
    monkeypatch.setattr(pipeline, "result_check", lambda *a, **k: {"ok": False, "errors": ["invalid stream"]})
    with pytest.raises(RuntimeError):
        pipeline.render(d, settings, pipeline.JobLogger(d))
    assert final.read_bytes() == old_bytes
    assert not (final.parent / "highlight_final.rendering.mp4").exists()



def test_hs039_six_hour_visual_plan_covers_start_middle_and_end():
    total, interval, stamps = pipeline.visual_sample_plan(6 * 3600, 5, 1200)
    assert total == 1200
    assert stamps[0] == 0
    assert stamps[-1] == pytest.approx(6 * 3600)
    assert min(abs(t - 3 * 3600) for t in stamps) <= interval


def test_hs039_short_visual_plan_keeps_requested_interval():
    total, interval, stamps = pipeline.visual_sample_plan(60, 5, 1200)
    assert total == 12 and interval == 5
    assert stamps[-1] == 55


def test_hs030_external_fast_import_missing_is_not_ready(tmp_path):
    d = make_project(tmp_path, with_source=False)
    external = tmp_path / "external.mp4"
    external.write_bytes(b"x" * 2048)
    p = read_json(d / "project.json", {})
    p["source_video_path"] = str(external)
    p["storage_mode"] = "reference"
    write_json(d / "project.json", p)
    assert source_readiness(d)["ready"] is True
    external.unlink()
    assert source_readiness(d)["ready"] is False


def test_hs005_electron_release_identity_matches_canonical_release():
    root = Path(__file__).resolve().parents[1]
    release = json.loads((root / "release_identity.json").read_text(encoding="utf-8"))
    package = json.loads((root / "desktop/electron/package.json").read_text(encoding="utf-8"))
    main_js = (root / "desktop/electron/main.js").read_text(encoding="utf-8")
    assert package["version"] == release["version"]
    assert "release_identity.json" in main_js
    assert release["version"] == "11.2.7"


def test_hs006_project_id_validation_is_canonical_for_legacy_ids():
    assert main.PROJECT_ID_RE.fullmatch("abcdef123456")
    assert main.PROJECT_ID_RE.fullmatch("legacy_project-2026")
    assert not main.PROJECT_ID_RE.fullmatch("../escape")
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "PROJECT_ID_PATTERN" in source


def test_hs006_legacy_project_id_rbac_supports_viewer_editor_owner(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.src.highlight_studio.infrastructure.database.models import Base, User
    from backend.src.highlight_studio.infrastructure.auth.service import (
        add_or_update_member, ensure_project, has_project_permission, register_user,
    )
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'rbac.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    owner = register_user(session, "owner@example.com", "Owner", "very-secure-passphrase")
    viewer = User(id="viewer", email="viewer@example.com", display_name="Viewer", password_hash=owner.password_hash, global_role="user")
    editor = User(id="editor", email="editor@example.com", display_name="Editor", password_hash=owner.password_hash, global_role="user")
    session.add_all([viewer, editor])
    session.flush()
    pid = "legacy_project-2026"
    ensure_project(session, pid, "Legacy", owner, str(tmp_path / pid))
    add_or_update_member(session, owner, pid, viewer.email, "viewer")
    add_or_update_member(session, owner, pid, editor.email, "editor")
    session.commit()
    assert has_project_permission(session, viewer, pid, "viewer")
    assert not has_project_permission(session, viewer, pid, "editor")
    assert has_project_permission(session, editor, pid, "editor")
    assert not has_project_permission(session, editor, pid, "owner")
    assert has_project_permission(session, owner, pid, "owner")


def test_hs037_web_ollama_url_is_server_managed(monkeypatch):
    monkeypatch.setattr(ai_client, "WEB_ACCOUNTS_ENABLED", True)
    monkeypatch.setattr(ai_client, "SERVER_OLLAMA_URL", "http://approved-ollama:11434")
    assert ai_client.effective_ollama_url({"ollama_url": "http://169.254.169.254/latest/meta-data"}) == "http://approved-ollama:11434"


def test_hs018_youtube_restart_reuses_completed_existing_session(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"v" * 4096)
    project = tmp_path / "project"
    project.mkdir()
    item = {"file_path": "video.mp4", "title": "Test", "privacy_status": "private"}
    key = youtube._upload_intent_key(video, item)
    write_json(project / ARTIFACTS["youtube_upload_state"], {"schema_version": 1, "intents": {key: {"status": "finalizing", "session_url": "https://session/1", "offset": video.stat().st_size}}})
    monkeypatch.setattr(youtube, "_query_upload_offset", lambda *a, **k: (video.stat().st_size, {"id": "already-created"}, False))
    monkeypatch.setattr(youtube, "_init_resumable_upload", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not create duplicate session")))
    result = youtube._upload_one("token", video, item, project_dir=project)
    assert result["video_id"] == "already-created"



def test_hs018_expired_finalized_session_never_auto_restarts(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"v" * 4096)
    project = tmp_path / "project"
    project.mkdir()
    item = {"file_path": "video.mp4", "title": "Test", "privacy_status": "private"}
    key = youtube._upload_intent_key(video, item)
    write_json(project / ARTIFACTS["youtube_upload_state"], {"schema_version": 1, "intents": {key: {
        "status": "finalizing", "session_url": "https://session/expired", "offset": video.stat().st_size
    }}})
    monkeypatch.setattr(youtube, "_query_upload_offset", lambda *a, **k: (0, None, True))
    monkeypatch.setattr(youtube, "_init_resumable_upload", lambda *a, **k: (_ for _ in ()).throw(AssertionError("duplicate session must not start")))
    with pytest.raises(youtube.YouTubeIntegrationError, match="дубликат"):
        youtube._upload_one("token", video, item, project_dir=project)
    state = read_json(project / ARTIFACTS["youtube_upload_state"], {})
    assert state["intents"][key]["status"] == "ambiguous"


def test_hs007_run_cmd_cancel_raises_operation_cancelled(tmp_path):
    cancel = tmp_path / "cancel.flag"
    cancel.write_text("1")
    with pytest.raises(OperationCancelled):
        run_cmd([sys.executable, "-c", "import time; time.sleep(2)"], timeout=5, project_dir=tmp_path, cancel_file=cancel)


def test_hs008_twitch_user_cancel_does_not_fallback(tmp_path, monkeypatch):
    d = make_project(tmp_path, with_source=False)
    p = read_json(d / "project.json", {})
    p["source_type"] = "twitch"
    p["source_url"] = "https://www.twitch.tv/videos/123456789"
    p["twitch"] = {"url": p["source_url"], "source_kind": "vod", "download_engine": "twitchdownloadercli", "fallback_enabled": True}
    write_json(d / "project.json", p)
    called = {"fallback": 0}
    monkeypatch.setattr(twitch_source, "_prepare_tdcli_vod", lambda *a, **k: (_ for _ in ()).throw(OperationCancelled("cancel")))
    monkeypatch.setattr(twitch_source, "_prepare_ytdlp_vod", lambda *a, **k: called.__setitem__("fallback", called["fallback"] + 1))
    with pytest.raises(OperationCancelled):
        twitch_source.prepare_twitch_source(d, p["settings"], pipeline.JobLogger(d))
    assert called["fallback"] == 0


def test_hs010_clear_project_cache_removes_authoritative_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "cache")
    for name in RECOMPUTABLE_CACHE_PATHS:
        path = d / name
        if Path(name).suffix or name.endswith(".wav"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"cache")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "x").write_text("cache")
    client = TestClient(main.app)
    r = client.post("/api/projects/cache/clear-cache", headers=auth_headers())
    assert r.status_code == 200
    assert all(not (d / name).exists() for name in RECOMPUTABLE_CACHE_PATHS)


def test_hs011_incompatible_cache_is_not_restamped_before_recompute(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "compat")
    write_json(d / "cache_manifest.json", {"fingerprint": "definitely-old"})
    (d / "ai_batches").mkdir()
    (d / "ai_batches" / "x.json").write_text("{}")
    client = TestClient(main.app)
    r = client.post("/api/projects/compat/clear-incompatible-cache", headers=auth_headers())
    assert r.status_code == 200
    assert not (d / "cache_manifest.json").exists()


def test_hs012_ocr_preprocess_key_changes_with_roi_or_upscale(tmp_path, monkeypatch):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame" * 100)
    project = tmp_path / "project"
    project.mkdir()
    def fake_run(cmd, **kwargs):
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"processed")
        return subprocess.CompletedProcess(cmd, 0, "", None)
    monkeypatch.setattr(pipeline, "run_cmd", fake_run)
    a = pipeline._prepare_ocr_frame(frame, project, {"ocr_roi_enabled": True, "ocr_roi_x": 0.0, "ocr_roi_y": 0.0, "ocr_roi_w": 1.0, "ocr_roi_h": 1.0, "ocr_upscale": 2})
    b = pipeline._prepare_ocr_frame(frame, project, {"ocr_roi_enabled": True, "ocr_roi_x": 0.1, "ocr_roi_y": 0.0, "ocr_roi_w": 0.9, "ocr_roi_h": 1.0, "ocr_upscale": 3})
    assert a != b


def test_hs013_visual_disabled_never_reuses_old_enabled_report(tmp_path):
    d = make_project(tmp_path)
    write_json(d / ARTIFACTS["visual"], {"enabled": True, "fingerprint": "old", "samples": [{"time": 1}]})
    data = pipeline.visual_scan_video(d, {"visual_scan_enabled": False}, pipeline.JobLogger(d))
    assert data["enabled"] is False and data["samples"] == []
    assert read_json(d / ARTIFACTS["visual"], {})["enabled"] is False


def test_hs014_scene_ffmpeg_failure_is_not_cached_as_success(tmp_path, monkeypatch):
    d = make_project(tmp_path)
    monkeypatch.setattr(pipeline, "run_cmd", lambda *a, **k: subprocess.CompletedProcess([], 1, "ffmpeg failed", None))
    with pytest.raises(RuntimeError, match="Scene detection FFmpeg failed"):
        pipeline.scene_detection(d, pipeline.JobLogger(d))
    assert not (d / ARTIFACTS["scene_manifest"]).exists()


def test_hs015_review_action_uses_central_segment_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "review")
    seed_analysis(d)
    monkeypatch.setattr(sys.modules[main.update_segments.__module__], "video_duration", lambda path: 120.0)
    client = TestClient(main.app)
    r = client.post("/api/projects/review/review-action", json={"action": "keep", "item": {"id": 99, "candidate_id": "bad", "start": 50, "end": 10, "score": 9}}, headers=auth_headers())
    assert r.status_code == 200
    assert all(float(x["end"]) > float(x["start"]) for x in r.json()["segments"])


def test_hs016_render_endpoint_has_authoritative_hard_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "nogate", with_source=False)
    client = TestClient(main.app)
    r = client.post("/api/projects/nogate/render", headers=auth_headers())
    assert r.status_code == 422
    assert "gate" in r.json()["detail"]


def test_hs019_partial_youtube_batch_is_not_reported_done(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    out = project / "outputs"
    out.mkdir()
    (out / "a.mp4").write_bytes(b"a" * 4096)
    (out / "b.mp4").write_bytes(b"b" * 4096)
    monkeypatch.setattr(youtube, "_access_token", lambda refresh=True: "token")
    calls = {"n": 0}
    def fake_one(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise youtube.YouTubeIntegrationError("quota")
        return {"ok": True, "video_id": "one"}
    monkeypatch.setattr(youtube, "_upload_one", fake_one)
    class Logger:
        def __init__(self): self.states=[]
        def set_status(self, state, progress, message, **kwargs): self.states.append(state)
        def log(self, *args, **kwargs): pass
    logger=Logger()
    with pytest.raises(youtube.YouTubeIntegrationError):
        youtube.upload_project_videos(project, [{"file_path":"outputs/a.mp4"},{"file_path":"outputs/b.mp4"}], logger)
    assert "partial_failed" in logger.states and "done" not in logger.states


def test_hs023_hs028_checkpoint_uses_actual_audio_visual_ocr_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "cp")
    write_json(d / ARTIFACTS["audio_events"], {"events": []})
    write_json(d / ARTIFACTS["visual"], {"enabled": True, "samples": []})
    write_json(d / ARTIFACTS["ocr"], {"enabled": True, "items": []})
    report = main.project_checkpoint_status(d)
    by_id = {x["id"]:x for x in report["checkpoints"]}
    assert by_id["audio"]["done"] and by_id["visual"]["done"] and by_id["ocr"]["done"]
    assert by_id["visual"]["path"] == "visual_scan_report.json"
    assert by_id["ocr"]["path"] == "ocr_scan_report.json"


def test_hs024_cancel_requested_does_not_claim_100_percent(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "cancel")
    write_json(d / "status.json", {"state":"running","progress":43,"message":"work"})
    # no actual process is required; endpoint must preserve truthful progress.
    client=TestClient(main.app)
    r=client.post("/api/projects/cancel/cancel", headers=auth_headers())
    assert r.status_code == 200
    status=read_json(d/"status.json",{})
    assert status["state"] == "cancel_requested"
    assert float(status["progress"]) < 100


def test_hs025_oauth_error_is_html_escaped():
    client=TestClient(main.app)
    r=client.get("/api/youtube/oauth/callback", params={"error":"<script>alert(1)</script>"})
    assert r.status_code == 400
    assert "<script>" not in r.text and "&lt;script&gt;" in r.text


def test_hs026_source_signature_detects_same_size_same_mtime_content_replacement(tmp_path):
    p=tmp_path/"source.mp4"
    p.write_bytes(b"A"*4096)
    st=p.stat()
    sig_a=source_file_signature(p)
    p.write_bytes(b"B"*4096)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    sig_b=source_file_signature(p)
    assert sig_a["size"] == sig_b["size"] and sig_a["mtime_ns"] == sig_b["mtime_ns"]
    assert sig_a["partial_sha256"] != sig_b["partial_sha256"]


def test_hs029_auto_probe_reads_actual_ocr_report_name():
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert 'ARTIFACTS["ocr"]' in source
    assert 'project_dir_ / "ocr_scan.json"' not in source


def test_hs033_direct_analyze_fatal_source_failure_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "badsource", with_source=False)
    client=TestClient(main.app)
    r=client.post("/api/projects/badsource/analyze", headers=auth_headers())
    assert r.status_code == 422


def test_hs035_segment_put_rejects_stale_revision_with_409(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d=make_project(tmp_path,"occ")
    seed_analysis(d)
    monkeypatch.setattr(sys.modules[main.update_segments.__module__], "video_duration", lambda path: 120.0)
    client=TestClient(main.app)
    first=client.get("/api/projects/occ/segments",headers=auth_headers())
    revision=first.headers["x-segments-revision"]
    segs=first.json()
    segs[0]["end"]=18
    ok=client.put("/api/projects/occ/segments",json=segs,headers={**auth_headers(),"X-Segments-Revision":revision})
    assert ok.status_code==200
    stale=client.put("/api/projects/occ/segments",json=segs,headers={**auth_headers(),"X-Segments-Revision":revision})
    assert stale.status_code==409


def test_hs036_ollama_streaming_cancel_is_observable(monkeypatch):
    checks={"n":0}
    def cancelled():
        checks["n"] += 1
        return checks["n"] >= 3
    class Resp:
        def __enter__(self): return self
        def __exit__(self,*a): return False
        def raise_for_status(self): pass
        def iter_lines(self,decode_unicode=True):
            yield json.dumps({"response":"a","done":False})
            yield json.dumps({"response":"b","done":False})
    monkeypatch.setattr("highlight_studio.integrations.ai.ollama.requests.post",lambda *a,**k:Resp())
    c=OllamaClient(cancel_check=cancelled)
    with pytest.raises(OperationCancelled):
        c._post_generate({"model":"x"},900)


def test_hs038_refill_never_adds_remove_candidate(tmp_path):
    logger=pipeline.JobLogger(tmp_path)
    keep=pipeline.Candidate(1,0,10,8,"keep","",decision="keep",candidate_id="keep")
    remove=pipeline.Candidate(2,20,40,10,"remove","",decision="remove",candidate_id="remove")
    acceptable=pipeline.Candidate(3,50,70,7,"ok","",decision="maybe",candidate_id="ok")
    out=pipeline.refill_after_dedup(tmp_path,[keep,remove,acceptable],[keep],30,{"target_fill_ratio":0.9,"min_final_segments":2,"max_final_segments":5,"micro_cut_enabled":False},logger)
    assert "remove" not in {c.candidate_id for c in out}
    assert "ok" in {c.candidate_id for c in out}


def test_hs040_short_smart_zoom_label_is_truthful_not_tracking_claim():
    root=Path(__file__).resolve().parents[1]
    src=(root/"frontend/src/app/App.jsx").read_text(encoding="utf-8")
    assert "без слежения" in src.lower()


def test_hs042_ci_separates_source_checkout_from_release_archive():
    root=Path(__file__).resolve().parents[1]
    ci=(root/".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "source-release-contract:" in ci
    assert "git check-ignore -q vendor/twitchdownloadercli/TwitchDownloaderCLI.exe" in ci
    assert "tools/release/make_release.py" not in ci
    assert "tools/release/verify_archive.py" not in ci
    assert "highlight_studio_v1050_hybrid_desktop.zip" not in ci


def test_hs009_corrupt_mp4_rejected_by_media_validator(tmp_path):
    p=tmp_path/"corrupt.mp4"
    p.write_bytes(b"not a real mp4")
    result=validate_media_file(p)
    assert result["ok"] is False
    assert result["code"] in {"ffprobe_failed","no_video","invalid_duration"}


def test_hs022_resume_does_not_rerun_analysis_when_analysis_is_current_but_segments_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(main,"PROJECTS_DIR",tmp_path)
    d=make_project(tmp_path,"resume")
    settings=seed_analysis(d)
    # Keep candidates current, then remove montage and restamp analysis generation.
    write_json(d/"segments.json",[])
    write_json(d/"transcript.json", [{"start": 0, "end": 1, "text": "ok"}])
    mark_analysis_complete(d,settings)
    client=TestClient(main.app)
    r=client.post("/api/projects/resume/resume",headers=auth_headers())
    assert r.status_code==200
    body=r.json()
    assert body["resume_stage"]=="review"
    assert body["started"] is False


def test_hs021_web_concurrency_is_not_hardcoded_to_one():
    root=Path(__file__).resolve().parents[1]
    src=(root/"backend/src/highlight_studio/api/app.py").read_text(encoding="utf-8")
    assert "HIGHLIGHT_STUDIO_MAX_CONCURRENT_JOBS" in src
    assert "else 4" in src or "default" in src



def test_hs002_audio_artifact_cleanup_happens_after_audio_consumers():
    root=Path(__file__).resolve().parents[1]
    src=(root/"backend/src/highlight_studio/services/pipeline.py").read_text(encoding="utf-8")
    cleanup=src.index('(project_dir / "audio_16k.wav").unlink')
    assert src.index('apply_audio_dynamics_to_candidates(') < cleanup
    assert src.index('detect_audio_events(') < cleanup


def test_hs041_music_heuristic_handles_music_speech_silence_and_loud_profiles():
    assert main.infer_music_presence({"enabled":True,"p50":-25,"p95":-17,"silence_ratio":0.05}) == "likely"
    assert main.infer_music_presence({"enabled":True,"p50":-35,"p95":-8,"silence_ratio":0.35}) == "unknown"
    assert main.infer_music_presence({"enabled":True,"p50":-90,"p95":-80,"silence_ratio":0.95}) == "unknown"
    assert main.infer_music_presence({"enabled":True,"p50":-5,"p95":-1,"silence_ratio":0.01}) == "unknown"


def test_hs020_orphan_reconcile_requires_recorded_project_identity(tmp_path, monkeypatch):
    from highlight_studio.core import utils as core_utils
    project=tmp_path/"project"
    project.mkdir()
    registry=project/"owned_processes.json"
    write_json(registry,[{"pid":12345,"command":["ffmpeg","-i",str(project/"input.mp4")]}])
    # Live command does not include the project: it must not be terminated.
    monkeypatch.setattr(core_utils,"_read_process_command",lambda pid:"ffmpeg -i /some/other/project.mp4")
    killed=[]
    monkeypatch.setattr(core_utils.os,"kill",lambda pid,sig:killed.append(pid))
    assert core_utils.reconcile_orphaned_processes(project) == 0
    assert killed == []


def test_hs020_orphan_reconcile_terminates_only_matching_recorded_process(tmp_path, monkeypatch):
    from highlight_studio.core import utils as core_utils
    project = tmp_path / "owned-project"
    project.mkdir()
    registry = project / "running_processes.json"
    write_json(registry, [{
        "pid": 23456,
        "process_start_marker": "start-1",
        "command": ["ffmpeg", "-i", str(project / "input.mp4")],
        "project_dir": str(project.resolve()),
    }])
    monkeypatch.setattr(core_utils, "_read_process_command", lambda pid: f"ffmpeg -i {project / 'input.mp4'}")
    monkeypatch.setattr(core_utils, "_read_process_start_marker", lambda pid: "start-1")
    killed = []
    if core_utils.os.name == "nt":
        monkeypatch.setattr(
            core_utils.subprocess,
            "run",
            lambda command, **kwargs: killed.append(int(command[2])),
        )
    else:
        monkeypatch.setattr(core_utils.os, "kill", lambda pid, sig: killed.append(pid))
    assert core_utils.reconcile_orphaned_processes(project) == 1
    assert killed == [23456]


def test_hs020_orphan_reconcile_rejects_reused_pid(tmp_path, monkeypatch):
    from highlight_studio.core import utils as core_utils
    project = tmp_path / "owned-project"
    project.mkdir()
    write_json(project / "running_processes.json", [{
        "pid": 34567,
        "process_start_marker": "old-process",
        "command": ["ffmpeg", "-i", str(project / "input.mp4")],
        "project_dir": str(project.resolve()),
    }])
    monkeypatch.setattr(core_utils, "_read_process_command", lambda pid: f"ffmpeg -i {project / 'input.mp4'}")
    monkeypatch.setattr(core_utils, "_read_process_start_marker", lambda pid: "new-process")
    killed = []
    monkeypatch.setattr(core_utils.os, "kill", lambda pid, sig: killed.append(pid))
    assert core_utils.reconcile_orphaned_processes(project) == 0
    assert killed == []


def test_hs017_youtube_upload_checks_cancel_between_small_bounded_chunks(tmp_path, monkeypatch):
    from highlight_studio.integrations.youtube import publisher as yt
    project = tmp_path / "project"
    project.mkdir()
    video = project / "clip.mp4"
    video.write_bytes(b"x" * (yt.CHUNK_SIZE + 128))
    item = {"file_path": "clip.mp4", "title": "clip", "privacy_status": "private"}
    monkeypatch.setattr(yt, "_init_resumable_upload", lambda *a, **k: ("https://upload.test/session", "clip", "private"))
    calls = []
    class Response:
        status_code = 308
        headers = {"Range": f"bytes=0-{yt.CHUNK_SIZE - 1}"}
        ok = False
        text = ""
        def json(self): return {}
    def fake_put(url, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("timeout") == (20, 30)
        (project / "cancel.flag").write_text("1", encoding="utf-8")
        return Response()
    monkeypatch.setattr(yt.requests, "put", fake_put)
    with pytest.raises(OperationCancelled):
        yt._upload_one("token", video, item, project_dir=project)
    assert len(calls) == 1
    assert yt.CHUNK_SIZE <= 2 * 1024 * 1024


def test_recovery_status_uses_authoritative_source_and_render_freshness(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "recovery")
    settings = seed_analysis(d)
    out = d / ARTIFACTS["final_render"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"render" * 1000)
    mark_render_complete(d, settings, out, {"ok": True, "duration_seconds": 20})
    assert main.project_recovery_status(d)["checkpoints"]["final_render"] is True
    segs = read_json(d / "segments.json", [])
    segs[0]["end"] = 17
    write_json(d / "segments.json", segs)
    mark_segments_updated(d, settings)
    report = main.project_recovery_status(d)
    assert report["checkpoints"]["final_render"] is False
    Path(read_json(d / "project.json", {})["source_video_path"]).unlink()
    report = main.project_recovery_status(d)
    assert report["checkpoints"]["source"] is False
    assert report["can_continue"] is False

# HS-NEW-005 — Content Factory must not temporarily overwrite the canonical
# montage/revision state, and its derivative videos must become stale when the
# canonical montage changes.
def test_factory_render_is_non_authoritative_and_becomes_stale_after_montage_edit(tmp_path, monkeypatch):
    d = make_project(tmp_path, "factorysafe")
    settings = seed_analysis(d)
    original_segments = read_json(d / "segments.json", [])
    original_revision = freshness_report(d, settings)["segments_revision"]

    factory = d / "content_factory"
    factory.mkdir(exist_ok=True)
    variant_segments = [{"id": 99, "candidate_id": "factory-99", "start": 1, "end": 8, "score": 8, "decision": "keep", "title": "Factory"}]
    write_json(factory / "montage_10min_segments.json", variant_segments)

    def fake_content_factory(project_dir, settings_arg):
        return {"outputs": [{"minutes": 10, "json": str(factory / "montage_10min_segments.json")}], "shorts": []}

    calls = []
    def fake_render(project_dir, settings_arg, logger, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("segments_override") == variant_segments
        assert kwargs.get("authoritative") is False
        assert kwargs.get("finalize_job_status") is False
        target = Path(kwargs["final_output"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"factory-video" * 200)
        return {"output": str(target), "result_check": {"ok": True}, "outputs": []}

    monkeypatch.setattr(pipeline, "content_factory", fake_content_factory)
    monkeypatch.setattr(pipeline, "render", fake_render)
    logger = pipeline.JobLogger(d)
    report = pipeline.render_factory_versions(d, settings, logger)

    assert calls
    assert read_json(d / "segments.json", []) == original_segments
    assert freshness_report(d, settings)["segments_revision"] == original_revision
    rel = report["rendered"][0]["path"]
    ok, reason = output_is_publishable(d, settings, rel)
    assert ok is True, reason

    edited = list(original_segments)
    edited[0] = {**edited[0], "end": 18}
    write_json(d / "segments.json", edited)
    mark_segments_updated(d, settings)
    ok2, reason2 = output_is_publishable(d, settings, rel)
    assert ok2 is False
    assert "Content Factory" in reason2


def test_factory_render_endpoint_rejects_stale_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "factorystale")
    seed_analysis(d)
    project = read_json(d / "project.json", {})
    project["settings"] = {**project["settings"], "prompt": str(project["settings"].get("prompt", "")) + " changed"}
    write_json(d / "project.json", project)
    assert freshness_report(d, main.load_project(d)["settings"])["segments_current"] is False
    client = TestClient(main.app)
    r = client.post("/api/projects/factorystale/render-factory", headers=auth_headers())
    assert r.status_code == 409


def test_authoritative_render_never_commits_newer_revision_after_mid_render_edit(tmp_path, monkeypatch):
    d = make_project(tmp_path, "render-race")
    settings = seed_analysis(d)
    settings = {**settings, "make_srt": False, "video_encoder": "libx264"}
    project = read_json(d / "project.json", {})
    project["settings"] = settings
    write_json(d / "project.json", project)

    canonical = d / ARTIFACTS["final_render"]
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"old-current-render" * 200)
    old_bytes = canonical.read_bytes()

    monkeypatch.setattr(pipeline, "video_duration", lambda _p: 120.0)
    monkeypatch.setattr(pipeline, "which", lambda name: f"/{name}")
    monkeypatch.setattr(pipeline, "video_encode_args", lambda *_a, **_k: ([], "libx264"))
    monkeypatch.setattr(pipeline, "result_check", lambda *_a, **_k: {"ok": True, "duration_seconds": 20.0})
    monkeypatch.setattr(pipeline, "validate_media_file", lambda *_a, **_k: {"ok": True, "duration_seconds": 20.0})
    monkeypatch.setattr(pipeline, "list_output_files", lambda *_a, **_k: [])

    edited = {"done": False}

    def fake_run_cmd(cmd, **_kwargs):
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"new-render-bytes" * 300)
        # Simulate the user editing the montage after the render job pinned its
        # input generation but before the final commit.
        if not edited["done"] and "-ss" in cmd:
            segs = read_json(d / "segments.json", [])
            segs[0]["end"] = 18
            write_json(d / "segments.json", segs)
            edited["done"] = True
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(pipeline, "run_cmd", fake_run_cmd)
    result = pipeline.render(d, settings, pipeline.JobLogger(d))

    assert result["stale_due_to_project_change"] is True
    assert result["authoritative"] is False
    assert ".stale_" in Path(result["output"]).name
    assert Path(result["output"]).exists()
    # The previously canonical deliverable is never overwritten by bytes from
    # the stale generation.
    assert canonical.read_bytes() == old_bytes
    state = read_json(d / ARTIFACTS["revision_state"], {})
    assert state.get("rendered_revision") is None
    stale = read_json(d / "render_stale_result.json", {})
    assert stale.get("reason") == "project_generation_changed_during_render"
    assert stale["rendered_generation"]["segments_revision"] != stale["current_generation"]["segments_revision"]
