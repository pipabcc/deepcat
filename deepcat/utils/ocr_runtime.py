from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


_DLL_DIRECTORY_HANDLES: list[object] = []
_ADDED_DLL_DIRECTORIES: set[str] = set()


def _norm_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _package_dir(package_name: str) -> Path | None:
    spec = importlib.util.find_spec(package_name)
    if spec is None:
        return None
    locations = list(spec.submodule_search_locations or [])
    if locations:
        return Path(locations[0]).resolve()
    if spec.origin:
        return Path(spec.origin).resolve().parent
    return None


def _append_existing_dir(paths: list[Path], path: Path) -> None:
    if not path.is_dir():
        return
    resolved = path.resolve()
    resolved_norm = _norm_path(resolved)
    if any(_norm_path(existing) == resolved_norm for existing in paths):
        return
    paths.append(resolved)


def iter_ocr_dll_search_dirs() -> tuple[Path, ...]:
    paths: list[Path] = []

    project_root = Path(__file__).resolve().parents[2]
    local_pyqt_dll_dir = project_root / ".venv" / "Lib" / "site-packages" / "PyQt6" / "Qt6" / "bin"
    has_local_pyqt_dll_dir = local_pyqt_dll_dir.is_dir()
    _append_existing_dir(paths, local_pyqt_dll_dir)

    pyqt_dir = _package_dir("PyQt6")
    if pyqt_dir is not None and not has_local_pyqt_dll_dir:
        _append_existing_dir(paths, pyqt_dir / "Qt6" / "bin")

    onnxruntime_dir = _package_dir("onnxruntime")
    if onnxruntime_dir is not None:
        _append_existing_dir(paths, onnxruntime_dir / "capi")

    base_candidates: list[Path] = []
    if bool(getattr(sys, "frozen", False)):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            base_candidates.append(Path(meipass))
        base_candidates.append(Path(sys.executable).resolve().parent)
        base_candidates.append(Path(sys.executable).resolve().parent / "_internal")

    for base in base_candidates:
        _append_existing_dir(paths, base)
        _append_existing_dir(paths, base / "PyQt6" / "Qt6" / "bin")
        _append_existing_dir(paths, base / "onnxruntime")
        _append_existing_dir(paths, base / "onnxruntime" / "capi")

    return tuple(paths)


def configure_ocr_dll_search_paths() -> tuple[Path, ...]:
    paths = iter_ocr_dll_search_dirs()
    current_path_parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    current_path_norms = {os.path.normcase(p) for p in current_path_parts}

    for path in paths:
        path_s = str(path)
        path_norm = _norm_path(path)
        if path_norm not in _ADDED_DLL_DIRECTORIES and hasattr(os, "add_dll_directory"):
            try:
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(path_s))
                _ADDED_DLL_DIRECTORIES.add(path_norm)
            except OSError:
                pass
        if path_norm not in current_path_norms:
            current_path_parts.insert(0, path_s)
            current_path_norms.add(path_norm)

    os.environ["PATH"] = os.pathsep.join(current_path_parts)
    return paths
