from __future__ import annotations

import argparse
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()

    try:
        from sqlalchemy import create_engine, text
    except ImportError as exc:
        print(f"SQLAlchemy is not installed: {exc}")
        return 2

    engine = create_engine(args.url, pool_pre_ping=True)
    deadline = time.monotonic() + max(1, args.timeout)
    last_error = ""
    try:
        while time.monotonic() < deadline:
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                print("PostgreSQL is ready")
                return 0
            except Exception as exc:  # database can legitimately be booting
                last_error = str(exc)
                time.sleep(2)
    finally:
        engine.dispose()
    print(f"PostgreSQL did not become ready: {last_error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
