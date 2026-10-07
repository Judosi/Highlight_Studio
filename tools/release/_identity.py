from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IDENTITY_PATH = ROOT / "release_identity.json"

def load_identity() -> dict[str, str]:
    data = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
    version = str(data.get("version") or "").strip()
    app_version = str(data.get("app_version") or "").strip()
    design_id = str(data.get("design_id") or "").strip()
    if not version or not app_version or not design_id:
        raise RuntimeError("release_identity.json is incomplete")
    return {"version": version, "app_version": app_version, "design_id": design_id, "root": f"Highlight_Studio_{version}"}
