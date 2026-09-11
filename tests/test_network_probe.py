from __future__ import annotations

import time
from unittest.mock import Mock

from PyQt6.QtCore import QCoreApplication

from deepcat.ui.main_window.window_embedded_settings import WindowEmbeddedSettingsMixin
from deepcat.ui.main_window.tray import TrayMixin
from deepcat.ui.network_probe import (
    CLOUDFLARE_204_TARGET,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_READ_TIMEOUT_SECONDS,
    GOOGLE_204_TARGET,
    GOOGLE_TCP_TARGET,
    NETWORK_PROBE_TARGETS,
    NetworkProbeEngine,
    NetworkProbeKind,
    NetworkProbeMonitor,
    NetworkProbeResult,
    NetworkProbeStatus,
    _ProbeTask,
    _effective_probe_timeouts,
    _log_error_text,
    _normalize_proxy_url,
    _normalize_preferred_route,
    normalize_network_probe_kind,
    _proxy_candidates,
    run_network_probe,
)


def _result(
    *,
    ok: bool,
    target=GOOGLE_204_TARGET,
    route: str = "direct",
) -> NetworkProbeResult:
    return NetworkProbeResult(
        target=target,
        ok=ok,
        status_code=204 if ok else None,
        elapsed_ms=100,
        error="" if ok else "timeout",
        via_proxy=route == "proxy",
        route=route,
        route_elapsed_ms=80,
        probe_kind=NetworkProbeKind.HTTP_204.value,
    )


def _task(
    *,
    target=GOOGLE_204_TARGET,
    purpose: str = "regular",
) -> _ProbeTask:
    return _ProbeTask(
        generation=1,
        kind=NetworkProbeKind.HTTP_204,
        target=target,
        purpose=purpose,
        timeout_seconds=2.5,
        read_timeout_seconds=2.5,
        proxy_url="socks5://127.0.0.1:1080",
        preferred_route="direct",
        started_monotonic=time.monotonic(),
    )


def _state_machine_monitor() -> NetworkProbeMonitor:
    monitor = NetworkProbeMonitor()
    monitor._enabled = True
    monitor._generation = 1
    monitor._status = NetworkProbeStatus.HEALTHY
    monitor._connected = True
    monitor._preferred_route = "direct"
    monitor._start_http_probe = Mock()
    monitor._schedule = Mock()
    return monitor


def _tray_notification_dummy() -> object:
    dummy = type("DummyTray", (), {})()
    dummy._NETWORK_PROBE_NOTIFICATION_FAILURE_THRESHOLD = 2
    dummy._NETWORK_PROBE_NOTIFICATION_COOLDOWN_SECONDS = 60.0
    dummy._network_probe_notification_failure_count = 0
    dummy._network_probe_last_alert_monotonic = 0.0
    dummy._refresh_network_probe_action = Mock()
    dummy._refresh_tray_icon = Mock()
    dummy._refresh_tray_tooltip = Mock()
    dummy._force_tray_notification = Mock()
    return dummy


def test_network_probe_targets_use_independent_zero_body_endpoints() -> None:
    assert NETWORK_PROBE_TARGETS == (GOOGLE_204_TARGET, CLOUDFLARE_204_TARGET)
    assert GOOGLE_204_TARGET.url == "https://connectivitycheck.gstatic.com/generate_204"
    assert CLOUDFLARE_204_TARGET.url == "https://cp.cloudflare.com/generate_204"
    assert all(target.method == "GET" for target in NETWORK_PROBE_TARGETS)
    assert all(target.expected_status == 204 for target in NETWORK_PROBE_TARGETS)


def test_ping_mode_is_tcp_connect_to_google_443() -> None:
    assert normalize_network_probe_kind("ping") is NetworkProbeKind.TCP_CONNECT
    assert GOOGLE_TCP_TARGET.url == "tcp://google.com:443"
    assert GOOGLE_TCP_TARGET.method == "CONNECT"


