from __future__ import annotations

from pathlib import Path

import highlight_studio.api.app as main


class _Logger:
    def __init__(self):
        self.statuses = []
        self.logs = []

    def heartbeat(self, *args, **kwargs):
        return None

    def log(self, message):
        self.logs.append(str(message))

    def set_status(self, state, progress, message, **extra):
        self.statuses.append((state, progress, message, extra))


def _capture_one_click_task(monkeypatch, tmp_path: Path, analyze_result):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    project = {"source_type": "local", "settings": main.default_settings()}
    captured = {}

    monkeypatch.setattr(main, "project_dir", lambda _project_id: tmp_path)
    monkeypatch.setattr(main, "load_project", lambda _project_dir: project)
    monkeypatch.setattr(main, "source_video_path", lambda _project_dir: source)
    monkeypatch.setattr(main, "authoritative_source_gate", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(main, "smart_preflight_report", lambda *_args, **_kwargs: {"can_start": True})
    monkeypatch.setattr(main, "analyze", lambda *_args, **_kwargs: analyze_result)
    monkeypatch.setattr(main, "pre_render_check", lambda *_args, **_kwargs: {"ok": True})

    def fake_start(project_id, title, task, *, kind):
        captured.update(project_id=project_id, title=title, task=task, kind=kind)
        return {"started": True, "project_id": project_id, "kind": kind}

    monkeypatch.setattr(main, "start_background_job", fake_start)
    result = main.start_one_click("p1")
    assert result["started"] is True
    return captured["task"], project["settings"]


def test_one_click_terminal_status_reports_real_candidate_count(monkeypatch, tmp_path: Path):
    task, settings = _capture_one_click_task(
        monkeypatch,
        tmp_path,
        {"candidates": [{"id": 1}, {"id": 2}], "segments": [{"id": 1}]},
    )
    logger = _Logger()
    task(tmp_path, settings, logger)
    state, progress, message, extra = logger.statuses[-1]
    assert state == "done"
    assert progress == 100
    assert "найдено 2 моментов" in message
    assert extra["candidate_count"] == 2
    assert extra["segment_count"] == 1
    assert extra["analysis_complete"] is True
    assert extra["manual_navigation"] is True


def test_one_click_terminal_status_does_not_claim_moments_when_zero(monkeypatch, tmp_path: Path):
    task, settings = _capture_one_click_task(
        monkeypatch,
        tmp_path,
        {"candidates": [], "segments": []},
    )
    logger = _Logger()
    task(tmp_path, settings, logger)
    _state, _progress, message, extra = logger.statuses[-1]
    assert "кандидатов найдено 0" in message
    assert "моменты найдены" not in message.lower()
    assert extra["candidate_count"] == 0
    assert extra["segment_count"] == 0
