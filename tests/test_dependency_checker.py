from __future__ import annotations

from pathlib import Path

from tools.diagnostics.check_deps_fast import parse_modules


def _write_requirements(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "requirements.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_conditional_audioop_dependency_is_skipped_before_python_313(tmp_path: Path) -> None:
    path = _write_requirements(
        tmp_path,
        'fastapi==1.0\naudioop-lts==0.2.2; python_version >= "3.13"\n',
    )
    modules = parse_modules(path, {"python_version": "3.12", "python_full_version": "3.12.9"})
    assert modules == ["fastapi"]


def test_audioop_lts_distribution_maps_to_audioop_import_on_python_313(tmp_path: Path) -> None:
    path = _write_requirements(
        tmp_path,
        'audioop-lts==0.2.2; python_version >= "3.13"\n',
    )
    modules = parse_modules(path, {"python_version": "3.13", "python_full_version": "3.13.4"})
    assert modules == ["audioop"]
    assert "audioop_lts" not in modules


def test_requirement_extras_and_hyphens_map_to_import_names(tmp_path: Path) -> None:
    path = _write_requirements(tmp_path, "uvicorn[standard]==0.34.3\npython-multipart==0.0.20\n")
    assert parse_modules(path) == ["uvicorn", "multipart"]


def test_argon2_cffi_distribution_maps_to_argon2_import(tmp_path: Path) -> None:
    path = _write_requirements(tmp_path, "argon2-cffi==25.1.0\n")
    modules = parse_modules(path)
    assert modules == ["argon2"]
    assert "argon2_cffi" not in modules
