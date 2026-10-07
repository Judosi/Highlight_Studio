from __future__ import annotations

import ipaddress
import json
import sys
import time
import urllib.request
import webbrowser
from urllib.parse import urlencode, urlsplit, urlunsplit

_ALLOWED_PATHS = {"/", "/youtube-publisher.html"}
_DEFAULT_VERSION = "v11.2.7-quality-recovery-audit"
_DEFAULT_DESIGN = "studio-audited-v15"


def validated_local_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only a local HTTP(S) URL is allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials are not allowed in the application URL")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("The application URL must point to the local server root")

    host = parsed.hostname.rstrip(".").lower()
    is_loopback = host == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise ValueError("Only localhost/loopback URLs are allowed")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def validated_open_path(value: str) -> str:
    path = str(value or "/").strip() or "/"
    if path not in _ALLOWED_PATHS:
        raise ValueError("Unsupported application page")
    return path


def _read_json(url: str) -> dict:
    request = urllib.request.Request(  # nosec B310 - caller validates loopback URL
        url,
        headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=1.5) as response:  # nosec B310
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("Expected a JSON object")
    return payload


def release_matches(url: str, expected_version: str, expected_design: str) -> tuple[bool, str]:
    health = _read_json(url + "/api/health")
    actual_version = str(health.get("app_version") or "")
    actual_design = str(health.get("design_id") or "")
    if actual_version != expected_version:
        return False, f"backend version mismatch: expected {expected_version}, got {actual_version or 'empty'}"
    if actual_design != expected_design:
        return False, f"backend design mismatch: expected {expected_design}, got {actual_design or 'empty'}"
    if not health.get("frontend_release_exists"):
        return False, "backend cannot see frontend/dist/release.json"

    release = _read_json(url + "/release.json")
    if str(release.get("app_version") or "") != expected_version:
        return False, "frontend release version does not match backend"
    if str(release.get("design_id") or "") != expected_design:
        return False, "frontend design identity does not match backend"
    return True, "release identity verified"


def main() -> int:
    raw_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8145"
    raw_path = sys.argv[2] if len(sys.argv) > 2 else "/"
    expected_version = sys.argv[3] if len(sys.argv) > 3 else _DEFAULT_VERSION
    expected_design = sys.argv[4] if len(sys.argv) > 4 else _DEFAULT_DESIGN
    try:
        url = validated_local_url(raw_url)
        open_path = validated_open_path(raw_path)
    except ValueError as exc:
        print(f"Invalid application URL: {exc}", file=sys.stderr)
        return 2

    deadline = time.time() + 60
    last_error = "backend did not answer"
    while time.time() < deadline:
        try:
            matches, detail = release_matches(url, expected_version, expected_design)
            if not matches:
                print(f"Refusing to open the wrong Highlight Studio release: {detail}", file=sys.stderr)
                return 3
            query = urlencode({"hs_release": expected_version, "design": expected_design})
            webbrowser.open(f"{url}{'' if open_path == '/' else open_path}?{query}")
            print(detail)
            return 0
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.5)
    print(f"Expected release did not become ready: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
