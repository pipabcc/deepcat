# 一键混合打包构建脚本 (5大核心业务模块二进制 .pyd 机器码化 + 完美子包兼容 Onedir 目录)
$ErrorActionPreference = "Stop"

python scripts\verify_ppocrv6_models.py

# 1. 杀死可能正在运行的旧进程，防止打包写入失败
Write-Host "[1/6] Terminating active DeepCat processes..." -ForegroundColor Cyan
Stop-Process -Name deepcat -Force -ErrorAction SilentlyContinue
Stop-Process -Name main -Force -ErrorAction SilentlyContinue

# 2. 清理与重置旧的编译产物目录
Write-Host "[2/6] Cleaning up old build directories..." -ForegroundColor Cyan
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force build_secure -ErrorAction SilentlyContinue

# 3. 创建物理隔离的安全打包工作区
Write-Host "[3/6] Initializing secure build workspace..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force build_secure | Out-Null
# 完整拷贝原开发代码 deepcat 到 build_secure 中 (确保路径为 build_secure/deepcat)
Copy-Item -Recurse -Force deepcat -Destination build_secure


# 4. 执行 Nuitka 针对 5 大原创核心业务代码的二进制机器码编译
Write-Host "[4/6] Compiling 5 core algorithms to binary .pyd machine code..." -ForegroundColor Cyan

# 定义核心需要保护的原创代码路径 (已添加用户指定的 Gemini 业务逻辑模块)
# 排除强耦合 Qt 排版的 ui/main_window.py 与 ui/post_capture_actions.py，防止 C 扩展重入死锁崩溃
$coreFiles = @(
    "deepcat/main.py",
    "deepcat/core/scroller.py",
    "deepcat/core/stitcher.py",
    "deepcat/gemini_web2api.py",
    "deepcat/local_gemini_web2api_server.py"
)


$nuitkaCompileDir = "build_secure/nuitka_compile"
New-Item -ItemType Directory -Force $nuitkaCompileDir | Out-Null

foreach ($file in $coreFiles) {
    $fileName = Split-Path $file -Leaf
    Write-Host "Compiling core module: $file ..." -ForegroundColor Green

    # 模块化编译单文件
    python -m nuitka --module --output-dir=$nuitkaCompileDir $file

    # 寻找生成的 .pyd 模块文件
    $filterPattern = ($fileName -replace "\.py$", "") + "*.pyd"
    $pydFile = Get-ChildItem -Path $nuitkaCompileDir -Filter $filterPattern | Select-Object -First 1

    if ($null -eq $pydFile) {
        Write-Host "Error: Failed to compile $file into binary!" -ForegroundColor Red
        Exit 1
    }

    # 决定拷贝的目标位置 (对应子目录)
    $relDir = Split-Path $file
    $targetDir = Join-Path "build_secure" $relDir
    $cleanName = ($fileName -replace "\.py$", ".pyd")
    $targetPath = Join-Path $targetDir $cleanName

    # 拷贝二进制模块进隔离区，并重命名为干净的子模块
    Copy-Item $pydFile.FullName -Destination $targetPath -Force
    Write-Host "Successfully generated secure binary: $targetPath" -ForegroundColor Green

    # 物理清除隔离区中原始的 .py 源码文件，彻底杜绝源码打包泄漏！
    $pyInSecure = Join-Path "build_secure" $file
    Remove-Item -Force $pyInSecure
    Write-Host "Securely deleted raw source file in build区: $pyInSecure" -ForegroundColor Yellow
}

# 清理编译生成的中间件
Remove-Item -Recurse -Force $nuitkaCompileDir -ErrorAction SilentlyContinue

# 5. 装配 PyInstaller 物理隔离打包环境
Write-Host "[5/6] Assembling PyInstaller secure workspace..." -ForegroundColor Cyan
Copy-Item packaging/entry_gui.py -Destination build_secure/entry_gui.py -Force
Copy-Item packaging/rthook_onnxruntime.py -Destination build_secure/rthook_onnxruntime.py -Force

# 创建专用于隔离打包区的 secure.spec 文件
$specContent = @'
import json
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs, copy_metadata

# Secure workspace absolute path
build_secure_dir = os.path.abspath("build_secure")
project_dir = os.path.abspath(".")
paddlex_build_cache = os.path.join(build_secure_dir, "paddlex-cache")
os.makedirs(paddlex_build_cache, exist_ok=True)
os.environ["PADDLE_PDX_CACHE_HOME"] = paddlex_build_cache
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

block_cipher = None

hiddenimports = ["httpx", "socksio", "python_socks", "python_socks.async_", "logging.handlers", "sqlite3", "websockets", "pypdf"]

datas = []
binaries = []

hiddenimports += collect_submodules("pynput")
hiddenimports += collect_submodules("pyautogui")
hiddenimports += collect_submodules("mss")

try:
    hiddenimports += collect_submodules("rapidocr_onnxruntime")
except Exception:
    pass

try:
    hiddenimports += collect_submodules("paddleocr")
    hiddenimports += collect_submodules("paddlex")
