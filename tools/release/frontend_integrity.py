"""Validate the source/build fingerprints emitted only after a successful Vite build."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

INPUTS = ('release_identity.json', 'frontend/src', 'frontend/public', 'frontend/scripts',
          'frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json', 'frontend/vite.config.js')


def verify_frontend(root: Path) -> list[str]:
    problems = []
    try:
        manifest = json.loads((root / 'frontend/dist/build-manifest.json').read_text(encoding='utf-8'))
        identity = json.loads((root / 'release_identity.json').read_text(encoding='utf-8'))
        if manifest.get('schema') != 1 or manifest.get('identity') != identity:
            problems.append('frontend build manifest identity/schema mismatch')
        inputs = set()
        for relative in INPUTS:
            p = root / relative
            inputs.update(x.relative_to(root).as_posix() for x in p.rglob('*') if x.is_file()) if p.is_dir() else inputs.add(relative)
        outputs = {p.relative_to(root).as_posix() for p in (root / 'frontend/dist').rglob('*')
                   if p.is_file() and p.name != 'build-manifest.json'}
        for label, paths in (('inputs', inputs), ('outputs', outputs)):
            recorded = manifest.get(label)
            if not isinstance(recorded, dict) or set(recorded) != paths:
                problems.append(f'frontend build {label} file set mismatch; rebuild required')
                continue
            for relative in sorted(paths):
                if hashlib.sha256((root / relative).read_bytes()).hexdigest() != recorded[relative]:
                    problems.append(f'frontend build {label} changed: {relative}')
        for p in (root / 'frontend/public').rglob('*'):
            if p.is_file():
                built = root / 'frontend/dist' / p.relative_to(root / 'frontend/public')
                if not built.is_file() or p.read_bytes() != built.read_bytes():
                    problems.append(f'public/dist mismatch: {p.name}')
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        problems.append(f'frontend build manifest unavailable/invalid: {exc}')
    return problems
