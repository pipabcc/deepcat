from __future__ import annotations

import logging
import re
import socket
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot

from deepcat.utils.logger import get_logger


logger = get_logger(
    "deepcat.network_probe",
    level=logging.INFO,
    enable_console=False,
)

DEFAULT_CONNECT_TIMEOUT_SECONDS = 2.5
DEFAULT_READ_TIMEOUT_SECONDS = 2.5
MAX_ROUTE_ATTEMPTS_PER_ROUND = 3


@dataclass(frozen=True)
class NetworkProbeTarget:
    name: str
    url: str
    method: str
    expected_status: int


class NetworkProbeKind(str, Enum):
    HTTP_204 = "http_204"
    TCP_CONNECT = "tcp_connect"


class NetworkProbeStatus(str, Enum):
    STOPPED = "stopped"
    CHECKING = "checking"
    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class NetworkProbeResult:
    target: NetworkProbeTarget
    ok: bool
    status_code: Optional[int]
    elapsed_ms: int
    error: str = ""
    via_proxy: bool = False
    route: str = "direct"
    route_elapsed_ms: int = 0
    probe_kind: str = NetworkProbeKind.HTTP_204.value


GOOGLE_204_TARGET = NetworkProbeTarget(
    name="Google 204",
    url="https://connectivitycheck.gstatic.com/generate_204",
    method="GET",
    expected_status=204,
)

CLOUDFLARE_204_TARGET = NetworkProbeTarget(
    name="Cloudflare 204",
    url="https://cp.cloudflare.com/generate_204",
    method="GET",
    expected_status=204,
)

GOOGLE_TCP_TARGET = NetworkProbeTarget(
    name="Google Ping",
    url="tcp://google.com:443",
    method="CONNECT",
    expected_status=0,
)

NETWORK_PROBE_TARGETS: tuple[NetworkProbeTarget, ...] = (
    GOOGLE_204_TARGET,
    CLOUDFLARE_204_TARGET,
)


# 只收录通常不会作为 HTTP 代理使用的 SOCKS5 端口。Clash 混合端口 7890
# 和 v2rayN HTTP 端口 10809 默认按 HTTP CONNECT 处理。
_SOCKS5_PORTS: frozenset[int] = frozenset({1080, 1081, 1086, 10808})


def _infer_proxy_scheme(host_port: str) -> str:
    port = 0
    if ":" in host_port:
        try:
            port = int(host_port.rsplit(":", 1)[1].strip())
        except ValueError:
            pass
    if port in _SOCKS5_PORTS:
        return f"socks5h://{host_port}"
    return f"http://{host_port}"


def _normalize_proxy_url(proxy_url: str) -> str:
    proxy = str(proxy_url or "").strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        return _infer_proxy_scheme(proxy)
    scheme, separator, remainder = proxy.partition("://")
    normalized_scheme = scheme.lower()
    if normalized_scheme == "socks5":
        normalized_scheme = "socks5h"
    return f"{normalized_scheme}{separator}{remainder}"


def _proxy_candidates(proxy_url: str) -> tuple[str, ...]:
    raw_proxy = str(proxy_url or "").strip()
    primary = _normalize_proxy_url(raw_proxy)
    if not primary:
        return ()

    candidates = [primary]
    # 无协议配置无法可靠区分混合端口和 SOCKS5，自适应回退只在首选协议失败后发生。
    if "://" not in raw_proxy:
        if primary.startswith("socks5h://"):
            candidates.append(primary.replace("socks5h://", "http://", 1))
        elif primary.startswith("http://"):
            candidates.append(primary.replace("http://", "socks5h://", 1))
    return tuple(dict.fromkeys(candidates))


def _bounded_timeout(timeout_seconds: float) -> float:
    return max(0.2, float(timeout_seconds))


