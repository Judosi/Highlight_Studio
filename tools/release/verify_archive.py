from __future__ import annotations

import hashlib
import json
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath
try:
    from ._identity import load_identity
    from .release_layout import REQUIRED_RELEASE_BINARY_MIN_SIZES, REQUIRED_RELEASE_FILES
except ImportError:  # direct script execution
    from _identity import load_identity
    from release_layout import REQUIRED_RELEASE_BINARY_MIN_SIZES, REQUIRED_RELEASE_FILES

FORBIDDEN_PARTS = {
    ".git",
    ".highlight_studio",
    ".pytest_cache",
    ".tmp_runtime_data",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    ".venv_test",
    "node_modules",
    "projects",
    "projects_web",
    "__pycache__",
    "render_parts",
    "transcript_chunks",
}
FORBIDDEN_NAMES = {
    "local_auth_token.txt",
    "jobs.sqlite3",
    ".coverage",
    "coverage.json",
    "coverage-final.json",
    ".env",
    ".env.generated",
    "launch_config.json",
}
FORBIDDEN_SUFFIXES = {".pfx", ".p12", ".pem"}
REQUIRED_RELATIVE_FILES = REQUIRED_RELEASE_FILES

_IDENTITY = load_identity()
EXPECTED_ROOT_NAME = _IDENTITY["root"]
EXPECTED_VERSION = _IDENTITY["version"]
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def is_safe_member_name(name: str) -> bool:
    """Reject traversal plus names ambiguous or invalid on supported Windows builds."""
    if not name or "\\" in name or name.startswith(("/", "\\")) or _DRIVE_PREFIX.match(name):
        return False
    if any(ord(char) < 32 for char in name):
        return False
    path = PurePosixPath(name)
    for part in path.parts:
        if part in {"", ".", ".."} or ":" in part or part.endswith((" ", ".")):
            return False
        if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
            return False
    return True


