import hashlib
import json
import zipfile

from tools.release.make_release import ROOT, should_include
from tools.release.release_layout import REQUIRED_RELEASE_BINARY_MIN_SIZES
from tools.release.verify_archive import (
    EXPECTED_ROOT_NAME,
    EXPECTED_VERSION,
    REQUIRED_RELATIVE_FILES,
    is_safe_member_name,
    validate_archive,
)


def _required_members() -> dict[str, bytes]:
    return {name: f"fixture:{name}".encode("utf-8") for name in REQUIRED_RELATIVE_FILES}


def test_release_builder_excludes_tool_caches_and_coverage_reports() -> None:
    excluded = [
        ROOT / ".ruff_cache" / "cache-entry",
        ROOT / ".pytest_cache" / "cache-entry",
        ROOT / ".mypy_cache" / "cache-entry",
        ROOT / ".venv_test" / "lib" / "python",
        ROOT / "coverage-final.json",
        ROOT / "coverage.json",
        ROOT / "RELEASE_CHECKSUMS.json",
    ]
    for path in excluded:
        assert should_include(path) is False


def test_release_builder_keeps_required_source_files() -> None:
    assert should_include(ROOT / "backend" / "requirements.txt") is True
    assert should_include(ROOT / "frontend" / "dist" / "index.html") is True


