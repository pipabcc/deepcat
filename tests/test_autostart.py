from __future__ import annotations

import os
import sys
import winreg
from pathlib import Path
from unittest.mock import patch

import pytest

from deepcat.utils.autostart import (
    APP_NAME,
    LEGACY_APP_NAMES,
    _command_for_run_key,
    is_autostart_enabled,
    refresh_autostart_command,
    set_autostart,
)


def test_command_for_run_key_frozen() -> None:
    with patch.object(sys, "frozen", True, create=True), patch.object(
        sys, "executable", r"C:\Programs\DeepCat\DeepCat.exe"
    ):
        cmd = _command_for_run_key()
        assert r'"C:\Programs\DeepCat\DeepCat.exe" --gui' == cmd


def test_command_for_run_key_unfrozen() -> None:
    with patch.object(sys, "frozen", False, create=True):
        cmd = _command_for_run_key()
        assert "--gui" in cmd
        assert "deepcat.main" in cmd


@pytest.fixture
def temp_autostart_app_name():
    test_name = f"DeepCat_UnitTestCase_{os.getpid()}"
    legacy_test_name = f"DeepCat_LegacyTest_{os.getpid()}"
    try:
        yield test_name, legacy_test_name
    finally:
        set_autostart(False, app_name=test_name)
        # 清理 legacy
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            for n in (test_name, legacy_test_name):
                try:
                    winreg.DeleteValue(key, n)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception:
            pass


def test_set_autostart_and_query_flow(temp_autostart_app_name) -> None:
    test_name, _ = temp_autostart_app_name

    # 1. 确保初始未启用
    set_autostart(False, app_name=test_name)
    assert not is_autostart_enabled(app_name=test_name)

    # 2. 开启自启
    err = set_autostart(True, app_name=test_name)
    assert err is None
    assert is_autostart_enabled(app_name=test_name)

    # 3. 验证真实注册表确实存在值
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_QUERY_VALUE,
    )
    try:
        val, typ = winreg.QueryValueEx(key, test_name)
        assert typ == winreg.REG_SZ
        assert len(val) > 0
    finally:
        winreg.CloseKey(key)

    # 4. 关闭自启
    err = set_autostart(False, app_name=test_name)
    assert err is None
    assert not is_autostart_enabled(app_name=test_name)

    # 5. 验证真实注册表中已被彻底删除
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_QUERY_VALUE,
    )
    try:
        with pytest.raises(FileNotFoundError):
            winreg.QueryValueEx(key, test_name)
    finally:
        winreg.CloseKey(key)


def test_case_insensitive_protection(temp_autostart_app_name) -> None:
    """验证大小写不同的同名项不会在清理循环中导致新项被秒删。"""
    test_name, _ = temp_autostart_app_name

    # 模拟 APP_NAME 为小写，而 LEGACY_APP_NAMES 包含其大写或反之
    with patch("deepcat.utils.autostart.APP_NAME", test_name), patch(
        "deepcat.utils.autostart.LEGACY_APP_NAMES",
        (test_name.lower(), test_name.upper(), "SomeOtherLegacy"),
    ):
        err = set_autostart(True, app_name=test_name)
        assert err is None
        # 核心断言：开启后必须依然处于启用状态，不能被内部清理循环误删
        assert is_autostart_enabled(app_name=test_name)


def test_refresh_and_legacy_migration(temp_autostart_app_name) -> None:
    test_name, legacy_name = temp_autostart_app_name

    # 先以 legacy 名称写入注册表（模拟老版本创建的启动项）
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    )
    winreg.SetValueEx(key, legacy_name, 0, winreg.REG_SZ, "old_dummy_command")
    winreg.CloseKey(key)

    with patch("deepcat.utils.autostart.APP_NAME", test_name), patch(
        "deepcat.utils.autostart.LEGACY_APP_NAMES", (legacy_name,)
    ):
        # 应该检测到旧版已开启
        assert is_autostart_enabled(app_name=test_name)

        # 触发启动刷新
        refresh_autostart_command(app_name=test_name)

        # 验证新项已建立，旧项已被清除
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_QUERY_VALUE,
        )
        try:
            with pytest.raises(FileNotFoundError):
                winreg.QueryValueEx(key, legacy_name)

            val, _ = winreg.QueryValueEx(key, test_name)
            assert "old_dummy_command" not in val
        finally:
            winreg.CloseKey(key)
