from __future__ import annotations

import argparse
import base64
import hashlib
from datetime import datetime, timezone
from pathlib import Path


def installer_for(artifacts_dir: Path, version: str) -> Path:
    candidates = sorted(
        (item for item in artifacts_dir.glob(f"Highlight-Studio-{version}-*.exe") if "portable" not in item.name.lower()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"NSIS installer not found in {artifacts_dir}")
    return candidates[0]


def sha512_base64(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return base64.b64encode(digest.digest()).decode("ascii")


def write_manifest(artifacts_dir: Path, version: str) -> Path:
    installer = installer_for(artifacts_dir, version)
    checksum = sha512_base64(installer)
    release_date = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    text = (
        f"version: {version}\n"
        "files:\n"
        f"  - url: {installer.name}\n"
        f"    sha512: {checksum}\n"
        f"    size: {installer.stat().st_size}\n"
        f"path: {installer.name}\n"
        f"sha512: {checksum}\n"
        f"releaseDate: '{release_date}'\n"
    )
    output = artifacts_dir / "latest.yml"
    output.write_text(text, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Create electron-updater latest.yml for an NSIS installer")
    parser.add_argument("artifacts_dir", type=Path)
    parser.add_argument("version")
    args = parser.parse_args()
    output = write_manifest(args.artifacts_dir.resolve(), args.version)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