def test_tcp_connect_mode_uses_direct_then_proxy_fallback() -> None:
    engine = Mock(spec=NetworkProbeEngine)
    engine.probe_tcp_direct.return_value = _result(ok=False, target=GOOGLE_TCP_TARGET, route="direct")
    proxy = _result(ok=True, target=GOOGLE_TCP_TARGET, route="proxy")
    engine.probe_tcp_proxy.return_value = proxy

    result = run_network_probe(
        GOOGLE_TCP_TARGET,
        proxy_url="http://127.0.0.1:7890",
        engine=engine,
        probe_kind=NetworkProbeKind.TCP_CONNECT,
    )

    assert result is proxy
    engine.probe_tcp_direct.assert_called_once()
    engine.probe_tcp_proxy.assert_called_once()
    engine.probe_http_direct.assert_not_called()
    engine.probe_http_proxy.assert_not_called()


def test_tcp_connect_mode_keeps_proxy_route_sticky() -> None:
    engine = Mock(spec=NetworkProbeEngine)
    proxy = _result(ok=True, target=GOOGLE_TCP_TARGET, route="proxy")
    engine.probe_tcp_proxy.return_value = proxy

    result = run_network_probe(
        GOOGLE_TCP_TARGET,
        proxy_url="socks5://127.0.0.1:1080",
        engine=engine,
        preferred_route="proxy",
        probe_kind=NetworkProbeKind.TCP_CONNECT,
    )

    assert result is proxy
    engine.probe_tcp_proxy.assert_called_once()
    engine.probe_tcp_direct.assert_not_called()


def test_network_probe_monitor_uses_reviewed_intervals() -> None:
    monitor = NetworkProbeMonitor()

    assert monitor._healthy_http_interval_ms == 5000
    assert monitor._degraded_http_interval_ms == 3000
    assert monitor._timeout_seconds == DEFAULT_CONNECT_TIMEOUT_SECONDS
    assert monitor._read_timeout_seconds == DEFAULT_READ_TIMEOUT_SECONDS
    assert monitor._timer.isSingleShot()
    assert monitor.probe_kind() == NetworkProbeKind.HTTP_204.value


def test_monitor_switches_mode_and_invalidates_inflight_result() -> None:
    monitor = _state_machine_monitor()
    monitor._busy = True
    old_task = _task()
    monitor._current_task = old_task
    old_generation = monitor._generation

    monitor.set_probe_kind(NetworkProbeKind.TCP_CONNECT)

    assert monitor.probe_kind() == NetworkProbeKind.TCP_CONNECT.value
    assert monitor._generation == old_generation + 1
    assert monitor._pending_immediate is True
    assert monitor.status() == NetworkProbeStatus.CHECKING.value

    monitor._handle_probe_result(old_task, _result(ok=False))

    monitor._start_http_probe.assert_not_called()


