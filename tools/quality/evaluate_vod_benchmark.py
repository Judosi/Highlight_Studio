from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any


def _number(row: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        value = float(str(row.get(key, "")).strip())
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _bool(row: dict[str, str], key: str) -> bool:
    return str(row.get(key, "")).strip().lower() in {"1", "true", "yes", "да", "ok", "pass"}


MEASUREMENT_FIELDS = {
    "duration_hours",
    "ground_truth_moments",
    "candidates_hit",
    "candidates_total",
    "candidates_usable",
    "duplicate_candidates",
    "cut_context_candidates",
    "render_ok",
    "project_ok",
    "time_saved_percent",
}


def _has_measurements(row: dict[str, str]) -> bool:
    """Ignore pre-numbered template rows until a tester enters actual evidence."""
    return any(str(row.get(field, "") or "").strip() for field in MEASUREMENT_FIELDS)


def evaluate(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if _has_measurements(row)]
    truth = sum(_number(row, "ground_truth_moments") for row in rows)
    hits = sum(_number(row, "candidates_hit") for row in rows)
    candidates = sum(_number(row, "candidates_total") for row in rows)
    usable = sum(_number(row, "candidates_usable") for row in rows)
    duplicates = sum(_number(row, "duplicate_candidates") for row in rows)
    cut_context = sum(_number(row, "cut_context_candidates") for row in rows)
    time_saved = [_number(row, "time_saved_percent") for row in rows if str(row.get("time_saved_percent", "")).strip()]
    result = {
        "vod_count": len(rows),
        "total_duration_hours": round(sum(_number(row, "duration_hours") for row in rows), 2),
        "moment_recall_percent": round((hits / truth) * 100, 2) if truth else 0.0,
        "usable_candidates_percent": round((usable / candidates) * 100, 2) if candidates else 0.0,
        "duplicate_candidates_percent": round((duplicates / candidates) * 100, 2) if candidates else 0.0,
        "cut_context_percent": round((cut_context / candidates) * 100, 2) if candidates else 0.0,
        "render_success_percent": round((sum(_bool(row, "render_ok") for row in rows) / len(rows)) * 100, 2) if rows else 0.0,
        "project_success_percent": round((sum(_bool(row, "project_ok") for row in rows) / len(rows)) * 100, 2) if rows else 0.0,
        "median_time_saved_percent": round(statistics.median(time_saved), 2) if time_saved else 0.0,
        "categories": sorted({str(row.get("category") or "unknown").strip() for row in rows}),
        "source": str(path),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Highlight Studio VOD benchmark CSV")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.csv_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
