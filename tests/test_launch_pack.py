from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tools.launch.configure_launch import render_launch_files, validate_launch_config


def _valid_config() -> dict[str, object]:
    public = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key = base64.urlsafe_b64encode(public).decode().rstrip("=")
    return {
        "brand_name": "Highlight Studio",
        "publisher_name": "Highlight Studio Software",
        "legal_name": "ИП Тестовый Продавец",
        "country": "Россия",
        "support_email": "support@highlightstudio.test",
        "domain": "highlightstudio.test",
        "download_url": "https://download.highlightstudio.test/setup.exe",
        "checkout_url": "https://highlightstudio.test/buy",
        "account_url": "https://highlightstudio.test/account",
        "license_server_url": "https://api.highlightstudio.test",
        "support_url": "https://highlightstudio.test/support",
        "privacy_url": "https://highlightstudio.test/privacy",
        "terms_url": "https://highlightstudio.test/terms",
        "stable_update_url": "https://updates.highlightstudio.test/stable",
        "beta_update_url": "https://updates.highlightstudio.test/beta",
        "telemetry_url": "https://api.highlightstudio.test/telemetry",
        "feedback_url": "https://api.highlightstudio.test/feedback",
        "crash_report_url": "https://api.highlightstudio.test/crash-reports",
        "license_public_key_b64": key,
        "currency": "RUB",
        "creator_price": "990 ₽/мес.",
        "pro_price": "1990 ₽/мес.",
        "trial_days": 14,
        "max_devices": 2,
    }


def test_launch_config_rejects_placeholders_and_http() -> None:
    config = _valid_config()
    config["domain"] = "example.com"
    config["checkout_url"] = "http://example.com/buy"
    problems = validate_launch_config(config)
    fields = {item["field"] for item in problems}
    assert "domain" in fields
    assert "checkout_url" in fields


def test_launch_config_renders_public_files_without_private_key(tmp_path: Path) -> None:
    config = _valid_config()
    written = render_launch_files(config, tmp_path)
    assert len(written) == 5
    website = json.loads((tmp_path / "website/config.json").read_text(encoding="utf-8"))
    desktop = json.loads((tmp_path / "desktop/electron/paid-beta-channel.json").read_text(encoding="utf-8"))
    release = json.loads((tmp_path / "desktop/electron/release-channel.json").read_text(encoding="utf-8"))
    env_text = (tmp_path / "deploy/.env.generated").read_text(encoding="utf-8")
    assert website["downloadUrl"].startswith("https://")
    assert desktop["licenseServerUrl"] == "https://api.highlightstudio.test"
    assert release["stableUpdateUrl"].endswith("/stable")
    assert "HIGHLIGHT_STUDIO_LICENSE_PRIVATE_KEY_B64=REPLACE_WITH_SECRET" in env_text
    assert "private" not in json.dumps(website).lower()


def test_launch_pack_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    required = [
        "deploy/docker-compose.yml",
        "deploy/Caddyfile",
        "launch/launch_config.example.json",
        "tools/launch/configure_launch.py",
        "tools/launch/validate_launch.py",
        "docs/launch/MASTER_LAUNCH_PLAN_RU.md",
        ".github/workflows/deploy-website.yml",
        ".github/workflows/launch-gate.yml",
    ]
    assert all((root / path).exists() for path in required)
