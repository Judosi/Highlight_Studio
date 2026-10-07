import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "backend" / "src"
for candidate in (ROOT, BACKEND_SRC):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)
