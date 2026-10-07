from __future__ import annotations

import argparse
import socket


def available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("start", type=int, nargs="?", default=8010)
    parser.add_argument("end", type=int, nargs="?", default=8099)
    args = parser.parse_args()
    for port in range(args.start, args.end + 1):
        if available(port):
            print(port)
            return 0
    print("No free local web port found", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
