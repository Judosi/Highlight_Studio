from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def item(name: str, ok: bool, path: Path | None = None, hint: str = "") -> dict[str, object]:
    return {"name": name, "ok": bool(ok), "path": str(path) if path else "", "hint": hint}


def inspect(
    require_engine: bool = False,
    require_windows_bins: bool = False,
    require_update_url: bool = False,
    require_paid_beta_config: bool = False,
) -> dict[str, object]:
    engine_name = "HighlightStudioEngine.exe" if os.name == "nt" else "HighlightStudioEngine"
    engine = ROOT / "build" / "desktop" / "engine" / "HighlightStudioEngine" / engine_name
    ffmpeg = ROOT / "vendor" / "ffmpeg" / "bin" / ("ffmpeg.exe" if require_windows_bins or os.name == "nt" else "ffmpeg")
    ffprobe = ROOT / "vendor" / "ffmpeg" / "bin" / ("ffprobe.exe" if require_windows_bins or os.name == "nt" else "ffprobe")
    electron_package_path = ROOT / "desktop" / "electron" / "package.json"
    release_channel_path = ROOT / "desktop" / "electron" / "release-channel.json"
    paid_beta_path = ROOT / "desktop" / "electron" / "paid-beta-channel.json"
    try:
        electron_package = json.loads(electron_package_path.read_text(encoding="utf-8"))
    except Exception:
        electron_package = {}
    try:
        release_channel = json.loads(release_channel_path.read_text(encoding="utf-8"))
    except Exception:
        release_channel = {}
    try:
        paid_beta = json.loads(paid_beta_path.read_text(encoding="utf-8"))
    except Exception:
        paid_beta = {}
    updater_dependency = bool((electron_package.get("dependencies") or {}).get("electron-updater"))
    update_url = str(release_channel.get("stableUpdateUrl") or release_channel.get("updateUrl") or "").strip()
    paid_beta_ready = all(
        str(paid_beta.get(key) or "").strip()
        for key in ["licensePublicKeyB64", "licenseServerUrl", "checkoutUrl", "supportUrl", "privacyUrl", "termsUrl", "crashReportUrl"]
    )
    checks = [
        item("Python 3.10–3.13", (3, 10) <= sys.version_info[:2] < (3, 14), hint=sys.version.split()[0]),
        item("Frontend build", (ROOT / "frontend" / "dist" / "index.html").exists(), ROOT / "frontend" / "dist" / "index.html"),
        item(
            "Electron lock",
            (ROOT / "desktop" / "electron" / "package-lock.json").exists(),
            ROOT / "desktop" / "electron" / "package-lock.json",
        ),
        item(
            "TwitchDownloaderCLI",
            (ROOT / "vendor" / "twitchdownloadercli" / "TwitchDownloaderCLI.exe").exists(),
            ROOT / "vendor" / "twitchdownloadercli" / "TwitchDownloaderCLI.exe",
        ),
        item("aria2", (ROOT / "vendor" / "aria2" / "aria2c.exe").exists(), ROOT / "vendor" / "aria2" / "aria2c.exe"),
        item(
            "FFmpeg",
            ffmpeg.exists() or (not require_windows_bins and shutil.which("ffmpeg") is not None),
            ffmpeg,
            "Запусти scripts/windows/fetch_ffmpeg.ps1",
        ),
        item(
            "FFprobe",
            ffprobe.exists() or (not require_windows_bins and shutil.which("ffprobe") is not None),
            ffprobe,
            "Запусти scripts/windows/fetch_ffmpeg.ps1",
        ),
        item(
            "First-run wizard",
            (ROOT / "frontend" / "src" / "features" / "onboarding" / "FirstRunWizard.jsx").exists(),
            ROOT / "frontend" / "src" / "features" / "onboarding" / "FirstRunWizard.jsx",
        ),
        item("electron-updater", updater_dependency, electron_package_path, "Добавь production dependency electron-updater"),
        item(
            "Release channel config",
            release_channel_path.exists() and (bool(update_url) or not require_update_url),
            release_channel_path,
            "Укажи stableUpdateUrl для подписанного релиза",
        ),
        item(
            "Paid Beta channel config",
            paid_beta_path.exists() and (paid_beta_ready or not require_paid_beta_config),
            paid_beta_path,
            "Для подписанного релиза укажи public key, license, checkout, support, privacy, terms и crash URLs",
        ),
        item(
            "Update manifest tool",
            (ROOT / "tools" / "desktop" / "write_update_manifest.py").exists(),
            ROOT / "tools" / "desktop" / "write_update_manifest.py",
        ),
        item(
            "Windows artifact verifier",
            (ROOT / "scripts" / "windows" / "verify_windows_artifacts.ps1").exists(),
            ROOT / "scripts" / "windows" / "verify_windows_artifacts.ps1",
        ),
        item("Standalone engine", engine.exists() or not require_engine, engine, "Запусти scripts/windows/build_hybrid_release.ps1"),
    ]
    return {
        "ok": all(check["ok"] for check in checks),
        "platform": platform.platform(),
        "root": str(ROOT),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-engine", action="store_true")
    parser.add_argument("--require-windows-bins", action="store_true")
    parser.add_argument("--require-update-url", action="store_true")
    parser.add_argument("--require-paid-beta-config", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = inspect(args.require_engine, args.require_windows_bins, args.require_update_url, args.require_paid_beta_config)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for check in report["checks"]:
            mark = "OK" if check["ok"] else "MISSING"
            print(f"[{mark}] {check['name']}: {check['path'] or check['hint']}")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
