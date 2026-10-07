from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_RE = re.compile(r"(?i)(replace[_ -]?me|example\.com|your[_ -]?(company|domain|email)|template|черновик|заполнить)")
URL_FIELDS = {
    "download_url",
    "checkout_url",
    "account_url",
    "license_server_url",
    "support_url",
    "privacy_url",
    "terms_url",
    "stable_update_url",
    "beta_update_url",
    "telemetry_url",
    "feedback_url",
    "crash_report_url",
}
REQUIRED_TEXT = {"brand_name", "publisher_name", "legal_name", "country", "support_email", "domain", "currency"}
REQUIRED_URLS = {
    "download_url",
    "checkout_url",
    "license_server_url",
    "support_url",
    "privacy_url",
    "terms_url",
    "stable_update_url",
}


def _https(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except Exception:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password


def _valid_email(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", text)) and not PLACEHOLDER_RE.search(text)


def _valid_public_key(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or PLACEHOLDER_RE.search(text):
        return False
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except Exception:
        return False
    return len(raw) == 32


def validate_launch_config(config: dict[str, Any]) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    for key in sorted(REQUIRED_TEXT):
        value = str(config.get(key) or "").strip()
        if not value or PLACEHOLDER_RE.search(value):
            problems.append({"field": key, "message": "Поле не заполнено или содержит шаблон."})
    if config.get("support_email") and not _valid_email(config.get("support_email")):
        problems.append({"field": "support_email", "message": "Нужен реальный email поддержки."})
    domain = str(config.get("domain") or "").strip().lower()
    if domain and ("/" in domain or ":" in domain or "." not in domain):
        problems.append({"field": "domain", "message": "Укажи только домен без https:// и путей."})
    for key in sorted(URL_FIELDS):
        value = str(config.get(key) or "").strip()
        if key in REQUIRED_URLS and not value:
            problems.append({"field": key, "message": "Обязательный HTTPS URL не заполнен."})
        elif value and (not _https(value) or PLACEHOLDER_RE.search(value)):
            problems.append({"field": key, "message": "URL должен быть реальным HTTPS-адресом без логина и пароля."})
    if not _valid_public_key(config.get("license_public_key_b64")):
        problems.append({"field": "license_public_key_b64", "message": "Нужен публичный Ed25519-ключ длиной 32 байта в Base64URL."})
    try:
        trial_days = int(config.get("trial_days", 0))
    except (TypeError, ValueError):
        trial_days = 0
    if not 1 <= trial_days <= 60:
        problems.append({"field": "trial_days", "message": "Пробный период должен быть от 1 до 60 дней."})
    try:
        max_devices = int(config.get("max_devices", 0))
    except (TypeError, ValueError):
        max_devices = 0
    if not 1 <= max_devices <= 5:
        problems.append({"field": "max_devices", "message": "Для первого релиза разреши от 1 до 5 устройств."})
    for key in ("creator_price", "pro_price"):
        if not str(config.get(key) or "").strip() or PLACEHOLDER_RE.search(str(config.get(key) or "")):
            problems.append({"field": key, "message": "Укажи отображаемую цену тарифа."})
    return problems


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Launch config must be a JSON object")
    return data


def render_launch_files(config: dict[str, Any], root: Path = ROOT) -> list[Path]:
    problems = validate_launch_config(config)
    if problems:
        joined = "\n".join(f"- {item['field']}: {item['message']}" for item in problems)
        raise ValueError(f"Launch config is incomplete:\n{joined}")

    website_config = {
        "downloadUrl": config["download_url"],
        "checkoutUrl": config["checkout_url"],
        "privacyUrl": config["privacy_url"],
        "termsUrl": config["terms_url"],
        "supportUrl": config["support_url"],
        "creatorPrice": config["creator_price"],
        "proPrice": config["pro_price"],
    }
    paid_beta = {
        "licensePublicKeyB64": config["license_public_key_b64"],
        "licenseServerUrl": config["license_server_url"],
        "checkoutUrl": config["checkout_url"],
        "accountUrl": config.get("account_url", ""),
        "supportUrl": config["support_url"],
        "privacyUrl": config["privacy_url"],
        "termsUrl": config["terms_url"],
        "telemetryUrl": config.get("telemetry_url", ""),
        "feedbackUrl": config.get("feedback_url", ""),
        "trialDays": int(config["trial_days"]),
        "crashReportUrl": config.get("crash_report_url", ""),
    }
    release_channel = {
        "channel": "stable",
        "stableUpdateUrl": config["stable_update_url"],
        "betaUpdateUrl": config.get("beta_update_url", ""),
        "allowChannelSwitch": True,
        "notes": "Generated by tools/launch/configure_launch.py",
    }
    generated_env = "\n".join(
        [
            f"HIGHLIGHT_STUDIO_WEB_DOMAIN={config['domain']}",
            f"HIGHLIGHT_STUDIO_API_DOMAIN={urlparse(config['license_server_url']).netloc}",
            f"HIGHLIGHT_STUDIO_CADDY_EMAIL={config['support_email']}",
            f"HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64={config['license_public_key_b64']}",
            "HIGHLIGHT_STUDIO_LICENSE_PRIVATE_KEY_B64=REPLACE_WITH_SECRET",
            "HIGHLIGHT_STUDIO_LICENSE_ADMIN_TOKEN=REPLACE_WITH_SECRET",
            "HIGHLIGHT_STUDIO_PAYMENT_WEBHOOK_SECRET=REPLACE_WITH_SECRET",
            "",
        ]
    )
    summary = f"""# Сводка запуска Highlight Studio\n\n- Бренд: **{config["brand_name"]}**\n- Издатель: **{config["publisher_name"]}**\n- Продавец: **{config["legal_name"]}**\n- Страна: **{config["country"]}**\n- Домен: **{config["domain"]}**\n- Поддержка: **{config["support_email"]}**\n- Trial: **{config["trial_days"]} дней**\n- Устройств на лицензию: **{config["max_devices"]}**\n- Creator: **{config["creator_price"]}**\n- Pro: **{config["pro_price"]}**\n\nСекретные ключи намеренно не записываются в эту сводку. Перед публикацией запусти `python tools/launch/validate_launch.py --config launch/launch_config.json --strict`.\n"""

    outputs = {
        root / "website" / "config.json": json.dumps(website_config, ensure_ascii=False, indent=2) + "\n",
        root / "desktop" / "electron" / "paid-beta-channel.json": json.dumps(paid_beta, ensure_ascii=False, indent=2) + "\n",
        root / "desktop" / "electron" / "release-channel.json": json.dumps(release_channel, ensure_ascii=False, indent=2) + "\n",
        root / "deploy" / ".env.generated": generated_env,
        root / "docs" / "launch" / "GENERATED_LAUNCH_SUMMARY.md": summary,
    }
    written: list[Path] = []
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and generate Highlight Studio launch files")
    parser.add_argument("--config", type=Path, default=ROOT / "launch" / "launch_config.json")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 2
    problems = validate_launch_config(config)
    if problems:
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False, indent=2))
        return 2
    if args.validate_only:
        print(json.dumps({"ok": True, "message": "Launch config is valid"}, ensure_ascii=False, indent=2))
        return 0
    written = render_launch_files(config)
    print(json.dumps({"ok": True, "written": [str(path.relative_to(ROOT)) for path in written]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