def _write_test_archive(path, members: dict[str, bytes], *, extra: dict[str, bytes] | None = None) -> None:
    root = f"{EXPECTED_ROOT_NAME}/"
    checksums = {name: hashlib.sha256(data).hexdigest() for name, data in members.items()}
    manifest = {
        "release": EXPECTED_ROOT_NAME,
        "version": EXPECTED_VERSION,
        "algorithm": "SHA-256",
        "file_count": len(checksums),
        "files": checksums,
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(root + name, data)
        archive.writestr(root + "RELEASE_CHECKSUMS.json", json.dumps(manifest))
        for name, data in (extra or {}).items():
            archive.writestr(name, data)


def test_archive_member_validation_rejects_traversal_and_absolute_paths() -> None:
    assert is_safe_member_name("release/backend/app.py") is True
    assert is_safe_member_name("../outside.txt") is False
    assert is_safe_member_name("release/../outside.txt") is False
    assert is_safe_member_name("/absolute.txt") is False
    assert is_safe_member_name("C:/absolute.txt") is False
    assert is_safe_member_name(r"release\\ambiguous.txt") is False


def test_release_verifier_rejects_unmanifested_extra_file(tmp_path) -> None:
    archive_path = tmp_path / "release.zip"
    required = _required_members()
    hidden = f"{EXPECTED_ROOT_NAME}/hidden-extra.txt"
    _write_test_archive(archive_path, required, extra={hidden: b"not in manifest"})
    bad, missing, mismatches = validate_archive(archive_path)
    assert not missing
    assert not mismatches
    assert f"unmanifested:{hidden}" in bad


def test_release_verifier_accepts_complete_manifest(tmp_path) -> None:
    archive_path = tmp_path / "release.zip"
    required = _required_members()
    for name, minimum_size in REQUIRED_RELEASE_BINARY_MIN_SIZES.items():
        required[name] = b"\0" * minimum_size
    _write_test_archive(archive_path, required)
    assert validate_archive(archive_path) == ([], [], [])


def test_release_verifier_rejects_placeholder_turbo_binaries(tmp_path) -> None:
    archive_path = tmp_path / "release.zip"
    _write_test_archive(archive_path, _required_members())
    bad, missing, mismatches = validate_archive(archive_path)
    assert not missing
    assert not mismatches
    for name in REQUIRED_RELEASE_BINARY_MIN_SIZES:
        assert any(item.startswith(f"undersized-release-binary:{name}:") for item in bad)


def test_release_builder_excludes_symlinks(tmp_path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("secret", encoding="utf-8")
    link = ROOT / "temporary_release_test_link"
    try:
        link.symlink_to(target)
        assert should_include(link) is False
    finally:
        link.unlink(missing_ok=True)


def test_archive_member_validation_rejects_windows_ambiguous_names() -> None:
    assert is_safe_member_name("release/AUX.txt") is False
    assert is_safe_member_name("release/name:stream.txt") is False
    assert is_safe_member_name("release/trailing-dot.") is False
    assert is_safe_member_name("release/control\x01.txt") is False


def test_release_verifier_rejects_manifest_root_mismatch(tmp_path) -> None:
    archive_path = tmp_path / "release.zip"
    members = _required_members()
    checksums = {name: hashlib.sha256(data).hexdigest() for name, data in members.items()}
    manifest = {
        "release": "different",
        "version": EXPECTED_VERSION,
        "algorithm": "SHA-256",
        "file_count": len(checksums),
        "files": checksums,
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, data in members.items():
            archive.writestr(f"{EXPECTED_ROOT_NAME}/" + name, data)
        archive.writestr(f"{EXPECTED_ROOT_NAME}/RELEASE_CHECKSUMS.json", json.dumps(manifest))
    bad, missing, mismatches = validate_archive(archive_path)
    assert "release-root-mismatch" in bad
    assert not missing
    assert not mismatches


def test_release_verifier_rejects_non_hex_checksum(tmp_path) -> None:
    archive_path = tmp_path / "release.zip"
    members = _required_members()
    checksums = {name: hashlib.sha256(data).hexdigest() for name, data in members.items()}
    checksums["README.md"] = "z" * 64
    manifest = {
        "release": EXPECTED_ROOT_NAME,
        "version": EXPECTED_VERSION,
        "algorithm": "SHA-256",
        "file_count": len(checksums),
        "files": checksums,
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, data in members.items():
            archive.writestr(f"{EXPECTED_ROOT_NAME}/" + name, data)
        archive.writestr(f"{EXPECTED_ROOT_NAME}/RELEASE_CHECKSUMS.json", json.dumps(manifest))
    bad, _, _ = validate_archive(archive_path)
    assert "invalid-checksum:README.md" in bad


def test_release_verifier_rejects_second_top_level_root(tmp_path) -> None:
    archive_path = tmp_path / "release.zip"
    required = _required_members()
    _write_test_archive(archive_path, required, extra={"other/file.txt": b"unexpected"})
    bad, _, _ = validate_archive(archive_path)
    assert "unexpected-root:other/file.txt" in bad


def test_hybrid_build_contract_keeps_python_313_and_dev_tools() -> None:
    requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
    build_requirements = (ROOT / "backend" / "requirements-build.txt").read_text(encoding="utf-8")
    spec = (ROOT / "tools" / "desktop" / "HighlightStudioEngine.spec").read_text(encoding="utf-8")
    assert 'audioop-lts==0.2.2; python_version >= "3.13"' in requirements
    assert "-r requirements.txt" in build_requirements
    assert "requirements-dev.txt" in (ROOT / "scripts" / "windows" / "build_hybrid_release.ps1").read_text(encoding="utf-8")
    assert '"audioop"' in spec
    assert 'collect_data_files("faster_whisper", includes=["assets/*.onnx"])' in spec
    assert 'for package in ["ctranslate2", "av", "onnxruntime", "mediapipe"]' in spec
    assert 'collect_data_files("mediapipe")' in spec
    assert "blaze_face_short_range.tflite" in spec
    assert '"onnxruntime.capi._pybind_state"' in spec
    assert "console=False" in spec


def test_hybrid_source_release_excludes_downloaded_ffmpeg_payload() -> None:
    assert should_include(ROOT / "vendor" / "ffmpeg" / "README.md") is True
    assert should_include(ROOT / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe") is False


def test_source_launcher_has_an_honest_ffmpeg_prerequisite_gate() -> None:
    launcher = (ROOT / "scripts" / "windows" / "run_windows.bat").read_text(encoding="utf-8")
    assert "where ffmpeg" in launcher
    assert "where ffprobe" in launcher
    assert "commands\\setup\\INSTALL_FFMPEG.bat" in launcher
    assert (ROOT / "commands/setup/INSTALL_FFMPEG.bat").is_file()


def test_electron_builder_never_implicitly_publishes_ci_artifacts() -> None:
    package = json.loads((ROOT / "desktop" / "electron" / "package.json").read_text(encoding="utf-8"))
    assert "--publish never" in package["scripts"]["dist:win"]
    assert "publisherName" not in package["build"]["win"]


def test_release_builder_excludes_launch_secrets() -> None:
    excluded = [
        ROOT / "launch" / "launch_config.json",
        ROOT / "deploy" / ".env",
        ROOT / "deploy" / ".env.generated",
        ROOT / "secrets" / "windows-signing.pfx",
        ROOT / "secrets" / "license_private_key.json",
    ]
    for path in excluded:
        assert should_include(path) is False


def test_archive_rejects_windows_case_collisions(tmp_path):
    archive_path = tmp_path / 'case.zip'
    _write_test_archive(archive_path, _required_members(),
                        extra={f'{EXPECTED_ROOT_NAME}/readme.md': b'conflicting Windows path'})
    bad, _, _ = validate_archive(archive_path)
    assert any(item.startswith('case-collision:') for item in bad)


def test_archive_rejects_unsafe_directory_entries(tmp_path):
    archive_path = tmp_path / 'directory.zip'
    _write_test_archive(archive_path, _required_members(), extra={'../outside/': b''})
    bad, _, _ = validate_archive(archive_path)
    assert 'unsafe:../outside/' in bad
