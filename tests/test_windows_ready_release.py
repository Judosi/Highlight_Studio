from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tools.desktop.verify_portable_zip import verify_portable_zip


ROOT = Path(__file__).resolve().parents[1]


def test_windows_build_creates_frontend_before_packaged_tests() -> None:
    script = (ROOT / "scripts/windows/build_hybrid_release.ps1").read_text(encoding="utf-8")

    build = script.index('Run-Step "Build frontend"')
    assert build < script.index('Run-Step "Backend tests"')
    assert build < script.index('Run-Step "Frontend tests"')


def test_windows_build_uses_only_checksum_pinned_runtime_downloads() -> None:
    script = (ROOT / "scripts/windows/build_hybrid_release.ps1").read_text(encoding="utf-8")
    ffmpeg = (ROOT / "scripts/windows/fetch_ffmpeg.ps1").read_text(encoding="utf-8")
    twitch = (ROOT / "scripts/windows/fetch_twitchdownloader.ps1").read_text(encoding="utf-8")

    assert "AllowUnverifiedFfmpegDownload" not in script
    assert 'Run-Step "Prepare checksum-pinned bundled FFmpeg"' in script
    assert 'Run-Step "Prepare checksum-pinned TwitchDownloaderCLI"' in script
    assert "autobuild-2026-10-08-13-05" in ffmpeg
    assert "cf94becb7d17ded5aab4f84e5e01f1e17550f9d4b1a8552665badbd0683f61c4" in ffmpeg
    assert "if (!$Sha256)" in ffmpeg and "throw" in ffmpeg
    assert "-version | Select-Object -First 1" not in ffmpeg
    assert "TwitchDownloaderCLI-1.56.5-Windows-x64.zip" in twitch
    assert "8b1b0695f2b1b6bf0d2535fab4b84032951cded8cf4078dfdf4d58e391c813a0" in twitch
    assert "if (!$Sha256)" in twitch and "throw" in twitch
    assert "Start-Process -FilePath $Executable" in twitch


def test_tracked_aria2_binary_matches_pinned_official_release() -> None:
    provenance = json.loads((ROOT / "vendor/aria2/SOURCE.json").read_text(encoding="utf-8"))
    assert provenance["version"] == "1.37.0"
    assert provenance["archive_sha256"] == "67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288"
    assert provenance["aria2c_sha256"] == "be2099c214f63a3cb4954b09a0becd6e2e34660b886d4c898d260febfe9d70c2"


def test_windows_workflow_is_manual_and_publishes_only_verified_zip() -> None:
    workflow = (ROOT / ".github/workflows/windows-desktop-build.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "softprops/action-gh-release" not in workflow
    assert "package_portable_release.ps1" in workflow
    assert "test_portable_release.ps1" in workflow
    assert "verify_portable_zip.py" in workflow
    assert "build/desktop/portable/*.zip" in workflow
    assert "PYTHONUTF8: '1'" in workflow
    assert "PYTHONIOENCODING: utf-8" in workflow


def test_unsigned_build_keeps_public_license_verification_key() -> None:
    build_script = (ROOT / "scripts/windows/build_hybrid_release.ps1").read_text(encoding="utf-8")
    config = json.loads((ROOT / "desktop/electron/paid-beta-channel.json").read_text(encoding="utf-8"))

    assert config["licensePublicKeyB64"]
    assert "ExistingPaidBeta" in build_script
    assert "licensePublicKeyB64 = Get-ConfigValue" in build_script


def test_standalone_engine_probes_packaged_media_runtime() -> None:
    entry = (ROOT / "backend/desktop_entry.py").read_text(encoding="utf-8")
    verifier = (ROOT / "tools/desktop/verify_engine_runtime.py").read_text(encoding="utf-8")
    spec = (ROOT / "tools/desktop/HighlightStudioEngine.spec").read_text(encoding="utf-8")

    assert "--probe-release-runtime" in entry
    assert "detect_faces" in entry
    assert 'collect_data_files("mediapipe")' in spec
    assert "blaze_face_short_range.tflite" in spec
    assert 'read_json(opener, f"http://127.0.0.1:{port}/api/twitch/tools")' in verifier


def test_packaged_electron_archive_has_secret_and_license_validation() -> None:
    verifier = (ROOT / "tools/desktop/verify_electron_asar.mjs").read_text(encoding="utf-8")
    layout = (ROOT / "scripts/windows/verify_packaged_layout.ps1").read_text(encoding="utf-8")

    assert "licensePublicKeyB64" in verifier
    assert "local_auth_token.txt" in verifier
    assert "private_keys_present: false" in verifier
    assert "verify_electron_asar.mjs" in layout
    assert "Frontend output checksum mismatch" in layout


def _write_portable_fixture(path: Path, *, extra_name: str | None = None) -> None:
    executable = b"MZ" + (b"portable" * 32)
    executable_sha = __import__("hashlib").sha256(executable).hexdigest()
    manifest = {
        "schema_version": 1,
        "app": "Highlight Studio",
        "version": "11.2.7",
        "platform": "windows-x64",
        "build_kind": "unsigned-test",
        "entrypoint": "Highlight-Studio-Portable-11.2.7-x64.exe",
        "files": [
            {
                "path": "Highlight-Studio-Portable-11.2.7-x64.exe",
                "size": len(executable),
                "sha256": executable_sha,
            }
        ],
    }
    prefix = "Highlight Studio 11.2.7 Portable/"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(prefix + manifest["entrypoint"], executable)
        archive.writestr(prefix + "BUILD_MANIFEST.json", json.dumps(manifest))
        archive.writestr(prefix + "README_FIRST_RU.txt", "Запустите portable EXE")
        if extra_name:
            archive.writestr(prefix + extra_name, "forbidden")


def test_portable_zip_verifier_accepts_minimal_safe_archive(tmp_path: Path) -> None:
    archive = tmp_path / "Highlight-Studio-11.2.7-Windows-x64-unsigned-test.zip"
    _write_portable_fixture(archive)

    report = verify_portable_zip(archive)

    assert report["ok"] is True
    assert report["build_kind"] == "unsigned-test"
    assert report["entrypoint"].endswith(".exe")


@pytest.mark.parametrize(
    "forbidden",
    [".env", "license-private.pem", "admin-token.txt", "projects/real-user/project.json"],
)
def test_portable_zip_verifier_rejects_secrets_and_user_data(tmp_path: Path, forbidden: str) -> None:
    archive = tmp_path / "unsafe.zip"
    _write_portable_fixture(archive, extra_name=forbidden)

    with pytest.raises(ValueError, match="forbidden"):
        verify_portable_zip(archive)

