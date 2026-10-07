"""Static architecture audit for Highlight Studio.

The audit is intentionally dependency-free so it can run before the application
virtual environment is created.  It verifies layer direction, root hygiene and
legacy wrapper shape, while reporting large modules as refactoring candidates.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "backend" / "src" / "highlight_studio"
LAYERS = ("api", "core", "services", "integrations", "infrastructure")

# A layer may depend only on the layers listed here.  Same-layer imports are
# always allowed.  This matches docs/ARCHITECTURE.md and prevents lower-level
# code from reaching back into FastAPI orchestration.
ALLOWED_DEPENDENCIES = {
    "core": {"core"},
    "infrastructure": {"core", "infrastructure"},
    "integrations": {"core", "infrastructure", "integrations"},
    "services": {"core", "infrastructure", "integrations", "services"},
    "api": set(LAYERS),
}

ALLOWED_ROOT_FILES = {
    ".gitattributes",
    ".gitignore",
    "EXTRACT_BEFORE_STARTING.txt",
    "README.md",
    "RELEASE_CHECKSUMS.json",
    "START_HERE.bat",
    "alembic.ini",
    "pyproject.toml",
    "release_identity.json",
}

LARGE_MODULE_THRESHOLDS = {
    "backend/src/highlight_studio/api/app.py": 5000,
    "backend/src/highlight_studio/services/pipeline.py": 9000,
    "frontend/src/app/App.jsx": 3000,
}


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    path: str
    message: str


def _layer_for_module(module: str) -> str | None:
    prefixes = (
        "backend.src.highlight_studio.",
        "highlight_studio.",
    )
    for prefix in prefixes:
        if module.startswith(prefix):
            module = module[len(prefix):]
            break
    first = module.split(".", 1)[0]
    return first if first in LAYERS else None


def _relative_target_layer(node: ast.ImportFrom, current_layer: str) -> str | None:
    module = node.module or ""
    # Relative imports in this package commonly spell the sibling top-level
    # layer explicitly (..core, ..services, ...infrastructure).
    explicit = _layer_for_module(module)
    if explicit:
        return explicit
    if node.level:
        return current_layer
    return None


def _python_files(path: Path) -> Iterable[Path]:
    return sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)


def dependency_findings() -> list[Finding]:
    findings: list[Finding] = []
    for layer in LAYERS:
        layer_root = PACKAGE_ROOT / layer
        if not layer_root.exists():
            findings.append(Finding("error", "missing-layer", str(layer_root.relative_to(ROOT)), f"Missing backend layer: {layer}"))
            continue
        for path in _python_files(layer_root):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                findings.append(Finding("error", "syntax", str(path.relative_to(ROOT)), f"Python syntax error: {exc}"))
                continue
            for node in ast.walk(tree):
                target: str | None = None
                if isinstance(node, ast.ImportFrom):
                    target = _relative_target_layer(node, layer)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        target = _layer_for_module(alias.name)
                        if target and target not in ALLOWED_DEPENDENCIES[layer]:
                            findings.append(Finding(
                                "error", "layer-direction", str(path.relative_to(ROOT)),
                                f"{layer} must not import {target} ({alias.name}) at line {node.lineno}",
                            ))
                    continue
                if target and target not in ALLOWED_DEPENDENCIES[layer]:
                    findings.append(Finding(
                        "error", "layer-direction", str(path.relative_to(ROOT)),
                        f"{layer} must not import {target} ({node.module or ''}) at line {node.lineno}",
                    ))
    return findings


def root_findings() -> list[Finding]:
    findings: list[Finding] = []
    for path in ROOT.iterdir():
        if path.is_file() and path.name not in ALLOWED_ROOT_FILES:
            findings.append(Finding("error", "root-clutter", path.name, "Unexpected file in release root"))
    return findings


def compatibility_wrapper_findings() -> list[Finding]:
    findings: list[Finding] = []
    wrapper_names = {"main.py", "settings.py", "pipeline.py", "job_store.py", "ai_client.py", "ollama_client.py", "twitch_source.py", "utils.py"}
    for name in sorted(wrapper_names):
        path = ROOT / "backend" / name
        if not path.is_file():
            findings.append(Finding("error", "missing-wrapper", f"backend/{name}", "Required compatibility import wrapper is missing"))
            continue
        text = path.read_text(encoding="utf-8")
        if "_import_module" not in text or "_sys.modules[__name__]" not in text:
            findings.append(Finding("error", "wrapper-implementation", f"backend/{name}", "Compatibility wrapper contains implementation logic"))
        if len(text.splitlines()) > 30:
            findings.append(Finding("warning", "wrapper-size", f"backend/{name}", "Compatibility wrapper is unexpectedly large"))
    return findings


def size_findings() -> list[Finding]:
    findings: list[Finding] = []
    for relative, threshold in LARGE_MODULE_THRESHOLDS.items():
        path = ROOT / relative
        if not path.is_file():
            continue
        lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        if lines > threshold:
            findings.append(Finding(
                "warning", "large-module", relative,
                f"{lines} lines; safe future decomposition target (warning threshold {threshold})",
            ))
    return findings


def audit() -> list[Finding]:
    return dependency_findings() + root_findings() + compatibility_wrapper_findings() + size_findings()


def main() -> int:
    findings = audit()
    as_json = "--json" in sys.argv[1:]
    if as_json:
        print(json.dumps({
            "ok": not any(f.severity == "error" for f in findings),
            "findings": [asdict(f) for f in findings],
        }, ensure_ascii=False, indent=2))
    else:
        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        print(f"Architecture audit: {len(errors)} error(s), {len(warnings)} warning(s)")
        for item in findings:
            print(f"[{item.severity.upper()}] {item.code}: {item.path}: {item.message}")
    return 1 if any(f.severity == "error" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
