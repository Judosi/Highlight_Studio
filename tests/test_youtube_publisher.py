from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from backend.src.highlight_studio.integrations.youtube import publisher


class FakeResponse:
    def __init__(self, status_code: int, payload=None, headers=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


@pytest.fixture()
def youtube_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "youtube"
    monkeypatch.setattr(publisher, "YOUTUBE_DIR", root)
    monkeypatch.setattr(publisher, "CLIENT_SECRETS_PATH", root / "client_secrets.json")
    monkeypatch.setattr(publisher, "TOKEN_PATH", root / "token.json")
    monkeypatch.setattr(publisher, "ACCOUNT_PATH", root / "account.json")
    monkeypatch.setattr(publisher, "OAUTH_STATE_PATH", root / "oauth_state.json")
    return root


def client_secrets_bytes() -> bytes:
    return json.dumps(
        {
            "installed": {
                "client_id": "client.apps.googleusercontent.com",
                "client_secret": "secret",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
    ).encode()


def test_oauth_credentials_and_authorization_url(youtube_storage: Path):
    saved = publisher.save_client_secrets(client_secrets_bytes())
    assert saved["credentials_configured"] is True
    result = publisher.create_authorization_url("http://127.0.0.1:8000/api/youtube/oauth/callback")
    query = parse_qs(urlparse(result["authorization_url"]).query)
    assert query["client_id"] == ["client.apps.googleusercontent.com"]
    assert query["access_type"] == ["offline"]
    assert "youtube.upload" in query["scope"][0]
    state = json.loads(publisher.OAUTH_STATE_PATH.read_text(encoding="utf-8"))
    assert state["state"] == query["state"][0]


def test_complete_oauth_saves_channel(youtube_storage: Path, monkeypatch: pytest.MonkeyPatch):
    publisher.save_client_secrets(client_secrets_bytes())
    auth = publisher.create_authorization_url("http://127.0.0.1:8000/api/youtube/oauth/callback")
    state = parse_qs(urlparse(auth["authorization_url"]).query)["state"][0]

    monkeypatch.setattr(
        publisher.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(200, {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}),
    )
    monkeypatch.setattr(
        publisher.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(
            200,
            {"items": [{"id": "channel123", "snippet": {"title": "Test Channel", "thumbnails": {}}}]},
        ),
    )
    result = publisher.complete_oauth(
        state=state,
        authorization_response=f"http://127.0.0.1:8000/api/youtube/oauth/callback?state={state}&code=abc",
    )
    assert result["connected"] is True
    assert result["channel_title"] == "Test Channel"
    assert json.loads(publisher.TOKEN_PATH.read_text(encoding="utf-8"))["refresh_token"] == "refresh"


def test_raw_resumable_upload(youtube_storage: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    video = tmp_path / "short_01.mp4"
    video.write_bytes(b"x" * 4096)
    seen = {}

    def fake_post(url, **kwargs):
        seen["init_url"] = url
        seen["body"] = kwargs["json"]
        return FakeResponse(200, {}, {"Location": "https://upload.example/session"})

    def fake_put(url, **kwargs):
        seen["content_range"] = kwargs["headers"]["Content-Range"]
        return FakeResponse(200, {"id": "video123"})

    monkeypatch.setattr(publisher.requests, "post", fake_post)
    monkeypatch.setattr(publisher.requests, "put", fake_put)
    progress = []
    result = publisher._upload_one(
        "access",
        video,
        {
            "file_path": "outputs/short_01.mp4",
            "title": "Short title",
            "description": "Description",
            "tags": ["stream", "shorts"],
            "privacy_status": "private",
            "category_id": "22",
        },
        progress_callback=lambda value, message: progress.append(value),
    )
    assert result["video_id"] == "video123"
    assert result["youtube_url"] == "https://youtu.be/video123"
    assert seen["content_range"] == "bytes 0-4095/4096"
    assert seen["body"]["snippet"]["tags"] == ["stream", "shorts"]
    assert progress[-1] == 1.0


def test_invalid_credentials_are_rejected(youtube_storage: Path):
    with pytest.raises(publisher.YouTubeIntegrationError):
        publisher.save_client_secrets(b'{"installed":{"client_id":"x"}}')
