import json
from pathlib import Path

from fastapi.testclient import TestClient

import backend.main as main
from backend.pipeline import Candidate, JobLogger, is_irl_settings, resolve_segment_overlaps
from backend.utils import overlaps, read_json, write_json
from highlight_studio.core.revisions import mark_analysis_complete, mark_render_complete


def auth_headers():
    return {"X-Local-Token": main.LOCAL_AUTH_TOKEN}


def make_project(root: Path, pid: str = "p1") -> Path:
    d = root / pid
    d.mkdir(parents=True, exist_ok=True)
    write_json(
        d / "project.json",
        {
            "id": pid,
            "name": "Smoke",
            "settings": main.default_settings(),
        },
    )
    return d


def test_health_reports_version():
    client = TestClient(main.app)
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["app_version"].startswith("v")


def test_settings_validation_rejects_dangerous_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "abc")
    client = TestClient(main.app)
    r = client.post("/api/projects/abc/settings", json={"block_seconds": 0}, headers=auth_headers())
    assert r.status_code == 422


def test_get_quality_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "abc")
    client = TestClient(main.app)
    r = client.get("/api/projects/abc/quality", headers=auth_headers())
    assert r.status_code == 200
    assert r.json()["quality_score"] == 0
    assert not (d / "quality_report.json").exists()


def test_file_endpoint_blocks_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "abc")
    client = TestClient(main.app)
    r = client.get("/api/projects/abc/file/../../backend/main.py", headers=auth_headers())
    assert r.status_code == 404


def test_native_file_path_is_desktop_only_and_project_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    project = make_project(tmp_path, "desktop")
    output = project / "outputs" / "highlight_final.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"video")
    client = TestClient(main.app)

    disabled = client.get("/api/projects/desktop/native-path/outputs/highlight_final.mp4", headers=auth_headers())
    assert disabled.status_code == 404

    monkeypatch.setenv("HIGHLIGHT_STUDIO_DESKTOP", "1")
    enabled = client.get("/api/projects/desktop/native-path/outputs/highlight_final.mp4", headers=auth_headers())
    assert enabled.status_code == 200
    assert enabled.json()["path"] == str(output.resolve())
    assert enabled.json()["size_bytes"] == 5

    traversal = client.get("/api/projects/desktop/native-path/../../outside.mp4", headers=auth_headers())
    assert traversal.status_code == 404


def test_overlap_resolver_outputs_non_overlapping_segments(tmp_path):
    logger = JobLogger(tmp_path)
    chosen = [
        Candidate(id=1, start=0, end=40, score=7.0, title="A", reason=""),
        Candidate(id=2, start=35, end=70, score=8.0, title="B", reason=""),
        Candidate(id=3, start=80, end=100, score=6.0, title="C", reason=""),
    ]
    resolved = resolve_segment_overlaps(chosen, duration=120, settings={"micro_min_seconds": 10}, logger=logger)
    assert resolved
    for i, a in enumerate(resolved):
        for b in resolved[i + 1 :]:
            assert not overlaps(a.start, a.end, b.start, b.end)
    assert all(s.confidence >= 0 for s in resolved)