except Exception:
    pass

try:
    import win32com
    hiddenimports += collect_submodules("win32com")
    hiddenimports += ["pythoncom", "pywintypes"]
except Exception:
    pass

hiddenimports += [
    "onnxruntime",
    "onnxruntime.capi._pybind_state",
    "onnxruntime.capi.onnxruntime_inference_collection",
    "onnxruntime.capi.onnxruntime_pybind11_state",
    "deepcat.ocr_engines",
    "deepcat.ocr_text_layout",
    "deepcat.ppocrv6_runtime",
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

ppocrv6_model_root = os.path.join(project_dir, "models", "pp-ocrv6-small")
ppocrv6_manifest_path = os.path.join(ppocrv6_model_root, "model_manifest.json")
if not os.path.isfile(ppocrv6_manifest_path):
    raise SystemExit("PP-OCRv6 Small model directory is missing")
with open(ppocrv6_manifest_path, "r", encoding="utf-8") as manifest_stream:
    ppocrv6_manifest = json.load(manifest_stream)
for model_entry in ppocrv6_manifest["files"]:
    relative_path = str(model_entry["path"])
    source_path = os.path.join(ppocrv6_model_root, relative_path)
    destination = os.path.join("models", "pp-ocrv6-small", os.path.dirname(relative_path))
    datas.append((source_path, destination))
for model_doc_name in ["model_manifest.json", "README.md", "LICENSE"]:
    model_doc_path = os.path.join(ppocrv6_model_root, model_doc_name)
    if os.path.isfile(model_doc_path):
        datas.append((model_doc_path, os.path.join("models", "pp-ocrv6-small")))

# Package the entire sanitized deepcat package folder (including all non-pyd files)
datas.append((os.path.join(build_secure_dir, "deepcat"), "deepcat"))

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

# Scan and inject all compiled .pyd binary extensions into their respective subfolders in binaries
for root, _, files in os.walk(os.path.join(build_secure_dir, "deepcat")):
    for fn in files:
        if fn.endswith(".pyd"):
            src = os.path.join(root, fn)
            rel_dir = os.path.relpath(root, build_secure_dir)
            binaries.append((src, rel_dir))

# Packaging tools folder
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


# Packaging necessary C++ runtimes
system32 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32")
for dll_name in ["msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"]:
    dll_path = os.path.join(system32, dll_name)
    if os.path.exists(dll_path):
        binaries.append((dll_path, "."))


a = Analysis(
    [os.path.join(build_secure_dir, "entry_gui.py")],
    pathex=[build_secure_dir, project_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(build_secure_dir, "rthook_onnxruntime.py")],
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
    icon=os.path.join(build_secure_dir, "deepcat", "ui", "assets", "app.ico"),

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
'@

$specPath = "build_secure/secure.spec"
Set-Content -Path $specPath -Value $specContent -Encoding utf8

# 6. 调用 PyInstaller 运行打包
Write-Host "[6/6] Launching PyInstaller secure packaging..." -ForegroundColor Cyan
python -m PyInstaller $specPath --noconfirm --clean

# 7. 检查与收尾
Write-Host "----------------------------------------" -ForegroundColor Cyan
if (Test-Path dist\deepcat\deepcat.exe) {
    Write-Host "Build SUCCESS!" -ForegroundColor Green

    # 自动生成并植入外层和内层的 qt.conf 导航地图，彻底根除 PyQt6.QtCore: Unable to embed qt.conf 致命崩溃！
    $qtConfOuter = "[Paths]`r`nPrefix = _internal/PyQt6/Qt6`r`nPlugins = plugins"
    $qtConfInner = "[Paths]`r`nPrefix = PyQt6/Qt6`r`nPlugins = plugins"
    Set-Content -Path "dist\deepcat\qt.conf" -Value $qtConfOuter -Encoding ascii -Force
    Set-Content -Path "dist\deepcat\_internal\qt.conf" -Value $qtConfInner -Encoding ascii -Force
    Write-Host "Successfully generated and injected outer/inner qt.conf navigation maps!" -ForegroundColor Green

    New-Item -ItemType Directory -Force "dist\deepcat\models" | Out-Null
    if (Test-Path "model_catalog.json") {
        Copy-Item "model_catalog.json" -Destination "dist\deepcat" -Force
    }
    foreach ($doc in @(
        "README.md",
        "LICENSE",
        "COPYRIGHT",
        "PRIVACY.md",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "THIRD_PARTY_LICENSES.md",
        "USER_GUIDE.md",
        "LOCAL_API_SERVICE.md"
    )) {
        if (Test-Path $doc) {
            Copy-Item $doc -Destination "dist\deepcat" -Force
        }
    }

    Write-Host "Output Directory: $(Resolve-Path dist\deepcat)" -ForegroundColor Green
    Write-Host "5 core logic files compiled to binary .pyd and raw .py code completely removed!" -ForegroundColor Green
} else {
    Write-Host "Build FAILED! Please check compilation logs." -ForegroundColor Red
}