def _effective_probe_timeouts(
    connect_timeout_seconds: float,
    read_timeout_seconds: float,
) -> tuple[float, float]:
    """探测任一阶段均不得早于读取上限超时，避免 TLS 握手被误报为读取失败。"""

    read_timeout = _bounded_timeout(read_timeout_seconds)
    connect_timeout = max(_bounded_timeout(connect_timeout_seconds), read_timeout)
    return connect_timeout, read_timeout


def _normalize_preferred_route(route: str) -> str:
    return "proxy" if str(route or "").strip().lower() == "proxy" else "direct"


def normalize_network_probe_kind(kind: object) -> NetworkProbeKind:
    if isinstance(kind, NetworkProbeKind):
        return kind
    raw = str(kind or "").strip().lower()
    if raw in {"ping", "tcp", NetworkProbeKind.TCP_CONNECT.value}:
        return NetworkProbeKind.TCP_CONNECT
    return NetworkProbeKind.HTTP_204


def _error_text(exc: BaseException) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _log_error_text(error: str, limit: int = 240) -> str:
    """把异常压成单行短文本，避免探测日志被堆栈或响应正文淹没。"""

    compact = " ".join(str(error or "").split())
    compact = re.sub(
        r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/@\s]+)@",
        r"\1***@",
        compact,
    )
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(0, limit - 3)]}..."


