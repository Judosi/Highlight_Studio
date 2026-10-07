from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import http.cookiejar
import secrets
from urllib.parse import urlparse
from pathlib import Path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _validated_loopback_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Engine verification accepts loopback HTTP URLs only")


def read_json(opener: urllib.request.OpenerDirector, url: str) -> dict:
    _validated_loopback_url(url)
    with opener.open(url, timeout=2) as response:  # nosec B310
        if response.status != 200:
            raise RuntimeError(f"Unexpected HTTP status: {response.status}")
        return json.loads(response.read().decode("utf-8"))


def read_text(opener: urllib.request.OpenerDirector, url: str) -> str:
    _validated_loopback_url(url)
    with opener.open(url, timeout=2) as response:  # nosec B310
        if response.status != 200:
            raise RuntimeError(f"Unexpected HTTP status: {response.status}")
        return response.read().decode("utf-8", errors="replace")


def request_shutdown(opener: urllib.request.OpenerDirector, url: str, token: str) -> dict:
    _validated_loopback_url(url)
    request = urllib.request.Request(
        url,
        method="POST",
        headers={"X-Desktop-Shutdown-Token": token},
    )
    with opener.open(request, timeout=3) as response:  # nosec B310
        if response.status != 200:
            raise RuntimeError(f"Unexpected shutdown HTTP status: {response.status}")
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch and verify the packaged Highlight Studio engine.")
    parser.add_argument("engine", type=Path)
    parser.add_argument("expected_version")
    parser.add_argument("--app-root", type=Path, default=Path.cwd())
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    engine = args.engine.resolve()
    if not engine.is_file():
        raise SystemExit(f"Engine executable not found: {engine}")

    port = free_port()
    with tempfile.TemporaryDirectory(prefix="highlight-studio-engine-check-") as temp_dir:
        temp = Path(temp_dir)
        env = os.environ.copy()
        shutdown_token = secrets.token_urlsafe(32)
        env.update(
            {
                "HIGHLIGHT_STUDIO_APP_ROOT": str(args.app_root.resolve()),
                "HIGHLIGHT_STUDIO_DATA_DIR": str(temp / "data"),
                "HIGHLIGHT_STUDIO_PROJECTS_DIR": str(temp / "projects"),
                "HIGHLIGHT_STUDIO_PORT": str(port),
                "HIGHLIGHT_STUDIO_LOG_LEVEL": "warning",
                "HIGHLIGHT_STUDIO_DESKTOP_SHUTDOWN_TOKEN": shutdown_token,
            }
        )
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        process = subprocess.Popen(  # noqa: S603 - executable is supplied by trusted build pipeline
            [str(engine)],
            env=env,
            cwd=str(args.app_root.resolve()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )
        output = ""
        cookie_jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
        try:
            deadline = time.monotonic() + args.timeout
            health: dict | None = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output = process.stdout.read() if process.stdout else ""
                    raise RuntimeError(f"Engine exited with code {process.returncode}.\n{output[-4000:]}")
                try:
                    health = read_json(opener, f"http://127.0.0.1:{port}/api/health")
                    break
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                    time.sleep(0.25)
            if health is None:
                raise RuntimeError("Engine did not become ready before timeout.")

            actual = str(health.get("app_version", ""))
            if not health.get("ok") or not actual.startswith(args.expected_version):
                raise RuntimeError(f"Packaged engine version mismatch: expected prefix {args.expected_version!r}, got {actual!r}.")

            root_html = read_text(opener, f"http://127.0.0.1:{port}/")
            if "Highlight Studio" not in root_html:
                raise RuntimeError("Packaged engine did not serve the production frontend.")
            onboarding = read_json(opener, f"http://127.0.0.1:{port}/api/onboarding")
            migrations = read_json(opener, f"http://127.0.0.1:{port}/api/migrations/status")
            recovery = read_json(opener, f"http://127.0.0.1:{port}/api/startup-recovery")
            runtime_components = read_json(opener, f"http://127.0.0.1:{port}/api/runtime-components")
            if not onboarding.get("ok") or "current_project_schema_version" not in migrations or not recovery.get("ok"):
                raise RuntimeError("Release-candidate startup APIs returned an invalid payload.")
            if not runtime_components.get("ok") or not runtime_components.get("whisper_vad", {}).get("ok"):
                raise RuntimeError(f"Packaged Whisper VAD runtime is incomplete: {runtime_components!r}")

            shutdown = request_shutdown(opener, f"http://127.0.0.1:{port}/api/desktop/shutdown", shutdown_token)
            if not shutdown.get("ok"):
                raise RuntimeError("Packaged engine rejected graceful desktop shutdown.")
            process.wait(timeout=15)
            session_state_path = temp / "data" / "session_state.json"
            session_state = json.loads(session_state_path.read_text(encoding="utf-8"))
            if session_state.get("clean_shutdown") is not True:
                raise RuntimeError("Packaged engine exited without recording a clean shutdown.")

            print(
                f"OK: packaged engine {actual} served the UI, verified Whisper VAD, "
                f"release-candidate APIs, and completed a graceful shutdown on loopback port {port}"
            )
            return 0
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
