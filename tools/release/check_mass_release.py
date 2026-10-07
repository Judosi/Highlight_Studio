from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from backend.src.highlight_studio.infrastructure.release_gate import release_readiness

    parser = argparse.ArgumentParser(description="Highlight Studio mass release gate")
    parser.add_argument("--strict", action="store_true", help="Return non-zero while blockers remain")
    args = parser.parse_args()
    report = release_readiness()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if args.strict and not report["ready_for_mass_release"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