class NetworkProbeEngine:
    """在单一后台线程中复用直连和显式代理连接池。"""

    def __init__(self) -> None:
        import requests

        self._requests = requests
        self._direct_session = self._new_session()
        self._proxy_session = self._new_session()

    def _new_session(self):
        session = self._requests.Session()
        session.trust_env = False
        adapter = self._requests.adapters.HTTPAdapter(
            pool_connections=4,
            pool_maxsize=2,
            max_retries=0,
            pool_block=False,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def close(self) -> None:
        self._direct_session.close()
        self._proxy_session.close()

    def probe_http_direct(
        self,
        target: NetworkProbeTarget,
        connect_timeout_seconds: float,
        overall_started: float,
        read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
    ) -> NetworkProbeResult:
        return self._probe_http(
            session=self._direct_session,
            target=target,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            overall_started=overall_started,
            proxies={},
            route="direct",
            via_proxy=False,
        )

    def probe_http_proxy(
        self,
        target: NetworkProbeTarget,
        connect_timeout_seconds: float,
        proxy_url: str,
        overall_started: float,
        read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
    ) -> NetworkProbeResult:
        candidates = _proxy_candidates(proxy_url)
        if not candidates:
            elapsed_ms = int((time.monotonic() - overall_started) * 1000)
            result = NetworkProbeResult(
                target=target,
                ok=False,
                status_code=None,
                elapsed_ms=elapsed_ms,
                error="未配置代理地址",
                via_proxy=True,
                route="proxy",
                probe_kind=NetworkProbeKind.HTTP_204.value,
            )
            logger.info(
                "network_probe route=proxy target=%s ok=false status=none elapsed_ms=%d "
                "route_elapsed_ms=0 error=%s",
                target.name,
                elapsed_ms,
                _log_error_text(result.error),
            )
            return result

        last_result: Optional[NetworkProbeResult] = None
        for candidate_index, candidate in enumerate(candidates, start=1):
            proxies = {"http": candidate, "https": candidate}
            last_result = self._probe_http(
                session=self._proxy_session,
                target=target,
                connect_timeout_seconds=connect_timeout_seconds,
                read_timeout_seconds=read_timeout_seconds,
                overall_started=overall_started,
                proxies=proxies,
                route="proxy",
                via_proxy=True,
            )
            logger.info(
                "network_probe proxy_candidate=%d/%d scheme=%s target=%s ok=%s status=%s "
                "route_elapsed_ms=%d error=%s",
                candidate_index,
                len(candidates),
                candidate.split(":", 1)[0].lower(),
                target.name,
                str(last_result.ok).lower(),
                last_result.status_code if last_result.status_code is not None else "none",
                last_result.route_elapsed_ms,
                _log_error_text(last_result.error),
            )
            if last_result.ok:
                return last_result
        assert last_result is not None
        return last_result

    def probe_tcp_direct(
        self,
        target: NetworkProbeTarget,
        timeout_seconds: float,
        overall_started: float,
    ) -> NetworkProbeResult:
        host, port = self._tcp_target_address(target)
        route_started = time.monotonic()
        error = ""
        connection = None
        try:
            connection = socket.create_connection(
                (host, port),
                timeout=_bounded_timeout(timeout_seconds),
            )
        except Exception as exc:
            error = _error_text(exc)
        finally:
            if connection is not None:
                connection.close()
        return self._tcp_result(
            target=target,
            ok=not error,
            error=error,
            overall_started=overall_started,
            route_started=route_started,
            route="direct",
            via_proxy=False,
        )

    def probe_tcp_proxy(
        self,
        target: NetworkProbeTarget,
        timeout_seconds: float,
        proxy_url: str,
        overall_started: float,
    ) -> NetworkProbeResult:
        candidates = _proxy_candidates(proxy_url)
        if not candidates:
            return self._tcp_result(
                target=target,
                ok=False,
                error="未配置代理地址",
                overall_started=overall_started,
                route_started=time.monotonic(),
                route="proxy",
                via_proxy=True,
            )

        last_result: Optional[NetworkProbeResult] = None
        for candidate_index, candidate in enumerate(candidates, start=1):
            last_result = self._probe_tcp_proxy_candidate(
                target,
                timeout_seconds,
                candidate,
                overall_started,
            )
            logger.info(
                "network_probe proxy_candidate=%d/%d kind=tcp_connect scheme=%s "
                "target=%s ok=%s route_elapsed_ms=%d error=%s",
                candidate_index,
                len(candidates),
                candidate.split(":", 1)[0].lower(),
                target.name,
                str(last_result.ok).lower(),
                last_result.route_elapsed_ms,
                _log_error_text(last_result.error),
            )
            if last_result.ok:
                return last_result
        assert last_result is not None
        return last_result

    def _probe_tcp_proxy_candidate(
        self,
        target: NetworkProbeTarget,
        timeout_seconds: float,
        proxy_url: str,
        overall_started: float,
    ) -> NetworkProbeResult:
        route_started = time.monotonic()
        error = ""
        connection = None
        try:
            import socks

            target_host, target_port = self._tcp_target_address(target)
            parsed = urlsplit(proxy_url)
            proxy_host = parsed.hostname
            if not proxy_host:
                raise ValueError("代理地址缺少主机名")

            scheme = parsed.scheme.lower()
            proxy_types = {
                "http": socks.PROXY_TYPE_HTTP,
                "socks4": socks.PROXY_TYPE_SOCKS4,
                "socks4a": socks.PROXY_TYPE_SOCKS4,
                "socks5": socks.PROXY_TYPE_SOCKS5,
                "socks5h": socks.PROXY_TYPE_SOCKS5,
            }
            if scheme not in proxy_types:
                raise ValueError(f"TCP 探测不支持代理协议: {scheme or '未知'}")

            default_port = 1080 if scheme.startswith("socks") else 80
            proxy_port = int(parsed.port or default_port)
            username = unquote(parsed.username) if parsed.username is not None else None
            password = unquote(parsed.password) if parsed.password is not None else None
            connection = socks.socksocket()
            connection.set_proxy(
                proxy_types[scheme],
                proxy_host,
                proxy_port,
                rdns=scheme != "socks4",
                username=username,
                password=password,
            )
            connection.settimeout(_bounded_timeout(timeout_seconds))
            connection.connect((target_host, target_port))
        except Exception as exc:
            error = _error_text(exc)
        finally:
            if connection is not None:
                connection.close()
        return self._tcp_result(
            target=target,
            ok=not error,
            error=error,
            overall_started=overall_started,
            route_started=route_started,
            route="proxy",
            via_proxy=True,
        )

    @staticmethod
    def _tcp_target_address(target: NetworkProbeTarget) -> tuple[str, int]:
        parsed = urlsplit(target.url)
        host = parsed.hostname
        if not host:
            raise ValueError("TCP 探测目标缺少主机名")
        return host, int(parsed.port or 443)

    @staticmethod
    def _tcp_result(
        *,
        target: NetworkProbeTarget,
        ok: bool,
        error: str,
        overall_started: float,
        route_started: float,
        route: str,
        via_proxy: bool,
    ) -> NetworkProbeResult:
        result = NetworkProbeResult(
            target=target,
            ok=ok,
            status_code=None,
            elapsed_ms=int((time.monotonic() - overall_started) * 1000),
            error=error,
            via_proxy=via_proxy,
            route=route,
            route_elapsed_ms=int((time.monotonic() - route_started) * 1000),
            probe_kind=NetworkProbeKind.TCP_CONNECT.value,
        )
        logger.info(
            "network_probe route=%s kind=tcp_connect target=%s ok=%s elapsed_ms=%d "
            "route_elapsed_ms=%d error=%s",
            route,
            target.name,
            str(result.ok).lower(),
            result.elapsed_ms,
            result.route_elapsed_ms,
            _log_error_text(result.error),
        )
        return result

    def _probe_http(
        self,
        *,
        session,
        target: NetworkProbeTarget,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        overall_started: float,
        proxies: dict[str, str],
        route: str,
        via_proxy: bool,
    ) -> NetworkProbeResult:
        route_started = time.monotonic()
        status_code: Optional[int] = None
        error = ""
        response = None
        try:
            connect_timeout, read_timeout = _effective_probe_timeouts(
                connect_timeout_seconds,
                read_timeout_seconds,
            )
            total_timeout = max(connect_timeout, read_timeout)
            timeout = self._requests.adapters.TimeoutSauce(
                total=total_timeout,
                connect=connect_timeout,
                read=read_timeout,
            )
            # stream=True 确保异常状态或劫持页面不会被下载；204 本身没有响应正文。
            response = session.request(
                target.method,
                target.url,
                proxies=proxies,
                headers={
                    "User-Agent": "DeepCat-NetworkProbe/2.0",
                    "Cache-Control": "no-cache",
                    "Accept": "*/*",
                },
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            status_code = int(response.status_code)
            if status_code != target.expected_status:
                error = f"HTTP {status_code}"
        except Exception as exc:
            error = _error_text(exc)
        finally:
            if response is not None:
                response.close()

        result = NetworkProbeResult(
            target=target,
            ok=status_code == target.expected_status,
            status_code=status_code,
            elapsed_ms=int((time.monotonic() - overall_started) * 1000),
            error=error,
            via_proxy=via_proxy,
            route=route,
            route_elapsed_ms=int((time.monotonic() - route_started) * 1000),
            probe_kind=NetworkProbeKind.HTTP_204.value,
        )
        logger.info(
            "network_probe route=%s target=%s ok=%s status=%s elapsed_ms=%d "
            "route_elapsed_ms=%d total_timeout_ms=%d connect_timeout_ms=%d "
            "read_timeout_ms=%d error=%s",
            route,
            target.name,
            str(result.ok).lower(),
            result.status_code if result.status_code is not None else "none",
            result.elapsed_ms,
            result.route_elapsed_ms,
            int(total_timeout * 1000),
            int(connect_timeout * 1000),
            int(read_timeout * 1000),
            _log_error_text(result.error),
        )
        return result


def _combine_failed_paths(
    direct_result: NetworkProbeResult,
    proxy_result: NetworkProbeResult,
) -> NetworkProbeResult:
    direct_error = direct_result.error or f"HTTP {direct_result.status_code}"
    proxy_error = proxy_result.error or f"HTTP {proxy_result.status_code}"
    return NetworkProbeResult(
        target=proxy_result.target,
        ok=False,
        status_code=proxy_result.status_code,
        elapsed_ms=max(direct_result.elapsed_ms, proxy_result.elapsed_ms),
        error=f"直连/TUN失败: {direct_error}; 配置代理失败: {proxy_error}",
        via_proxy=True,
        route="proxy",
        route_elapsed_ms=proxy_result.route_elapsed_ms,
        probe_kind=proxy_result.probe_kind,
    )


def run_network_probe(
    target: NetworkProbeTarget,
    timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    proxy_url: str = "",
    *,
    engine: Optional[NetworkProbeEngine] = None,
    preferred_route: str = "direct",
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
    probe_kind: object = NetworkProbeKind.HTTP_204,
) -> NetworkProbeResult:
    """优先检测上轮成功路线，仅在失败时回退到另一条路线。"""

    owns_engine = engine is None
    active_engine = engine or NetworkProbeEngine()
    started = time.monotonic()
    normalized_route = _normalize_preferred_route(preferred_route)
    normalized_kind = normalize_network_probe_kind(probe_kind)
    direct_result: Optional[NetworkProbeResult] = None
    proxy_result: Optional[NetworkProbeResult] = None

    def probe_direct() -> NetworkProbeResult:
        if normalized_kind is NetworkProbeKind.TCP_CONNECT:
            return active_engine.probe_tcp_direct(target, timeout_seconds, started)
        return active_engine.probe_http_direct(
            target,
            timeout_seconds,
            started,
            read_timeout_seconds,
        )

    def probe_proxy() -> NetworkProbeResult:
        if normalized_kind is NetworkProbeKind.TCP_CONNECT:
            return active_engine.probe_tcp_proxy(target, timeout_seconds, proxy_url, started)
        return active_engine.probe_http_proxy(
            target,
            timeout_seconds,
            proxy_url,
            started,
            read_timeout_seconds,
        )

    try:
        if normalized_route == "proxy":
            proxy_result = probe_proxy()
            if proxy_result.ok:
                final_result = proxy_result
            else:
                direct_result = probe_direct()
                final_result = (
                    direct_result
                    if direct_result.ok
                    else _combine_failed_paths(direct_result, proxy_result)
                )
        else:
            direct_result = probe_direct()
            if direct_result.ok:
                final_result = direct_result
            else:
                proxy_result = probe_proxy()
                final_result = (
                    proxy_result
                    if proxy_result.ok
                    else _combine_failed_paths(direct_result, proxy_result)
                )

        direct_ok = "skipped" if direct_result is None else str(direct_result.ok).lower()
        direct_status = (
            "skipped"
            if direct_result is None
            else direct_result.status_code if direct_result.status_code is not None else "none"
        )
        direct_elapsed_ms = 0 if direct_result is None else direct_result.route_elapsed_ms
        proxy_ok = "skipped" if proxy_result is None else str(proxy_result.ok).lower()
        proxy_status = (
            "skipped"
            if proxy_result is None
            else proxy_result.status_code if proxy_result.status_code is not None else "none"
        )
        proxy_elapsed_ms = 0 if proxy_result is None else proxy_result.route_elapsed_ms

        logger.info(
            "network_probe round kind=%s target=%s preferred_route=%s direct_ok=%s "
            "direct_status=%s direct_elapsed_ms=%d "
            "proxy_ok=%s proxy_status=%s proxy_elapsed_ms=%d final_ok=%s final_route=%s",
            normalized_kind.value,
            target.name,
            normalized_route,
            direct_ok,
            direct_status,
            direct_elapsed_ms,
            proxy_ok,
            proxy_status,
            proxy_elapsed_ms,
            str(final_result.ok).lower(),
            final_result.route,
        )
        return final_result
    finally:
        if owns_engine:
            active_engine.close()


def run_proxy_network_probe(
    target: NetworkProbeTarget,
    timeout_seconds: float,
    proxy_url: str,
    *,
    engine: Optional[NetworkProbeEngine] = None,
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
) -> NetworkProbeResult:
    """只检测显式代理，供设置页的代理地址测试按钮复用。"""

    owns_engine = engine is None
    active_engine = engine or NetworkProbeEngine()
    started = time.monotonic()
    try:
        return active_engine.probe_http_proxy(
            target,
            timeout_seconds,
            proxy_url,
            started,
            read_timeout_seconds,
        )
    finally:
        if owns_engine:
            active_engine.close()


@dataclass(frozen=True)
class _ProbeTask:
    generation: int
    kind: NetworkProbeKind
    target: NetworkProbeTarget
    purpose: str
    timeout_seconds: float
    read_timeout_seconds: float
    proxy_url: str
    preferred_route: str
    started_monotonic: float


class _NetworkProbeWorker(QObject):
    finished = pyqtSignal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self._engine: Optional[NetworkProbeEngine] = None

    @pyqtSlot(object)
    def run(self, task: _ProbeTask) -> None:
        if self._engine is None:
            self._engine = NetworkProbeEngine()
        try:
            result = run_network_probe(
                task.target,
                task.timeout_seconds,
                task.proxy_url,
                engine=self._engine,
                preferred_route=task.preferred_route,
                read_timeout_seconds=task.read_timeout_seconds,
                probe_kind=task.kind,
            )
        except Exception as exc:
            result = NetworkProbeResult(
                target=task.target,
                ok=False,
                status_code=None,
                elapsed_ms=0,
                error=_error_text(exc),
                probe_kind=task.kind.value,
            )
        self.finished.emit(task, result)

    def close(self) -> None:
        if self._engine is not None:
            self._engine.close()
            self._engine = None


class NetworkProbeMonitor(QObject):
    connectivity_changed = pyqtSignal(bool, object)
    status_changed = pyqtSignal(str, object)
    probe_finished = pyqtSignal(object)
    running_changed = pyqtSignal(bool)
    _probe_requested = pyqtSignal(object)

    HEALTHY_HTTP_INTERVAL_MS = 5000
    DEGRADED_HTTP_INTERVAL_MS = 3000

    def __init__(
        self,
        parent: Optional[QObject] = None,
        *,
        targets: tuple[NetworkProbeTarget, ...] = NETWORK_PROBE_TARGETS,
        interval_ms: int = HEALTHY_HTTP_INTERVAL_MS,
        degraded_interval_ms: int = DEGRADED_HTTP_INTERVAL_MS,
        timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
        proxy_url_provider: Optional[Callable[[], str]] = None,
        probe_kind: object = NetworkProbeKind.HTTP_204,
    ) -> None:
        super().__init__(parent)
        if not targets:
            raise ValueError("节点探测至少需要一个 HTTP 目标")

        self._targets = tuple(targets)
        self._primary_target = self._targets[0]
        self._healthy_http_interval_ms = max(1000, int(interval_ms))
        self._degraded_http_interval_ms = max(1000, int(degraded_interval_ms))
        self._timeout_seconds = _bounded_timeout(timeout_seconds)
        self._read_timeout_seconds = _bounded_timeout(read_timeout_seconds)
        self._proxy_url_provider = proxy_url_provider
        self._probe_kind = normalize_network_probe_kind(probe_kind)

        self._enabled = False
        self._status = NetworkProbeStatus.STOPPED
        self._connected: Optional[bool] = None
        self._generation = 0
        self._busy = False
        self._pending_immediate = False
        self._pending_preferred_route: Optional[str] = None
        self._current_task: Optional[_ProbeTask] = None
        self._last_result: Optional[NetworkProbeResult] = None
        self._last_http_result: Optional[NetworkProbeResult] = None
        self._preferred_route = "direct"

        self._thread: Optional[QThread] = None
        self._worker: Optional[_NetworkProbeWorker] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._healthy_http_interval_ms)
        self._timer.timeout.connect(self._run_scheduled_probe)

    def is_running(self) -> bool:
        return self._enabled

    def status(self) -> str:
        return self._status.value

    def last_result(self) -> Optional[NetworkProbeResult]:
        return self._last_result

    def last_http_result(self) -> Optional[NetworkProbeResult]:
        return self._last_http_result

    def preferred_route(self) -> str:
        return self._preferred_route

    def probe_kind(self) -> str:
        return self._probe_kind.value

    def set_probe_kind(self, kind: object) -> None:
        normalized_kind = normalize_network_probe_kind(kind)
        if normalized_kind is self._probe_kind:
            return
        self._probe_kind = normalized_kind
        self._generation += 1
        self._timer.stop()
        self._pending_immediate = False
        self._pending_preferred_route = None
        self._last_result = None
        self._last_http_result = None
        if not self._enabled:
            return
        self._connected = None
        self._set_status(NetworkProbeStatus.CHECKING, None)
        if self._busy:
            self._pending_immediate = True
            return
        self._start_current_probe(purpose="mode_change")

    def start(self) -> None:
        if self._enabled:
            return
        self._ensure_worker_thread()
        self._enabled = True
        self._generation += 1
        self._connected = None
        self._pending_immediate = False
        self._pending_preferred_route = None
        self._preferred_route = "direct"
        self._set_status(NetworkProbeStatus.CHECKING, None)
        self.running_changed.emit(True)
        self._start_current_probe(purpose="regular")

    def stop(self) -> None:
        if not self._enabled:
            return
        self._enabled = False
        self._generation += 1
        self._connected = None
        self._pending_immediate = False
        self._pending_preferred_route = None
        self._timer.stop()
        self._set_status(NetworkProbeStatus.STOPPED, None)
        self.running_changed.emit(False)

    def request_immediate_probe(self, *, preferred_route: Optional[str] = None) -> None:
        if not self._enabled:
            return
        self._timer.stop()
        if self._busy:
            self._pending_immediate = True
            self._pending_preferred_route = (
                _normalize_preferred_route(preferred_route)
                if preferred_route is not None
                else None
            )
            return
        if preferred_route is not None:
            self._preferred_route = _normalize_preferred_route(preferred_route)
        self._start_current_probe(purpose="manual")

    def shutdown(self, wait_ms: int = 0) -> None:
        if self._enabled:
            self.stop()
        else:
            self._generation += 1
            self._timer.stop()

        worker = self._worker
        if worker is not None:
            try:
                worker.close()
            except Exception:
                pass

        thread = self._thread
        if thread is None:
            return
        thread.quit()
        # 一轮最多包含直连与两个代理协议候选；每次尝试受统一总超时约束。
        maximum_round_seconds = (
            max(self._timeout_seconds, self._read_timeout_seconds)
            * MAX_ROUTE_ATTEMPTS_PER_ROUND
        )
        minimum_wait_ms = int((maximum_round_seconds + 0.8) * 1000)
        thread.wait(max(int(wait_ms), minimum_wait_ms))
        if thread.isRunning():
            from deepcat.ui.thread_utils import request_thread_cancel

            request_thread_cancel(thread)
            self._thread = None
            self._worker = None
            return
        self._worker = None
        self._thread = None

    def _ensure_worker_thread(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        thread = QThread(None)
        worker = _NetworkProbeWorker()
        worker.moveToThread(thread)
        self._probe_requested.connect(worker.run, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._handle_probe_result)
        thread.finished.connect(worker.close)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _proxy_url(self) -> str:
        if self._proxy_url_provider is None:
            return ""
        try:
            return str(self._proxy_url_provider() or "").strip()
        except Exception:
            return ""

    def _start_http_probe(self, target: NetworkProbeTarget, *, purpose: str) -> None:
        self._start_task(
            kind=NetworkProbeKind.HTTP_204,
            target=target,
            purpose=purpose,
            timeout_seconds=self._timeout_seconds,
        )

    def _start_current_probe(self, *, purpose: str) -> None:
        if self._probe_kind is NetworkProbeKind.TCP_CONNECT:
            self._start_task(
                kind=NetworkProbeKind.TCP_CONNECT,
                target=GOOGLE_TCP_TARGET,
                purpose=purpose,
                timeout_seconds=self._timeout_seconds,
            )
            return
        self._start_http_probe(self._primary_target, purpose=purpose)

    def _start_task(
        self,
        *,
        kind: NetworkProbeKind,
        target: NetworkProbeTarget,
        purpose: str,
        timeout_seconds: float,
    ) -> None:
        if not self._enabled:
            return
        if self._busy:
            self._pending_immediate = True
            return
        self._timer.stop()
        task = _ProbeTask(
            generation=self._generation,
            kind=kind,
            target=target,
            purpose=purpose,
            timeout_seconds=timeout_seconds,
            read_timeout_seconds=self._read_timeout_seconds,
            proxy_url=self._proxy_url(),
            preferred_route=self._preferred_route,
            started_monotonic=time.monotonic(),
        )
        self._busy = True
        self._current_task = task
        self._probe_requested.emit(task)

    @pyqtSlot(object, object)
    def _handle_probe_result(self, task: _ProbeTask, result: NetworkProbeResult) -> None:
        self._busy = False
        self._current_task = None

        if not self._enabled or task.generation != self._generation:
            if self._enabled and self._pending_immediate:
                self._run_pending_immediate_probe()
            return

        self._last_result = result
        self._last_http_result = result
        if result.ok:
            self._preferred_route = _normalize_preferred_route(result.route)
        self.probe_finished.emit(result)
        self._handle_http_result(task, result)

    def _handle_http_result(self, task: _ProbeTask, result: NetworkProbeResult) -> None:
        if result.ok:
            self._set_status(NetworkProbeStatus.HEALTHY, result)
            self._schedule_after_task(
                task,
                self._healthy_http_interval_ms,
            )
            return

        self._set_status(NetworkProbeStatus.UNAVAILABLE, result)
        self._schedule_after_task(
            task,
            self._degraded_http_interval_ms,
        )

    def _schedule_after_task(
        self,
        task: _ProbeTask,
        phase_ms: int,
    ) -> None:
        elapsed_ms = int((time.monotonic() - task.started_monotonic) * 1000)
        self._schedule(max(1, int(phase_ms) - elapsed_ms))

    def _schedule(self, delay_ms: int) -> None:
        if not self._enabled:
            return
        if self._pending_immediate:
            self._run_pending_immediate_probe()
            return
        self._timer.start(max(1, int(delay_ms)))

    def _run_pending_immediate_probe(self) -> None:
        preferred_route = self._pending_preferred_route
        self._pending_immediate = False
        self._pending_preferred_route = None
        if preferred_route is not None:
            self._preferred_route = preferred_route
        self._start_current_probe(purpose="manual")

    def _run_scheduled_probe(self) -> None:
        if not self._enabled:
            return
        self._start_current_probe(purpose="regular")

    def _set_status(
        self,
        status: NetworkProbeStatus,
        result: Optional[NetworkProbeResult],
    ) -> None:
        previous = self._status
        self._status = status
        if previous is not status or result is not None:
            self.status_changed.emit(status.value, result)

        connected: Optional[bool]
        if status is NetworkProbeStatus.HEALTHY:
            connected = True
        elif status is NetworkProbeStatus.UNAVAILABLE:
            connected = False
        else:
            connected = None
        if connected is not None and connected is not self._connected:
            self._connected = connected
            self.connectivity_changed.emit(connected, result)
