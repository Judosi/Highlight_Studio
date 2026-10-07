from __future__ import annotations

import argparse
import secrets
from pathlib import Path

REQUIRED = {"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_PORT"}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def ensure_env(path: Path, port: int = 54329) -> dict[str, str]:
    values = parse_env(path)
    values.setdefault("POSTGRES_DB", "highlight_studio_web")
    values.setdefault("POSTGRES_USER", "highlight")
    values.setdefault("POSTGRES_PASSWORD", secrets.token_hex(24))
    values.setdefault("POSTGRES_PORT", str(port))
    if not REQUIRED.issubset(values) or len(values["POSTGRES_PASSWORD"]) < 24:
        raise ValueError("Local web environment is invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Generated automatically for local web mode. Do not share this file.\n"
        f"POSTGRES_DB={values['POSTGRES_DB']}\n"
        f"POSTGRES_USER={values['POSTGRES_USER']}\n"
        f"POSTGRES_PASSWORD={values['POSTGRES_PASSWORD']}\n"
        f"POSTGRES_PORT={values['POSTGRES_PORT']}\n"
    )
    path.write_text(content, encoding="utf-8")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--port", type=int, default=54329)
    args = parser.parse_args()
    values = ensure_env(args.path.resolve(), args.port)
    print(f"Local web database config ready: {args.path}")
    print(f"PostgreSQL: 127.0.0.1:{values['POSTGRES_PORT']}/{values['POSTGRES_DB']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
