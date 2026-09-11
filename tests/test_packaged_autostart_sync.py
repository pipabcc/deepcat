"""成品副本测试不应修改用户的正式开机启动项。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from deepcat.ui.main_window.window_shell import WindowShellMixin


def test_packaged_smoke_can_skip_autostart_sync(monkeypatch):
    monkeypatch.setenv("DEEPCAT_SKIP_AUTOSTART_SYNC", "1")
    window = SimpleNamespace(_app_settings=SimpleNamespace(autostart=True))
    with patch("deepcat.utils.autostart.set_autostart") as update:
        WindowShellMixin._sync_autostart_if_enabled(window)
    update.assert_not_called()


def test_normal_startup_still_syncs_enabled_autostart(monkeypatch):
    monkeypatch.delenv("DEEPCAT_SKIP_AUTOSTART_SYNC", raising=False)
    window = SimpleNamespace(_app_settings=SimpleNamespace(autostart=True), _show_general_status=MagicMock())
    with patch("deepcat.utils.autostart.set_autostart", return_value=None) as update:
        WindowShellMixin._sync_autostart_if_enabled(window)
    update.assert_called_once_with(True)