def test_proxy_normalization_preserves_explicit_scheme_and_infers_known_socks_port() -> None:
    assert _normalize_proxy_url("socks5://127.0.0.1:1080") == "socks5h://127.0.0.1:1080"
    assert _normalize_proxy_url("127.0.0.1:1080") == "socks5h://127.0.0.1:1080"
    assert _normalize_proxy_url("127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert _normalize_proxy_url("HTTP://proxy.example:8080") == "http://proxy.example:8080"


def test_unschemed_proxy_has_protocol_fallback_but_explicit_proxy_does_not() -> None:
    assert _proxy_candidates("127.0.0.1:1080") == (
        "socks5h://127.0.0.1:1080",
        "http://127.0.0.1:1080",
    )
    assert _proxy_candidates("http://127.0.0.1:7890") == ("http://127.0.0.1:7890",)


def test_probe_log_error_redacts_proxy_credentials() -> None:
    error = "ProxyError: http://user:secret@127.0.0.1:7890 failed"

    assert _log_error_text(error) == "ProxyError: http://***@127.0.0.1:7890 failed"


def test_preferred_route_normalization_defaults_to_direct() -> None:
    assert _normalize_preferred_route("proxy") == "proxy"
    assert _normalize_preferred_route("PROXY") == "proxy"
    assert _normalize_preferred_route("direct") == "direct"
    assert _normalize_preferred_route("unknown") == "direct"


def test_probe_never_times_out_before_read_limit() -> None:
    assert _effective_probe_timeouts(1.2, 2.5) == (2.5, 2.5)
    assert _effective_probe_timeouts(3.0, 2.5) == (3.0, 2.5)


def test_run_network_probe_skips_proxy_after_direct_or_tun_success() -> None:
    engine = Mock(spec=NetworkProbeEngine)
    direct = _result(ok=True, route="direct")
    engine.probe_http_direct.return_value = direct

    result = run_network_probe(GOOGLE_204_TARGET, proxy_url="socks5://127.0.0.1:1080", engine=engine)

    assert result is direct
    engine.probe_http_direct.assert_called_once()
    engine.probe_http_proxy.assert_not_called()


def test_run_network_probe_always_falls_back_to_saved_proxy_after_direct_failure() -> None:
    engine = Mock(spec=NetworkProbeEngine)
    engine.probe_http_direct.return_value = _result(ok=False, route="direct")
    proxy = _result(ok=True, route="proxy")
    engine.probe_http_proxy.return_value = proxy

    result = run_network_probe(GOOGLE_204_TARGET, proxy_url="socks5://127.0.0.1:1080", engine=engine)

    assert result is proxy
    engine.probe_http_proxy.assert_called_once()
    assert engine.probe_http_proxy.call_args.args[2] == "socks5://127.0.0.1:1080"


def test_run_network_probe_combines_direct_and_proxy_errors() -> None:
    engine = Mock(spec=NetworkProbeEngine)
    engine.probe_http_direct.return_value = _result(ok=False, route="direct")
    engine.probe_http_proxy.return_value = _result(ok=False, route="proxy")

    result = run_network_probe(GOOGLE_204_TARGET, proxy_url="", engine=engine)

    assert result.ok is False
    assert "直连/TUN失败" in result.error
    assert "配置代理失败" in result.error


def test_run_network_probe_skips_direct_after_preferred_proxy_success() -> None:
    engine = Mock(spec=NetworkProbeEngine)
    proxy = _result(ok=True, route="proxy")
    engine.probe_http_proxy.return_value = proxy

    result = run_network_probe(
        GOOGLE_204_TARGET,
        proxy_url="http://127.0.0.1:7890",
        engine=engine,
        preferred_route="proxy",
    )

    assert result is proxy
    engine.probe_http_proxy.assert_called_once()
    engine.probe_http_direct.assert_not_called()


def test_run_network_probe_falls_back_to_direct_after_preferred_proxy_failure() -> None:
    engine = Mock(spec=NetworkProbeEngine)
    engine.probe_http_proxy.return_value = _result(ok=False, route="proxy")
    direct = _result(ok=True, route="direct")
    engine.probe_http_direct.return_value = direct

    result = run_network_probe(
        GOOGLE_204_TARGET,
        proxy_url="http://127.0.0.1:7890",
        engine=engine,
        preferred_route="proxy",
    )

    assert result is direct
    engine.probe_http_proxy.assert_called_once()
    engine.probe_http_direct.assert_called_once()


def test_http_probe_streams_and_closes_response_without_reading_body() -> None:
    engine = NetworkProbeEngine()
    response = Mock()
    response.status_code = 204
    engine._direct_session.request = Mock(return_value=response)
    started = time.monotonic()

    result = engine.probe_http_direct(GOOGLE_204_TARGET, 2.5, started)

    assert result.ok is True
    kwargs = engine._direct_session.request.call_args.kwargs
    assert kwargs["stream"] is True
    assert kwargs["allow_redirects"] is False
    assert kwargs["proxies"] == {}
    assert kwargs["timeout"].total == 2.5
    assert kwargs["timeout"].connect_timeout == 2.5
    assert kwargs["timeout"].read_timeout == 2.5
    response.close.assert_called_once()
    engine.close()


def test_http_probe_honors_explicit_read_timeout() -> None:
    engine = NetworkProbeEngine()
    response = Mock()
    response.status_code = 204
    engine._direct_session.request = Mock(return_value=response)

    engine.probe_http_direct(
        GOOGLE_204_TARGET,
        2.5,
        time.monotonic(),
        read_timeout_seconds=3.0,
    )

    timeout = engine._direct_session.request.call_args.kwargs["timeout"]
    assert timeout.total == 3.0
    assert timeout.connect_timeout == 3.0
    assert timeout.read_timeout == 3.0
    engine.close()


def test_monitor_worker_thread_can_stop_restart_and_shutdown(monkeypatch) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    closed: list[bool] = []

    def direct_success(
        self,
        target,
        timeout_seconds,
        overall_started,
        read_timeout_seconds=DEFAULT_READ_TIMEOUT_SECONDS,
    ):
        return NetworkProbeResult(
            target=target,
            ok=True,
            status_code=204,
            elapsed_ms=1,
            route="direct",
            route_elapsed_ms=1,
        )

    monkeypatch.setattr(NetworkProbeEngine, "probe_http_direct", direct_success)
    monkeypatch.setattr(NetworkProbeEngine, "close", lambda self: closed.append(True))

    monitor = NetworkProbeMonitor(interval_ms=1000, degraded_interval_ms=1000)

    def process_until(predicate, timeout_seconds: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and not predicate():
            app.processEvents()
            time.sleep(0.005)
        app.processEvents()
        return bool(predicate())

    monitor.start()
    assert process_until(lambda: monitor.status() == NetworkProbeStatus.HEALTHY.value)
    monitor.stop()
    assert monitor.status() == NetworkProbeStatus.STOPPED.value

    monitor.start()
    assert process_until(lambda: monitor.status() == NetworkProbeStatus.HEALTHY.value)
    monitor.shutdown(wait_ms=1000)

    assert monitor.is_running() is False
    assert monitor._thread is None
    assert monitor._worker is None
    assert closed == [True]


def test_two_route_http_failure_turns_unavailable_without_yellow_state(monkeypatch) -> None:
    monitor = _state_machine_monitor()
    statuses: list[str] = []
    monitor.status_changed.connect(lambda status, result: statuses.append(status))
    task = _task()
    monkeypatch.setattr("deepcat.ui.network_probe.time.monotonic", lambda: task.started_monotonic + 0.1)

    monitor._handle_http_result(task, _result(ok=False))

    assert monitor.status() == NetworkProbeStatus.UNAVAILABLE.value
    assert statuses == [NetworkProbeStatus.UNAVAILABLE.value]
    monitor._start_http_probe.assert_not_called()
    monitor._schedule.assert_called_once()
    assert 2899 <= monitor._schedule.call_args.args[0] <= 2901


def test_http_success_stays_healthy_and_uses_five_second_cycle(monkeypatch) -> None:
    monitor = _state_machine_monitor()
    task = _task()
    monkeypatch.setattr("deepcat.ui.network_probe.time.monotonic", lambda: task.started_monotonic + 0.1)
    monitor._handle_http_result(task, _result(ok=True, route="proxy"))

    assert monitor.status() == NetworkProbeStatus.HEALTHY.value
    monitor._schedule.assert_called_once()
    assert 4899 <= monitor._schedule.call_args.args[0] <= 4901


def test_monitor_switches_to_successful_proxy_route() -> None:
    monitor = _state_machine_monitor()
    task = _task()

    monitor._handle_probe_result(task, _result(ok=True, route="proxy"))

    assert monitor._preferred_route == "proxy"


def test_monitor_switches_back_to_successful_direct_route() -> None:
    monitor = _state_machine_monitor()
    monitor._preferred_route = "proxy"
    task = _task()

    monitor._handle_probe_result(task, _result(ok=True, route="direct"))

    assert monitor._preferred_route == "direct"


def test_monitor_keeps_preferred_route_when_both_routes_fail() -> None:
    monitor = _state_machine_monitor()
    monitor._preferred_route = "proxy"
    task = _task()

    monitor._handle_probe_result(task, _result(ok=False, route="proxy"))

    assert monitor._preferred_route == "proxy"


def test_next_task_uses_monitor_preferred_route(monkeypatch) -> None:
    monitor = NetworkProbeMonitor(proxy_url_provider=lambda: "http://127.0.0.1:7890")
    monitor._enabled = True
    monitor._generation = 1
    monitor._preferred_route = "proxy"
    emitted: list[_ProbeTask] = []
    monitor._probe_requested.connect(emitted.append)
    monkeypatch.setattr("deepcat.ui.network_probe.time.monotonic", lambda: 100.0)

    monitor._start_http_probe(GOOGLE_204_TARGET, purpose="regular")

    assert len(emitted) == 1
    assert emitted[0].preferred_route == "proxy"


def test_immediate_probe_can_override_preferred_route() -> None:
    monitor = _state_machine_monitor()
    monitor._start_http_probe = Mock()

    monitor.request_immediate_probe(preferred_route="proxy")

    assert monitor.preferred_route() == "proxy"
    monitor._start_http_probe.assert_called_once_with(GOOGLE_204_TARGET, purpose="manual")


def test_busy_immediate_probe_applies_requested_route_after_inflight_result() -> None:
    monitor = _state_machine_monitor()
    monitor._busy = True
    monitor._current_task = _task()
    monitor._schedule = NetworkProbeMonitor._schedule.__get__(monitor, NetworkProbeMonitor)
    monitor._run_pending_immediate_probe = NetworkProbeMonitor._run_pending_immediate_probe.__get__(
        monitor,
        NetworkProbeMonitor,
    )

    monitor.request_immediate_probe(preferred_route="proxy")
    assert monitor._pending_preferred_route == "proxy"

    monitor._handle_probe_result(_task(), _result(ok=True, route="direct"))

    assert monitor.preferred_route() == "proxy"
    assert monitor._pending_preferred_route is None
    monitor._start_http_probe.assert_called_once_with(GOOGLE_204_TARGET, purpose="manual")


def test_recovery_http_success_turns_healthy_without_consecutive_success_threshold() -> None:
    monitor = _state_machine_monitor()
    monitor._status = NetworkProbeStatus.UNAVAILABLE
    monitor._connected = False
    events: list[bool] = []
    monitor.connectivity_changed.connect(lambda connected, result: events.append(connected))

    monitor._handle_http_result(
        _task(purpose="recovery"),
        _result(ok=True, route="proxy"),
    )

    assert monitor.status() == NetworkProbeStatus.HEALTHY.value
    assert events == [True]


def test_network_probe_notification_requires_two_consecutive_failures(monkeypatch) -> None:
    dummy = _tray_notification_dummy()
    result = _result(ok=False)
    monotonic = iter((100.0,))
    monkeypatch.setattr("deepcat.ui.main_window.tray.time.monotonic", lambda: next(monotonic))

    TrayMixin._on_network_probe_status_changed(dummy, NetworkProbeStatus.UNAVAILABLE.value, result)
    dummy._force_tray_notification.assert_not_called()

    TrayMixin._on_network_probe_status_changed(dummy, NetworkProbeStatus.UNAVAILABLE.value, result)
    dummy._force_tray_notification.assert_called_once()
    assert "连续 2 轮检测失败" in dummy._force_tray_notification.call_args.args[1]


def test_first_network_failure_alert_is_not_suppressed_soon_after_boot(monkeypatch) -> None:
    dummy = _tray_notification_dummy()
    result = _result(ok=False)
    monkeypatch.setattr("deepcat.ui.main_window.tray.time.monotonic", lambda: 12.0)

    for _ in range(2):
        TrayMixin._on_network_probe_status_changed(dummy, NetworkProbeStatus.UNAVAILABLE.value, result)

    dummy._force_tray_notification.assert_called_once()


def test_network_probe_success_resets_failure_streak_but_keeps_alert_cooldown(monkeypatch) -> None:
    dummy = _tray_notification_dummy()
    result = _result(ok=False)
    now = iter((100.0, 130.0, 161.0))
    monkeypatch.setattr("deepcat.ui.main_window.tray.time.monotonic", lambda: next(now))

    for _ in range(2):
        TrayMixin._on_network_probe_status_changed(dummy, NetworkProbeStatus.UNAVAILABLE.value, result)
    assert dummy._force_tray_notification.call_count == 1

    TrayMixin._on_network_probe_status_changed(dummy, NetworkProbeStatus.HEALTHY.value, _result(ok=True))
    assert dummy._network_probe_notification_failure_count == 0

    for _ in range(2):
        TrayMixin._on_network_probe_status_changed(dummy, NetworkProbeStatus.UNAVAILABLE.value, result)
    assert dummy._force_tray_notification.call_count == 1

    TrayMixin._on_network_probe_status_changed(dummy, NetworkProbeStatus.HEALTHY.value, _result(ok=True))
    for _ in range(2):
        TrayMixin._on_network_probe_status_changed(dummy, NetworkProbeStatus.UNAVAILABLE.value, result)
    assert dummy._force_tray_notification.call_count == 2


def test_continuous_network_failure_can_remind_again_after_cooldown(monkeypatch) -> None:
    dummy = _tray_notification_dummy()
    result = _result(ok=False)
    now = iter((100.0, 159.9, 160.0))
    monkeypatch.setattr("deepcat.ui.main_window.tray.time.monotonic", lambda: next(now))

    TrayMixin._on_network_probe_status_changed(dummy, NetworkProbeStatus.UNAVAILABLE.value, result)
    TrayMixin._on_network_probe_status_changed(dummy, NetworkProbeStatus.UNAVAILABLE.value, result)
    assert dummy._force_tray_notification.call_count == 1

    TrayMixin._on_network_probe_status_changed(dummy, NetworkProbeStatus.UNAVAILABLE.value, result)
    TrayMixin._on_network_probe_status_changed(dummy, NetworkProbeStatus.UNAVAILABLE.value, result)
    assert dummy._force_tray_notification.call_count == 2


def test_opening_tray_menu_does_not_force_an_extra_network_probe(monkeypatch) -> None:
    monitor = Mock()
    monitor.is_running.return_value = True
    dummy = type("DummyTrayMenu", (), {})()
    dummy._network_probe_monitor = monitor
    dummy._tray = None
    dummy._tray_menu = None
    dummy._refresh_tray_pinned_action = Mock()
    dummy._refresh_network_probe_action = Mock()
    dummy.isMinimized = Mock(return_value=False)
    dummy.isVisible = Mock(return_value=True)
    dummy.winId = Mock(return_value=0)
    dummy._tray_menu_visible = False
    dummy._was_minimized_before_tray_menu = False
    monkeypatch.setattr("deepcat.ui.main_window.tray.write_crash_breadcrumb", Mock())
    monkeypatch.setattr("deepcat.ui.main_window.tray.QTimer.singleShot", Mock())

    TrayMixin._on_tray_menu_about_to_show(dummy)

    monitor.request_immediate_probe.assert_not_called()


def test_stale_result_after_stop_cannot_change_status() -> None:
    monitor = NetworkProbeMonitor()
    monitor._enabled = False
    monitor._generation = 2
    monitor._status = NetworkProbeStatus.STOPPED
    monitor._busy = True

    monitor._handle_probe_result(_task(), _result(ok=True))

    assert monitor.status() == NetworkProbeStatus.STOPPED.value
    assert monitor.last_result() is None


def test_saved_proxy_address_change_requests_immediate_probe_when_proxy_is_active(monkeypatch) -> None:
    monitor = Mock()
    monitor.is_running.return_value = True
    monitor.preferred_route.return_value = "proxy"
    monitor.status.return_value = NetworkProbeStatus.HEALTHY.value
    dummy = type("Dummy", (), {})()
    dummy._proxy_url = "socks5://127.0.0.1:1080"
    dummy._network_probe_monitor = monitor
    dummy._network_probe_proxy_url = lambda: dummy._proxy_url
    dummy._sync_settings_after_change = lambda: None

    def apply_settings(_self) -> None:
        dummy._proxy_url = "http://127.0.0.1:7890"

    monkeypatch.setattr(
        "deepcat.ui.main_window.window_embedded_settings.SettingsDialog._apply_translator_settings",
        apply_settings,
    )

    WindowEmbeddedSettingsMixin._apply_translator_settings(dummy)

    monitor.request_immediate_probe.assert_called_once_with(preferred_route="proxy")


def test_saved_proxy_address_change_does_not_probe_when_direct_is_healthy(monkeypatch) -> None:
    monitor = Mock()
    monitor.is_running.return_value = True
    monitor.preferred_route.return_value = "direct"
    monitor.status.return_value = NetworkProbeStatus.HEALTHY.value
    dummy = type("Dummy", (), {})()
    dummy._proxy_url = "socks5://127.0.0.1:1080"
    dummy._network_probe_monitor = monitor
    dummy._network_probe_proxy_url = lambda: dummy._proxy_url
    dummy._sync_settings_after_change = lambda: None

    def apply_settings(_self) -> None:
        dummy._proxy_url = "http://127.0.0.1:7890"

    monkeypatch.setattr(
        "deepcat.ui.main_window.window_embedded_settings.SettingsDialog._apply_translator_settings",
        apply_settings,
    )

    WindowEmbeddedSettingsMixin._apply_translator_settings(dummy)

    monitor.request_immediate_probe.assert_not_called()


def test_saved_proxy_address_change_probes_proxy_when_network_is_unavailable(monkeypatch) -> None:
    monitor = Mock()
    monitor.is_running.return_value = True
    monitor.preferred_route.return_value = "direct"
    monitor.status.return_value = NetworkProbeStatus.UNAVAILABLE.value
    dummy = type("Dummy", (), {})()
    dummy._proxy_url = "socks5://127.0.0.1:1080"
    dummy._network_probe_monitor = monitor
    dummy._network_probe_proxy_url = lambda: dummy._proxy_url
    dummy._sync_settings_after_change = lambda: None

    def apply_settings(_self) -> None:
        dummy._proxy_url = "http://127.0.0.1:7890"

    monkeypatch.setattr(
        "deepcat.ui.main_window.window_embedded_settings.SettingsDialog._apply_translator_settings",
        apply_settings,
    )

    WindowEmbeddedSettingsMixin._apply_translator_settings(dummy)

    monitor.request_immediate_probe.assert_called_once_with(preferred_route="proxy")


def test_unchanged_proxy_address_does_not_add_probe_traffic(monkeypatch) -> None:
    monitor = Mock()
    monitor.is_running.return_value = True
    dummy = type("Dummy", (), {})()
    dummy._proxy_url = "socks5://127.0.0.1:1080"
    dummy._network_probe_monitor = monitor
    dummy._network_probe_proxy_url = lambda: dummy._proxy_url
    dummy._sync_settings_after_change = lambda: None
    monkeypatch.setattr(
        "deepcat.ui.main_window.window_embedded_settings.SettingsDialog._apply_translator_settings",
        lambda _self: None,
    )

    WindowEmbeddedSettingsMixin._apply_translator_settings(dummy)

    monitor.request_immediate_probe.assert_not_called()
