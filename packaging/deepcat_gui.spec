import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs, copy_metadata
import PyInstaller.building.utils as pyi_utils
import PyInstaller.building.api as pyi_api

_orig_make_clean = pyi_utils._make_clean_directory
def _safe_make_clean_directory(path):
    try:
        _orig_make_clean(path)
    except Exception:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path, topdown=False):
                for f in files:
                    try:
                        os.remove(os.path.join(root, f))
                    except Exception:
                        pass
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except Exception:
                        pass
        else:
            os.makedirs(path, exist_ok=True)
pyi_utils._make_clean_directory = _safe_make_clean_directory
pyi_api._make_clean_directory = _safe_make_clean_directory

spec_path = os.path.abspath(SPECPATH)
if os.path.isdir(spec_path):
    spec_dir = spec_path
else:
    spec_dir = os.path.dirname(spec_path)

project_dir = os.path.dirname(spec_dir)
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

import PyQt6

qt_binary_dir = Path(PyQt6.__file__).resolve().parent / "Qt6" / "bin"
system_root = Path(os.environ.get("WINDIR", r"C:\Windows"))
# 构建工具可能把 Poppler、libheif 等 DLL 目录加入 PATH。它们的同名 ICU/UCRT
# 与 Qt、Python 不兼容，依赖扫描必须只使用当前 Python、Qt 和 Windows 运行库。
runtime_search_paths = [
    qt_binary_dir,
    Path(sys.executable).resolve().parent,
    Path(sys.base_prefix),
    Path(sys.base_prefix) / "DLLs",
    system_root / "System32",
    system_root,
]
os.environ["PATH"] = os.pathsep.join(str(path) for path in runtime_search_paths if path.is_dir())

paddlex_build_cache = os.path.join(project_dir, "build", "paddlex-cache")
os.makedirs(paddlex_build_cache, exist_ok=True)
os.environ["PADDLE_PDX_CACHE_HOME"] = paddlex_build_cache
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from deepcat.ppocrv6_runtime import resolve_ppocrv6_model_root, verify_ppocrv6_models

block_cipher = None

# socks: PySocks 模块名，requests 用 socks5h:// 代理时依赖此模块。
# 缺失时 requests 抛 "Missing dependencies for SOCKS support"，节点探测代理回退分支会失败。
hiddenimports = ["httpx", "socksio", "socks"]
datas = []
binaries = []
hiddenimports += collect_submodules("pynput")
hiddenimports += collect_submodules("pyautogui")
hiddenimports += collect_submodules("mss")
hiddenimports += collect_submodules("pywinauto")
hiddenimports += collect_submodules("comtypes")
# 显式补齐 pywin32 顶层模块：collect_submodules("win32com") 只收集 win32com.* 子包，
# 不包含 win32clipboard/win32con/win32gui/win32process 等独立顶层模块。
# 这些模块在 clipboard_monitor.py 里以 try/except 形式动态导入，PyInstaller 静态扫描不稳定，
# 必须显式声明，否则打包后 detect_clipboard_content() 静默返回 None，剪贴板记录完全失效。
hiddenimports += [
    "pythoncom",
    "pywintypes",
    "win32clipboard",
    "win32con",
    "win32gui",
    "win32process",
    "win32api",
    "win32com",
    "win32com.client",
    "psutil",
]
try:
    hiddenimports += collect_submodules("win32com")
except Exception:
    pass
# comtypes / pywinauto 需要数据文件（typelib、预生成包装器等）才能在 frozen 模式下正常工作。
# 缺失这些数据文件时，UIA 后端在打包后首次调用会因 typelib 加载失败而抛异常。
try:
    datas += collect_data_files("comtypes")
except Exception:
    pass
try:
    datas += collect_data_files("pywinauto")
except Exception:
    pass
# pynput 在 Windows 上依赖 native hook 相关动态库，必须显式收集。
try:
    binaries += collect_dynamic_libs("pynput")
except Exception:
    pass
try:
    hiddenimports += collect_submodules("rapidocr_onnxruntime")
except Exception:
    pass
try:
    hiddenimports += collect_submodules("paddleocr")
    hiddenimports += collect_submodules("paddlex")
except Exception:
    pass
