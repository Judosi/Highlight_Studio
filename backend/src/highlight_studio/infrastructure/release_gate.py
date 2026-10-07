from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..core.settings import APP_ROOT, APP_VERSION, DATA_DIR
from ..core.utils import read_json, write_json
from .runtime_state import stability_metrics

EVIDENCE_PATH = DATA_DIR / "mass_release_evidence.json"
BENCHMARK_REPORT_PATH = DATA_DIR / "quality_benchmark_report.json"
PLACEHOLDER_RE = re.compile(r"(?i)(replace[_ -]?me|example\.com|your[_ -]?(company|domain|email)|template|черновик|заполнить)")
REQUIRED_LEGAL_FILES = {
    "privacy": APP_ROOT / "docs" / "legal" / "PRIVACY_POLICY_RU.md",
    "terms": APP_ROOT / "docs" / "legal" / "TERMS_OF_USE_RU.md",
    "eula": APP_ROOT / "docs" / "legal" / "EULA_RU.md",
    "refund": APP_ROOT / "docs" / "legal" / "REFUND_POLICY_RU.md",
    "third_party": APP_ROOT / "docs" / "legal" / "THIRD_PARTY_NOTICES.md",
}
URL_ENV = {
    "license_server": "HIGHLIGHT_STUDIO_LICENSE_SERVER_URL",
    "checkout": "HIGHLIGHT_STUDIO_CHECKOUT_URL",
    "account": "HIGHLIGHT_STUDIO_ACCOUNT_URL",
    "support": "HIGHLIGHT_STUDIO_SUPPORT_URL",
    "privacy": "HIGHLIGHT_STUDIO_PRIVACY_URL",
    "terms": "HIGHLIGHT_STUDIO_TERMS_URL",
    "telemetry": "HIGHLIGHT_STUDIO_TELEMETRY_URL",
    "feedback": "HIGHLIGHT_STUDIO_FEEDBACK_URL",
    "crash_reports": "HIGHLIGHT_STUDIO_CRASH_REPORT_URL",
    "updates": "HIGHLIGHT_STUDIO_UPDATE_URL",
}
BENCHMARK_THRESHOLDS = {
    "moment_recall_percent": (80.0, "min"),
    "usable_candidates_percent": (65.0, "min"),
    "duplicate_candidates_percent": (5.0, "max"),
    "cut_context_percent": (10.0, "max"),
    "render_success_percent": (98.0, "min"),
    "project_success_percent": (98.0, "min"),
    "median_time_saved_percent": (50.0, "min"),
}


def _https(value: str) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except Exception:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password


def _valid_public_key(value: str) -> bool:
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(value + padding)
        return len(raw) == 32
    except Exception:
        return False


def _legal_check(name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"id": f"legal_{name}", "ok": False, "severity": "blocker", "message": f"Нет файла {path.name}."}
    text = path.read_text(encoding="utf-8", errors="ignore")
    placeholder = bool(PLACEHOLDER_RE.search(text))
    sufficient = len(text.strip()) >= 300
    ok = sufficient and not placeholder
    return {
        "id": f"legal_{name}",
        "ok": ok,
        "severity": "blocker" if not ok else "info",
        "message": f"{path.name}: {'готов' if ok else 'содержит шаблон или слишком короткий текст'}.",
    }


