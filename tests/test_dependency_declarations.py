from __future__ import annotations

import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _normalized_requirement(value: str) -> str:
    return "".join(str(value or "").split()).casefold()


def _requirements_txt_dependencies() -> set[str]:
    dependencies: set[str] = set()
    for raw_line in (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        dependencies.add(_normalized_requirement(line))
    return dependencies


def test_pyproject_and_requirements_txt_runtime_dependencies_match() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_dependencies = {
        _normalized_requirement(item)
        for item in pyproject["project"]["dependencies"]
    }

    assert project_dependencies == _requirements_txt_dependencies()


def test_ppocrv6_builds_preserve_ocr_core_distribution_metadata() -> None:
    required_distributions = {
        "paddlex",
        "imagesize",
        "opencv-contrib-python",
        "pyclipper",
        "pypdfium2",
        "python-bidi",
        "shapely",
    }
    spec_text = (PROJECT_ROOT / "packaging" / "deepcat_gui.spec").read_text(encoding="utf-8")
    secure_build_text = (PROJECT_ROOT / "scripts" / "build_secure_onedir.ps1").read_text(encoding="utf-8")
    nuitka_build_text = (PROJECT_ROOT / "scripts" / "nuitka_build.ps1").read_text(encoding="utf-8")

    for distribution in required_distributions:
        assert f'"{distribution}"' in spec_text
        assert f'"{distribution}"' in secure_build_text
        assert f"--include-distribution-metadata={distribution}" in nuitka_build_text
