from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from deepcat.utils.paths import get_app_dir


APP_NAME = "DeepCat"
LEGACY_APP_NAMES = ("deepcat", "LongScreenshot")


def _command_for_run_key() -> str:
    exe = str(Path(sys.executable).resolve())
    workdir = str(get_app_dir())
    if getattr(sys, "frozen", False):
        return f"\"{exe}\" --gui"
    pythonw = Path(exe).with_name("pythonw.exe")
    launcher = str(pythonw if pythonw.exists() else Path(exe))
    code = (
        f"import os,sys; os.chdir({workdir!r}); sys.path.insert(0,{workdir!r}); "
        "from deepcat.main import main; main(['--gui'])"
    )
    return f"\"{launcher}\" -c \"{code}\""


def _iter_existing_value_names(key) -> list[str]:
    try:
        import winreg
    except Exception:
        return []
    names: list[str] = []
    i = 0
    while True:
        try:
            val_name, _, _ = winreg.EnumValue(key, i)
            names.append(str(val_name))
            i += 1
        except OSError:
            break
    return names


def set_autostart(enabled: bool, app_name: str = APP_NAME) -> Optional[str]:
    try:
        import winreg
    except Exception:
        return "当前系统不支持开机启动设置"
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        )
    except Exception as e:
        return str(e)
    try:
        app_name_cf = str(app_name).strip().casefold()
        existing_names = _iter_existing_value_names(key)

        if enabled:
            # 1. 先清理历史遗留且名称不属于当前 app_name (忽略大小写) 的旧项
            # 必须使用 casefold，严防 Windows 注册表大小写不敏感导致误删当前项
            if app_name_cf == APP_NAME.strip().casefold():
                legacy_names_cf = {
                    str(n).strip().casefold()
                    for n in LEGACY_APP_NAMES
                    if str(n).strip().casefold() != app_name_cf
                }
                for ex_name in existing_names:
                    if ex_name.strip().casefold() in legacy_names_cf:
                        try:
                            winreg.DeleteValue(key, ex_name)
                        except FileNotFoundError:
                            pass
                for name in LEGACY_APP_NAMES:
                    if str(name).strip().casefold() == app_name_cf:
                        continue
                    try:
                        winreg.DeleteValue(key, str(name))
                    except FileNotFoundError:
                        pass

            # 2. 写入当前启动项
            winreg.SetValueEx(key, str(app_name), 0, winreg.REG_SZ, _command_for_run_key())
        else:
            # 关闭自启动：清理当前项（若为主应用，则一并清理历史旧项）
            target_names_cf = {app_name_cf}
            if app_name_cf == APP_NAME.strip().casefold():
                target_names_cf |= {str(n).strip().casefold() for n in LEGACY_APP_NAMES}

            for ex_name in existing_names:
                if ex_name.strip().casefold() in target_names_cf:
                    try:
                        winreg.DeleteValue(key, ex_name)
                    except FileNotFoundError:
                        pass
            for name in target_names_cf:
                try:
                    winreg.DeleteValue(key, str(name))
                except FileNotFoundError:
                    pass
    except Exception as e:
        return str(e)
    finally:
        try:
            winreg.CloseKey(key)
        except Exception:
            pass
    return None


def is_autostart_enabled(app_name: str = APP_NAME) -> bool:
    try:
        import winreg
    except Exception:
        return False
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_QUERY_VALUE,
        )
    except Exception:
        return False
    try:
        app_name_cf = str(app_name).strip().casefold()
        target_names_cf = {app_name_cf}
        if app_name_cf == APP_NAME.strip().casefold():
            target_names_cf |= {str(n).strip().casefold() for n in LEGACY_APP_NAMES}

        for ex_name in _iter_existing_value_names(key):
            if ex_name.strip().casefold() in target_names_cf:
                try:
                    val, _ = winreg.QueryValueEx(key, ex_name)
                    if bool(val and str(val).strip()):
                        return True
                except FileNotFoundError:
                    continue
        fallback_names = [str(app_name)]
        if app_name_cf == APP_NAME.strip().casefold():
            fallback_names.extend(LEGACY_APP_NAMES)
        for name in fallback_names:
            try:
                val, _ = winreg.QueryValueEx(key, str(name))
                if bool(val and str(val).strip()):
                    return True
            except FileNotFoundError:
                continue
        return False
    except Exception:
        return False
    finally:
        try:
            winreg.CloseKey(key)
        except Exception:
            pass


def refresh_autostart_command(app_name: str = APP_NAME) -> None:
    """应用启动时调用：自启动已启用但注册的命令指向旧路径（程序目录被移动、
    解释器更换）或存在历史旧名称时自动改写为当前名称和路径，避免自启动静默失效。"""
    try:
        import winreg
    except Exception:
        return
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_QUERY_VALUE,
        )
    except Exception:
        return
    current: Optional[str] = None
    has_legacy = False
    app_name_cf = str(app_name).strip().casefold()
    target_names_cf = {app_name_cf}
    if app_name_cf == APP_NAME.strip().casefold():
        target_names_cf |= {str(n).strip().casefold() for n in LEGACY_APP_NAMES}
    try:
        for ex_name in _iter_existing_value_names(key):
            name_cf = ex_name.strip().casefold()
            if name_cf in target_names_cf:
                try:
                    val, _ = winreg.QueryValueEx(key, ex_name)
                    if val:
                        current = str(val)
                        if name_cf != app_name_cf:
                            has_legacy = True
                        break
                except FileNotFoundError:
                    continue
        if current is None:
            fallback_names = [str(app_name)]
            if app_name_cf == APP_NAME.strip().casefold():
                fallback_names.extend(LEGACY_APP_NAMES)
            for name in fallback_names:
                try:
                    val, _ = winreg.QueryValueEx(key, str(name))
                    if val:
                        current = str(val)
                        if name.strip().casefold() != app_name_cf:
                            has_legacy = True
                        break
                except FileNotFoundError:
                    continue
    except Exception:
        current = None
    finally:
        try:
            winreg.CloseKey(key)
        except Exception:
            pass
    if current is None:
        return
    expected = _command_for_run_key()
    if current != expected or has_legacy:
        set_autostart(True, app_name)
