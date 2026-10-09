from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath


ALLOWED_BUILD_KINDS = {"signed", "unsigned-test"}
PRIVATE_KEY_SUFFIXES = {".key", ".pem", ".pfx", ".p12"}
FORBIDDEN_PARTS = {".env", "projects", "user-data", "userdata", "admin-tools"}
FORBIDDEN_NAMES = {
    "license.json",
    "trial.json",
    "local_auth_token.txt",
    "jobs.sqlite3",
    "activation.sqlite3",
    "admin-token.txt",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    member = PurePosixPath(name)
    if not name or name.startswith(("/", "\\")) or "\\" in name or member.is_absolute() or ".." in member.parts:
        raise ValueError(f"unsafe archive path: {name!r}")
    return member


def _check_forbidden(member: PurePosixPath) -> None:
    lowered_parts = tuple(part.lower() for part in member.parts)
    lowered_name = member.name.lower()
    if (
        any(part in FORBIDDEN_PARTS for part in lowered_parts)
        or lowered_name in FORBIDDEN_NAMES
        or member.suffix.lower() in PRIVATE_KEY_SUFFIXES
        or re.search(r"(?:private|secret|admin)[-_ ]?(?:key|token)", lowered_name)
    ):
        raise ValueError(f"forbidden secret, administrative tool, or user data in archive: {member}")


def verify_portable_zip(archive_path: Path) -> dict[str, object]:
    archive_path = archive_path.resolve()
    if not archive_path.is_file():
        raise ValueError(f"portable ZIP not found: {archive_path}")

    with zipfile.ZipFile(archive_path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if not infos:
            raise ValueError("portable ZIP is empty")
        names: dict[str, zipfile.ZipInfo] = {}
        roots: set[str] = set()
        for info in infos:
            member = _safe_member(info.filename)
            _check_forbidden(member)
            normalized = member.as_posix()
            if normalized.casefold() in {name.casefold() for name in names}:
                raise ValueError(f"duplicate archive path: {normalized}")
            if len(member.parts) < 2:
                raise ValueError(f"archive member is outside the portable root directory: {normalized}")
            roots.add(member.parts[0])
            names[normalized] = info
        if len(roots) != 1:
            raise ValueError(f"portable ZIP must contain exactly one root directory, got: {sorted(roots)}")

        root = next(iter(roots))
        manifest_name = f"{root}/BUILD_MANIFEST.json"
        readme_name = f"{root}/README_FIRST_RU.txt"
        if manifest_name not in names or readme_name not in names:
            raise ValueError("portable ZIP is missing BUILD_MANIFEST.json or README_FIRST_RU.txt")
        try:
            manifest = json.loads(archive.read(manifest_name).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid portable build manifest: {exc}") from exc

        if manifest.get("schema_version") != 1 or manifest.get("app") != "Highlight Studio":
            raise ValueError("unsupported portable build manifest identity")
        if manifest.get("platform") != "windows-x64":
            raise ValueError("portable build is not marked windows-x64")
        build_kind = str(manifest.get("build_kind") or "")
        if build_kind not in ALLOWED_BUILD_KINDS:
            raise ValueError(f"unsupported build_kind: {build_kind!r}")
        if build_kind == "unsigned-test" and "unsigned-test" not in archive_path.name.lower():
            raise ValueError("unsigned test ZIP filename must be explicitly marked unsigned-test")

        entrypoint = str(manifest.get("entrypoint") or "")
        if not entrypoint.lower().endswith(".exe") or PurePosixPath(entrypoint).name != entrypoint:
            raise ValueError("portable manifest entrypoint must be a root-level EXE filename")
        entry_name = f"{root}/{entrypoint}"
        if entry_name not in names:
            raise ValueError(f"portable entrypoint is missing: {entrypoint}")
        entry_data = archive.read(entry_name)
        if not entry_data.startswith(b"MZ"):
            raise ValueError("portable entrypoint is not a Windows PE executable")

        declared = manifest.get("files")
        if not isinstance(declared, list) or not declared:
            raise ValueError("portable manifest contains no file integrity records")
        verified: list[str] = []
        for record in declared:
            if not isinstance(record, dict):
                raise ValueError("invalid file integrity record")
            relative = _safe_member(str(record.get("path") or ""))
            if len(relative.parts) != 1:
                raise ValueError(f"manifest file must be root-level: {relative}")
            full_name = f"{root}/{relative.as_posix()}"
            if full_name not in names:
                raise ValueError(f"manifest file is missing from ZIP: {relative}")
            data = archive.read(full_name)
            if int(record.get("size", -1)) != len(data) or str(record.get("sha256") or "").lower() != _sha256(data):
                raise ValueError(f"integrity mismatch for {relative}")
            verified.append(relative.as_posix())
        if entrypoint not in verified:
            raise ValueError("portable entrypoint is not covered by the integrity manifest")

    return {
        "ok": True,
        "archive": str(archive_path),
        "root": root,
        "version": str(manifest.get("version") or ""),
        "build_kind": build_kind,
        "entrypoint": entrypoint,
        "verified_files": verified,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the ready-to-run Highlight Studio portable ZIP.")
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    report = verify_portable_zip(args.archive)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
