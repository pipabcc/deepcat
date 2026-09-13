from __future__ import annotations

import atexit
import hashlib
import json
import threading
import time
from http.server import HTTPServer
from socketserver import ThreadingMixIn
from typing import Any
from urllib import request
from urllib.parse import urlparse


IDLE_SECONDS = 180

_lock = threading.RLock()
_active_count = 0
_idle_timer: threading.Timer | None = None
_in_process_server: "ThreadedServer | None" = None
_running_use_proxy: bool | None = None
_running_proxy_url: str | None = None
_running_auth_digest: str | None = None


class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _is_loopback_url(base_url: str) -> bool:
    raw = str(base_url or "").strip()
    if not raw:
        return True
    parse_target = raw if "://" in raw else f"http://{raw}"
    try:
        parsed = urlparse(parse_target)
    except Exception:
        return False
    host = str(parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _is_local_chatgpt_web2api_url(base_url: str) -> bool:
    raw = str(base_url or "").strip()
    if not raw:
        return False
    parse_target = raw if "://" in raw else f"http://{raw}"
    try:
        parsed = urlparse(parse_target)
    except Exception:
        return False
    host = str(parsed.hostname or "").lower()
    try:
        port = int(parsed.port or 0)
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1"} and port == 8082


def identify_spec(cfg: dict[str, object]) -> str | None:
    model_type = str(cfg.get("model_type", "") or "").strip().lower().replace("-", "_")
    base_url = str(cfg.get("base_url", "") or "").strip()
    if model_type in {"chatgpt_web", "chatgpt_web2api", "chatgpt"} and _is_loopback_url(base_url):
        return "chatgpt-web"
    if _is_local_chatgpt_web2api_url(base_url):
        return "chatgpt-web"
    return None


def ensure_server(cfg: dict[str, object]) -> None:
    global _active_count
    spec = identify_spec(cfg)
    if spec is None:
        return
    base_url = _base_url(cfg)
    use_proxy = bool(cfg.get("use_proxy", False))
    proxy_url = _proxy_url_from_settings(use_proxy, cfg)
    auth_digest = _auth_digest(str(cfg.get("api_key", "") or ""))

    with _lock:
        _cancel_idle_timer_locked()
        if (
            _server_is_ready(base_url)
            and _running_use_proxy == use_proxy
            and _running_proxy_url == proxy_url
            and _running_auth_digest == auth_digest
        ):
            _mark_active_locked()
            return
        _stop_server_locked()
        _start_server_locked(base_url, cfg, proxy_url=proxy_url, auth_digest=auth_digest)
        _wait_until_ready(base_url)
        _mark_active_locked()


def release_server(cfg: dict[str, object]) -> None:
    if identify_spec(cfg) is None:
        return
    with _lock:
        global _active_count
        _active_count = max(0, _active_count - 1)
        if _active_count == 0:
            _schedule_idle_stop_locked()


def stop_server_now() -> None:
    with _lock:
        _cancel_idle_timer_locked()
        _stop_server_locked()


def _mark_active_locked() -> None:
    global _active_count
    _active_count += 1


def _auth_digest(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()


def _base_url(cfg: dict[str, object]) -> str:
    base = str(cfg.get("base_url", "") or "http://127.0.0.1:8082").strip().rstrip("/")
    return base or "http://127.0.0.1:8082"


def _host_port(base_url: str) -> tuple[str, int]:
    parse_target = base_url if "://" in base_url else f"http://{base_url}"
    parsed = urlparse(parse_target)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8082)
    return host, port


def _proxy_url_from_settings(use_proxy: bool, cfg: dict[str, object]) -> str | None:
    if not use_proxy:
        return None
    proxy_url = str(cfg.get("proxy_url", "") or "").strip()
    if not proxy_url:
        try:
            from deepcat.settings_store import load_settings

            settings = load_settings()
            translator = settings.ui.get("translator", {})
            proxy_url = str(translator.get("proxy_url", "socks5://127.0.0.1:1080")).strip()
        except Exception:
            proxy_url = ""
    if not proxy_url:
        return None
    if "://" not in proxy_url:
        return f"socks5h://{proxy_url}"
    if proxy_url.startswith("socks5://"):
        return proxy_url.replace("socks5://", "socks5h://", 1)
    return proxy_url


def _server_is_ready(base_url: str) -> bool:
    try:
        from deepcat.chatgpt_web2api import SERVICE_BUILD
    except ImportError:
        from chatgpt_web2api import SERVICE_BUILD  # type: ignore

    host, port = _host_port(base_url)
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    probe_url = f"http://{display_host}:{port}/"
    try:
        req = request.Request(probe_url)
        opener = request.build_opener(request.ProxyHandler({}))
        with opener.open(req, timeout=1.0) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return (
            isinstance(payload, dict)
            and payload.get("service") == "chatgpt_web2api"
            and payload.get("build") == SERVICE_BUILD
        )
    except Exception:
        return False


def _start_server_locked(
    base_url: str,
    cfg: dict[str, object],
    *,
    proxy_url: str | None,
    auth_digest: str,
) -> None:
    global _in_process_server, _running_use_proxy, _running_proxy_url, _running_auth_digest

    host, port = _host_port(base_url)
    use_proxy = bool(cfg.get("use_proxy", False))

    try:
        from deepcat.chatgpt_web2api import CONFIG as CHATGPT_CONFIG, ChatGPTWeb2APIHandler
    except ImportError:
        from chatgpt_web2api import CONFIG as CHATGPT_CONFIG, ChatGPTWeb2APIHandler  # type: ignore

    CHATGPT_CONFIG["host"] = host
    CHATGPT_CONFIG["port"] = port
    CHATGPT_CONFIG["api_key"] = str(cfg.get("api_key", "") or "")
    CHATGPT_CONFIG["proxy"] = proxy_url
    CHATGPT_CONFIG["log_requests"] = bool(cfg.get("log_requests", True))

    _in_process_server = ThreadedServer((host, port), ChatGPTWeb2APIHandler)

    _running_use_proxy = use_proxy
    _running_proxy_url = proxy_url
    _running_auth_digest = auth_digest

    def _run() -> None:
        try:
            _in_process_server.serve_forever()
        except Exception:
            pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def _wait_until_ready(base_url: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _server_is_ready(base_url):
            return
        time.sleep(0.5)
    raise TimeoutError("本地 ChatGPT Web2API 服务启动超时。")


def _schedule_idle_stop_locked() -> None:
    global _idle_timer
    _cancel_idle_timer_locked()
    _idle_timer = threading.Timer(IDLE_SECONDS, _idle_stop)
    _idle_timer.daemon = True
    _idle_timer.start()


def _cancel_idle_timer_locked() -> None:
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None


def _idle_stop() -> None:
    with _lock:
        if _active_count == 0:
            _stop_server_locked()


def _stop_server_locked() -> None:
    global _in_process_server, _running_use_proxy, _running_proxy_url, _running_auth_digest
    if _in_process_server is not None:
        from deepcat.chatgpt_web2api import clear_sentinel_prefetch

        clear_sentinel_prefetch()
        try:
            _in_process_server.shutdown()
            _in_process_server.server_close()
        except Exception:
            pass
        _in_process_server = None
    from deepcat.chatgpt_transport import clear_session_cache

    clear_session_cache()
    _running_use_proxy = None
    _running_proxy_url = None
    _running_auth_digest = None


atexit.register(stop_server_now)
