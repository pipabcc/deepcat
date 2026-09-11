from __future__ import annotations

import os
import sys
import ctypes
from pathlib import Path


_dll_directory_handles = []


def _add_dir(p: Path) -> None:
    try:
        if not p.exists():
            return
        if hasattr(os, "add_dll_directory"):
            # 句柄被回收时目录会立即从 DLL 搜索路径移除，必须保留到进程退出。
            _dll_directory_handles.append(os.add_dll_directory(str(p)))
        os.environ["PATH"] = f"{str(p)};{os.environ.get('PATH','')}"
    except Exception:
        return


if bool(getattr(sys, "frozen", False)):
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    base = Path(__file__).resolve().parent.parent

_add_dir(base)
_add_dir(base / "PyQt6" / "Qt6" / "bin")
_add_dir(base / "onnxruntime")
_add_dir(base / "onnxruntime" / "capi")

for n in ["vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "concrt140.dll"]:
    try:
        p = base / n
        if p.exists():
            ctypes.WinDLL(str(p))
    except Exception:
        pass