def test_fast_import_reference_project_uses_external_source(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    main.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, 'validate_media_file', lambda _p, **_k: {'ok': True, 'duration': 10.0, 'video_stream': True})
    src = tmp_path / "huge_stream.mp4"
    src.write_bytes(b"fake-video-bytes")
    client = TestClient(main.app)
    r = client.post("/api/projects/from-path", json={"source_path": str(src), "storage_mode": "reference"}, headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["storage_mode"] == "reference"
    assert data["source_video_path"] == str(src.resolve())
    assert not (main.PROJECTS_DIR / data["id"] / "input.mp4").exists()


def test_fast_import_rejects_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    main.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    client = TestClient(main.app)
    r = client.post(
        "/api/projects/from-path", json={"source_path": str(tmp_path / "missing.mp4"), "storage_mode": "reference"}, headers=auth_headers()
    )
    assert r.status_code == 404


def test_irl_settings_are_validated_and_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "irl")
    client = TestClient(main.app)
    r = client.post("/api/projects/irl/settings", json={"content_type": "IRL стрим", "edit_mode": "IRL история"}, headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["content_type"] == "IRL стрим"
    assert data["edit_mode"] == "IRL история"


def test_irl_edit_mode_auto_enables_content_type(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "irl2")
    client = TestClient(main.app)
    r = client.post("/api/projects/irl2/settings", json={"content_type": "Auto", "edit_mode": "IRL плотный"}, headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["content_type"] == "IRL стрим"
    assert data["edit_mode"] == "IRL плотный"
    assert is_irl_settings(data) is True


def test_fast_import_accepts_windows_copy_as_path_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    main.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, 'validate_media_file', lambda _p, **_k: {'ok': True, 'duration': 10.0, 'video_stream': True})
    src = tmp_path / "quoted stream.mp4"
    src.write_bytes(b"fake-video-bytes")
    client = TestClient(main.app)
    r = client.post("/api/projects/from-path", json={"source_path": f'"{src}"', "storage_mode": "reference"}, headers=auth_headers())
    assert r.status_code == 200
    assert r.json()["source_video_path"] == str(src.resolve())


def test_local_browse_lists_video_files(tmp_path):
    src = tmp_path / "clip.mkv"
    src.write_bytes(b"fake-video-bytes")
    (tmp_path / "subdir").mkdir()
    client = TestClient(main.app)
    r = client.get("/api/local/browse", params={"path": str(tmp_path)}, headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert any(v["name"] == "clip.mkv" for v in data["videos"])
    assert any(d["name"] == "subdir" for d in data["dirs"])


def test_metadata_fast_does_not_require_ollama(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "meta")
    # Minimal final segments so metadata can be created without running the whole pipeline.
    write_json(
        d / "segments.json",
        [
            {"id": 1, "start": 0, "end": 30, "score": 9, "title": "Смешная реакция", "reason": "чат спровоцировал реакцию"},
            {"id": 2, "start": 40, "end": 80, "score": 8, "title": "Неожиданный момент", "reason": "сильная эмоция"},
        ],
    )
    client = TestClient(main.app)
    r = client.post("/api/projects/meta/metadata", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["ai_used"] is False
    assert data["titles"]
    assert (d / "youtube_metadata.json").exists()


def test_api_requires_local_token_for_private_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "secure")
    client = TestClient(main.app)
    r = client.get("/api/projects")
    assert r.status_code == 401
    ok = client.get("/api/projects", headers=auth_headers())
    assert ok.status_code == 200


def test_package_json_has_no_latest_dependencies():
    import json

    pkg = json.loads((Path(__file__).resolve().parents[1] / "frontend" / "package.json").read_text())
    deps = pkg.get("dependencies", {})
    assert deps
    assert all(v != "latest" for v in deps.values())


def test_timeline_export_creates_edl_and_fcpxml(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "export")
    src = d / "input.mp4"
    src.write_bytes(b"fake")
    write_json(d / "project.json", {"id": "export", "name": "Export", "source_video_path": str(src), "settings": main.default_settings()})
    write_json(d / "segments.json", [{"id": 1, "start": 0, "end": 10, "score": 9, "title": "Hook", "reason": ""}])
    client = TestClient(main.app)
    r = client.post("/api/projects/export/timeline-export", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert (d / "outputs" / "timeline_exports" / "highlight_timeline.edl").exists()
    assert (d / "outputs" / "timeline_exports" / "highlight_timeline.fcpxml").exists()


def test_default_settings_include_ocr_roi_options():
    s = main.default_settings()
    assert s["ocr_languages"] == "rus+eng"
    assert s["ocr_roi_enabled"] is False
    assert 0 <= s["ocr_roi_x"] <= 1
    assert 1 <= s["ocr_upscale"] <= 4


def test_browser_upload_is_single_copy_and_preserves_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    main.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, 'validate_media_file', lambda _p, **_k: {'ok': True, 'duration': 10.0, 'video_stream': True})
    client = TestClient(main.app)
    r = client.post(
        "/api/projects",
        headers=auth_headers(),
        files={"file": ("small_clip.mkv", b"tiny-video", "video/x-matroska")},
    )
    assert r.status_code == 200
    data = r.json()
    d = main.PROJECTS_DIR / data["id"]
    assert (d / "input.mkv").exists()
    assert not (d / "original.mkv").exists()
    assert data["source_video_path"].endswith("input.mkv")


def test_browser_upload_rejects_files_over_backend_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(main, "MAX_BROWSER_UPLOAD_BYTES", 4)
    main.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    client = TestClient(main.app)
    r = client.post(
        "/api/projects",
        headers=auth_headers(),
        files={"file": ("too_big.mp4", b"12345", "video/mp4")},
    )
    assert r.status_code == 413
    assert not any(main.PROJECTS_DIR.iterdir())


def test_empty_export_and_compare_are_safe_no_500(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "empty")
    client = TestClient(main.app)
    r1 = client.post("/api/projects/empty/timeline-export", headers=auth_headers())
    assert r1.status_code == 200
    assert r1.json()["ok"] is False
    assert "Нет сегментов" in r1.json()["error"]
    r2 = client.post("/api/projects/empty/compare-mode", headers=auth_headers())
    assert r2.status_code == 200
    assert r2.json()["ok"] is False
    assert "Нет кандидатов" in r2.json()["error"]


def test_twitch_time_parser_and_classifier():
    from backend.twitch_source import classify_twitch_url, parse_time_to_seconds

    assert parse_time_to_seconds("01:02:03") == 3723
    assert parse_time_to_seconds("12:34") == 754
    assert parse_time_to_seconds("90") == 90
    assert parse_time_to_seconds("1h2m3s") == 3723
    assert classify_twitch_url("https://www.twitch.tv/videos/1234567890", "auto")["kind"] == "vod"
    assert classify_twitch_url("https://www.twitch.tv/some_channel", "auto")["kind"] == "live"


def test_create_twitch_vod_project_without_auto_start(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    main.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    client = TestClient(main.app)
    r = client.post(
        "/api/projects/from-twitch",
        headers=auth_headers(),
        json={
            "url": "https://www.twitch.tv/videos/1234567890",
            "source_kind": "vod",
            "vod_start": "00:10:00",
            "vod_end": "00:20:00",
            "auto_start": False,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["source_type"] == "twitch"
    assert data["storage_mode"] == "twitch_cache"
    assert data["twitch"]["start_seconds"] == 600
    assert data["twitch"]["end_seconds"] == 1200
    assert data["twitch"]["status"] == "created"


def test_create_twitch_vod_project_rejects_bad_range(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    main.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    client = TestClient(main.app)
    r = client.post(
        "/api/projects/from-twitch",
        headers=auth_headers(),
        json={
            "url": "https://www.twitch.tv/videos/1234567890",
            "source_kind": "vod",
            "vod_start": "00:20:00",
            "vod_end": "00:10:00",
            "auto_start": False,
        },
    )
    assert r.status_code == 422


def test_cloud_ai_settings_are_migrated_back_to_ollama(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "cloud_old")
    client = TestClient(main.app)
    r = client.post(
        "/api/projects/cloud_old/settings",
        json={
            "ai_engine": "hybrid",
            "openai_api_key": "test-key",
            "openai_base_url": "https://api.openai.com/v1",
            "openai_text_model": "gpt-4o-mini",
        },
        headers=auth_headers(),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ai_engine"] == "ollama"
    assert "openai_api_key" not in data
    assert "openai_text_model" not in data


def test_dashboard_never_returns_legacy_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    project = make_project(tmp_path, "legacy-secrets")
    raw = read_json(project / "project.json", {})
    raw["settings"]["openai_api_key"] = "sk-project-secret"
    raw["settings"]["nested_service_token"] = "project-token-secret"
    write_json(project / "project.json", raw)
    write_json(
        project / "youtube_metadata.json",
        {
            "title": "Safe title",
            "api_key": "metadata-secret",
            "nested": {"service_token": "nested-secret", "description": "safe"},
        },
    )

    client = TestClient(main.app)
    response = client.get("/api/projects/legacy-secrets/dashboard-state", headers=auth_headers())
    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload)
    assert "sk-project-secret" not in serialized
    assert "project-token-secret" not in serialized
    assert "metadata-secret" not in serialized
    assert "nested-secret" not in serialized
    assert payload["metadata"]["api_key"] == ""
    assert payload["metadata"]["nested"]["service_token"] == ""
    assert payload["metadata"]["nested"]["description"] == "safe"


def test_ai_client_effective_models_stay_on_ollama():
    from backend.ai_client import effective_text_model, effective_vision_model, effective_ai_engine

    settings = main.default_settings()
    settings.update(
        {
            "ai_engine": "openai",
            "openai_api_key": "dummy",
            "text_model": "qwen3:8b",
            "vision_model": "qwen3-vl:8b",
        }
    )
    assert effective_ai_engine(settings) == "ollama"
    assert effective_text_model(settings) == "qwen3:8b"
    assert effective_vision_model(settings) == "qwen3-vl:8b"


def test_openai_key_check_endpoint_is_disabled_in_ollama_only_build():
    client = TestClient(main.app)
    r = client.post("/api/ai/check-openai", json=main.default_settings(), headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["code"] == "ollama_only"
    assert data["engine"] == "ollama"


def test_system_check_endpoint_returns_readiness(monkeypatch, tmp_path):
    client = TestClient(main.app)
    r = client.get("/api/system-check", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert "checks" in data
    assert "recommendations" in data
    assert "ready_basic" in data
    assert data["checks"]["whisper_vad"]["ok"] is True


def test_runtime_components_include_working_whisper_vad():
    client = TestClient(main.app)
    response = client.get("/api/runtime-components", headers=auth_headers())
    assert response.status_code == 200
    assert response.json()["whisper_vad"]["ok"] is True


def test_stop_ollama_models_endpoint_is_safe_without_ollama():
    client = TestClient(main.app)
    r = client.post("/api/ollama/stop-models", json={"models": ["qwen3:8b"]}, headers=auth_headers())
    assert r.status_code == 200
    assert "ok" in r.json()


def test_safe_defaults_endpoint_saves_stable_values(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "safe")
    client = TestClient(main.app)
    r = client.post(
        "/api/projects/safe/settings",
        json={"ai_batch_size": 8, "metadata_max_segments": 80, "metadata_timeout": 1800},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    r2 = client.post("/api/projects/safe/safe-defaults", headers=auth_headers())
    assert r2.status_code == 200
    data = r2.json()["settings"]
    assert data["ai_batch_size"] == 1
    assert data["micro_batch_size"] == 1
    assert data["metadata_max_segments"] == 12
    assert data["metadata_timeout"] == 300
    saved = read_json(tmp_path / "safe" / "project.json", {})["settings"]
    assert saved["metadata_max_segments"] == 12


def test_metadata_preset_endpoint_saves_and_clamps_for_ollama(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "preset")
    client = TestClient(main.app)
    r = client.post("/api/projects/preset/metadata-preset", json={"preset": "max"}, headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["settings"]["metadata_max_segments"] == 25
    assert data["settings"]["metadata_timeout"] == 900
    check = client.get("/api/projects/preset/settings-check", headers=auth_headers())
    assert check.status_code == 200
    assert check.json()["important"]["metadata_max_segments"] == 25


def test_hardware_preset_endpoint_saves_gtx1050ti_balanced(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "hw")
    client = TestClient(main.app)
    r = client.post("/api/projects/hw/hardware-preset", json={"preset": "gtx1050ti_balanced"}, headers=auth_headers())
    assert r.status_code == 200
    data = r.json()["settings"]
    assert data["hardware_profile"] == "Auto"
    assert data["hardware_auto_optimize"] is True
    assert data["analysis_profile"] == "balanced"
    assert data["ai_batch_size"] >= 1
    assert data["micro_batch_size"] >= 1
    assert data["whisper_model"] == "small"
    assert data["whisper_device"] in {"cpu", "cuda"}
    assert data["whisper_compute"] in {"int8", "int8_float16", "float16", "float32"}
    assert data["visual_scan_max_samples"] == 1200
    assert data["metadata_max_segments"] == 12
    saved = read_json(tmp_path / "hw" / "project.json", {})["settings"]
    assert saved["hardware_profile"] == "Auto"


def test_ollama_context_and_keep_alive_are_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "ctx")
    client = TestClient(main.app)
    ok = client.post("/api/projects/ctx/settings", json={"ollama_keep_alive": "5m", "ollama_num_ctx": 4096}, headers=auth_headers())
    assert ok.status_code == 200
    bad = client.post("/api/projects/ctx/settings", json={"ollama_keep_alive": "forever"}, headers=auth_headers())
    assert bad.status_code == 422


def test_task_presets_endpoint_lists_working_presets():
    client = TestClient(main.app)
    r = client.get("/api/task-presets", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    ids = {p["id"] for p in data["presets"]}
    assert {"irl_funny", "irl_conflict", "sport", "podcast", "shorts_only"} <= ids


def test_task_preset_endpoint_saves_without_breaking_old_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "task")
    client = TestClient(main.app)
    r = client.post("/api/projects/task/task-preset", json={"preset": "sport"}, headers=auth_headers())
    assert r.status_code == 200
    data = r.json()["settings"]
    assert data["task_preset"] == "sport"
    assert data["content_type"] == "Спорт"
    assert data["micro_cut_enabled"] is True
    saved = read_json(tmp_path / "task" / "project.json", {})["settings"]
    assert saved["task_preset_label"] == "Спорт / теннис"
    # Old core settings still exist after applying the new preset.
    assert "ollama_url" in saved
    assert "render_preset" in saved
    assert "twitch_download_threads" in saved


def test_status_contains_hang_diagnosis(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "diag")
    write_json(
        d / "status.json",
        {
            "state": "running",
            "stage": "block_ai",
            "message": "Ollama анализирует batch",
            "progress": 42,
            "updated_at": time.time() - 1000,
            "started_at": time.time() - 1200,
        },
    )
    client = TestClient(main.app)
    r = client.get("/api/projects/diag/status", headers=auth_headers())
    assert r.status_code == 200
    diag = r.json()["diagnosis"]
    assert diag["severity"] == "warning"
    assert "Ollama" in diag["title"] or "AI" in diag["title"]


def test_twitch_turbo_settings_defaults_validate():
    s = main.default_settings()
    assert s["twitch_download_engine"] == "auto"
    assert s["twitch_quality"] == "best"
    assert s["twitch_fallback_enabled"] is True
    assert 1 <= s["twitch_aria2_connections"] <= 64


def test_twitch_tools_endpoint_is_readable():
    client = TestClient(main.app)
    r = client.get("/api/twitch/tools", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert "recommended_engine" in data
    assert "engines" in data
    assert any(e["id"] == "twitchdownloadercli" for e in data["engines"])


def test_twitch_project_saves_turbo_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    main.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    client = TestClient(main.app)
    r = client.post(
        "/api/projects/from-twitch",
        json={
            "url": "https://www.twitch.tv/videos/1234567890",
            "source_kind": "vod",
            "vod_start": "00:00:00",
            "vod_end": "00:10:00",
            "twitch_download_engine": "twitchdownloadercli",
            "twitch_quality": "1080p60",
            "twitch_fallback_enabled": True,
            "auto_start": False,
        },
        headers=auth_headers(),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["twitch"]["download_engine"] == "twitchdownloadercli"
    assert data["twitch"]["quality"] == "1080p60"


def test_dashboard_state_endpoint_aggregates_core_ui_state(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "dash")
    write_json(d / "segments.json", [{"id": 1, "candidate_id": "dash-c1", "start": 0, "end": 20, "score": 8.5, "title": "Hook"}])
    write_json(d / "candidates.json", [{"id": 1, "candidate_id": "dash-c1", "start": 0, "end": 20, "score": 8.5, "title": "Hook"}])
    project = read_json(d / "project.json", {})
    mark_analysis_complete(d, project.get("settings", main.default_settings()))
    client = TestClient(main.app)
    r = client.get("/api/projects/dash/dashboard-state", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["project"]["id"] == "dash"
    assert data["segments"][0]["title"] == "Hook"
    assert data["candidates"][0]["score"] == 8.5
    assert "status" in data
    assert "poll_after_ms" in data
    assert "project_history" in data


def test_dashboard_state_keeps_running_poll_fast(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "runpoll")
    write_json(d / "status.json", {"state": "running", "progress": 25, "message": "AI batch"})
    client = TestClient(main.app)
    r = client.get("/api/projects/runpoll/dashboard-state", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["active"] is True
    assert data["poll_after_ms"] <= 3000


def test_read_json_recovers_from_backup_for_critical_file(tmp_path):
    path = tmp_path / "project.json"
    write_json(path, {"id": "old", "settings": {}})
    write_json(path, {"id": "new", "settings": {"target_minutes": 30}})
    path.write_text("{broken-json", encoding="utf-8")
    data = read_json(path, {})
    assert data["id"] == "old"


def test_project_integrity_endpoint_can_repair_corrupted_json(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "safe")
    # second write creates project.json.bak, then we simulate a Windows/app shutdown
    # that leaves the main JSON unreadable.
    write_json(d / "project.json", {"id": "safe", "name": "Safe", "settings": main.default_settings(), "marker": "latest"})
    (d / "project.json").write_text("not-json", encoding="utf-8")
    client = TestClient(main.app)
    r = client.get("/api/projects/safe/integrity", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["recoverable_count"] >= 1
    assert any(i["name"] == "project.json" and i["recoverable"] for i in data["issues"])
    rr = client.post("/api/projects/safe/repair-json-backups", headers=auth_headers())
    assert rr.status_code == 200
    repaired = rr.json()
    assert repaired["repaired"] >= 1
    assert repaired["integrity"]["critical_ok"] is True
    assert isinstance(read_json(d / "project.json", {}), dict)


def test_product_readiness_endpoint_reports_blockers(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "ready")
    client = TestClient(main.app)
    r = client.get("/api/projects/ready/product-readiness", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert "score" in data
    assert "items" in data
    assert any(item["id"] == "source" for item in data["items"])
    assert data["ok"] is False


def test_debug_bundle_excludes_video_and_contains_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "debug")
    (d / "input.mp4").write_bytes(b"fake-video")
    project = read_json(d / "project.json", {})
    project["source_video_path"] = str(d / "input.mp4")
    write_json(d / "project.json", project)
    client = TestClient(main.app)
    r = client.post("/api/projects/debug/debug-bundle", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    bundle = d / data["path"]
    assert bundle.exists()
    import zipfile

    names = zipfile.ZipFile(bundle).namelist()
    assert "product_readiness.json" in names
    assert not any(name.endswith("input.mp4") for name in names)


def test_ai_quality_audit_and_backfill_explanations(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "aiq")
    write_json(
        d / "candidates.json",
        [
            {"id": 1, "start": 0, "end": 20, "score": 9, "title": "Смешной момент"},
            {"id": 2, "start": 25, "end": 40, "score": 5, "title": "Пауза"},
        ],
    )
    write_json(d / "segments.json", [{"id": 1, "start": 0, "end": 4, "score": 9, "title": "Hook"}])
    client = TestClient(main.app)
    r = client.get("/api/projects/aiq/ai-quality-audit", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert "score" in data
    assert data["missing_explanations"] >= 1
    b = client.post("/api/projects/aiq/backfill-explanations", headers=auth_headers())
    assert b.status_code == 200
    bd = b.json()
    assert bd["ok"] is True
    cands = read_json(d / "candidates.json", [])
    assert cands[0]["what_happens"]
    assert cands[0]["why_selected"]


def test_project_doctor_creates_missing_json_and_normalizes_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "doctor")
    # Remove optional files and create broken/duplicate segments.
    (d / "status.json").unlink(missing_ok=True)
    write_json(
        d / "segments.json",
        [
            {"id": 99, "start": -5, "end": 2, "score": 9, "title": "A"},
            {"id": 100, "start": 10, "end": 8, "score": 8, "title": "bad"},
            {"id": 101, "start": 20, "end": 40, "score": 7, "title": "B"},
            {"id": 102, "start": 20, "end": 40, "score": 7, "title": "B"},
        ],
    )
    client = TestClient(main.app)
    r = client.post("/api/projects/doctor/project-doctor", json={"backfill_explanations": True}, headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert (d / "status.json").exists()
    segs = read_json(d / "segments.json", [])
    assert len(segs) == 2
    assert segs[0]["id"] == 1
    assert segs[0]["start"] == 0
    assert segs[0]["end"] >= 5
    assert data["ai_quality"]["segments"] == 2


def test_product_test_plan_endpoint_lists_acceptance_scenarios(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "plan")
    client = TestClient(main.app)
    r = client.get("/api/projects/plan/product-test-plan", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    ids = {x["id"] for x in data["scenarios"]}
    assert {"short_local", "twitch_speed", "long_vod"} <= ids
    assert data["acceptance_metrics"]


def test_dashboard_state_includes_quality_doctor_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "dashdoctor")
    write_json(d / "candidates.json", [{"id": 1, "start": 0, "end": 20, "score": 9, "title": "Hook"}])
    client = TestClient(main.app)
    r = client.get("/api/projects/dashdoctor/dashboard-state", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert "ai_quality_audit" in data
    assert "product_test_plan" in data
    assert data["ai_quality_audit"]["candidates"] == 1


def test_my_best_settings_save_apply_and_quality_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path / "data")
    main.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    main.DATA_DIR.mkdir(parents=True, exist_ok=True)
    d = make_project(main.PROJECTS_DIR, "best")
    client = TestClient(main.app)
    r = client.post("/api/projects/best/my-best-settings/save", headers=auth_headers())
    assert r.status_code == 200
    assert (d / "my_best_settings.json").exists()
    q = client.get("/api/projects/best/quality-lock", headers=auth_headers())
    assert q.status_code == 200
    assert q.json()["exists"] is True
    assert q.json()["active"] is True
    changed = dict(main.default_settings())
    changed["ai_batch_size"] = 2
    rr = client.post("/api/projects/best/settings", json=changed, headers=auth_headers())
    assert rr.status_code == 200
    q2 = client.get("/api/projects/best/quality-lock", headers=auth_headers()).json()
    assert q2["active"] is False
    assert q2["critical_diffs"]
    ap = client.post("/api/projects/best/my-best-settings/apply", headers=auth_headers())
    assert ap.status_code == 200
    assert ap.json()["quality_lock"]["active"] is True


def test_final_success_and_success_history_reports_ready_video(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    main.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    d = make_project(main.PROJECTS_DIR, "done")
    source = d / "input.mp4"
    source.write_bytes(b"source")
    project = read_json(d / "project.json", {})
    project["source_video_path"] = str(source)
    write_json(d / "project.json", project)
    (d / "outputs").mkdir(exist_ok=True)
    final = d / "outputs" / "highlight_final.mp4"
    final.write_bytes(b"not-real-video-but-output")
    write_json(d / "candidates.json", [{"id": 1, "candidate_id": "c1", "start": 0, "end": 20, "score": 9, "title": "Hook"}])
    write_json(d / "segments.json", [{"id": 1, "candidate_id": "c1", "start": 0, "end": 20, "score": 9, "title": "Hook"}])
    settings = read_json(d / "project.json", {}).get("settings", main.default_settings())
    mark_analysis_complete(d, settings)
    mark_render_complete(d, settings, final, {"ok": True, "duration": 20.0})
    client = TestClient(main.app)
    fs = client.get("/api/projects/done/final-success", headers=auth_headers())
    assert fs.status_code == 200
    data = fs.json()
    assert data["final_ready"] is True
    assert data["segments"] == 1
    hist = client.get("/api/success-history", headers=auth_headers())
    assert hist.status_code == 200
    assert any(x["id"] == "done" for x in hist.json()["items"])


def test_dashboard_state_contains_stable_candidate_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path / "data")
    main.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    main.DATA_DIR.mkdir(parents=True, exist_ok=True)
    make_project(main.PROJECTS_DIR, "dash")
    client = TestClient(main.app)
    r = client.get("/api/projects/dash/dashboard-state", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert "quality_lock" in data
    assert "final_success" in data
    assert "stable_candidate" in data
    assert "success_history" in data


def test_release_app_audit_reports_packaging_state():
    client = TestClient(main.app)
    r = client.get("/api/app-audit", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert "score" in data
    ids = {x["id"] for x in data["checks"]}
    assert "frontend_dist" in ids
    assert "tests" in ids
    assert "script_START_HERE.bat" in ids


def test_workflow_guard_guides_next_action_and_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "guard")
    src = tmp_path / "source.mp4"
    src.write_bytes(b"fake-video")
    project = read_json(d / "project.json", {})
    project["source_video_path"] = str(src)
    write_json(d / "project.json", project)
    write_json(d / "candidates.json", [{"id": 1, "start": 0, "end": 30, "score": 9, "title": "Hook"}])
    write_json(d / "segments.json", [{"id": 1, "candidate_id": "guard-c1", "start": 0, "end": 30, "score": 9, "title": "Hook"}])
    mark_analysis_complete(d, project.get("settings", main.default_settings()))
    client = TestClient(main.app)
    r = client.get("/api/projects/guard/workflow-guard", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["source_ready"] is True
    assert data["next_action"]["step"] == "export"
    assert any(x["id"] == "segments" and x["ok"] for x in data["steps"])
    a = client.get("/api/projects/guard/render-artifact-check", headers=auth_headers())
    assert a.status_code == 200
    assert a.json()["final_ready"] is False


def test_dashboard_state_contains_release_hardening_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "dashhard")
    client = TestClient(main.app)
    r = client.get("/api/projects/dashhard/dashboard-state", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert "workflow_guard" in data
    assert "render_artifact_check" in data
    assert "app_audit" in data
    assert data["app_audit"]["app_version"].startswith("v11.2.7")


def test_background_job_writes_queued_status_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    make_project(tmp_path, "jobguard")
    d = tmp_path / "jobguard"

    def task(project_dir, settings, logger):
        logger.heartbeat("test_running", 10, "test running")

    result = main.start_background_job("jobguard", "Тестовая задача", task, kind="test")
    assert result["started"] is True
    status = read_json(d / "status.json", {})
    assert status.get("state") in {"queued", "running", "done"}
    assert status.get("progress", 0) >= 1
    assert status.get("job_kind") == "test" or status.get("stage") == "test_running"


def test_twitch_progress_parsers():
    from backend import twitch_source

    assert twitch_source._extract_twitch_percent("[download]  12.3% of 1.00GiB at 8.50MiB/s ETA 01:20") == 12.3
    assert twitch_source._extract_twitch_percent("no percent here") is None
    assert round(twitch_source._extract_twitch_speed("at 8.50MiB/s"), 2) == 8.5
    assert round(twitch_source._extract_twitch_speed("at 512.0KiB/s"), 2) == 0.5


def test_health_sets_cookie_without_exposing_token():
    client = TestClient(main.app)
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data.get("auth") == "cookie"
    assert "local_auth_token" not in data
    assert "highlight_studio_local_token=" in r.headers.get("set-cookie", "")


def test_project_id_blocks_encoded_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path / "projects")
    main.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    make_project(main.PROJECTS_DIR, "abc")
    client = TestClient(main.app)
    for bad in ["..%2Fsecret", "%252e%252e%252fsecret", "abc%2F..%2F..%2Fsecret", "abc.."]:
        r = client.get(f"/api/projects/{bad}", headers=auth_headers())
        assert r.status_code == 404


def test_twitch_classifier_rejects_lookalike_host():
    from backend.twitch_source import classify_twitch_url
    import pytest

    with pytest.raises(ValueError):
        classify_twitch_url("https://twitch.tv.evil.example/videos/1234567890")
    assert classify_twitch_url("https://www.twitch.tv/videos/1234567890")["kind"] == "vod"


def test_project_export_excludes_runtime_and_media_files(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "exsafe")
    (d / "input.mp4").write_bytes(b"fake-video")
    (d / "audio_16k.wav").write_bytes(b"fake-audio")
    (d / "transcript_chunks").mkdir()
    (d / "transcript_chunks" / "chunk_0001.wav").write_bytes(b"chunk")
    write_json(d / "segments.json", [{"id": 1, "start": 0, "end": 5}])
    client = TestClient(main.app)
    r = client.post("/api/projects/exsafe/export", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "metadata"
    import zipfile

    names = zipfile.ZipFile(d / data["path"]).namelist()
    assert "segments.json" in names
    assert not any(name.endswith((".mp4", ".wav")) for name in names)
    assert not any(name.startswith("transcript_chunks/") for name in names)


def test_cookie_auth_allows_browser_file_links(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "cookie")
    (d / "note.txt").write_text("ok", encoding="utf-8")
    client = TestClient(main.app)
    assert client.get("/api/health").status_code == 200
    r = client.get("/api/projects")
    assert r.status_code == 200
    f = client.get("/api/projects/cookie/file/note.txt")
    assert f.status_code == 200
    assert f.text == "ok"


def test_modular_source_tree_is_present():
    root = Path(__file__).resolve().parents[1]
    required = [
        root / "backend" / "src" / "highlight_studio" / "api" / "app.py",
        root / "backend" / "src" / "highlight_studio" / "core" / "settings.py",
        root / "backend" / "src" / "highlight_studio" / "services" / "pipeline.py",
        root / "backend" / "src" / "highlight_studio" / "integrations" / "ai" / "client.py",
        root / "backend" / "src" / "highlight_studio" / "integrations" / "twitch" / "source.py",
        root / "backend" / "src" / "highlight_studio" / "infrastructure" / "job_store.py",
        root / "frontend" / "src" / "api" / "client.js",
        root / "frontend" / "src" / "components" / "ui.jsx",
        root / "frontend" / "src" / "config" / "taskPresets.js",
        root / "docs" / "ARCHITECTURE.md",
    ]
    assert all(path.exists() for path in required)


def test_legacy_backend_imports_alias_canonical_modules():
    import backend.main as legacy_main
    import backend.pipeline as legacy_pipeline
    from backend.src.highlight_studio.api import app as canonical_main
    from backend.src.highlight_studio.services import pipeline as canonical_pipeline

    assert legacy_main is canonical_main
    assert legacy_pipeline is canonical_pipeline


def test_twitch_project_plan_uses_validated_project_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "twplan")
    project = read_json(d / "project.json", {})
    project.update(
        {
            "source_url": "https://www.twitch.tv/videos/1234567890",
            "twitch": {"url": "https://www.twitch.tv/videos/1234567890", "source_kind": "vod"},
        }
    )
    project["settings"]["twitch_quality"] = "720p60"
    write_json(d / "project.json", project)
    captured = {}

    def fake_plan(url, settings, source_kind):
        captured.update(url=url, settings=settings, source_kind=source_kind)
        return {"ok": True, "engine": "fake"}

    monkeypatch.setattr(main, "twitch_download_plan", fake_plan)
    client = TestClient(main.app)
    response = client.get("/api/projects/twplan/twitch-download-plan", headers=auth_headers())
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert captured["settings"]["twitch_quality"] == "720p60"
    assert captured["source_kind"] == "vod"


def test_twitch_speed_test_no_longer_raises_missing_settings_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "twspeed")
    project = read_json(d / "project.json", {})
    project.update(
        {
            "source_url": "https://www.twitch.tv/videos/1234567890",
            "twitch": {"url": "https://www.twitch.tv/videos/1234567890", "source_kind": "vod"},
        }
    )
    write_json(d / "project.json", project)

    def fake_speed(project_dir, payload, settings, logger):
        assert project_dir == d
        assert settings["twitch_download_engine"] == "auto"
        return {"ok": True, "best_engine": "twitchdownloadercli", "results": []}

    monkeypatch.setattr(main, "run_twitch_speed_test", fake_speed)
    client = TestClient(main.app)
    response = client.post("/api/projects/twspeed/twitch-speed-test", json={}, headers=auth_headers())
    assert response.status_code == 200
    assert response.json()["best_engine"] == "twitchdownloadercli"
    saved = read_json(d / "project.json", {})
    assert saved["twitch"]["download_engine"] == "twitchdownloadercli"


def test_ffmpeg_encoder_detection_parses_encoder_tokens(monkeypatch):
    import backend.pipeline as pipeline
    from subprocess import CompletedProcess

    pipeline.ffmpeg_encoder_available.cache_clear()
    output = " Encoders:\n V..... h264_nvenc           NVIDIA NVENC H.264 encoder\n V..... libx264              libx264 H.264\n"
    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, stdout=output, stderr=""),
    )
    assert pipeline.ffmpeg_encoder_available("ffmpeg", "h264_nvenc") is True
    assert pipeline.ffmpeg_encoder_available("ffmpeg", "hevc_nvenc") is False
    pipeline.ffmpeg_encoder_available.cache_clear()


def test_job_store_closes_every_sqlite_connection(tmp_path, monkeypatch):
    import sqlite3
    import backend.job_store as job_store

    monkeypatch.setattr(job_store, "JOBS_DB", tmp_path / "jobs.sqlite3")
    real_connect = job_store._connect
    opened = []

    def tracking_connect():
        con = real_connect()
        opened.append(con)
        return con

    monkeypatch.setattr(job_store, "_connect", tracking_connect)
    job_id = job_store.create_job("p1", "render", "Render")
    job_store.update_job(job_id, state="running")
    assert job_store.get_latest_job("p1")["id"] == job_id
    assert job_store.list_jobs("p1")
    job_store.recover_interrupted_jobs()
    assert opened
    for con in opened:
        try:
            con.execute("SELECT 1")
        except sqlite3.ProgrammingError as exc:
            assert "closed" in str(exc).lower()
        else:
            raise AssertionError("SQLite connection was left open")


def test_output_list_is_deduplicated_and_hides_internal_files(tmp_path):
    from backend.pipeline import list_output_files

    project = tmp_path / "outputs-safe"
    (project / "outputs" / "shorts").mkdir(parents=True)
    (project / "outputs" / "timeline_exports").mkdir(parents=True)
    (project / "content_factory").mkdir(parents=True)
    (project / "render_parts").mkdir(parents=True)
    (project / "outputs" / "highlight_final.mp4").write_bytes(b"video")
    (project / "outputs" / "shorts" / "short_01.mp4").write_bytes(b"short")
    (project / "outputs" / "timeline_exports" / "highlight_timeline.edl").write_text("EDL", encoding="utf-8")
    (project / "content_factory" / "montage_10min_segments.json").write_text("[]", encoding="utf-8")
    (project / "content_factory" / "content_factory_manifest.json").write_text("{}", encoding="utf-8")
    (project / "highlight_subtitles.srt").write_text("1", encoding="utf-8")
    (project / "project.json").write_text("{}", encoding="utf-8")
    (project / "status.json").write_text("{}", encoding="utf-8")
    (project / "render_parts" / "part_001.mp4").write_bytes(b"part")

    items = list_output_files(project)
    paths = [item["path"] for item in items]
    assert len(paths) == len(set(paths))
    assert "outputs/highlight_final.mp4" in paths
    assert "outputs/shorts/short_01.mp4" in paths
    assert "highlight_subtitles.srt" in paths
    assert "content_factory/montage_10min_segments.json" in paths
    assert "content_factory/content_factory_manifest.json" not in paths
    assert "project.json" not in paths
    assert "status.json" not in paths
    assert not any(path.startswith("render_parts/") for path in paths)
    assert {item["kind"] for item in items} >= {"video", "short", "subtitles", "timeline", "edit_data"}


def test_app_audit_is_honestly_labeled_as_packaging_audit():
    client = TestClient(main.app)
    response = client.get("/api/app-audit", headers=auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["audit_type"] == "packaging"
    assert data["packaging_score"] == data["score"]
    assert "Packaging audit" in data["title"]
    assert data["limitations"]


def test_security_headers_are_added_to_local_ui_responses():
    client = TestClient(main.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_all_product_version_markers_are_synchronized():
    root = Path(__file__).resolve().parents[1]
    assert main.APP_VERSION.startswith("v11.2.7")
    assert "v11.2.7" in (root / "README.md").read_text(encoding="utf-8")
    assert "v11.2.7" in (root / "frontend/index.html").read_text(encoding="utf-8")
    assert json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))["version"] == "11.2.7"
    assert "v11.2.7" in (root / "scripts/windows/run_windows.bat").read_text(encoding="utf-8")
    assert "v11.2.7" in (root / "scripts/windows/setup_windows.bat").read_text(encoding="utf-8")
    assert (root / "commands/setup/INSTALL_FFMPEG.bat").is_file()
    for name in ("youtube-publisher.html", "youtube-publisher.js", "youtube-publisher.css"):
        assert (root / "frontend" / "public" / name).is_file()


def test_normalize_segments_never_extends_beyond_short_source(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "short-source")
    (d / "input.mp4").write_bytes(b"probe-is-mocked")
    write_json(d / "segments.json", [{"id": 7, "start": 0, "end": 1.2, "title": "Short"}])
    monkeypatch.setattr(main, "video_duration", lambda _path: 3.0)

    client = TestClient(main.app)
    response = client.post("/api/projects/short-source/normalize-segments", headers=auth_headers())
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_duration"] == 3.0
    assert payload["segments"] == [{"id": 1, "start": 0.0, "end": 3.0, "title": "Short"}]
    assert read_json(d / "segments.json", [])[0]["end"] == 3.0


def test_normalize_segments_removes_clip_starting_after_source_end(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "past-eof")
    (d / "input.mp4").write_bytes(b"probe-is-mocked")
    write_json(d / "segments.json", [{"id": 1, "start": 10, "end": 12, "title": "Outside"}])
    monkeypatch.setattr(main, "video_duration", lambda _path: 3.0)

    client = TestClient(main.app)
    response = client.post("/api/projects/past-eof/normalize-segments", headers=auth_headers())
    assert response.status_code == 200
    payload = response.json()
    assert payload["segments"] == []
    assert payload["removed_items"][0]["reason"] == "start_after_source_end"


def test_fastapi_routes_have_unique_method_path_and_operation_ids():
    from collections import Counter
    from fastapi.routing import APIRoute

    routes = [route for route in main.app.routes if isinstance(route, APIRoute)]
    method_paths = [(method, route.path) for route in routes for method in route.methods]
    assert len(method_paths) == len(set(method_paths))
    operation_ids = [route.operation_id for route in routes if route.operation_id]
    assert not [operation_id for operation_id, count in Counter(operation_ids).items() if count > 1]


def test_put_segments_clamps_to_source_and_drops_non_finite_values(tmp_path, monkeypatch):
    import backend.src.highlight_studio.services.pipeline as pipeline_mod

    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "segment-clamp")
    (d / "input.mp4").write_bytes(b"probe-is-mocked")
    monkeypatch.setattr(pipeline_mod, "video_duration", lambda _path: 3.0)
    client = TestClient(main.app)
    response = client.put(
        "/api/projects/segment-clamp/segments",
        headers=auth_headers(),
        json=[
            {"id": 9, "start": 2.95, "end": 8, "score": "bad", "title": "Tail"},
            {"id": 10, "start": "NaN", "end": 1, "title": "Invalid"},
            {"id": 11, "start": 4, "end": 5, "title": "Past EOF"},
        ],
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["start"] == 2.9
    assert payload[0]["end"] == 3.0
    assert payload[0]["score"] == 0.0
    assert read_json(d / "segments.json", []) == payload


def test_normalize_segments_removes_non_finite_times(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "non-finite")
    # Emulate a legacy/corrupted file: the current writer refuses to persist
    # non-standard NaN values.
    (d / "segments.json").write_text(
        '[{"id": 1, "start": NaN, "end": 2, "title": "Broken"}]',
        encoding="utf-8",
    )
    client = TestClient(main.app)
    response = client.post("/api/projects/non-finite/normalize-segments", headers=auth_headers())
    assert response.status_code == 200
    payload = response.json()
    assert payload["segments"] == []
    assert payload["removed_items"][0]["reason"] == "non_finite_time"


def test_write_json_replaces_non_finite_numbers_with_null(tmp_path):
    path = tmp_path / "strict.json"
    write_json(
        path,
        {
            "nan": float("nan"),
            "positive_infinity": float("inf"),
            "nested": [1.0, float("-inf")],
        },
    )
    raw = path.read_text(encoding="utf-8")
    assert "NaN" not in raw
    assert "Infinity" not in raw
    assert read_json(path) == {
        "nan": None,
        "positive_infinity": None,
        "nested": [1.0, None],
    }


def test_corrupt_non_finite_json_does_not_crash_read_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    d = make_project(tmp_path, "corrupt-json")
    # Python accepts these legacy non-standard constants, while strict JSON
    # responses do not. Keep the raw files to exercise recovery at the API edge.
    (d / "candidates.json").write_text(
        '[{"id":1,"start":NaN,"end":Infinity,"score":NaN,"title":"Broken"}]',
        encoding="utf-8",
    )
    (d / "segments.json").write_text(
        '[{"id":1,"start":NaN,"end":Infinity,"score":NaN,"title":"Broken"}]',
        encoding="utf-8",
    )
    (d / "status.json").write_text(
        '{"state":"idle","progress":NaN,"message":"Broken"}',
        encoding="utf-8",
    )
    # This test exercises JSON sanitization, not stale-artifact filtering. Mark
    # the intentionally malformed analysis artifacts as the current generation.
    project = read_json(d / "project.json", {})
    mark_analysis_complete(d, project.get("settings", main.default_settings()))

    client = TestClient(main.app, raise_server_exceptions=False)
    paths = [
        "/api/project-history",
        "/api/projects/corrupt-json/final-success",
        "/api/success-history",
        "/api/projects/corrupt-json/duration-control",
        "/api/projects/corrupt-json/quality-before-render",
        "/api/projects/corrupt-json/creator-pack",
        "/api/projects/corrupt-json/workflow-guard",
        "/api/projects/corrupt-json/dashboard-state",
        "/api/projects/corrupt-json/product-readiness",
        "/api/projects/corrupt-json/ai-quality-audit",
        "/api/projects/corrupt-json/candidates",
        "/api/projects/corrupt-json/segments",
    ]
    for path in paths:
        response = client.get(path, headers=auth_headers())
        assert response.status_code < 500, (path, response.text)

    candidates = client.get("/api/projects/corrupt-json/candidates", headers=auth_headers()).json()
    segments = client.get("/api/projects/corrupt-json/segments", headers=auth_headers()).json()
    assert candidates[0]["start"] is None
    assert segments[0]["score"] is None


def test_frozen_twitch_module_command_uses_engine_dispatch(monkeypatch):
    from backend.src.highlight_studio.integrations.twitch import source

    monkeypatch.setattr(source.sys, "frozen", True, raising=False)
    monkeypatch.setattr(source.importlib.util, "find_spec", lambda name: object())
    command = source._python_module_cmd("yt_dlp", "yt-dlp")
    assert command == [source.sys.executable, "--run-module", "yt_dlp"]
