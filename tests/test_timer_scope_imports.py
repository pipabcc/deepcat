from pathlib import Path


def test_single_shot_scoped_callers_import_helper():
    repo_root = Path(__file__).resolve().parents[1]
    missing_imports: list[str] = []

    for path in (repo_root / "deepcat").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if path.name == "timer_scope.py" or "single_shot_scoped" not in source:
            continue
        if "from deepcat.ui.timer_scope import single_shot_scoped" not in source:
            missing_imports.append(str(path.relative_to(repo_root)))

    assert missing_imports == []
