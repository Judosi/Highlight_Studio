import json
import shutil
from pathlib import Path

import pytest

from tools.release.frontend_integrity import INPUTS, verify_frontend

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def build_copy(tmp_path):
    for relative in (*INPUTS, 'frontend/dist'):
        source, target = ROOT / relative, tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    assert verify_frontend(tmp_path) == []
    return tmp_path


@pytest.mark.parametrize('path', ['frontend/src/app/App.jsx', 'frontend/public/release.json',
                                 'frontend/dist/index.html', 'frontend/package-lock.json'])
def test_build_gate_rejects_changed_input_or_output(build_copy, path):
    with (build_copy / path).open('a') as f:
        f.write('\nchanged\n')
    assert verify_frontend(build_copy)


def test_build_gate_rejects_stale_extra_bundle(build_copy):
    (build_copy / 'frontend/dist/assets/index-1126-old.js').write_text('old')
    assert verify_frontend(build_copy)


def test_build_gate_rejects_missing_manifest(build_copy):
    (build_copy / 'frontend/dist/build-manifest.json').unlink()
    assert verify_frontend(build_copy)


def test_all_package_versions_match_identity():
    expected = json.loads((ROOT / 'release_identity.json').read_text())['version']
    for folder in ['frontend', 'desktop/electron']:
        for filename in ['package.json', 'package-lock.json']:
            data = json.loads((ROOT / folder / filename).read_text())
            assert data['version'] == expected
            if filename == 'package-lock.json':
                assert data['packages']['']['version'] == expected
