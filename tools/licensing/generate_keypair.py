from __future__ import annotations

import argparse
import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an Ed25519 keypair for Highlight Studio Paid Beta licenses.")
    parser.add_argument("--private-key", type=Path, required=True, help="Private PEM output. Keep this file outside the app repository.")
    parser.add_argument("--public-key", type=Path, required=True, help="Public base64url key output.")
    args = parser.parse_args()
    if args.private_key.exists() or args.public_key.exists():
        raise SystemExit("Refusing to overwrite an existing key file.")
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    args.private_key.parent.mkdir(parents=True, exist_ok=True)
    args.public_key.parent.mkdir(parents=True, exist_ok=True)
    args.private_key.write_bytes(private_bytes)
    try:
        args.private_key.chmod(0o600)
    except OSError:
        pass
    args.public_key.write_text(base64.urlsafe_b64encode(public_bytes).decode("ascii").rstrip("=") + "\n", encoding="utf-8")
    print(f"Private key: {args.private_key}")
    print(f"Public key:  {args.public_key}")
    print("Set HIGHLIGHT_STUDIO_LICENSE_PUBLIC_KEY_B64 to the public file contents in the Windows build environment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
