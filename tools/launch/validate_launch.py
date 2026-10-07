from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.launch.configure_launch import load_config, validate_launch_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate external launch configuration")
    parser.add_argument("--config", type=Path, default=ROOT / "launch" / "launch_config.json")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        problems = validate_launch_config(config)
    except Exception as exc:
        problems = [{"field": "config", "message": str(exc)}]
    report = {"ok": not problems, "problem_count": len(problems), "problems": problems}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if args.strict and problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
