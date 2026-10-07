import pytest

from tools.diagnostics.wait_and_open import release_matches, validated_local_url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("http://localhost:8000/", "http://localhost:8000"),
        ("http://[::1]:8000", "http://[::1]:8000"),
        ("https://127.0.0.2:9443/", "https://127.0.0.2:9443"),
    ],
)
def test_validated_local_url_accepts_only_loopback(value, expected):
    assert validated_local_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com",
        "http://127.0.0.1.evil.example:8000",
        "file:///tmp/index.html",
        "javascript:alert(1)",
        "http://user:pass@127.0.0.1:8000",
        "http://127.0.0.1:8000/app",
        "http://127.0.0.1:8000?next=/api/health",
        "http://127.0.0.1:8000/#section",
        "",
    ],
)
def test_validated_local_url_rejects_remote_or_unsafe_targets(value):
    with pytest.raises(ValueError):
        validated_local_url(value)


def test_release_matches_requires_backend_and_frontend_identity(monkeypatch):
    payloads = {
        "http://127.0.0.1:8144/api/health": {
            "ok": True,
            "app_version": "v10.14.4-layout-verified-redesign",
            "design_id": "studio-sidebar-layout-v4",
            "frontend_release_exists": True,
        },
        "http://127.0.0.1:8144/release.json": {
            "app_version": "v10.14.4-layout-verified-redesign",
            "design_id": "studio-sidebar-layout-v4",
        },
    }
    monkeypatch.setattr("tools.diagnostics.wait_and_open._read_json", payloads.__getitem__)
    assert (
        release_matches(
            "http://127.0.0.1:8144",
            "v10.14.4-layout-verified-redesign",
            "studio-sidebar-layout-v4",
        )[0]
        is True
    )
    payloads["http://127.0.0.1:8144/release.json"]["design_id"] = "old-horizontal-stepper"
    assert (
        release_matches(
            "http://127.0.0.1:8144",
            "v10.14.4-layout-verified-redesign",
            "studio-sidebar-layout-v4",
        )[0]
        is False
    )
