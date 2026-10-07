from __future__ import annotations

import socket
import sys


def is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main() -> int:
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 8145
    end = int(sys.argv[2]) if len(sys.argv) > 2 else start + 64
    start = max(1024, min(65535, start))
    end = max(start, min(65535, end))
    for port in range(start, end + 1):
        if is_free(port):
            print(port)
            return 0
    print("No free local port found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
