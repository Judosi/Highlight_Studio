from pathlib import Path

from tools.diagnostics.verify_release_identity import EXPECTED_ROOT, verify


ROOT = Path(__file__).resolve().parents[1]


def test_current_release_has_verified_english_identity_and_redesigned_dist():
    assert ROOT.name == EXPECTED_ROOT
    assert verify(ROOT) == []


def test_release_identity_rejects_an_incomplete_or_merged_folder(tmp_path):
    problems = verify(tmp_path)
    assert problems
    assert any("invalid metadata" in item or "missing required file" in item for item in problems)
