from pathlib import Path

from fastapi.testclient import TestClient

from backend import main


def auth_headers():
    return {"x-local-token": main.LOCAL_AUTH_TOKEN}


def test_clip_preview_rejects_invalid_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    project = tmp_path / "player01"
    project.mkdir()
    (project / "project.json").write_text('{"id":"player01"}', encoding="utf-8")
    (project / "source.mp4").write_bytes(b"not-a-video")
    monkeypatch.setattr(main, "source_video_path", lambda _d: project / "source.mp4")
    client = TestClient(main.app)
    response = client.post(
        "/api/projects/player01/clip-preview",
        headers=auth_headers(),
        json={"start": 20, "end": 10},
    )
    assert response.status_code == 422


def test_clip_preview_returns_cached_browser_safe_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    project = tmp_path / "player02"
    project.mkdir()
    source = project / "source.mp4"
    source.write_bytes(b"source-data")
    (project / "project.json").write_text('{"id":"player02"}', encoding="utf-8")
    monkeypatch.setattr(main, "source_video_path", lambda _d: source)
    monkeypatch.setattr(main, "video_duration", lambda _p: 120.0)
    monkeypatch.setattr(main, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(main, "video_info", lambda _p: {"duration": 10.0, "width": 1280, "height": 720})

    calls = []

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"x" * 5000)
        return Result()

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    client = TestClient(main.app)
    payload = {"start": 10, "end": 20}
    first = client.post("/api/projects/player02/clip-preview", headers=auth_headers(), json=payload)
    second = client.post("/api/projects/player02/clip-preview", headers=auth_headers(), json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["url"].endswith(".mp4")
    assert first.json()["cache_key"] == second.json()["cache_key"]
    assert len(calls) == 1