def _benchmark_checks(report: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    sample_count = int(report.get("vod_count") or 0)
    checks.append(
        {
            "id": "benchmark_sample",
            "ok": sample_count >= 30,
            "severity": "blocker" if sample_count < 30 else "info",
            "message": f"Benchmark: {sample_count} VOD; для mass release нужно минимум 30.",
        }
    )
    for key, (threshold, direction) in BENCHMARK_THRESHOLDS.items():
        raw = report.get(key)
        value = float(raw) if isinstance(raw, (int, float)) else None
        ok = value is not None and (value >= threshold if direction == "min" else value <= threshold)
        sign = "≥" if direction == "min" else "≤"
        checks.append(
            {
                "id": f"benchmark_{key}",
                "ok": ok,
                "severity": "blocker" if not ok else "info",
                "message": f"{key}: {value if value is not None else 'нет данных'}; цель {sign}{threshold}%.",
            }
        )
    return checks


def release_readiness() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "id": "license_public_key",
            "ok": _valid_public_key(os.environ.get("HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64", "")),
            "severity": "blocker",
            "message": "Публичный Ed25519-ключ лицензии настроен."
            if _valid_public_key(os.environ.get("HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64", ""))
            else "Не настроен корректный публичный ключ лицензии.",
        }
    )
    for name, env_name in URL_ENV.items():
        value = os.environ.get(env_name, "")
        ok = _https(value)
        checks.append(
            {
                "id": f"url_{name}",
                "ok": ok,
                "severity": "blocker" if name in {"license_server", "checkout", "support", "privacy", "terms", "updates"} else "warning",
                "message": f"{name}: {'HTTPS настроен' if ok else 'не настроен безопасный HTTPS URL'}.",
            }
        )
    checks.extend(_legal_check(name, path) for name, path in REQUIRED_LEGAL_FILES.items())
    website_files = [APP_ROOT / "website" / "index.html", APP_ROOT / "website" / "styles.css", APP_ROOT / "website" / "app.js"]
    website_ok = all(path.exists() and path.stat().st_size > 100 for path in website_files)
    checks.append(
        {
            "id": "sales_website",
            "ok": website_ok,
            "severity": "blocker",
            "message": "Сайт продажи присутствует." if website_ok else "Нет полной статической страницы продажи.",
        }
    )

    benchmark = read_json(BENCHMARK_REPORT_PATH, {}) or {}
    checks.extend(_benchmark_checks(benchmark if isinstance(benchmark, dict) else {}))

    evidence = read_json(EVIDENCE_PATH, {}) or {}
    local_stability = stability_metrics()
    windows_devices = int(evidence.get("windows_devices_tested") or 0)
    sessions = int(evidence.get("production_sessions") or local_stability.get("production_sessions") or 0)
    crashes = int(evidence.get("crashed_sessions") if "crashed_sessions" in evidence else local_stability.get("crashed_sessions") or 0)
    crash_free = round(((sessions - crashes) / sessions) * 100, 2) if sessions else 0.0
    signed = bool(evidence.get("artifacts_signed"))
    installer_verified = bool(evidence.get("installer_verified"))
    update_verified = bool(evidence.get("update_verified"))
    checks.extend(
        [
            {
                "id": "evidence_windows",
                "ok": windows_devices >= 10,
                "severity": "blocker",
                "message": f"Проверено Windows-устройств: {windows_devices}/10.",
            },
            {
                "id": "evidence_sessions",
                "ok": sessions >= 100,
                "severity": "blocker",
                "message": f"Пользовательских сессий: {sessions}/100.",
            },
            {
                "id": "evidence_crash_free",
                "ok": sessions >= 100 and crash_free >= 99.0,
                "severity": "blocker",
                "message": f"Crash-free sessions: {crash_free}% (цель ≥99%).",
            },
            {
                "id": "evidence_signed",
                "ok": signed,
                "severity": "blocker",
                "message": "Installer и собственные бинарники подписаны." if signed else "Нет подтверждения цифровой подписи.",
            },
            {
                "id": "evidence_installer",
                "ok": installer_verified,
                "severity": "blocker",
                "message": "Installer проверен на чистой Windows."
                if installer_verified
                else "Installer ещё не подтверждён на чистой Windows.",
            },
            {
                "id": "evidence_update",
                "ok": update_verified,
                "severity": "blocker",
                "message": "Обновление и сохранность проектов проверены."
                if update_verified
                else "Обновление/rollback ещё не подтверждены.",
            },
        ]
    )
    blockers = [item for item in checks if not item["ok"] and item["severity"] == "blocker"]
    warnings = [item for item in checks if not item["ok"] and item["severity"] == "warning"]
    passed = sum(1 for item in checks if item["ok"])
    score = round((passed / len(checks)) * 100) if checks else 0
    return {
        "ok": True,
        "app_version": APP_VERSION,
        "ready_for_mass_release": not blockers,
        "score": score,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "checks": checks,
        "benchmark": benchmark,
        "evidence": evidence,
        "message": "Mass release gate пройден."
        if not blockers
        else f"Mass release заблокирован: {len(blockers)} обязательных условий не выполнено.",
    }


def save_release_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "windows_devices_tested": max(0, int(payload.get("windows_devices_tested") or 0)),
        "production_sessions": max(0, int(payload.get("production_sessions") or 0)),
        "crashed_sessions": max(0, int(payload.get("crashed_sessions") or 0)),
        "artifacts_signed": bool(payload.get("artifacts_signed")),
        "installer_verified": bool(payload.get("installer_verified")),
        "update_verified": bool(payload.get("update_verified")),
        "notes": str(payload.get("notes") or "")[:2000],
    }
    if allowed["crashed_sessions"] > allowed["production_sessions"]:
        raise ValueError("crashed_sessions cannot exceed production_sessions")
    write_json(EVIDENCE_PATH, allowed)
    return release_readiness()
