from __future__ import annotations

import sys

MIN_VERSION = (3, 10)
MAX_EXCLUSIVE = (3, 14)

if not (MIN_VERSION <= sys.version_info[:2] < MAX_EXCLUSIVE):
    print(f"Unsupported Python {sys.version.split()[0]}. Use Python 3.10-3.13.")
    raise SystemExit(2)
print(f"Python {sys.version.split()[0]} supported")
