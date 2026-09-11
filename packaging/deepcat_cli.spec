import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

spec_path = os.path.abspath(SPECPATH)
if os.path.isdir(spec_path):
    spec_dir = spec_path
else:
    spec_dir = os.path.dirname(spec_path)

project_dir = os.path.dirname(spec_dir)

block_cipher = None

hiddenimports = ["httpx", "socksio"]
hiddenimports += collect_submodules("pynput")
hiddenimports += collect_submodules("pyautogui")
hiddenimports += collect_submodules("mss")

datas = []
a = Analysis(
    [os.path.join(spec_dir, "entry_cli.py")],
    pathex=[project_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PySide2", "PySide6", "tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="deepcat-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
