"""本地 API 服务的回环自检与 Windows 防火墙放行助手测试。

背景：服务 bind 成功不代表客户端连得上。防火墙在 WFP 层丢弃入站 SYN 时端口仍是
LISTENING，客户端只会停在 SYN_SENT 直到超时，这里覆盖该场景的判定与放行动作。
"""

from __future__ import annotations

import socket
from unittest import mock

import pytest

from deepcat.translation_server import (
    PROBE_STATUS_REFUSED,
    PROBE_STATUS_TIMEOUT,
    LoopbackProbeResult,
    TranslationServiceProbeReport,
    probe_background_translation_server,
    probe_translation_service_loopback,
    start_background_translation_server,
    stop_background_translation_server,
)
from deepcat.utils import windows_firewall


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def running_service():
    host, port = start_background_translation_server(host="127.0.0.1", port=0, api_key="probe-test-key")
    try:
        yield host, port
    finally:
        stop_background_translation_server()


def test_loopback_probe_passes_for_running_service(running_service) -> None:
    _, port = running_service
    report = probe_translation_service_loopback(port)
    assert report.is_healthy
    assert not report.is_blocked
    assert report.ipv4 is not None and report.ipv4.reachable


def test_loopback_probe_reports_not_healthy_for_unused_port() -> None:
    """未监听的环回端口在部分 Windows 环境同样会静默超时，所以只断言不可达。

    区分「没启动」与「被拦截」靠 probe_background_translation_server()：服务没 bind 时
    它直接返回空报告，不会把超时误判成防火墙问题。
    """
    report = probe_translation_service_loopback(_unused_port())
    assert not report.is_healthy
    assert report.ipv4 is not None
    assert report.ipv4.status in {PROBE_STATUS_REFUSED, PROBE_STATUS_TIMEOUT}


def test_background_probe_reports_not_started_without_service() -> None:
    report = probe_background_translation_server()
    assert report.probes == ()
    assert not report.is_healthy
    assert not report.is_blocked
    assert report.reason == "本地 API 服务尚未启动。"


def test_loopback_probe_without_port_reports_not_started() -> None:
    report = probe_translation_service_loopback(0)
    assert report.probes == ()
    assert report.reason == "本地 API 服务尚未启动。"


def test_report_reason_prefers_timeout_over_refused() -> None:
    report = TranslationServiceProbeReport(
        port=11888,
        probes=(
            LoopbackProbeResult("127.0.0.1", 11888, PROBE_STATUS_TIMEOUT, 1500, "connect 超时"),
            LoopbackProbeResult("::1", 11888, PROBE_STATUS_REFUSED, 1, "连接被拒绝"),
        ),
    )
    assert report.is_blocked
    assert "防火墙" in report.reason
    assert "127.0.0.1" in report.reason


def test_build_firewall_allow_parameters_quotes_program_path() -> None:
    parameters = windows_firewall.build_firewall_allow_parameters(r"D:\Tencent Files\DeepCat\deepcat.exe")
    assert parameters.startswith("advfirewall firewall add rule ")
    assert 'program="D:\\Tencent Files\\DeepCat\\deepcat.exe"' in parameters
    assert 'name="DeepCat"' in parameters
    assert "enable=yes" in parameters
    assert "profile=any" in parameters


@pytest.mark.parametrize("bad_path", ["", "   ", 'C:\\a"b\\deepcat.exe'])
def test_build_firewall_allow_parameters_rejects_invalid_program(bad_path: str) -> None:
    with pytest.raises(ValueError):
        windows_firewall.build_firewall_allow_parameters(bad_path)


def test_request_firewall_allow_refuses_source_run(monkeypatch) -> None:
    monkeypatch.setattr(windows_firewall, "is_packaged_app", lambda: False)
    issued, message = windows_firewall.request_firewall_allow(r"C:\DeepCat\deepcat.exe")
    assert issued is False
    assert "一键允许" in message


def test_request_firewall_allow_launches_elevated_netsh(monkeypatch) -> None:
    monkeypatch.setattr(windows_firewall, "is_packaged_app", lambda: True)
    calls: dict[str, object] = {}

    def fake_shell_execute(hwnd, operation, file, parameters, directory, show):
        calls.update(operation=operation, file=file, parameters=parameters)
        return 33  # 大于 32 表示进程创建成功

    monkeypatch.setattr(windows_firewall.ctypes.windll.shell32, "ShellExecuteW", fake_shell_execute)
    issued, message = windows_firewall.request_firewall_allow(r"C:\DeepCat\deepcat.exe")
    assert issued is True
    assert calls["operation"] == "runas"
    assert calls["file"] == "netsh"
    assert 'program="C:\\DeepCat\\deepcat.exe"' in str(calls["parameters"])
    assert "已申请放行" in message


def test_firewall_button_visibility_follows_probe_and_packaging(monkeypatch) -> None:
    from deepcat.ui.settings_dialog.dialog import SettingsDialog

    button = mock.MagicMock()
    stub = mock.MagicMock()
    stub._local_translation_firewall_btn = button
    stub._translator_footer_active = True
    stub._local_translation_service_probe_report = TranslationServiceProbeReport(
        port=11888,
        probes=(LoopbackProbeResult("127.0.0.1", 11888, PROBE_STATUS_TIMEOUT, 1500, "connect 超时"),),
    )

    monkeypatch.setattr(windows_firewall, "is_packaged_app", lambda: True)
    SettingsDialog._refresh_local_translation_firewall_button(stub)
    button.setVisible.assert_called_with(True)

    monkeypatch.setattr(windows_firewall, "is_packaged_app", lambda: False)
    SettingsDialog._refresh_local_translation_firewall_button(stub)
    button.setVisible.assert_called_with(False)


def test_sync_state_unbound_call_works_for_main_window_like_self(monkeypatch) -> None:
    """MainWindow 以内嵌设置页复用 SettingsDialog 未绑定方法时必须能走完全程。"""
    from deepcat import translation_server
    from deepcat.ui.settings_dialog.dialog import SettingsDialog

    healthy_report = TranslationServiceProbeReport(
        port=11888,
        probes=(
            LoopbackProbeResult("127.0.0.1", 11888, "reachable", 1, ""),
            LoopbackProbeResult("::1", 11888, "reachable", 1, ""),
        ),
    )
    monkeypatch.setattr(
        translation_server,
        "start_background_translation_server",
        lambda **kwargs: ("127.0.0.1", 11888),
    )
    monkeypatch.setattr(translation_server, "probe_background_translation_server", lambda timeout=1.5: healthy_report)

    stub = mock.MagicMock()
    stub._translator = {
        "local_translation_service_enabled": True,
        "local_translation_service_host": "127.0.0.1",
        "local_translation_service_port": 11888,
        "local_translation_service_api_key": "probe-test-key",
    }
    stub._local_translation_service_config = lambda: ("127.0.0.1", 11888, "probe-test-key")
    stub._local_translation_firewall_btn = mock.MagicMock()
    stub._translator_footer_active = True

    assert SettingsDialog._sync_local_translation_service_state(stub, show_errors=False) is True
    assert stub._local_translation_service_probe_report is healthy_report
    stub._local_translation_service_switch.setToolTip.assert_called()