hiddenimports += [
    "deepcat.ocr_ipc",
    "deepcat.ocr_engines",
    "deepcat.ocr_text_layout",
    "deepcat.ppocrv6_runtime",
    "deepcat.ocr_worker",
    "deepcat.ocr_worker_client",
    # 内置 Gemini 代理走进程内直调：显式声明，避免打包漏收 inproc_http 后回退成「启动超时」
    "deepcat.inproc_http",
    "deepcat.gemini_web2api",
    "deepcat.local_gemini_web2api_server",
    # 直连时用 Python 预解析地址喂给 libcurl，避免其解析线程被安全策略拦截
    "deepcat.utils.curl_dns",
    "onnxruntime",
    "onnxruntime.capi._pybind_state",
    "onnxruntime.capi.onnxruntime_inference_collection",
    "onnxruntime.capi.onnxruntime_pybind11_state",
]

try:
    datas += collect_data_files("rapidocr_onnxruntime")
except Exception:
    pass
try:
    datas += collect_data_files("paddleocr")
    datas += collect_data_files("paddlex")
except Exception:
    pass

# PaddleX 3.7 在运行时通过 importlib.metadata 校验 ocr-core extra。
for distribution_name in [
    "paddlex",
    "imagesize",
    "opencv-contrib-python",
    "pyclipper",
    "pypdfium2",
    "python-bidi",
    "shapely",
]:
    datas += copy_metadata(distribution_name)

ppocrv6_model_root = resolve_ppocrv6_model_root(Path(project_dir))
ppocrv6_manifest = verify_ppocrv6_models(ppocrv6_model_root, check_hashes=True)
for model_entry in ppocrv6_manifest["files"]:
    relative_path = str(model_entry["path"])
    source_path = os.path.join(str(ppocrv6_model_root), relative_path)
    destination = os.path.join("models", "pp-ocrv6-small", os.path.dirname(relative_path))
    datas.append((source_path, destination))
for model_doc_name in ["model_manifest.json", "README.md", "LICENSE"]:
    model_doc_path = os.path.join(str(ppocrv6_model_root), model_doc_name)
    if os.path.isfile(model_doc_path):
        datas.append((model_doc_path, os.path.join("models", "pp-ocrv6-small")))
assets_dir = os.path.join(project_dir, "deepcat", "ui", "assets")
for root, _, files in os.walk(assets_dir):
    for fn in files:
        src = os.path.join(root, fn)
        rel = os.path.relpath(root, project_dir)
        datas.append((src, rel))

rules_dir = os.path.join(project_dir, "deepcat", "rules")
if os.path.exists(rules_dir):
    for root, _, files in os.walk(rules_dir):
        for fn in files:
            src = os.path.join(root, fn)
            rel = os.path.relpath(root, project_dir)
            datas.append((src, rel))

model_catalog_path = os.path.join(project_dir, "model_catalog.json")
if os.path.exists(model_catalog_path):
    datas.append((model_catalog_path, "."))

for doc_name in [
    "README.md",
    "LICENSE",
    "COPYRIGHT",
    "PRIVACY.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "THIRD_PARTY_LICENSES.md",
    "USER_GUIDE.md",
    "LOCAL_API_SERVICE.md",
]:
    doc_path = os.path.join(project_dir, doc_name)
    if os.path.exists(doc_path):
        datas.append((doc_path, "."))

tools_dir = os.path.join(project_dir, "tools")
if os.path.exists(tools_dir):
    datas.append((tools_dir, "tools"))

try:
    binaries += collect_dynamic_libs("onnxruntime")
except Exception:
    pass


def _is_root_llama_tool_binary(item):
    dest, src, _typecode = item
    dest_norm = os.path.normpath(str(dest))
    src_norm = os.path.normpath(str(src))
    if os.path.dirname(dest_norm):
        return False
    if os.path.normcase(os.path.join("tools", "llama.cpp-b9360")) not in os.path.normcase(src_norm):
        return False
    name = os.path.basename(dest_norm).lower()
    return (
        name.startswith("llama")
        or name.startswith("ggml")
        or name in {"mtmd.dll", "libomp140.x86_64.dll"}
    )


for dll_name in [
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "vcruntime140_threads.dll", "concrt140.dll",
]:
    # 优先使用与 Qt 同批分发的 VC 运行库，避免把系统中的旧版 14.0 强行提前加载。
    dll_path = next((directory / dll_name for directory in runtime_search_paths if (directory / dll_name).is_file()), None)
    if dll_path is not None:
        binaries.append((str(dll_path), "."))

a = Analysis(
    [os.path.join(spec_dir, "entry_gui.py")],
    pathex=[project_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(spec_dir, "rthook_onnxruntime.py")],
    excludes=["PyQt5", "PySide2", "PySide6", "tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
a.binaries = [item for item in a.binaries if not _is_root_llama_tool_binary(item)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="deepcat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(assets_dir, "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="deepcat",
)
