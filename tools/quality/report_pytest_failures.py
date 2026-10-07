from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def escape_annotation(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    report = Path(sys.argv[1] if len(sys.argv) > 1 else "pytest-results.xml")
    if not report.is_file():
        print(f"::error title=Pytest report missing::{escape_annotation(str(report))} was not created")
        return 0

    root = ET.parse(report).getroot()
    for case in root.iter("testcase"):
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if problem is None:
            continue
        test_id = "::".join(filter(None, (case.get("classname"), case.get("name"))))
        message = (problem.get("message") or problem.text or "pytest failure").strip()
        location = ""
        if case.get("file"):
            location = f"file={case.get('file')}"
            if case.get("line"):
                location += f",line={case.get('line')}"
            location += ","
        print(
            f"::error {location}title={escape_annotation('Pytest: ' + test_id)}::"
            f"{escape_annotation(message[:4000])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
