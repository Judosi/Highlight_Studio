from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Mapping

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement

REQ_TO_MODULE = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "python-multipart": "multipart",
    "requests": "requests",
    "faster-whisper": "faster_whisper",
    "pydantic": "pydantic",
    "httpx": "httpx",
    "pytest": "pytest",
    "yt-dlp": "yt_dlp",
    "streamlink": "streamlink",
    # The distribution is named audioop-lts, but it intentionally provides
    # the import-compatible module name ``audioop`` on Python 3.13+.
    "audioop-lts": "audioop",
    # The distribution is named argon2-cffi, while applications import
    # the top-level package as ``argon2``.
    "argon2-cffi": "argon2",
}


def requirements_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_modules(path: Path, environment: Mapping[str, str] | None = None) -> list[str]:
    """Return import names for requirements that apply to this interpreter.

    Requirement markers must be evaluated before checking imports. Without
    this, a conditional dependency such as ``audioop-lts; python_version >=
    '3.13'`` was incorrectly checked on Python 3.10-3.12 and was also mapped
    to the non-existent import name ``audioop_lts``.
    """
    env = dict(default_environment())
    if environment:
        env.update(environment)

    modules: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement as exc:
            raise ValueError(f"Invalid requirement line: {line!r}") from exc
        if requirement.marker is not None and not requirement.marker.evaluate(env):
            continue
        package_name = requirement.name.lower()
        module = REQ_TO_MODULE.get(package_name, package_name.replace("-", "_"))
        if module not in seen:
            seen.add(module)
            modules.append(module)
    return modules


def modules_ok(modules: list[str]) -> tuple[bool, list[str]]:
    missing = [module for module in modules if importlib.util.find_spec(module) is None]
    return not missing, missing


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: check_deps_fast.py requirements.txt stamp.txt [--write]")
        return 2
    req = Path(sys.argv[1])
    stamp = Path(sys.argv[2])
    write = "--write" in sys.argv
    fp = requirements_fingerprint(req)
    try:
        modules = parse_modules(req)
    except (OSError, ValueError) as exc:
        print("requirements check failed:", exc)
        return 1
    ok, missing = modules_ok(modules)
    if not ok:
        print("missing modules:", ", ".join(missing))
        return 1
    if write:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(
            json.dumps(
                {"requirements_sha256": fp, "modules": modules},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("dependency stamp written")
        return 0
    if not stamp.exists():
        print("dependency stamp missing")
        return 1
    try:
        saved = json.loads(stamp.read_text(encoding="utf-8"))
    except Exception as exc:
        print("dependency stamp invalid:", exc)
        return 1
    if saved.get("requirements_sha256") != fp:
        print("requirements changed")
        return 1
    if saved.get("modules") != modules:
        print("dependency module set changed")
        return 1
    print("dependencies cached and importable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
