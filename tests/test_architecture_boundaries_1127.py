from __future__ import annotations

from tools.quality.architecture_audit import audit


def test_backend_layer_direction_and_release_root_are_clean():
    errors = [finding for finding in audit() if finding.severity == "error"]
    assert errors == [], "\n".join(f"{item.code}: {item.path}: {item.message}" for item in errors)
