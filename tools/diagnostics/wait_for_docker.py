from __future__ import annotations

import argparse
import subprocess
import time


def docker_ready() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    deadline = time.monotonic() + max(1, args.timeout)
    while time.monotonic() < deadline:
        if docker_ready():
            print("Docker is ready")
            return 0
        time.sleep(2)
    print("Docker daemon did not become ready in time")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
