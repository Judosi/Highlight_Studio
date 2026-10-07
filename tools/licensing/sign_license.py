from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sign an offline Highlight Studio Paid Beta license token.")
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--license-id", required=True)
    parser.add_argument(
        "--device-id", required=True, help="Full device_code from license status. Use * only intentionally for an unbound founders key."
    )
    parser.add_argument("--plan", default="paid_beta")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--entitlements", default="analysis,render,shorts,twitch,metadata_ai")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.days <= 3650:
        raise SystemExit("--days must be between 1 and 3650")
    if not args.device_id.strip():
        raise SystemExit("--device-id must not be empty")
    private_key = serialization.load_pem_private_key(args.private_key.read_bytes(), password=None)
    if not hasattr(private_key, "sign"):
        raise SystemExit("Private key does not support Ed25519 signing")
    now = int(time.time())
    payload = {
        "license_id": args.license_id,
        "plan": args.plan,
        "device_id": args.device_id,
        "issued_at": now,
        "expires_at": now + max(1, args.days) * 86400,
        "entitlements": [item.strip() for item in args.entitlements.split(",") if item.strip()],
    }
    encoded_payload = b64url(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signed = f"HSB1.{encoded_payload}".encode("ascii")
    token = f"HSB1.{encoded_payload}.{b64url(private_key.sign(signed))}"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(token + "\n", encoding="utf-8")
    else:
        print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
