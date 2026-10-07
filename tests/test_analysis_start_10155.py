from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

import highlight_studio.api.app as main


def test_legacy_safe_analysis_profile_is_migrated_instead_of_rejected():
    payload = main.default_settings()
    payload["analysis_profile"] = "safe"
    validated = main.validate_settings(payload)
    assert validated["analysis_profile"] == "fast"


def test_one_click_rejects_bad_prepared_source_before_starting_job(monkeypatch, tmp_path: Path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"not-a-real-video")
    project = {"source_type": "local", "settings": main.default_settings()}
    started = []

    monkeypatch.setattr(main, "project_dir", lambda _project_id: tmp_path)
    monkeypatch.setattr(main, "load_project", lambda _project_dir: project)
    monkeypatch.setattr(main, "source_video_path", lambda _project_dir: source)
    monkeypatch.setattr(
        main,
        "authoritative_source_gate",
        lambda _project_dir, deep_media_check=True: {"ok": False, "message": "Видео повреждено"},
    )
    monkeypatch.setattr(main, "start_background_job", lambda *args, **kwargs: started.append((args, kwargs)))

    with pytest.raises(HTTPException) as exc:
        main.start_one_click("p1")

    assert exc.value.status_code == 422
    assert exc.value.detail["message"] == "Видео повреждено"
    assert started == []


def test_one_click_rejects_failed_runtime_preflight_before_starting_job(monkeypatch, tmp_path: Path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    project = {"source_type": "local", "settings": main.default_settings()}
    started = []

    monkeypatch.setattr(main, "project_dir", lambda _project_id: tmp_path)
    monkeypatch.setattr(main, "load_project", lambda _project_dir: project)
    monkeypatch.setattr(main, "source_video_path", lambda _project_dir: source)
    monkeypatch.setattr(main, "authoritative_source_gate", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(
        main,
        "smart_preflight_report",
        lambda *_args, **_kwargs: {
            "can_start": False,
            "title": "Нельзя запускать — исправь ошибки",
            "recommendation": "Запусти Ollama",
            "errors": [{"label": "Ollama запущен", "ok": False}],
        },
    )
    monkeypatch.setattr(main, "start_background_job", lambda *args, **kwargs: started.append((args, kwargs)))

    with pytest.raises(HTTPException) as exc:
        main.start_one_click("p1")

    assert exc.value.status_code == 422
    assert exc.value.detail["recommendation"] == "Запусти Ollama"
    assert exc.value.detail["errors"][0]["label"] == "Ollama запущен"
    assert started == []


def test_one_click_starts_after_preflight_passes(monkeypatch, tmp_path: Path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    project = {"source_type": "local", "settings": main.default_settings()}
    calls = []

    monkeypatch.setattr(main, "project_dir", lambda _project_id: tmp_path)
    monkeypatch.setattr(main, "load_project", lambda _project_dir: project)
    monkeypatch.setattr(main, "source_video_path", lambda _project_dir: source)
    monkeypatch.setattr(main, "authoritative_source_gate", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(main, "smart_preflight_report", lambda *_args, **_kwargs: {"can_start": True})

    def fake_start(project_id, title, task, *, kind):
        calls.append((project_id, title, task, kind))
        return {"started": True, "project_id": project_id, "kind": kind}

    monkeypatch.setattr(main, "start_background_job", fake_start)
    result = main.start_one_click("p1")

    assert result["started"] is True
    assert result["kind"] == "one_click"
    assert len(calls) == 1
    assert calls[0][0] == "p1"
    assert calls[0][3] == "one_click"


def test_twitch_without_prepared_source_can_still_enter_one_click_prepare(monkeypatch, tmp_path: Path):
    missing_source = tmp_path / "input.mp4"
    project = {"source_type": "twitch", "settings": main.default_settings()}
    calls = []

    monkeypatch.setattr(main, "project_dir", lambda _project_id: tmp_path)
    monkeypatch.setattr(main, "load_project", lambda _project_dir: project)
    monkeypatch.setattr(main, "source_video_path", lambda _project_dir: missing_source)
    monkeypatch.setattr(main, "authoritative_source_gate", lambda *_args, **_kwargs: pytest.fail("source gate must wait until Twitch is prepared"))
    monkeypatch.setattr(main, "smart_preflight_report", lambda *_args, **_kwargs: pytest.fail("runtime preflight must wait until Twitch is prepared"))

    def fake_start(project_id, title, task, *, kind):
        calls.append((project_id, kind))
        return {"started": True, "project_id": project_id, "kind": kind}

    monkeypatch.setattr(main, "start_background_job", fake_start)
    result = main.start_one_click("twitch1")

    assert result["started"] is True
    assert calls == [("twitch1", "one_click")]


def test_ollama_probe_uses_short_cache_unless_forced(monkeypatch):
    class Response:
        ok = True
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"models": [{"name": main.DEFAULT_TEXT_MODEL}, {"name": main.DEFAULT_VISION_MODEL}]}

    import requests

    calls = []
    monkeypatch.setattr(requests, "get", lambda url, timeout: calls.append((url, timeout)) or Response())
    main._OLLAMA_PROBE_CACHE.update({"ts": 0.0, "url": "", "data": None})

    first = main._ollama_probe(force=True)
    second = main._ollama_probe()
    third = main._ollama_probe(force=True)

    assert first["ok"] is True and first["cached"] is False
    assert second["ok"] is True and second["cached"] is True
    assert third["ok"] is True and third["cached"] is False
    assert len(calls) == 2


def test_smart_preflight_light_mode_does_not_require_vision_or_twitch_tool_after_download(monkeypatch, tmp_path: Path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    main.write_json(tmp_path / "project.json", {"source_type": "twitch", "source_video_path": str(source)})
    settings = main.default_settings()
    settings.update({"visual_mode": "Лёгкий", "visual_scan_enabled": True})

    monkeypatch.setattr(main, "source_video_path", lambda _project_dir: source)
    monkeypatch.setattr(main, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(
        main,
        "_ollama_probe",
        lambda **_kwargs: {
            "ok": True,
            "models": [settings["text_model"]],
            "hint": "ok",
            "text_model_ok": True,
            "vision_model_ok": False,
        },
    )
    monkeypatch.setattr(main, "_module_probe", lambda *_args, **_kwargs: {"ok": True, "hint": "ok"})
    monkeypatch.setattr(main, "twitch_tool_status", lambda _settings: {"recommended_engine": "none", "tools": {}})
    monkeypatch.setattr(main, "video_duration", lambda _video: 3600.0)

    report = main.smart_preflight_report(tmp_path, settings)
    checks = {item["id"]: item for item in report["checks"]}

    assert checks["vision_model"]["required"] is False
    assert checks["vision_model"]["ok"] is True
    assert checks["twitch_downloader"]["required"] is False
    assert checks["twitch_downloader"]["ok"] is True
    assert report["can_start"] is True
