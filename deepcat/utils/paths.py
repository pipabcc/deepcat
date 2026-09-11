from __future__ import annotations

import sys
from pathlib import Path


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    package_root = Path(__file__).resolve().parents[2]
    if (package_root / "deepcat").is_dir():
        return package_root
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and argv0 not in {"-c", "-m"}:
        p = Path(argv0)
        if p.exists():
            return p.resolve().parent
    return Path.cwd().resolve()


def get_files_dir() -> Path:
    p = get_app_dir() / "files"
    p.mkdir(parents=True, exist_ok=True)
    return p