def validate_archive(archive_path: Path) -> tuple[list[str], list[str], list[str]]:
    bad: list[str] = []
    missing: list[str] = []
    mismatches: list[str] = []

    with zipfile.ZipFile(archive_path) as archive:
        seen_casefolded: set[str] = set()
        for info in archive.infolist():
            if not is_safe_member_name(info.filename):
                bad.append(f"unsafe:{info.filename}")
            folded = info.filename.rstrip("/").casefold()
            if folded in seen_casefolded:
                bad.append(f"case-collision:{info.filename}")
            seen_casefolded.add(folded)
        file_infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename for info in file_infos]

        for info in file_infos:
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if unix_mode and stat.S_ISLNK(unix_mode):
                bad.append(f"symlink:{info.filename}")
            if info.flag_bits & 0x1:
                bad.append(f"encrypted:{info.filename}")

        duplicates = sorted({name for name in names if names.count(name) > 1})
        bad.extend(f"duplicate:{name}" for name in duplicates)

        for name in names:
            if not is_safe_member_name(name):
                bad.append(f"unsafe:{name}")
                continue
            posix = PurePosixPath(name)
            if (
                set(posix.parts) & FORBIDDEN_PARTS
                or posix.name in FORBIDDEN_NAMES
                or posix.suffix.lower() in FORBIDDEN_SUFFIXES
                or "private_key" in posix.name.lower()
            ):
                bad.append(name)

        checksum_names = [name for name in names if PurePosixPath(name).name == "RELEASE_CHECKSUMS.json"]
        if len(checksum_names) != 1:
            bad.append(f"checksum-manifest-count:{len(checksum_names)}")
            return sorted(set(bad)), missing, mismatches

        checksum_name = checksum_names[0]
        checksum_parts = PurePosixPath(checksum_name).parts
        if len(checksum_parts) != 2 or checksum_parts[1] != "RELEASE_CHECKSUMS.json":
            bad.append(f"invalid-release-root:{checksum_name}")
            return sorted(set(bad)), missing, mismatches
        root_name = checksum_parts[0]
        root_prefix = f"{root_name}/"
        if root_name != EXPECTED_ROOT_NAME:
            bad.append(f"unexpected-release-root:{root_name}")

        info_by_name = {info.filename: info for info in file_infos}
        for relative_name, minimum_size in REQUIRED_RELEASE_BINARY_MIN_SIZES.items():
            member_name = root_prefix + relative_name
            info = info_by_name.get(member_name)
            if info is not None and info.file_size < minimum_size:
                bad.append(
                    f"undersized-release-binary:{relative_name}:"
                    f"{info.file_size}<{minimum_size}"
                )

        try:
            manifest = json.loads(archive.read(checksum_name))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            bad.append(f"invalid-checksum-manifest:{exc}")
            return sorted(set(bad)), missing, mismatches

        if not isinstance(manifest, dict):
            bad.append("invalid-checksum-manifest-type")
            return sorted(set(bad)), missing, mismatches
        if manifest.get("release") != root_name:
            bad.append("release-root-mismatch")
        if manifest.get("version") != EXPECTED_VERSION:
            bad.append("release-version-mismatch")
        expected_version = re.search(r"_v(\d{2})(\d{2})(\d+)_", root_name)
        if expected_version:
            version = ".".join(str(int(part)) for part in expected_version.groups())
            if manifest.get("version") != version:
                bad.append("release-version-mismatch")
        if str(manifest.get("algorithm", "")).upper().replace("_", "-") != "SHA-256":
            bad.append("unsupported-checksum-algorithm")

        checksums = manifest.get("files", {})
        if not isinstance(checksums, dict) or manifest.get("file_count") != len(checksums):
            bad.append("invalid-checksum-manifest-file-count")
            return sorted(set(bad)), missing, mismatches

        # A release must be a single top-level directory. This prevents a valid
        # manifest from hiding additional files under a second archive root.
        for name in names:
            if not name.startswith(root_prefix):
                bad.append(f"unexpected-root:{name}")

        required_members = {root_prefix + relative for relative in REQUIRED_RELATIVE_FILES}
        name_set = set(names)
        missing.extend(sorted(member.removeprefix(root_prefix) for member in required_members - name_set))

        expected_members: set[str] = set()
        for relative_name, expected in checksums.items():
            if not isinstance(relative_name, str) or not is_safe_member_name(relative_name):
                bad.append(f"unsafe-manifest-entry:{relative_name}")
                continue
            if relative_name == "RELEASE_CHECKSUMS.json":
                bad.append("manifest-must-not-hash-itself")
                continue
            if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
                bad.append(f"invalid-checksum:{relative_name}")
                continue
            member = root_prefix + relative_name
            expected_members.add(member)
            try:
                actual = hashlib.sha256(archive.read(member)).hexdigest()
            except KeyError:
                mismatches.append(f"missing:{relative_name}")
                continue
            if actual != expected.lower():
                mismatches.append(relative_name)

        actual_members = set(names) - {checksum_name}
        for extra in sorted(actual_members - expected_members):
            bad.append(f"unmanifested:{extra}")
        for missing_member in sorted(expected_members - actual_members):
            mismatches.append(f"missing:{missing_member.removeprefix(root_prefix)}")

    return sorted(set(bad)), sorted(set(missing)), sorted(set(mismatches))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_archive.py RELEASE.zip")
        return 2
    archive_path = Path(sys.argv[1]).resolve()
    if not archive_path.exists():
        print(f"missing archive: {archive_path}")
        return 2

    try:
        bad, missing, mismatches = validate_archive(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        print(f"invalid archive: {exc}")
        return 1

    if mismatches:
        print("checksum mismatches:")
        print("\n".join(f"  - {item}" for item in mismatches[:20]))
        return 1
    if bad:
        print("forbidden or untrusted release entries:")
        print("\n".join(f"  - {item}" for item in bad[:20]))
        return 1
    if missing:
        print("missing required release entries:")
        print("\n".join(f"  - {item}" for item in missing))
        return 1

    with zipfile.ZipFile(archive_path) as archive:
        entry_count = len([info for info in archive.infolist() if not info.is_dir()])
    print(f"OK: {archive_path.name}, {entry_count} files, checksums complete, clean runtime data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
