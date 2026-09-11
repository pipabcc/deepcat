"""内置 Gemini Web2API 服务：进程内直调，不监听端口。

历史实现会在 127.0.0.1:8081 上启动一个真实的 HTTP 服务器，再由 DeepCat 自己
通过 TCP 回环访问它。这条链路要求操作系统放行进程的「监听端口」动作：防火墙
或安全软件未放行时 listen 会被拒绝、连接会被静默丢弃，用户只能看到「启动内置
Gemini 代理服务失败：本地 Gemini 代理服务启动超时」，并且必须把程序加入白名单
才能恢复。

服务与它的调用方本来就同处一个进程，HTTP 环回是多余环节。现在请求直接在内存
里交给同一个 GeminiHandler 处理：不监听端口、不依赖防火墙规则，也不再占用 8081。
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse

from deepcat.inproc_http import HttpxInProcessTransport, InProcessExchange, send_requests_request

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_LOCAL_PORT = 8081
_SPEC = "gemini-3.5-flash-thinking"


def _is_local_gemini_web2api_url(base_url: str) -> bool:
    """判断 URL 是否指向内置 Gemini 代理服务（回环地址 + 8081 端口）。"""
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
    return host in _LOCAL_HOSTS and port == _LOCAL_PORT


def identify_spec(cfg: dict[str, object]) -> str | None:
    """配置指向内置 Gemini 服务时返回模型标识，否则返回 None。"""
    if _is_local_gemini_web2api_url(str(cfg.get("base_url", "") or "")):
        return _SPEC
    return None


def ensure_server(cfg: dict[str, object]) -> None:
    """准备内置服务的运行参数。

    进程内直调没有可启动的服务器，这里只把代理等参数写入 handler 的模块级配置，
    因此该函数不会再阻塞、也不可能因为防火墙而超时。
    """
    if identify_spec(cfg) is None:
        return
    _apply_runtime_config(cfg)


def release_server(cfg: dict[str, object]) -> None:
    """进程内直调没有需要回收的资源；保留接口以兼容既有调用方。"""
    return None


def inprocess_exchange(
    method: str,
    target: str,
    body: bytes = b"",
    headers: Mapping[str, str] | None = None,
) -> InProcessExchange:
    """把一次请求交给 GeminiHandler 在进程内处理。"""
    return InProcessExchange(_handler_class(), method, target, body, headers)


def local_gemini_response(method: str, url: str, **kwargs: Any) -> Any:
    """URL 指向内置 Gemini 服务时返回进程内 requests.Response。

    返回 None 表示该 URL 不是内置服务，调用方应自行发起真实网络请求。
    """
    if not _is_local_gemini_web2api_url(url):
        return None
    return send_requests_request(inprocess_exchange, str(method).upper(), url, **kwargs)


def local_gemini_httpx_transport(url: str) -> HttpxInProcessTransport | None:
    """URL 指向内置 Gemini 服务时返回进程内 httpx 传输层，否则返回 None。"""
    if not _is_local_gemini_web2api_url(url):
        return None
    return HttpxInProcessTransport(inprocess_exchange)


def _handler_class() -> type:
    try:
        from deepcat.gemini_web2api import GeminiHandler
    except ImportError:  # 独立运行 gemini_web2api.py 时的模块搜索路径
        from gemini_web2api import GeminiHandler
    return GeminiHandler


def _apply_runtime_config(cfg: Mapping[str, object]) -> None:
    """把内置服务需要的运行参数写进 gemini_web2api 的模块级 CONFIG。"""
    try:
        from deepcat.gemini_web2api import CONFIG as gemini_config
    except ImportError:
        from gemini_web2api import CONFIG as gemini_config
    gemini_config["host"] = "127.0.0.1"
    gemini_config["port"] = _LOCAL_PORT
    gemini_config["proxy"] = _resolve_proxy_url(cfg)


def _resolve_proxy_url(cfg: Mapping[str, object]) -> str | None:
    """按设置解析上游代理地址；未启用代理时返回 None。"""
    if not bool(cfg.get("use_proxy", False)):
        return None
    proxy_url = ""
    try:
        from deepcat.settings_store import load_settings

        translator = load_settings().ui.get("translator", {})
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
