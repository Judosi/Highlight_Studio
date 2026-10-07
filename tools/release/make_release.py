from __future__ import annotations

import fnmatch
import hashlib
import json
import sys
import zipfile
from pathlib import Path

try:
    from ._identity import ROOT, load_identity
    from .release_layout import REQUIRED_RELEASE_FILES
except ImportError:  # direct script execution
    from _identity import ROOT, load_identity
    from release_layout import REQUIRED_RELEASE_FILES
_IDENTITY = load_identity()
RELEASE_VERSION = _IDENTITY["version"]
RELEASE_ROOT_NAME = _IDENTITY["root"]
DEFAULT_OUT = ROOT.parent / f"{RELEASE_ROOT_NAME}.zip"

EXCLUDE_DIRS = {
    ".git",
    ".highlight_studio",
    ".pytest_cache",
    ".tmp_runtime_data",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    ".venv_test",
    ".desktop-build-venv",
    ".desktop-test-venv",
    "build",
    "venv",
    "env",
    "node_modules",
    "projects",
    "projects_web",
    "__pycache__",
    "transcript_chunks",
    "render_parts",
    "frames",
    "preview",
    "hls",
    "outputs",
    "exports",
}
EXCLUDE_NAMES = {
    ".coverage",
    "coverage.json",
    "coverage-final.json",
    "local_auth_token.txt",
    "jobs.sqlite3",
    "startup.log",
    "RELEASE_CHECKSUMS.json",
    "--help",
    "--help.sha256",
    ".env",
    ".env.generated",
    "launch_config.json",
}
EXCLUDE_PATTERNS = {
    "*.pyc",
    "*.pyo",
    "coverage*.json",
    "htmlcov*",
    "*.sqlite3",
    "*.log",
    "*.tmp",
    "*.part",
    "*.wav",
    "*.mp4",
    "*.mkv",
    "*.mov",
    "*.webm",
    "*.avi",
    "*.ts",
    "*.pfx",
    "*.p12",
    "*.pem",
    "*private_key*.json",
}


def should_include(path: Path) -> bool:
    if path.is_symlink():
        return False
    rel_parts = path.relative_to(ROOT).parts
    if rel_parts[:3] == ("vendor", "ffmpeg", "bin"):
        return False
    if any(part in EXCLUDE_DIRS for part in rel_parts):
        return False
    if path.name in EXCLUDE_NAMES:
        return False
    if any(fnmatch.fnmatch(path.name.lower(), pattern.lower()) for pattern in EXCLUDE_PATTERNS):
        return False
    return path.is_file()


def verify_required_files() -> list[str]:
    return sorted(item for item in REQUIRED_RELEASE_FILES if not (ROOT / item).exists())


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from tools.diagnostics.verify_release_identity import verify
    problems = verify(ROOT)
    if problems:
        print("ERROR: release identity/build validation failed:\n" + "\n".join(problems))
        return 2
    missing = verify_required_files()
    if missing:
        print("ERROR: release is incomplete:")
        for item in missing:
            print(f"  - {item}")
        return 2

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out = out.resolve()
    if out.is_relative_to(ROOT.resolve()):
        print("ERROR: output archive must be outside the source tree")
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = out.with_suffix(out.suffix + ".tmp")

    included = 0
    checksums: dict[str, str] = {}
    with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            if not should_include(path):
                continue
            rel = path.relative_to(ROOT).as_posix()
            archive.write(path, f"{RELEASE_ROOT_NAME}/{rel}")
            checksums[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            included += 1
        manifest = {
            "release": RELEASE_ROOT_NAME,
            "version": RELEASE_VERSION,
            "algorithm": "SHA-256",
            "file_count": included,
            "files": checksums,
        }
        archive.writestr(
            f"{RELEASE_ROOT_NAME}/RELEASE_CHECKSUMS.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        )

    # Preserve the previous archive if creating the replacement fails.
    staging.replace(out)
    archive_sha256 = hashlib.sha256(out.read_bytes()).hexdigest()
    out.with_suffix(out.suffix + ".sha256").write_text(f"{archive_sha256}  {out.name}\n", encoding="utf-8")
    print(f"OK: {out} ({included + 1} files, {out.stat().st_size / 1024 / 1024:.2f} MB, sha256={archive_sha256})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
