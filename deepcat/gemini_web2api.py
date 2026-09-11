#!/usr/bin/env python3
"""
来源：https://github.com/Sophomoresty/gemini-web2api/blob/main/README_CN.md
gemini-web2api - Gemini Web to OpenAI API proxy.

Converts Google Gemini's web interface into an OpenAI-compatible API server.
Zero authentication required. Works on any platform (Windows/macOS/Linux).

Usage:
    pip install httpx
    python gemini_web2api.py [--port 8081] [--config config.json]

Client configuration (Cherry Studio, ChatBox, etc.):
    Base URL: http://localhost:8081/v1
    API Key: (anything or empty)

How it works:
    Sends requests directly to Gemini's public StreamGenerate endpoint.
    The backend does not verify authentication for basic text generation.
    Model selection uses the server model header plus legacy MODE_CATEGORY [79].
    This is NOT a user-tier spoofing attack - the endpoint simply doesn't
    require auth for anonymous access.
"""
import codecs
import json
import queue
import urllib.request
import urllib.parse
import time
import ssl
import sys
import uuid
import re
import os
import hashlib
import argparse
import base64
import traceback
import threading
import select
import socket
import html
from typing import Any
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

try:
    from deepcat.utils.curl_dns import apply_to_session as _apply_curl_resolve
except ImportError:  # 独立运行 gemini_web2api.py 时退回 libcurl 自身解析
    _apply_curl_resolve = None

__version__ = "1.1.1"

# ─── Configuration ───────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "port": 8081,
    # 默认仅监听回环地址：本服务持有浏览器 Cookie 且端点能消耗上游配额，
    # 暴露到局域网必须显式改 host 并配置 api_key（见 main() 的启动校验）。
    "host": "127.0.0.1",
    # 非空时所有 /v1* 端点要求 X-API-Key 或 Bearer 匹配（"/" 健康检查除外）
    "api_key": None,
    "max_request_body_bytes": 64 * 1024 * 1024,
    # 服务端代抓 image_url 时默认拒绝私网/回环地址，防 SSRF 内网探测
    "allow_private_image_urls": False,
    "retry_attempts": 3,
    "retry_delay_sec": 2,
    "request_timeout_sec": 1800,
    "request_connect_timeout_sec": 30,
    "response_stream_heartbeat_sec": 3,
    "gemini_bl": "boq_assistant-bard-web-server_20260525.09_p0",
    "gemini_web_context_ttl_sec": 300,
    "default_model": "gemini-3.5-flash",
    "log_requests": True,
    "cookie_file": None,
    "proxy": None,
    "gemini_stream_stability_tail_chars": 24,
    "chat_stream_chunk_chars": 160,
    "chat_stream_chunk_delay_ms": 15,
    "model_aliases": {
        "gpt-5.4": "gemini-3.5-flash-thinking",
        "gpt-5.4-mini": "gemini-3.5-flash",
        "gpt-5": "gemini-3.5-flash-thinking",
        "gpt-5-mini": "gemini-3.5-flash",
        "gpt-4.1": "gemini-3.5-flash-thinking",
        "gpt-4.1-mini": "gemini-3.5-flash",
        "o3": "gemini-3.5-flash-thinking",
        "o4-mini": "gemini-3.5-flash",
        "codex-mini-latest": "gemini-3.5-flash",
        "gemini 3.6 flash": "gemini-3.6-flash",
        "gemini 3.7 flash": "gemini-3.7-flash",
        "gemini 3.8 flash": "gemini-3.8-flash",
    },
    "max_tool_description_chars": 4000,
    "max_tool_schema_chars": 1200,
}

CONFIG = dict(DEFAULT_CONFIG)

# ─── Models ──────────────────────────────────────────────────────────────────
# Legacy MODE_CATEGORY enum; current models may also require an authoritative hex ID header.
#   1=FAST, 2=THINKING, 3=PRO, 4=AUTO, 5=FAST_DYNAMIC_THINKING, 6=FLASH_LITE

MODELS = {
    "gemini-3.8-flash": {
        "mode": 1, "think": 0, "hex_id": "56fdd199312815e2",
        "desc": "All-around model (Gemini 3.8 Flash)",
    },
    "gemini-3.7-flash": {
        "mode": 1, "think": 0,
        "desc": "Latest all-around model (Gemini 3.7 Flash)",
    },
    "gemini-3.6-flash": {
        "mode": 1, "think": 0, "hex_id": "fbb127bbb056c959",
        "desc": "All-around model (Gemini 3.6 Flash)",
    },
    "gemini-3.5-flash": {
        "mode": 1, "think": 4,
        "desc": "Fast general-purpose model",
    },
    "gemini-3.5-flash-thinking": {
        "mode": 2, "think": 0,
        "desc": "Deep thinking mode, longest output (~20k chars)",
    },
    "gemini-3.1-pro": {
        "mode": 3, "think": 4,
        "desc": "Pro model (requires cookie for real routing)",
    },
    "gemini-auto": {
        "mode": 4, "think": 4,
        "desc": "Auto model selection",
    },
    "gemini-3.5-flash-thinking-lite": {
        "mode": 5, "think": 0,
        "desc": "Dynamic thinking with adaptive depth",
    },
    "gemini-flash-lite": {
        "mode": 6, "think": 4,
        "desc": "Lightweight fast model",
    },
}

# ─── Utilities ───────────────────────────────────────────────────────────────

def log(msg: str):
    if CONFIG["log_requests"]:
        try:
            print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
        except Exception:
            pass


def _curl_request(method: str, url: str, *, proxy: "str | None", **kwargs: Any) -> Any:
    """用 libcurl 发请求，并在直连时预置 Python 解析出的地址。

    libcurl 默认在自己进程里解析域名，未获系统放行的程序会被拦掉（详见
    ``deepcat.utils.curl_dns``）。流式响应持有独立句柄，因此这里关闭会话不影响后续读取。
    """
    session_factory = getattr(curl_requests, "Session", None)
    if session_factory is None:
        # 只暴露模块级 get/post 的精简实现，退回直接调用
        return getattr(curl_requests, method.lower())(url, **kwargs)
    session = session_factory()
    if _apply_curl_resolve is not None:
        _apply_curl_resolve(session, url, proxy)
    try:
        return session.request(method, url, **kwargs)
    finally:
        session.close()


_INLINE_DATA_URL_RE = re.compile(r"data:([^,\s\"']+),[A-Za-z0-9+/=_-]*")


def redact_inline_data_urls(text: str) -> str:
    def repl(match):
        mime = match.group(1).split(";", 1)[0] or "application/octet-stream"
        return f"[Inline data URL redacted: {mime}]"

    return _INLINE_DATA_URL_RE.sub(repl, str(text or ""))


def preview_text(value, limit: int = 800) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    text = redact_inline_data_urls(text)
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def mask_secret(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 12:
        return "***"
    return value[:8] + "..." + value[-4:]


_URL_KEY_QUERY_RE = re.compile(r"([?&](?:key|api_key|apikey|access_token|token)=)[^&\s'\"]+", re.IGNORECASE)
_GOOGLE_API_KEY_RE = re.compile(r"AIzaSy[A-Za-z0-9_-]{20,}")


def redact_api_keys(text: str) -> str:
    """剥掉异常消息/日志里随 URL 携带的 key（urllib/httpx 的报错原样包含完整 URL）。"""
    result = _URL_KEY_QUERY_RE.sub(r"\1***", str(text or ""))
    return _GOOGLE_API_KEY_RE.sub("AIzaSy***", result)


def is_public_http_url(url: str) -> bool:
    """SSRF 防护：仅放行解析到公网地址的 http/https 链接。"""
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    if CONFIG.get("allow_private_image_urls"):
        return True
    import ipaddress
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not addr.is_global:
            return False
    return True


def summarize_headers(headers) -> dict:
    result = {}
    for key in ("Content-Type", "Content-Length", "Accept", "Origin", "Referer", "User-Agent"):
        val = headers.get(key)
        if val:
            result[key] = preview_text(val, 240)
    auth = headers.get("Authorization")
    if auth:
        if auth.startswith("Bearer "):
            result["Authorization"] = "Bearer " + mask_secret(auth[7:].strip())
        else:
            result["Authorization"] = mask_secret(auth)
    api_key = headers.get("X-API-Key") or headers.get("x-api-key")
    if api_key:
        result["X-API-Key"] = mask_secret(api_key)
    return result


def proxy_env_summary() -> dict:
    result = {}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        val = os.environ.get(key)
        if val:
            if "PROXY" in key.upper() and key.upper() != "NO_PROXY":
                result[key] = mask_secret(val)
            else:
                result[key] = preview_text(val, 240)
    return result


def exception_summary(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def friendly_upstream_error_message(error) -> str:
    err = str(error or "").strip()
    low = err.lower()
    if "429" in low or "too many requests" in low:
        return "抱歉，Gemini 当前请求过于频繁或额度暂时受限（HTTP 429）。请稍后再试，或更换账号/模型后重试。"
    if "unexpected_eof_while_reading" in low or "eof occurred in violation of protocol" in low:
        return "抱歉，Gemini 连接在传输中意外中断，没有拿到有效回复。通常是网络或代理波动导致，请稍后重试。"
    if "10060" in err or "timeout" in low or "timed out" in low:
        return "抱歉，连接 Gemini 超时，没有拿到有效回复。请检查网络或代理后重试。"
    if "10054" in err or "connection reset" in low or "远程主机强迫关闭" in err:
        return "抱歉，Gemini 连接被远端重置，没有拿到有效回复。请稍后重试，或检查代理是否稳定。"
    if "10053" in err or "connection aborted" in low or "连接被中止" in err:
        return "抱歉，Gemini 连接被中止，没有拿到有效回复。请稍后重试，或检查网络/代理设置。"
    if "service unavailable" in low or "503" in low:
        return "抱歉，Gemini 服务暂时不可用，请稍后再试。"
    return "抱歉，这次没有从 Gemini 拿到有效回复。请稍后重试，或检查网络、代理、Cookie/API Key 后再试。"


def upstream_httpx_timeout():
    total = float(CONFIG.get("request_timeout_sec", 1800) or 1800)
    connect = float(CONFIG.get("request_connect_timeout_sec", 30) or 30)
    if not HAS_HTTPX:
        return total
    return httpx.Timeout(total, connect=connect, read=total, write=connect, pool=connect)


def load_cookie() -> tuple:
    """Load cookie from file. Returns (cookie_str, sapisid)."""
    cookie_file = CONFIG.get("cookie_file")
    if not cookie_file:
        return "", None
    if not os.path.exists(cookie_file):
        return "", None
    try:
        with open(cookie_file, "r") as f:
            content = f.read().strip()
        if content.startswith("{"):
            data = json.loads(content)
            cookie_str = data.get("cookie", "")
            sapisid = data.get("sapisid", "")
        else:
            cookie_str = content
            pairs = dict(p.split("=", 1) for p in cookie_str.split("; ") if "=" in p)
            sapisid = pairs.get("SAPISID", "")
        return cookie_str, sapisid if sapisid else None
    except Exception as e:
        log(f"Cookie load error: {e}")
        return "", None


def make_sapisidhash(sapisid: str) -> str:
    ts = int(time.time())
    h = hashlib.sha1(f"{ts} {sapisid} https://gemini.google.com".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{h}"


_GEMINI_WEB_CONTEXT_LOCK = threading.Lock()
_GEMINI_WEB_CONTEXT_CACHE = {
    "cookie_fingerprint": "",
    "expires_at": 0.0,
    "bl": "",
    "f_sid": "",
    "at": "",
}


def _json_string_field(source: str, field: str) -> str:
    """从 Gemini /app 的 JSON 启动数据中读取标量字段并解码转义。"""
    match = re.search(
        rf'"{re.escape(field)}"\s*:\s*("(?:\\.|[^"\\])*"|[^,}}\]]+)',
        str(source or ""),
    )
    if not match:
        return ""
    raw_value = match.group(1).strip()
    try:
        value = json.loads(raw_value) if raw_value.startswith('"') else raw_value
    except (TypeError, json.JSONDecodeError):
        return ""
    return str(value or "").strip()


def _extract_gemini_web_context(html_text: str) -> dict:
    """提取 /app 启动信息；不返回页面原文或任何 Cookie。"""
    return {
        "bl": _json_string_field(html_text, "cfb2h"),
        "f_sid": _json_string_field(html_text, "FdrFJe"),
        "at": _json_string_field(html_text, "SNlM0e"),
    }


def _gemini_cookie_fingerprint(cookie_str: str) -> str:
    """用 Cookie 的 SHA-256 指纹隔离不同登录态，日志中不暴露 Cookie。"""
    return hashlib.sha256(str(cookie_str or "").encode("utf-8")).hexdigest()


def _fetch_gemini_web_context(cookie_str: str = "", timeout_sec: float | None = None) -> dict:
    """从 Gemini 网页启动页获取动态 bl/f.sid/at，并按 Cookie 短期缓存。"""
    # 没有登录态时无法取得网页会话参数，保留原有匿名/旧协议路径。
    if not str(cookie_str or "").strip():
        return {}
    fingerprint = _gemini_cookie_fingerprint(cookie_str)
    now = time.time()
    ttl = max(0.0, float(CONFIG.get("gemini_web_context_ttl_sec", 300) or 0))
    with _GEMINI_WEB_CONTEXT_LOCK:
        cached = dict(_GEMINI_WEB_CONTEXT_CACHE)
        if (
            cached.get("cookie_fingerprint") == fingerprint
            and cached.get("expires_at", 0.0) > now
            and cached.get("f_sid")
            and cached.get("at")
        ):
            return cached

    timeout = float(timeout_sec or CONFIG.get("request_connect_timeout_sec", 30) or 30)
    timeout = max(5.0, min(timeout, 60.0))
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Referer": "https://gemini.google.com/app",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str
    url = "https://gemini.google.com/app"
    try:
        if HAS_CURL_CFFI:
            proxy = CONFIG.get("proxy")
            proxies = {"http": proxy, "https": proxy} if proxy else None
            response = _curl_request(
                "GET",
                url,
                proxy=proxy,
                headers=headers,
                impersonate="chrome120",
                proxies=proxies,
                timeout=timeout,
            )
            response.raise_for_status()
            html_text = response.text
        else:
            request = urllib.request.Request(url, headers=headers, method="GET")
            context = ssl.create_default_context()
            if CONFIG.get("proxy"):
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": CONFIG["proxy"], "https": CONFIG["proxy"]}),
                    urllib.request.HTTPSHandler(context=context),
                )
                with opener.open(request, timeout=timeout) as response:
                    html_text = response.read().decode("utf-8", errors="replace")
            else:
                with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
                    html_text = response.read().decode("utf-8", errors="replace")
        context = _extract_gemini_web_context(html_text)
    except Exception as exc:
        log(f"Gemini web context fetch failed error={exception_summary(exc)}")
        return {}

    if not (context.get("f_sid") and context.get("at")):
        log(
            "Gemini web context incomplete "
            f"bl={bool(context.get('bl'))} f_sid={bool(context.get('f_sid'))} at={bool(context.get('at'))}"
        )
        return context

    context.update({"cookie_fingerprint": fingerprint, "expires_at": now + ttl})
    with _GEMINI_WEB_CONTEXT_LOCK:
        _GEMINI_WEB_CONTEXT_CACHE.update(context)
    log(
        "Gemini web context refreshed "
        f"bl={context.get('bl') or '-'} f_sid_len={len(context.get('f_sid') or '')} "
        f"at_len={len(context.get('at') or '')} ttl={ttl:g}s"
    )
    return context


def _gemini_request_context(cookie_str: str = "", timeout_sec: float | None = None) -> dict:
    """返回本次请求可用的动态上下文；旧协议无法获取时返回空字典。"""
    return _fetch_gemini_web_context(cookie_str, timeout_sec=timeout_sec)


def build_gemini_headers(
    cookie_str: str = "",
    sapisid: "str | None" = None,
    model_hex_id: "str | None" = None,
) -> dict:
    """Build shared Gemini Web headers, including the authoritative model selector."""
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/app",
        "X-Same-Domain": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    model_hex_id = str(model_hex_id or "").strip()
    if model_hex_id:
        headers["x-goog-ext-525001261-jspb"] = json.dumps(
            [1, None, None, None, model_hex_id],
            separators=(",", ":"),
        )
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    return headers


def _build_gemini_request(
    prompt: str,
    model_id: int,
    think_mode: int,
    model_hex_id: "str | None" = None,
    timeout_sec: "float | None" = None,
):
    """构造 StreamGenerate 请求，并带上当前网页协议需要的动态上下文。"""
    inner = [None] * 80
    inner[0] = [prompt, 0, None, None, None, None, 0]
    inner[1] = ["en"]
    inner[2] = ["", "", "", None, None, None, None, None, None, ""]
    inner[6] = [0]
    inner[7] = 1
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[think_mode]]
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    inner[41] = [2]
    inner[53] = 0
    inner[59] = str(uuid.uuid4())
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id

    cookie_str, sapisid = load_cookie()
    web_context = dict(_gemini_request_context(cookie_str, timeout_sec=timeout_sec) or {})
    web_context["_cookie_present"] = bool(cookie_str)
    web_context["_sapisid_present"] = bool(sapisid)
    query = {
        "bl": web_context.get("bl") or CONFIG["gemini_bl"],
        "hl": "en",
        "_reqid": str(int(time.time()) % 1000000),
        "rt": "c",
    }
    if web_context.get("f_sid"):
        query["f.sid"] = web_context["f_sid"]
    body_fields = {"f.req": json.dumps([None, json.dumps(inner)])}
    if web_context.get("at"):
        body_fields["at"] = web_context["at"]
    body = urllib.parse.urlencode(body_fields)
    url = (
        "https://gemini.google.com/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate?"
        f"{urllib.parse.urlencode(query)}"
    )
    headers = build_gemini_headers(cookie_str, sapisid, model_hex_id)
    return url, body, headers, web_context


# ─── Gemini Protocol ─────────────────────────────────────────────────────────

def gemini_stream_generate(
    prompt: str,
    model_id: int,
    think_mode: int,
    timeout_sec: "float | None" = None,
    cancel_event: "threading.Event | None" = None,
    model_hex_id: "str | None" = None,
) -> str:
    """Send prompt to Gemini StreamGenerate with retry."""
    upstream_timeout = float(CONFIG["request_timeout_sec"])
    if timeout_sec is not None:
        # 客户端（如连通测试）可要求更短的上游超时，避免客户端早已断开后服务端仍在白等
        upstream_timeout = max(5.0, min(float(timeout_sec), upstream_timeout))
    url, body, headers, web_context = _build_gemini_request(
        prompt,
        model_id,
        think_mode,
        model_hex_id=model_hex_id,
        timeout_sec=upstream_timeout,
    )
    body = body.encode()

    proxy = CONFIG.get("proxy")
    diag_id = uuid.uuid4().hex[:8]
    log(
        f"upstream Gemini nonstream start gid={diag_id} model_id={model_id} think_mode={think_mode} "
        f"model_hex_id={model_hex_id or '-'} "
        f"web_context={'yes' if web_context.get('f_sid') and web_context.get('at') else 'fallback'} "
        f"prompt_chars={len(prompt or '')} body_bytes={len(body)} timeout={upstream_timeout:g} "
        f"proxy={bool(proxy)} cookie={web_context.get('_cookie_present')} "
        f"sapisid={web_context.get('_sapisid_present')}"
    )
    last_err = None
    for attempt in range(CONFIG["retry_attempts"]):
        if cancel_event is not None and cancel_event.is_set():
            log(f"upstream Gemini nonstream cancelled gid={diag_id} before attempt={attempt+1}: client disconnected")
            raise ConnectionAbortedError("client disconnected; upstream request cancelled")
        started = time.perf_counter()
        try:
            if HAS_CURL_CFFI:
                proxies = {"http": proxy, "https": proxy} if proxy else None
                resp = _curl_request(
                    "POST",
                    url,
                    proxy=proxy,
                    data=body,
                    headers=headers,
                    impersonate="chrome120",
                    proxies=proxies,
                    timeout=upstream_timeout
                )
                resp.raise_for_status()
                raw = resp.content
                status_val = resp.status_code
            else:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                ctx = ssl.create_default_context()
                if proxy:
                    opener = urllib.request.build_opener(
                        urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                        urllib.request.HTTPSHandler(context=ctx)
                    )
                    resp = opener.open(req, timeout=upstream_timeout)
                else:
                    resp = urllib.request.urlopen(req, context=ctx, timeout=upstream_timeout)
                raw = resp.read()
                status_val = getattr(resp, 'status', getattr(resp, 'code', ''))
            log(
                f"upstream Gemini nonstream response gid={diag_id} attempt={attempt+1}/{CONFIG['retry_attempts']} "
                f"status={status_val} bytes={len(raw)} "
                f"elapsed_ms={int((time.perf_counter() - started) * 1000)}"
            )
            return raw.decode("utf-8", errors="replace")
        except Exception as e:
            last_err = e
            log(
                f"upstream Gemini nonstream attempt failed gid={diag_id} attempt={attempt+1}/{CONFIG['retry_attempts']} "
                f"elapsed_ms={int((time.perf_counter() - started) * 1000)} error={exception_summary(e)}"
            )
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"Retry {attempt+1}/{CONFIG['retry_attempts']}: {e}")
                if cancel_event is not None:
                    if cancel_event.wait(CONFIG["retry_delay_sec"]):
                        log(f"upstream Gemini nonstream cancelled gid={diag_id} during retry wait: client disconnected")
                        raise ConnectionAbortedError("client disconnected; upstream retries cancelled") from e
                else:
                    time.sleep(CONFIG["retry_delay_sec"])
    log(f"upstream Gemini nonstream failed gid={diag_id} error={exception_summary(last_err)}")
    raise last_err


def _select_latest_gemini_text_snapshot(previous_text: str, candidate_text: str) -> str:
    """Keep Gemini Web's latest non-empty cumulative response snapshot."""
    candidate = str(candidate_text or "")
    if not candidate.strip():
        return str(previous_text or "")
    return candidate


_GEMINI_STREAM_PRIVATE_MARKER_RE = re.compile(
    r"<ElicitationsGroup\b|<Elicitation\b|<FollowUp\b|"
    r"\{\s*/\s*Reason\s*:|"
    r"```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=",
    re.IGNORECASE,
)
_GEMINI_STREAM_PRIVATE_MARKER_PREFIXES = (
    "<ElicitationsGroup",
    "<Elicitation",
    "<FollowUp",
    "{/ Reason:",
    "```python?code_",
    "```javascript?code_",
    "```text?code_",
)


def _common_prefix_length(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def _unclosed_inline_code_start(text: str) -> int | None:
    """Return the opening inline-code offset while ignoring fenced code blocks."""
    in_fence = False
    fence_size = 0
    inline_start = None
    inline_size = 0
    for match in re.finditer(r"`+", str(text or "")):
        if match.start() > 0 and text[match.start() - 1] == "\\":
            continue
        marker_size = len(match.group(0))
        if in_fence:
            if marker_size >= fence_size:
                in_fence = False
                fence_size = 0
            continue
        if inline_start is not None:
            if marker_size == inline_size:
                inline_start = None
                inline_size = 0
            continue
        if marker_size >= 3:
            in_fence = True
            fence_size = marker_size
            continue
        inline_start = match.start()
        inline_size = marker_size
    return inline_start


def _unclosed_markdown_label_start(text: str) -> int | None:
    """Return the last unclosed Markdown label offset, ignoring escaped brackets."""
    open_brackets = []
    for index, char in enumerate(str(text or "")):
        if char not in "[]":
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and text[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2:
            continue
        if char == "[":
            open_brackets.append(index)
        elif open_brackets:
            open_brackets.pop()
    return open_brackets[-1] if open_brackets else None


def _private_marker_start(snapshot: str) -> int | None:
    """Locate a complete marker or a marker prefix still forming at snapshot end."""
    text = str(snapshot or "")
    complete_match = _GEMINI_STREAM_PRIVATE_MARKER_RE.search(text)
    earliest = complete_match.start() if complete_match else None
    folded_text = text.casefold()
    for marker in _GEMINI_STREAM_PRIVATE_MARKER_PREFIXES:
        folded_marker = marker.casefold()
        marker_start = folded_text.find(folded_marker)
        if marker_start >= 0:
            earliest = marker_start if earliest is None else min(earliest, marker_start)
            continue
        max_partial = min(len(folded_marker) - 1, len(folded_text))
        for partial_size in range(max_partial, 2, -1):
            if folded_text.endswith(folded_marker[:partial_size]):
                partial_start = len(folded_text) - partial_size
                earliest = partial_start if earliest is None else min(earliest, partial_start)
                break
    return earliest


def _gemini_stream_safe_boundary(snapshot: str, boundary: int) -> int:
    """Move a tentative boundary before syntax Gemini may still rewrite."""
    boundary = max(0, min(len(snapshot), int(boundary)))
    prefix = snapshot[:boundary]

    private_marker_start = _private_marker_start(snapshot)
    if private_marker_start is not None and private_marker_start < boundary:
        boundary = private_marker_start
        prefix = snapshot[:boundary]

    inline_code_start = _unclosed_inline_code_start(prefix)
    if inline_code_start is not None:
        boundary = min(boundary, inline_code_start)
        prefix = snapshot[:boundary]

    markdown_label_start = _unclosed_markdown_label_start(prefix)
    if markdown_label_start is not None:
        boundary = min(boundary, markdown_label_start)
        prefix = snapshot[:boundary]

    link_target_start = prefix.rfind("](")
    if link_target_start >= 0 and prefix.find(")", link_target_start + 2) < 0:
        link_label_start = prefix.rfind("[", 0, link_target_start)
        boundary = min(boundary, link_label_start if link_label_start >= 0 else link_target_start)
    return boundary


def _gemini_stream_visible_text(raw_text: str) -> str:
    return clean_gemini_text(str(raw_text or ""), strip_whitespace=False).strip()


class _GeminiStableTextStream:
    """Turn revisable cumulative Gemini snapshots into append-only text deltas."""

    def __init__(self, tail_chars: int | None = None):
        configured_tail = CONFIG.get("gemini_stream_stability_tail_chars", 24)
        if configured_tail is None:
            configured_tail = 24
        selected_tail = configured_tail if tail_chars is None else tail_chars
        self.tail_chars = max(0, int(selected_tail))
        self.latest_snapshot = ""
        self.accepted_snapshot = ""
        self.committed_raw_prefix = ""
        self.emitted_text = ""
        self.rewrite_conflicts = 0

    def push(self, candidate_text: str) -> str:
        candidate = _select_latest_gemini_text_snapshot(self.latest_snapshot, candidate_text)
        if candidate == self.latest_snapshot:
            return ""
        self.latest_snapshot = candidate

        if not self.accepted_snapshot:
            self.accepted_snapshot = candidate
            return ""

        if self.committed_raw_prefix and not candidate.startswith(self.committed_raw_prefix):
            self.rewrite_conflicts += 1
            return ""

        common_length = _common_prefix_length(self.accepted_snapshot, candidate)
        boundary = _gemini_stream_safe_boundary(
            candidate,
            max(0, common_length - self.tail_chars),
        )
        boundary = max(len(self.committed_raw_prefix), boundary)
        stable_raw_prefix = candidate[:boundary]
        stable_text = _gemini_stream_visible_text(stable_raw_prefix)
        if not stable_text.startswith(self.emitted_text):
            self.rewrite_conflicts += 1
            return ""

        self.accepted_snapshot = candidate
        if len(stable_text) <= len(self.emitted_text):
            return ""

        delta = stable_text[len(self.emitted_text):]
        self.committed_raw_prefix = stable_raw_prefix
        self.emitted_text = stable_text
        return delta

    def finish(self) -> str:
        final_text = _gemini_stream_visible_text(self.latest_snapshot)
        if not final_text.startswith(self.emitted_text):
            self.rewrite_conflicts += 1
            return ""
        delta = final_text[len(self.emitted_text):]
        self.emitted_text = final_text
        return delta


def _iter_utf8_text_chunks(byte_chunks):
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    for chunk in byte_chunks:
        if not chunk:
            continue
        if isinstance(chunk, str):
            text = chunk
        else:
            text = decoder.decode(bytes(chunk), final=False)
        if text:
            yield text
    tail = decoder.decode(b"", final=True)
    if tail:
        yield tail


def gemini_stream_generate_iter(
    prompt: str,
    model_id: int,
    think_mode: int,
    cancel_event: "threading.Event | None" = None,
    model_hex_id: "str | None" = None,
):
    """Yield append-only stable deltas from Gemini Web's revisable snapshots."""
    url, body, headers, web_context = _build_gemini_request(
        prompt,
        model_id,
        think_mode,
        model_hex_id=model_hex_id,
        timeout_sec=CONFIG.get("request_timeout_sec"),
    )

    proxy = CONFIG.get("proxy")
    diag_id = uuid.uuid4().hex[:8]
    log(
        f"upstream Gemini stream start gid={diag_id} model_id={model_id} think_mode={think_mode} "
        f"model_hex_id={model_hex_id or '-'} "
        f"web_context={'yes' if web_context.get('f_sid') and web_context.get('at') else 'fallback'} "
        f"prompt_chars={len(prompt or '')} body_chars={len(body)} timeout={CONFIG['request_timeout_sec']} "
        f"connect_timeout={CONFIG.get('request_connect_timeout_sec')} proxy={bool(proxy)} "
        f"cookie={web_context.get('_cookie_present')} "
        f"sapisid={web_context.get('_sapisid_present')} httpx={HAS_HTTPX}"
    )

    if not HAS_CURL_CFFI and not HAS_HTTPX:
        # Fallback: non-streaming with urllib
        log(f"upstream Gemini stream fallback_non_httpx gid={diag_id}")
        generate_kwargs = {"cancel_event": cancel_event}
        if model_hex_id:
            generate_kwargs["model_hex_id"] = model_hex_id
        raw = gemini_stream_generate(prompt, model_id, think_mode, **generate_kwargs)
        text = extract_response_text(raw)
        if text:
            log(f"upstream Gemini stream fallback_text gid={diag_id} text_chars={len(text)}")
            yield text
        return

    stable_stream = _GeminiStableTextStream()
    raw_chunks = 0
    matched_lines = 0
    snapshots = 0
    deltas = 0
    output_chars = 0
    started = time.perf_counter()

    def consume_snapshot(candidate_text: str) -> str:
        nonlocal snapshots
        previous_snapshot = stable_stream.latest_snapshot
        delta = stable_stream.push(candidate_text)
        if stable_stream.latest_snapshot != previous_snapshot:
            snapshots += 1
        return delta

    def log_delta(delta_text: str) -> None:
        nonlocal deltas, output_chars
        deltas += 1
        output_chars += len(delta_text)
        if deltas <= 3 or deltas % 20 == 0:
            log(
                f"upstream Gemini stream stable_delta gid={diag_id} index={deltas} "
                f"chars={len(delta_text)} output_chars={output_chars}"
            )

    try:
        if HAS_CURL_CFFI:
            proxies = {"http": proxy, "https": proxy} if proxy else None
            resp = _curl_request(
                "POST",
                url,
                proxy=proxy,
                data=body,
                headers=headers,
                impersonate="chrome120",
                proxies=proxies,
                timeout=CONFIG["request_timeout_sec"],
                stream=True
            )
            try:
                log(
                    f"upstream Gemini stream headers gid={diag_id} status={resp.status_code} "
                    f"content_type={resp.headers.get('content-type', '')} "
                    f"elapsed_ms={int((time.perf_counter() - started) * 1000)}"
                )
                resp.raise_for_status()

                buf = ""
                for chunk in _iter_utf8_text_chunks(resp.iter_content(chunk_size=None)):
                    if cancel_event is not None and cancel_event.is_set():
                        raise ConnectionAbortedError("client disconnected; Gemini stream cancelled")
                    raw_chunks += 1
                    if raw_chunks <= 3 or raw_chunks % 20 == 0:
                        log(
                            f"upstream Gemini stream raw_chunk gid={diag_id} index={raw_chunks} "
                            f"chars={len(chunk or '')} elapsed_ms={int((time.perf_counter() - started) * 1000)}"
                        )
                    buf += chunk
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        if '"wrb.fr"' not in line or len(line) < 200:
                            continue
                        matched_lines += 1
                        try:
                            arr = json.loads(line)
                            inner_str = arr[0][2]
                            if not inner_str or len(inner_str) < 50:
                                 continue
                            inner2 = json.loads(inner_str)
                            if isinstance(inner2, list) and len(inner2) > 4 and inner2[4]:
                                for part in inner2[4]:
                                    if isinstance(part, list) and len(part) > 1 and part[1] and isinstance(part[1], list):
                                        for t in part[1]:
                                            if isinstance(t, str):
                                                delta = consume_snapshot(t)
                                                if delta:
                                                    log_delta(delta)
                                                    yield delta
                        except (json.JSONDecodeError, IndexError, TypeError) as parse_exc:
                            if matched_lines <= 3 or matched_lines % 20 == 0:
                                log(
                                    f"upstream Gemini stream parse_skip gid={diag_id} line_index={matched_lines} "
                                    f"error={exception_summary(parse_exc)} line_preview={preview_text(line, 400)}"
                                )
            finally:
                resp.close()
        else:
            transport = httpx.HTTPTransport(proxy=proxy) if proxy else None
            with httpx.Client(transport=transport, timeout=upstream_httpx_timeout(), verify=True) as client:
                with client.stream("POST", url, content=body, headers=headers) as resp:
                    log(
                        f"upstream Gemini stream headers gid={diag_id} status={resp.status_code} "
                        f"content_type={resp.headers.get('content-type', '')} "
                        f"elapsed_ms={int((time.perf_counter() - started) * 1000)}"
                    )
                    if resp.is_error:
                        error_body = resp.read()
                        log(
                            f"upstream Gemini stream http_error_body gid={diag_id} status={resp.status_code} "
                            f"preview={preview_text(error_body, 900)}"
                        )
                    resp.raise_for_status()
                    buf = ""
                    for chunk in resp.iter_text():
                        if cancel_event is not None and cancel_event.is_set():
                            raise ConnectionAbortedError("client disconnected; Gemini stream cancelled")
                        raw_chunks += 1
                        if raw_chunks <= 3 or raw_chunks % 20 == 0:
                            log(
                                f"upstream Gemini stream raw_chunk gid={diag_id} index={raw_chunks} "
                                f"chars={len(chunk or '')} elapsed_ms={int((time.perf_counter() - started) * 1000)}"
                            )
                        buf += chunk
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            if '"wrb.fr"' not in line or len(line) < 200:
                                continue
                            matched_lines += 1
                            try:
                                arr = json.loads(line)
                                inner_str = arr[0][2]
                                if not inner_str or len(inner_str) < 50:
                                    continue
                                inner2 = json.loads(inner_str)
                                if isinstance(inner2, list) and len(inner2) > 4 and inner2[4]:
                                    for part in inner2[4]:
                                        if isinstance(part, list) and len(part) > 1 and part[1] and isinstance(part[1], list):
                                            for t in part[1]:
                                                if isinstance(t, str):
                                                    delta = consume_snapshot(t)
                                                    if delta:
                                                        log_delta(delta)
                                                        yield delta
                            except (json.JSONDecodeError, IndexError, TypeError) as parse_exc:
                                if matched_lines <= 3 or matched_lines % 20 == 0:
                                    log(
                                        f"upstream Gemini stream parse_skip gid={diag_id} line_index={matched_lines} "
                                        f"error={exception_summary(parse_exc)} line_preview={preview_text(line, 400)}"
                                    )
        final_delta = stable_stream.finish()
        if final_delta:
            log_delta(final_delta)
            yield final_delta
        if stable_stream.emitted_text:
            log(
                f"upstream Gemini stream final_snapshot gid={diag_id} snapshots={snapshots} "
                f"output_chars={output_chars} conflicts={stable_stream.rewrite_conflicts}"
            )
        log(
            f"upstream Gemini stream completed gid={diag_id} raw_chunks={raw_chunks} matched_lines={matched_lines} "
            f"snapshots={snapshots} deltas={deltas} output_chars={output_chars} "
            f"conflicts={stable_stream.rewrite_conflicts} "
            f"elapsed_ms={int((time.perf_counter() - started) * 1000)}"
        )
    except Exception as exc:
        log(
            f"upstream Gemini stream failed gid={diag_id} raw_chunks={raw_chunks} matched_lines={matched_lines} "
            f"snapshots={snapshots} deltas={deltas} output_chars={output_chars} "
            f"conflicts={stable_stream.rewrite_conflicts} "
            f"elapsed_ms={int((time.perf_counter() - started) * 1000)} "
            f"error={exception_summary(exc)}"
        )
        log(preview_text(traceback.format_exc(), 1600))
        raise


_ELICITATIONS_GROUP_RE = re.compile(
    r"<ElicitationsGroup\b(?P<attrs>[^>]*)>(?P<body>.*?)</ElicitationsGroup>",
    re.DOTALL | re.IGNORECASE,
)
_ELICITATIONS_GROUP_OPEN_RE = re.compile(r"<ElicitationsGroup\b(?P<attrs>[^>]*)>", re.DOTALL | re.IGNORECASE)
_ELICITATIONS_GROUP_CLOSE_RE = re.compile(r"</ElicitationsGroup\s*>", re.IGNORECASE)
_ELICITATION_RE = re.compile(r"<Elicitation\b(?P<attrs>[^>]*)/?>", re.DOTALL | re.IGNORECASE)
_FOLLOW_UP_RE = re.compile(r"<FollowUp\b(?P<attrs>[^>]*)/?>", re.DOTALL | re.IGNORECASE)
_XML_ATTR_RE = re.compile(r"([A-Za-z_:][\w:.-]*)\s*=\s*(\"([^\"]*)\"|'([^']*)')", re.DOTALL)
_ELICITATION_REASON_RE = re.compile(r"\{\s*/\s*Reason\s*:.*?/\s*\}\s*", re.DOTALL | re.IGNORECASE)
_FOLLOW_UP_LINE_RE = re.compile(
    r"^\s*(?:[-*+]\s+|\d{1,3}[.)、]\s+)?(?P<label>[^：:\n]{2,180}?)[：:](?P<query>\s*\S.*)$"
)
_FOLLOW_UP_LABEL_PREFIXES = (
    "想深入了解",
    "深入了解",
    "详细了解",
    "进一步了解",
    "继续了解",
    "想了解",
    "了解",
    "看一看",
    "看看",
    "查看",
    "详细分析",
)
_FOLLOW_UP_MESSAGE_PREFIXES = ("想要深入了解", "如果您想继续", "可继续了解", "可以继续了解")


def xml_like_attrs(attr_text: str) -> dict:
    attrs = {}
    for match in _XML_ATTR_RE.finditer(str(attr_text or "")):
        value = match.group(3) if match.group(3) is not None else match.group(4)
        attrs[match.group(1)] = html.unescape(value or "")
    return attrs


def _follow_up_dedupe_key(line: str) -> str:
    text = re.sub(r"\s+", " ", str(line or "").strip())
    if not text:
        return ""
    item_match = _FOLLOW_UP_LINE_RE.match(text)
    if item_match:
        label = str(item_match.group("label") or "").strip()
        query = str(item_match.group("query") or "").strip()
        if any(label.startswith(prefix) for prefix in _FOLLOW_UP_LABEL_PREFIXES):
            label_key = re.sub(r"\s+", "", label)
            query_key = re.sub(r"\s+", "", query)
            return f"item:{label_key}\0{query_key}"
    if any(text.startswith(prefix) for prefix in _FOLLOW_UP_MESSAGE_PREFIXES):
        return "message:" + re.sub(r"\s+", "", text)
    return ""


def _dedupe_follow_up_lines(text: str) -> str:
    lines = str(text or "").splitlines()
    keyed_indices: dict[str, int] = {}
    keys: list[str] = []
    for idx, line in enumerate(lines):
        key = _follow_up_dedupe_key(line)
        keys.append(key)
        if key:
            keyed_indices[key] = idx

    out: list[str] = []
    for idx, line in enumerate(lines):
        key = keys[idx]
        if key and keyed_indices.get(key) != idx:
            continue
        out.append(line)
    return "\n".join(out)


def convert_elicitations_markup(text: str) -> str:
    """Convert Gemini Web elicitation markup into readable plain text."""
    def format_item(attrs: dict) -> str:
        label = str(attrs.get("label") or "").strip()
        query = str(attrs.get("query") or "").strip()
        if label and query and label != query:
            return f"- {label}：{query}"
        if label or query:
            return f"- {label or query}"
        return ""

    def replace_group(match):
        group_attrs = xml_like_attrs(match.group("attrs"))
        body = match.group("body") or ""
        message = str(group_attrs.get("message") or "").strip()
        lines = [message] if message else ["可继续了解："]
        for item in _ELICITATION_RE.finditer(body):
            line = format_item(xml_like_attrs(item.group("attrs")))
            if line:
                lines.append(line)
        return "\n".join(lines)

    converted = _ELICITATIONS_GROUP_RE.sub(replace_group, str(text or ""))
    converted = _ELICITATION_REASON_RE.sub("", converted)
    converted = _ELICITATIONS_GROUP_OPEN_RE.sub(
        lambda match: str(xml_like_attrs(match.group("attrs")).get("message") or "").strip(),
        converted,
    )
    converted = _ELICITATIONS_GROUP_CLOSE_RE.sub("", converted)
    converted = _ELICITATION_RE.sub(lambda match: format_item(xml_like_attrs(match.group("attrs"))), converted)
    converted = _FOLLOW_UP_RE.sub(lambda match: format_item(xml_like_attrs(match.group("attrs"))), converted)
    return _dedupe_follow_up_lines(converted)


def clean_gemini_text(text: str, strip_whitespace: bool = True) -> str:
    """Remove internal code execution artifacts."""
    text = re.sub(
        r'```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n.*?```\n?',
        '', text, flags=re.DOTALL
    )
    text = convert_elicitations_markup(text)
    return text.strip() if strip_whitespace else text


def extract_response_text(raw: str) -> str:
    """Parse StreamGenerate response to extract final text."""
    bard_err = re.search(r'BardErrorInfo["\s,:]*\[?(\d+)\]?', raw or "")
    if bard_err:
        raise RuntimeError(f"Gemini upstream rejected request: BardErrorInfo [{bard_err.group(1)}]")
    texts = []
    for line in raw.split("\n"):
        if '"wrb.fr"' not in line or len(line) < 200:
            continue
        try:
            arr = json.loads(line)
            inner_str = arr[0][2]
            if not inner_str or len(inner_str) < 50:
                continue
            inner = json.loads(inner_str)
            if isinstance(inner, list) and len(inner) > 4 and inner[4]:
                for part in inner[4]:
                    if isinstance(part, list) and len(part) > 1 and part[1]:
                        if isinstance(part[1], list):
                            for t in part[1]:
                                if isinstance(t, str) and len(t) > 0:
                                    texts.append(t)
        except (json.JSONDecodeError, IndexError, TypeError):
            pass
    text = ""
    for t in reversed(texts):
        if t.strip():
            text = t
            break
    return clean_gemini_text(text)


# ─── OpenAI Format Helpers ───────────────────────────────────────────────────

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """使用 pypdf 提取 PDF 字节中的纯文本。"""
    import io
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            return "[Error: pypdf library is not installed on the server]"
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text_list = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text_list.append(t)
        return "\n".join(text_list)
    except Exception as e:
        return f"[PDF parsing error: {e}]"


def extract_text_from_generic_file(file_bytes: bytes, mime_type: str) -> str:
    """根据 mime_type 解析文本或 PDF 附件。"""
    if not file_bytes:
        return ""
    if "pdf" in mime_type.lower():
        return extract_text_from_pdf(file_bytes)
    try:
        return file_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        return f"[File decoding error: {e}]"


def value_url(value) -> str:
    if isinstance(value, dict):
        for key in ("url", "file_data", "data", "image_url"):
            nested = value.get(key)
            if nested:
                return value_url(nested)
        return ""
    if value is None:
        return ""
    return str(value or "").strip()


def part_url(part: dict, *keys: str) -> str:
    if not isinstance(part, dict):
        return ""
    for key in keys:
        if key in part:
            url = value_url(part.get(key))
            if url:
                return url
    return value_url(part.get("url"))


def data_url_info(url: str) -> tuple[str, int]:
    text = str(url or "")
    if not text.startswith("data:"):
        return "", 0
    try:
        header, payload = text.split(",", 1)
        mime_type = header.split(";", 1)[0].split(":", 1)[1] or "application/octet-stream"
        if ";base64" in header:
            approx_bytes = int(len(payload) * 3 / 4)
        else:
            approx_bytes = len(urllib.parse.unquote_to_bytes(payload))
        return mime_type, max(0, approx_bytes)
    except Exception:
        return "application/octet-stream", 0


def attachment_placeholder(url: str, kind: str = "attachment") -> str:
    url = str(url or "")
    mime_type, approx_bytes = data_url_info(url)
    if mime_type:
        size = f", approx {approx_bytes} bytes" if approx_bytes else ""
        return f"[Inline {kind}: {mime_type}{size}. Binary content is not visible to this Gemini Web text proxy.]"
    return f"[{kind.capitalize()} attachment URL: {url}]"


def messages_to_prompt(messages: list, tools: list = None, response_options: dict = None) -> str:
    """滑动上下文收缩 wrapper：
    当生成的 Prompt 长度超过 85000 字符时，自动滑动裁剪最早的历史对话（保留系统指令），
    以彻底避免 Gemini 网页端返回 BardErrorInfo: 1152 (Prompt 太长或安全限制) 错误。
    """
    system_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
    other_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]

    active_other_msgs = list(other_msgs)
    while True:
        candidate_messages = system_msgs + active_other_msgs
        prompt = _messages_to_prompt_raw(candidate_messages, tools, response_options)
        if len(prompt) <= 85000 or len(active_other_msgs) <= 2:
            if len(active_other_msgs) < len(other_msgs):
                log(f"[Context Shrink] Original prompt would exceed safety limit. "
                    f"Shrank history messages count: {len(other_msgs)} -> {len(active_other_msgs)}. "
                    f"Final prompt length: {len(prompt)} chars.")
            return prompt
        active_other_msgs.pop(0)


def _messages_to_prompt_raw(messages: list, tools: list = None, response_options: dict = None) -> str:
    """Convert OpenAI messages to prompt string and extract document/image attachments for Web API."""
    parts = []
    response_options = response_options or {}
    if tools:
        tool_defs = []
        for tool in tools:
            fn = tool.get("function", tool) if tool.get("type") == "function" else tool
            tool_defs.append({
                "name": fn.get("name", tool.get("name", "")),
                "description": fn.get("description", tool.get("description", "")),
                "parameters": fn.get("parameters", tool.get("parameters", {})),
            })
        if tool_defs:
            tool_names = ", ".join(t["name"] for t in tool_defs if t.get("name"))
            parts.append(
                "[System instruction]: You have access to tools. "
                "When an action requires filesystem, shell, browser, or any other external operation, "
                "you MUST call a tool instead of claiming the action is done in text. "
                "The client will execute the tool and send the result back to you.\n"
                f"Allowed tool names: {tool_names}\n"
                "All allowed tools listed here are available. Never say a listed tool is missing, unavailable, "
                "or cannot be found. Never invent a different tool name. Use exactly one of the allowed names.\n"
                "To call a tool, respond with only one or more blocks in this exact format:\n"
                '```tool_call\n{"name": "func_name", "arguments": {...}}\n```\n'
                "The arguments value must be a JSON object. Do not wrap tool calls in prose. "
                "Do not say you created, edited, read, or ran anything until a tool result is provided.\n\n"
                "If you need to inspect files, list directories, search code, run tests, or edit the workspace, "
                "your next response must be a tool_call block, not a description of what you would do.\n\n"
                f"Available tools:\n{json.dumps(tool_defs, indent=2)}"
            )
    option_prompt = responses_options_prompt(response_options)
    if option_prompt:
        parts.append(option_prompt)
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # 提取并解析多模态附件的容器
        text_parts = []
        file_attachments = []

        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                c_type = c.get("type", "")
                if c_type in ("text", "input_text", "output_text", "summary_text"):
                    text_parts.append(c.get("text", ""))
                elif c_type == "refusal":
                    text_parts.append(c.get("refusal", c.get("text", "")))
                elif c_type in ("image_url", "input_image") or "image_url" in c:
                    img_url = part_url(c, "image_url")
                    if img_url.startswith("data:image/"):
                        # 免 Key Web 模式无法接收本地 Base64 图片，进行友好提示
                        text_parts.append(
                            "[本地上传图片: (由于匿名免Key限制，图片转换为文本上下文已忽略，请使用官方APIKey或公开的网络图片URL获得视觉多模态支持)]"
                        )
                    elif img_url:
                        # 网络图片，在 Prompt 里保留链接
                        text_parts.append(f"[图片附件: {img_url}]")
                elif c_type == "file_url" or c_type == "file" or c_type == "input_file" or "file_url" in c or "file_data" in c:
                    file_meta = c.get("file_url", {})
                    if isinstance(file_meta, dict):
                        file_url = file_meta.get("url", "") or file_meta.get("file_data", "")
                        filename = file_meta.get("name") or file_meta.get("filename")
                    else:
                        file_url = str(file_meta or "")
                        filename = None
                    file_url = part_url(c, "file_url", "file_data") or file_url or c.get("url", "")
                    if file_url.startswith("data:"):
                        try:
                            header, b64_data = file_url.split(",", 1)
                            mime_type = header.split(";")[0].split(":")[1]
                            file_bytes = base64.b64decode(b64_data)
                            file_text = extract_text_from_generic_file(file_bytes, mime_type)
                            filename = filename or c.get("filename") or c.get("name") or "untitled_file"
                            file_attachments.append((filename, file_text))
                        except Exception as e:
                            log(f"Error parsing base64 file attachment: {e}")
                    elif file_url:
                        # 网络文件链接
                        text_parts.append(f"[网络文件链接: {file_url}]")

        # 将文本与文件附件进行美化排版拼接
        merged_text = "\n".join(p for p in text_parts if p)
        if file_attachments:
            attach_blocks = []
            for fname, ftext in file_attachments:
                attach_blocks.append(
                    f"[已上传的文件附件: {fname}]\n"
                    f"--- 文件内容开始 ---\n"
                    f"{ftext}\n"
                    f"--- 文件内容结束 ---"
                )
            merged_text = "\n\n".join(attach_blocks) + "\n\n" + merged_text

        if role == "system":
            parts.append(f"[System instruction]: {merged_text}")
        elif role == "assistant":
            if msg.get("tool_calls"):
                tc_strs = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    tc_strs.append(
                        f'```tool_call\n{{"name": "{fn.get("name")}", '
                        f'"arguments": {fn.get("arguments", "{}")}}}\n```'
                    )
                parts.append(f"[Assistant]: {merged_text or ''}\n" + "\n".join(tc_strs))
            else:
                parts.append(f"[Assistant]: {merged_text}")
        elif role == "tool":
            tool_name = msg.get("name", "") or msg.get("tool_call_id", "")
            parts.append(f"[Tool result for {tool_name}]: {merged_text}")
        else:
            parts.append(merged_text if merged_text else "")

    return "\n\n".join(p for p in parts if p)


def tool_names_from_tools(tools) -> set:
    names = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = fn.get("name") or tool.get("name")
        if name:
            names.add(str(name))
    return names


def _tool_name_tail(name) -> str:
    parts = re.split(r"(?:__|\.)", str(name or "").strip())
    return parts[-1] if parts else ""


def _tool_name_canonical(name) -> str:
    return str(name or "").strip().replace(".", "__")


def normalize_tool_name(name, allowed_names=None) -> str:
    name = str(name or "").strip()
    if allowed_names:
        allowed_names = {str(item) for item in allowed_names if item}
        if name in allowed_names:
            return name
        # 命名空间分隔符兼容：模型可能用 "ns.tool" 而注册名是 "ns__tool"（或相反）
        canonical_matches = {_tool_name_canonical(allowed): allowed for allowed in sorted(allowed_names)}
        canonical = _tool_name_canonical(name)
        if canonical in canonical_matches:
            return canonical_matches[canonical]
        short = _tool_name_tail(name)
        if short in allowed_names:
            return short
        tail_matches = [allowed for allowed in sorted(allowed_names) if _tool_name_tail(allowed) == short]
        if len(tail_matches) == 1:
            return tail_matches[0]
    return name


def normalize_tool_arguments(value) -> str:
    if value is None or value == "":
        return "{}"
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return "{}"
        try:
            parsed = json.loads(stripped)
            return json.dumps(parsed, ensure_ascii=False)
        except Exception:
            return json.dumps({"input": value}, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def normalize_tool_schema(schema):
    normalized = dict(schema) if isinstance(schema, dict) else {}
    normalized.setdefault("type", "object")
    if not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    if not isinstance(normalized.get("required"), list):
        normalized["required"] = []
    return normalized


def flatten_namespace_tool_name(namespace: str, name: str) -> str:
    namespace = str(namespace or "")
    name = str(name or "")
    if not namespace:
        return name
    if not name:
        return namespace
    if namespace.endswith("__") or name.startswith("__"):
        return f"{namespace}{name}"
    return f"{namespace}__{name}"


def combine_tool_description(namespace_description: str, child_description: str) -> str:
    namespace_description = str(namespace_description or "").strip()
    child_description = str(child_description or "").strip()
    if namespace_description and child_description:
        return f"{namespace_description}\n\n{child_description}"
    return child_description or namespace_description


def load_jsonish(value):
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        raise ValueError("empty JSON")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, ch in enumerate(text):
            if ch not in "[{":
                continue
            try:
                parsed, _ = decoder.raw_decode(text[index:])
                return parsed
            except json.JSONDecodeError:
                continue
        raise


def parse_loose_tool_call_block(block: str):
    try:
        return load_jsonish(block)
    except Exception:
        pass

    name_match = re.search(
        r'(?im)(?:^|[,{]\s*)["\']?(?:name|tool_name|recipient_name)["\']?\s*[:=]\s*["`\']?([A-Za-z0-9_.-]+)',
        block,
    )
    if not name_match:
        return None
    data = {"name": name_match.group(1)}
    args_match = re.search(r'(?is)(?:^|[,{]\s*)["\']?(?:arguments|args|parameters|input)["\']?\s*[:=]\s*(.+)$', block)
    if args_match:
        raw_args = args_match.group(1).strip().strip("`")
        if block.strip().startswith("{") and raw_args.startswith("{") and raw_args.endswith("}"):
            raw_args = raw_args[:-1].rstrip()
        try:
            data["arguments"] = load_jsonish(raw_args)
        except Exception:
            data["arguments"] = parse_loose_argument_object(raw_args)
    return data


def parse_loose_argument_object(raw_args: str):
    raw = str(raw_args or "").strip().rstrip(",")
    match = re.match(
        r'(?is)^\{\s*["\']?([A-Za-z_][A-Za-z0-9_.-]*)["\']?\s*[:=]\s*(.+)\}\s*$',
        raw,
    )
    if not match:
        return raw

    key = match.group(1)
    value = match.group(2).strip().rstrip(",").strip()
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        value = value[1:-1]
    else:
        if value[:1] in {"'", '"'}:
            value = value[1:]
        if value[-1:] in {"'", '"'}:
            value = value[:-1]
    value = (
        value.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
    )
    return {key: value}


def coerce_tool_calls(payload, allowed_names=None) -> list:
    calls = []
    if payload is None:
        return calls
    if isinstance(payload, list):
        for item in payload:
            calls.extend(coerce_tool_calls(item, allowed_names))
        return calls
    if not isinstance(payload, dict):
        return calls

    for key in ("tool_calls", "function_calls", "calls"):
        nested = payload.get(key)
        if isinstance(nested, list):
            calls.extend(coerce_tool_calls(nested, allowed_names))
    for key in ("tool_call", "function_call", "call"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            calls.extend(coerce_tool_calls(nested, allowed_names))

    fn = payload.get("function") if isinstance(payload.get("function"), dict) else {}
    raw_name = (
        fn.get("name")
        or payload.get("name")
        or payload.get("tool_name")
        or payload.get("recipient_name")
    )
    name = normalize_tool_name(raw_name, allowed_names)
    if not name:
        return calls
    if allowed_names and name not in allowed_names:
        return calls

    if "arguments" in fn:
        args = fn.get("arguments")
    elif "arguments" in payload:
        args = payload.get("arguments")
    elif "args" in payload:
        args = payload.get("args")
    elif "parameters" in payload:
        args = payload.get("parameters")
    elif "input" in payload:
        args = payload.get("input")
    else:
        args = {}

    call_id = str(payload.get("id") or payload.get("call_id") or f"call_{uuid.uuid4().hex[:8]}")
    if not call_id.startswith("call_"):
        call_id = f"call_{call_id}"
    calls.append({
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": normalize_tool_arguments(args),
        },
    })
    return calls


def parse_tool_calls(text: str, tools: list = None) -> tuple:
    """Extract tool calls from Gemini text. Returns (clean_text, tool_calls_list)."""
    text = text or ""
    allowed_names = tool_names_from_tools(tools)
    tool_calls = []
    consumed_spans = []

    def consume_payload(payload, span=None):
        calls = coerce_tool_calls(payload, allowed_names)
        if not calls:
            return
        tool_calls.extend(calls)
        if span:
            consumed_spans.append(span)

    def span_consumed(span):
        if not span:
            return False
        start, end = span
        return any(start >= used_start and end <= used_end for used_start, used_end in consumed_spans)

    def scan_json_payloads():
        decoder = json.JSONDecoder()
        for index, ch in enumerate(text):
            if ch not in "[{":
                continue
            try:
                parsed, end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            span = (index, index + end)
            if not span_consumed(span):
                consume_payload(parsed, span)

    def scan_function_syntax():
        if not allowed_names:
            return
        name_pattern = re.compile(r'(?<![\w.])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\(')
        decoder = json.JSONDecoder()
        for match in name_pattern.finditer(text):
            raw_name = match.group(1)
            name = normalize_tool_name(raw_name, allowed_names)
            if name not in allowed_names:
                continue
            index = match.end()
            while index < len(text) and text[index].isspace():
                index += 1
            if index >= len(text):
                continue
            if text[index] == ")":
                consume_payload({"name": name, "arguments": {}}, (match.start(), index + 1))
                continue
            if text[index] not in "[{":
                continue
            try:
                parsed_args, end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            close = index + end
            while close < len(text) and text[close].isspace():
                close += 1
            if close < len(text) and text[close] == ")":
                close += 1
            span = (match.start(), close)
            if not span_consumed(span):
                consume_payload({"name": name, "arguments": parsed_args}, span)

    fence_pattern = re.compile(r'```([A-Za-z0-9_-]*)\s*\n(.*?)```', re.DOTALL)
    for match in fence_pattern.finditer(text):
        language = (match.group(1) or "").strip().lower()
        block = match.group(2)
        if language and language not in {"tool_call", "tool", "json", "jsonc"}:
            continue
        consume_payload(parse_loose_tool_call_block(block), match.span())

    tag_pattern = re.compile(
        r'<(?:tool_call|function_call)>\s*(.*?)\s*</(?:tool_call|function_call)>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in tag_pattern.finditer(text):
        consume_payload(parse_loose_tool_call_block(match.group(1)), match.span())

    if not tool_calls:
        stripped = text.strip()
        if stripped.startswith(("{", "[")):
            try:
                consume_payload(load_jsonish(stripped), (0, len(text)))
            except Exception:
                pass

    scan_json_payloads()
    scan_function_syntax()

    clean = text
    for start, end in sorted(consumed_spans, reverse=True):
        clean = clean[:start] + clean[end:]
    return clean.strip(), tool_calls


def text_looks_like_tool_intent(text: str, tools: list = None, options: dict = None) -> bool:
    text = str(text or "")
    lowered = text.lower()
    options = options or {}
    if options.get("tool_choice") == "required" or tool_choice_function_name(options.get("tool_choice")):
        return True
    tool_names = tool_names_from_tools(tools)
    if any(name and name.lower() in lowered for name in tool_names):
        return True
    missing_claims = (
        "tool wasn't found",
        "tool was not found",
        "tool is not available",
        "tool unavailable",
        "shell_command wasn't found",
        "shell_command was not found",
        "not able to use",
        "cannot use the tool",
        "don't have access to",
        "no access to",
    )
    if any(phrase in lowered for phrase in missing_claims):
        return True
    intent_phrases = (
        "i'll check",
        "i will check",
        "i'll inspect",
        "i will inspect",
        "let me search",
        "let me inspect",
        "let me look",
        "let me check",
        "let me try",
        "let me find",
        "let me read",
        "let me view",
        "let me run",
        "let me list",
        "let me explore",
        "let me fix",
        "let me create",
        "let me generate",
        "let me write",
        "let me rewrite",
        "let me regenerate",
        "let me update",
        "let me modify",
        "let me apply",
        "i'll use",
        "i will use",
        "i need to",
        "i'll try",
        "i will try",
        "i'll fix",
        "i'll create",
        "i'll write",
        "i'll generate",
        "i'll update",
        "i'll regenerate",
        "i'll rewrite",
        "i'll run",
        "i will run",
        "now i'll",
        "i'm going to",
        "look for files",
        "search for files",
        "list the files",
        "run tests",
        "check the repository",
        "inspect the repository",
        "read the file",
        "look at the",
        "browse the",
        "explore the",
    )
    if any(phrase in lowered for phrase in intent_phrases):
        return True

    chinese_intent_phrases = (
        "让我检查",
        "让我搜索",
        "让我查看",
        "让我查找",
        "让我看看",
        "让我试试",
        "让我打开",
        "让我读取",
        "让我列出",
        "让我浏览",
        "让我重新",
        "让我修复",
        "让我生成",
        "让我创建",
        "让我编写",
        "让我写入",
        "让我运行",
        "让我执行",
        "让我更新",
        "让我修改",
        "让我删除",
        "让我安装",
        "让我应用",
        "我来检查",
        "我来搜索",
        "我来查看",
        "我原意检查",
        "我将检查",
        "我将搜索",
        "我将查看",
        "我将重新",
        "我将修复",
        "我将生成",
        "我来看看",
        "我来试试",
        "我来重新",
        "我来修复",
        "我来生成",
        "我来创建",
        "我来运行",
        "我来执行",
        "我来修改",
        "现在让我",
        "现在我来",
        "接下来我",
        "我需要检查",
        "我需要搜索",
        "我需要查看",
        "先查看",
        "先检查",
        "先搜索",
        "先看看",
        "先读取",
        "查看一下",
        "检查一下",
        "搜索一下",
        "看一下",
        "试一下",
        "没有找到",
        "找不到",
        "无法找到",
        "不可用",
        "超时了",
        "该命令超时",
    )
    if any(phrase in text for phrase in chinese_intent_phrases):
        return True
    # 末尾冒号：模型宣告即将执行动作（如"让我重新生成正确的文件："）却没有发出工具调用，
    # 这类"话说一半"的回复几乎总以冒号收尾，是最通用的意图信号。
    stripped = text.rstrip().rstrip("*`")
    return bool(stripped) and stripped.endswith(("：", ":"))


def tool_retry_prompt(prompt: str, previous_text: str, tools: list) -> str:
    tool_name_set = tool_names_from_tools(tools)
    names = ", ".join(sorted(tool_name_set))
    first_name = sorted(tool_name_set)[0] if tool_name_set else "tool_name"
    return (
        f"{prompt}\n\n"
        "[System instruction]: ERROR: Your previous response described an action in prose instead of calling a tool. "
        "The client cannot execute prose descriptions — only valid tool_call blocks are executable.\n\n"
        f"All of these tools ARE available and functional: {names}\n\n"
        "You MUST respond with ONLY a tool_call block. Example:\n"
        f'```tool_call\n{{"name": "{first_name}", "arguments": {{"...": "..."}}}}\n```\n\n'
        "Rules:\n"
        "- Do NOT include any text outside the tool_call block\n"
        "- Do NOT apologize or explain\n"
        "- Do NOT claim any tool is missing or unavailable\n"
        "- Do NOT describe what you plan to do — just DO it by calling the tool\n\n"
        f"Your previous invalid response (excerpt):\n{preview_text(previous_text, 800)}"
    )


def bool_value(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "stream"}


def json_string(value) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def response_role(role) -> str:
    role = str(role or "user").strip().lower()
    if role in ("system", "developer"):
        return "system"
    if role == "assistant":
        return "assistant"
    if role in ("tool", "function"):
        return "tool"
    return "user"


def response_reasoning_summary(item: dict) -> str:
    summary = item.get("summary")
    if summary:
        return response_content_text(summary)
    for key in ("text", "content"):
        text = response_content_text(item.get(key))
        if text:
            return text
    return ""


def response_content_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = [response_content_text(item).strip() for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        c_type = str(value.get("type", ""))
        if c_type in ("function_call", "custom_tool_call"):
            return ""
        if c_type == "function_call_output":
            return response_content_text(value.get("output"))
        if c_type == "reasoning":
            return response_reasoning_summary(value)
        for key in ("text", "input_text", "output_text", "summary_text", "refusal", "output", "content"):
            if key in value:
                text = response_content_text(value.get(key))
                if text:
                    return text
        image_url = part_url(value, "image_url") if (
            c_type in ("image_url", "input_image") or "image_url" in value
        ) else ""
        if image_url:
            return attachment_placeholder(image_url, "image")
        file_url = part_url(value, "file_url", "file_data") if (
            c_type in ("file_url", "file", "input_file") or "file_url" in value or "file_data" in value
        ) else ""
        if file_url:
            return attachment_placeholder(file_url, "file")
        file_name = value.get("filename") or value.get("name") or value.get("file_id")
        if file_name:
            return f"[File attachment: {file_name}]"
    return ""


def response_message_content(content):
    if isinstance(content, dict):
        return [content]
    return content


def response_tool_call_message(item: dict) -> dict:
    call_id = str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}")
    name = str(item.get("name") or item.get("tool_name") or "")
    namespace = str(item.get("namespace") or "")
    upstream_name = flatten_namespace_tool_name(namespace, name) if namespace else name
    if str(item.get("type") or "") == "custom_tool_call":
        arguments = json_string({"input": item.get("input", "")})
    else:
        arguments = json_string(item.get("arguments", "{}"))
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": upstream_name, "arguments": arguments},
        }],
    }


def response_tool_calls_from_content(content) -> list:
    calls = []
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, (list, tuple)):
        return calls
    for i, part in enumerate(content):
        if not isinstance(part, dict):
            continue
        if part.get("type") not in ("function_call", "custom_tool_call"):
            continue
        call_id = str(part.get("call_id") or part.get("id") or f"call_{i}")
        name = str(part.get("name") or part.get("tool_name") or "")
        namespace = str(part.get("namespace") or "")
        upstream_name = flatten_namespace_tool_name(namespace, name) if namespace else name
        calls.append({
            "id": call_id,
            "type": "function",
            "function": {
                "name": upstream_name,
                "arguments": json_string({"input": part.get("input", "")})
                if part.get("type") == "custom_tool_call"
                else json_string(part.get("arguments", "{}")),
            },
        })
    return calls


def compact_tool_schema(schema):
    schema = normalize_tool_schema(schema)
    compact = {"type": schema.get("type", "object")}
    properties = schema.get("properties")
    if isinstance(properties, dict):
        compact_props = {}
        for name, prop in properties.items():
            if isinstance(prop, dict):
                compact_prop = {}
                for key in ("type", "description", "enum"):
                    if key in prop:
                        value = prop[key]
                        if key == "description":
                            value = preview_text(value, 160)
                        compact_prop[key] = value
                compact_props[name] = compact_prop or {"type": prop.get("type", "string")}
            else:
                compact_props[name] = {}
        compact["properties"] = compact_props
    required = schema.get("required")
    if isinstance(required, list):
        compact["required"] = required
    schema_text = json.dumps(compact, ensure_ascii=False)
    if len(schema_text) > int(CONFIG.get("max_tool_schema_chars", 1200)):
        return {"type": "object"}
    return compact


def responses_to_messages(req: dict) -> list:
    messages = []
    instructions = req.get("instructions")
    if instructions is None:
        instructions = req.get("system") or req.get("system_prompt") or req.get("systemPrompt")
    instruction_text = response_content_text(instructions)
    if instruction_text:
        messages.append({"role": "system", "content": instruction_text})

    input_items = req.get("input")
    if input_items is None:
        input_items = req.get("messages", [])

    def add_message(role, content, **extra):
        role = response_role(role)
        normalized = response_message_content(content)
        text = response_content_text(normalized)
        if not text and not (role == "assistant" and extra.get("tool_calls")):
            return
        msg = {"role": role, "content": normalized if isinstance(normalized, (str, list)) else text}
        msg.update(extra)
        messages.append(msg)

    def handle_item(item):
        if isinstance(item, str):
            add_message("user", item)
            return
        if not isinstance(item, dict):
            add_message("user", response_content_text(item))
            return

        item_type = str(item.get("type", ""))
        if item_type == "message" or "role" in item:
            role = item.get("role", "user")
            content = item.get("content", item.get("text", item.get("input_text", "")))
            tool_calls = item.get("tool_calls") or response_tool_calls_from_content(content)
            if response_role(role) == "assistant" and tool_calls:
                add_message("assistant", content, tool_calls=tool_calls)
            else:
                add_message(role, content)
            return
        if item_type in ("input_text", "text"):
            add_message("user", item.get("text", item.get("input_text", "")))
            return
        if item_type in ("output_text", "summary_text"):
            add_message("assistant", item.get("text", item.get("output_text", "")))
            return
        if item_type in ("input_image", "input_file"):
            add_message("user", [item])
            return
        if item_type == "function_call":
            messages.append(response_tool_call_message(item))
            return
        if item_type == "function_call_output":
            messages.append({
                "role": "tool",
                "tool_call_id": str(item.get("call_id") or item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "content": response_content_text(item.get("output")),
            })
            return
        if item_type == "reasoning":
            summary = response_reasoning_summary(item)
            if summary:
                add_message("assistant", f"[Reasoning summary]: {summary}")
            return

        text = response_content_text(item)
        if text:
            add_message(item.get("role", "user"), text)

    if isinstance(input_items, list):
        for input_item in input_items:
            handle_item(input_item)
    else:
        handle_item(input_items)

    return messages


def normalize_responses_tools(tools):
    normalized = []
    if not isinstance(tools, list):
        return normalized
    for tool in tools:
        if isinstance(tool, str):
            parameters = {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "Raw input text for the custom tool.",
                    },
                },
                "required": ["input"],
            }
            normalized.append({
                "type": "function",
                "function": {
                    "name": str(tool),
                    "description": "",
                    "parameters": compact_tool_schema(parameters),
                },
            })
            continue
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type") or "")
        if tool_type == "namespace":
            namespace = str(tool.get("name") or "")
            namespace_description = str(tool.get("description") or "")
            for child in tool.get("tools") or []:
                if not isinstance(child, dict) or str(child.get("type") or "") != "function":
                    continue
                child_name = str(child.get("name") or "")
                if not child_name:
                    continue
                child_parameters = child.get("parameters")
                if child_parameters is None:
                    child_parameters = child.get("input_schema", child.get("schema", {}))
                function = {
                    "name": flatten_namespace_tool_name(namespace, child_name),
                    "description": preview_text(
                        combine_tool_description(namespace_description, child.get("description") or ""),
                        int(CONFIG.get("max_tool_description_chars", 240)),
                    ),
                    "parameters": compact_tool_schema(child_parameters or {}),
                }
                if "strict" in child:
                    function["strict"] = bool(child.get("strict"))
                normalized.append({"type": "function", "function": function})
            continue
        if tool_type == "function":
            fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        elif tool_type == "custom":
            fn = dict(tool)
            fn.setdefault("parameters", {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "Raw input text for the custom tool.",
                    },
                },
                "required": ["input"],
            })
        elif isinstance(tool.get("function"), dict):
            fn = tool["function"]
        else:
            fn = tool
        name = fn.get("name") or tool.get("name")
        if not name:
            continue
        parameters = fn.get("parameters")
        if parameters is None:
            parameters = fn.get("input_schema", fn.get("schema", {}))
        description = str(fn.get("description") or tool.get("description") or "")
        function = {
            "name": str(name),
            "description": preview_text(description, int(CONFIG.get("max_tool_description_chars", 240))),
            "parameters": compact_tool_schema(parameters or {}),
        }
        if "strict" in fn or "strict" in tool:
            function["strict"] = bool(fn.get("strict", tool.get("strict")))
        normalized.append({
            "type": "function",
            "function": function,
        })
    return normalized


def normalize_max_output_tokens(value):
    try:
        tokens = int(value)
    except Exception:
        return None
    if tokens <= 0:
        return None
    return min(tokens, 200000)


def normalize_responses_tool_choice(tool_choice):
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        choice = tool_choice.strip().lower()
        if choice in {"auto", "none", "required"}:
            return choice
        return tool_choice
    if not isinstance(tool_choice, dict):
        return tool_choice
    if tool_choice.get("type") == "function":
        fn = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
        name = fn.get("name") or tool_choice.get("name")
        if name:
            namespace = fn.get("namespace") or tool_choice.get("namespace")
            upstream_name = flatten_namespace_tool_name(namespace, name) if namespace else str(name)
            return {"type": "function", "function": {"name": upstream_name}}
    name = tool_choice.get("name")
    if name:
        return {"type": "function", "function": {"name": str(name)}}
    return tool_choice


def responses_request_options(req: dict) -> dict:
    opts = {}
    if not isinstance(req, dict):
        return opts
    max_tokens = normalize_max_output_tokens(req.get("max_output_tokens", req.get("max_tokens")))
    if max_tokens is not None:
        opts["max_output_tokens"] = max_tokens
    if "tool_choice" in req:
        opts["tool_choice"] = normalize_responses_tool_choice(req.get("tool_choice"))
    if "parallel_tool_calls" in req:
        opts["parallel_tool_calls"] = bool_value(req.get("parallel_tool_calls"))
    if isinstance(req.get("response_format"), dict):
        opts["response_format"] = req.get("response_format")
    text_config = req.get("text")
    if isinstance(text_config, dict) and isinstance(text_config.get("format"), dict):
        opts["text_format"] = text_config.get("format")
    return opts


def tool_choice_function_name(tool_choice) -> str:
    if not isinstance(tool_choice, dict):
        return ""
    fn = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
    return str(fn.get("name") or tool_choice.get("name") or "").strip()


def tools_for_responses_options(tools: list, options: dict) -> list:
    options = options or {}
    tool_choice = options.get("tool_choice")
    if tool_choice == "none":
        return []
    required_name = tool_choice_function_name(tool_choice)
    if not required_name:
        return tools or []
    filtered = []
    for tool in tools or []:
        fn = tool.get("function") if isinstance(tool, dict) and isinstance(tool.get("function"), dict) else tool
        if isinstance(fn, dict) and str(fn.get("name") or "") == required_name:
            filtered.append(tool)
    return filtered


def apply_responses_tool_policy(tool_calls, options: dict) -> list:
    calls = list(tool_calls or [])
    options = options or {}
    tool_choice = options.get("tool_choice")
    if tool_choice == "none":
        return []
    required_name = tool_choice_function_name(tool_choice)
    if required_name:
        filtered = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else {}
            if str(fn.get("name") or call.get("name") or "") == required_name:
                filtered.append(call)
        calls = filtered
    if options.get("parallel_tool_calls") is False and len(calls) > 1:
        return calls[:1]
    return calls


def response_format_prompt(format_config: dict) -> str:
    if not isinstance(format_config, dict):
        return ""
    fmt_type = str(format_config.get("type") or "").strip()
    if fmt_type in {"json_object", "json_schema"}:
        pieces = ["When you provide final assistant text, it must be valid JSON."]
        if fmt_type == "json_schema":
            schema = format_config.get("schema") or format_config.get("json_schema")
            if schema:
                pieces.append(f"Use this JSON schema: {preview_text(json.dumps(schema, ensure_ascii=False), 1200)}")
        return " ".join(pieces)
    return ""


def responses_options_prompt(options: dict) -> str:
    options = options or {}
    lines = []
    max_tokens = options.get("max_output_tokens")
    if max_tokens:
        lines.append(
            f"Keep final assistant text within about {max_tokens} output tokens. "
            "This limit does not prevent necessary tool calls."
        )
    tool_choice = options.get("tool_choice")
    if tool_choice == "none":
        lines.append("Tool choice is none: do not call tools in this turn; answer in text only.")
    elif tool_choice == "required":
        lines.append("Tool choice is required: call at least one available tool before giving a final answer.")
    else:
        required_name = tool_choice_function_name(tool_choice)
        if required_name:
            lines.append(f"Tool choice requires function `{required_name}`: call only this function for the next tool action.")
    if options.get("parallel_tool_calls") is False:
        lines.append("parallel_tool_calls is false: return at most one tool call in this response.")
    fmt_prompt = response_format_prompt(options.get("response_format")) or response_format_prompt(options.get("text_format"))
    if fmt_prompt:
        lines.append(fmt_prompt)
    if not lines:
        return ""
    return "[System instruction]: " + "\n".join(lines)


def response_usage(prompt: str, output_text: str) -> dict:
    input_tokens = len(prompt or "") // 4
    output_tokens = len(output_text or "") // 4
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
    }


def text_chunks(text: str, chunk_size: int = 120):
    text = str(text or "")
    if not text:
        yield ""
        return
    for i in range(0, len(text), max(1, chunk_size)):
        yield text[i:i + max(1, chunk_size)]


def chat_stream_chunks(text: str):
    chunk_size = int(CONFIG.get("chat_stream_chunk_chars", 160) or 160)
    yield from text_chunks(text, chunk_size=chunk_size)


THINK_OPEN_TAG = "<think>"
THINK_CLOSE_TAG = "</think>"


def split_leading_think_block(text: str):
    text = str(text or "")
    leading_ws_len = len(text) - len(text.lstrip())
    after_ws = text[leading_ws_len:]
    if not after_ws.startswith(THINK_OPEN_TAG):
        return None
    body_start = leading_ws_len + len(THINK_OPEN_TAG)
    close_start = text.find(THINK_CLOSE_TAG, body_start)
    if close_start < 0:
        return None
    answer_start = close_start + len(THINK_CLOSE_TAG)
    return text[body_start:close_start].strip(), text[answer_start:].lstrip()


def reasoning_output_item(text: str) -> dict:
    return {
        "type": "reasoning",
        "id": f"rs_{uuid.uuid4().hex[:12]}",
        "summary": [{"type": "summary_text", "text": text or ""}],
    }


def message_output_item(text: str) -> dict:
    return {
        "type": "message",
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text or "", "annotations": []}],
    }


def function_call_output_item(tc) -> dict:
    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
    call_id = str(tc.get("id") or tc.get("call_id") or f"call_{uuid.uuid4().hex[:8]}")
    item_id = str(tc.get("item_id") or f"fc_{call_id}")
    name = str(fn.get("name") or tc.get("name") or "")
    arguments = json_string(fn.get("arguments", tc.get("arguments", "{}")))
    tool_context = tc.get("_tool_context") if isinstance(tc, dict) and isinstance(tc.get("_tool_context"), dict) else None
    if codex_is_custom_tool_proxy(tool_context, name):
        return {
            "type": "custom_tool_call",
            "id": str(tc.get("item_id") or f"ctc_{call_id}"),
            "call_id": call_id,
            "name": codex_original_custom_tool_name(tool_context, name),
            "input": reconstruct_custom_tool_call_input(tool_context, name, arguments),
            "status": "completed",
        }
    display_name, namespace = codex_function_tool_name(tool_context, name)
    item = {
        "type": "function_call",
        "id": item_id,
        "call_id": call_id,
        "name": display_name,
        "arguments": arguments,
        "status": "completed",
    }
    if namespace:
        item["namespace"] = namespace
    return item


APPLY_PATCH_ACTION_SUFFIXES = {
    "add_file": "add_file",
    "delete_file": "delete_file",
    "update_file": "update_file",
    "replace_file": "replace_file",
    "batch": "batch",
}


def codex_tool_context(tools) -> dict:
    context = {"custom_tools": {}, "function_tools": {}}
    custom_tools = context["custom_tools"]
    function_tools = context["function_tools"]
    if not isinstance(tools, list):
        return context

    def add_custom(upstream_name: str, openai_name: str = None, *, kind: str = "raw", action: str = None):
        if not upstream_name:
            return
        custom_tools[str(upstream_name)] = {
            "openai_name": str(openai_name or upstream_name),
            "kind": kind,
            "action": action,
        }

    def add_function(upstream_name: str, name: str = None, namespace: str = ""):
        if not upstream_name:
            return
        function_tools[str(upstream_name)] = {
            "name": str(name or upstream_name),
            "namespace": str(namespace or ""),
        }

    def add_apply_patch_proxies(name: str):
        add_custom(name, name, kind="apply_patch")
        for suffix, action in APPLY_PATCH_ACTION_SUFFIXES.items():
            add_custom(f"{name}_{suffix}", name, kind="apply_patch", action=action)

    for tool in tools:
        if isinstance(tool, str):
            if tool.startswith("apply_patch_"):
                suffix = tool.removeprefix("apply_patch_")
                add_custom(tool, "apply_patch", kind="apply_patch", action=APPLY_PATCH_ACTION_SUFFIXES.get(suffix))
            else:
                add_custom(tool)
            continue
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type") or "")
        name = str(tool.get("name") or "").strip()
        if tool_type == "custom" and name:
            if name == "apply_patch":
                add_apply_patch_proxies(name)
            else:
                add_custom(name)
        elif tool_type == "function" and name:
            add_function(name, name, "")
        elif tool_type == "namespace":
            namespace = name
            for child in tool.get("tools") or []:
                if not isinstance(child, dict) or str(child.get("type") or "") != "function":
                    continue
                child_name = str(child.get("name") or "").strip()
                if not child_name:
                    continue
                add_function(flatten_namespace_tool_name(namespace, child_name), child_name, namespace)
        elif tool_type in {"web_search", "local_shell", "computer_use"}:
            add_custom(name or tool_type, name or tool_type, kind="built_in")
    return context


def _codex_context_lookup(table, name):
    """按注册名查工具规格；命名空间分隔符（"." 与 "__"）差异做规范化回退匹配。"""
    if not isinstance(table, dict):
        return None
    key = str(name or "")
    if key in table:
        return table[key]
    canonical = _tool_name_canonical(key)
    for registered, spec in table.items():
        if _tool_name_canonical(registered) == canonical:
            return spec
    return None


def codex_is_custom_tool_proxy(tool_context, name: str) -> bool:
    if not isinstance(tool_context, dict):
        return False
    return _codex_context_lookup(tool_context.get("custom_tools"), name) is not None


def codex_original_custom_tool_name(tool_context, name: str) -> str:
    if not isinstance(tool_context, dict):
        return str(name or "")
    spec = _codex_context_lookup(tool_context.get("custom_tools"), name)
    if isinstance(spec, dict):
        return str(spec.get("openai_name") or name or "")
    return str(name or "")


def codex_function_tool_name(tool_context, name: str) -> tuple:
    if not isinstance(tool_context, dict):
        return str(name or ""), ""
    spec = _codex_context_lookup(tool_context.get("function_tools"), name)
    if isinstance(spec, dict):
        return str(spec.get("name") or name or ""), str(spec.get("namespace") or "")
    return str(name or ""), ""


def reconstruct_custom_tool_call_input(tool_context, name: str, arguments: str) -> str:
    spec = None
    if isinstance(tool_context, dict):
        spec = _codex_context_lookup(tool_context.get("custom_tools"), name)
    if isinstance(spec, dict) and spec.get("kind") == "apply_patch":
        return reconstruct_apply_patch_input(str(spec.get("action") or ""), arguments)
    try:
        value = json.loads(str(arguments or "{}"))
    except Exception:
        return str(arguments or "")
    if isinstance(value, dict) and "input" in value:
        return response_content_text(value.get("input")) or str(value.get("input") or "")
    return str(arguments or "")


def reconstruct_apply_patch_input(action: str, arguments: str) -> str:
    try:
        value = json.loads(str(arguments or "{}"))
    except Exception:
        return str(arguments or "")
    if not isinstance(value, dict):
        return str(arguments or "")
    for key in ("raw_patch", "patch", "input"):
        raw_patch = value.get(key)
        if isinstance(raw_patch, str) and raw_patch:
            return raw_patch

    if action == "add_file":
        operations = [{"type": "add_file", "path": str(value.get("path") or ""), "content": str(value.get("content") or "")}]
    elif action == "delete_file":
        operations = [{"type": "delete_file", "path": str(value.get("path") or "")}]
    elif action == "update_file":
        operations = [{
            "type": "update_file",
            "path": str(value.get("path") or ""),
            "move_to": str(value.get("move_to") or ""),
            "hunks": value.get("hunks") if isinstance(value.get("hunks"), list) else [],
        }]
    elif action == "replace_file":
        operations = [{"type": "replace_file", "path": str(value.get("path") or ""), "content": str(value.get("content") or "")}]
    else:
        operations = value.get("operations") if isinstance(value.get("operations"), list) else []
    return build_apply_patch_text(operations)


def build_apply_patch_text(operations: list) -> str:
    text = "*** Begin Patch"
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        op_type = str(operation.get("type") or "")
        path = str(operation.get("path") or "")
        if op_type == "add_file":
            text += f"\n*** Add File: {path}"
            for line in str(operation.get("content") or "").splitlines():
                text += f"\n+{line}"
        elif op_type == "delete_file":
            text += f"\n*** Delete File: {path}"
        elif op_type == "update_file":
            text += f"\n*** Update File: {path}"
            move_to = str(operation.get("move_to") or "")
            if move_to:
                text += f"\n*** Move to: {move_to}"
            hunks = operation.get("hunks") if isinstance(operation.get("hunks"), list) else []
            for hunk in hunks:
                if not isinstance(hunk, dict):
                    continue
                context = str(hunk.get("context") or "")
                text += f"\n@@ {context}".rstrip()
                lines = hunk.get("lines") if isinstance(hunk.get("lines"), list) else []
                for line in lines:
                    if isinstance(line, dict):
                        op = str(line.get("op") or "context")
                        prefix = "+" if op == "add" else "-" if op in {"remove", "delete"} else " "
                        text += f"\n{prefix}{line.get('text') or ''}"
                    else:
                        text += f"\n {line}"
        elif op_type == "replace_file":
            text += f"\n*** Delete File: {path}"
            text += f"\n*** Add File: {path}"
            for line in str(operation.get("content") or "").splitlines():
                text += f"\n+{line}"
    return f"{text}\n*** End Patch"


def responses_output_items(text: str, tool_calls, tool_context=None) -> list:
    output = []
    text = text or ""
    think = split_leading_think_block(text)
    if think:
        reasoning_text, text = think
        if reasoning_text:
            output.append(reasoning_output_item(reasoning_text))

    if text:
        output.append(message_output_item(text))

    for tc in tool_calls or []:
        if isinstance(tc, dict) and tool_context is not None:
            tc = dict(tc)
            tc["_tool_context"] = tool_context
        output.append(function_call_output_item(tc))

    if not output:
        output.append(message_output_item(""))
    return output


def response_output_text(output: list) -> str:
    parts = []
    for item in output or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                parts.append(content.get("text", ""))
    return "".join(parts)


def response_reasoning_text(output: list) -> str:
    parts = []
    for item in output or []:
        if item.get("type") != "reasoning":
            continue
        for summary in item.get("summary", []):
            if isinstance(summary, dict):
                parts.append(summary.get("text", ""))
            elif isinstance(summary, str):
                parts.append(summary)
    return "".join(parts)


def response_usage_from_output(prompt: str, output: list) -> dict:
    output_text = response_output_text(output)
    reasoning_text = response_reasoning_text(output)
    input_tokens = len(prompt or "") // 4
    reasoning_tokens = len(reasoning_text or "") // 4
    output_tokens = (len(output_text or "") + len(reasoning_text or "")) // 4
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
    }


def response_id_from_chat_id(chat_id: str = None) -> str:
    chat_id = str(chat_id or f"chatcmpl_{uuid.uuid4().hex[:12]}")
    return chat_id if chat_id.startswith("resp_") else f"resp_{chat_id}"


def chat_usage_to_response_usage(usage) -> dict:
    if not isinstance(usage, dict):
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "output_tokens_details": {"reasoning_tokens": 0},
        }
    input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
    result = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    cached = None
    if isinstance(usage.get("prompt_tokens_details"), dict):
        cached = usage["prompt_tokens_details"].get("cached_tokens")
    if cached is None and isinstance(usage.get("input_tokens_details"), dict):
        cached = usage["input_tokens_details"].get("cached_tokens")
    if cached is not None:
        result["input_tokens_details"] = {"cached_tokens": cached}
    if isinstance(usage.get("completion_tokens_details"), dict):
        details = dict(usage["completion_tokens_details"])
        details.setdefault("reasoning_tokens", 0)
        result["output_tokens_details"] = details
    return result


def chat_message_reasoning_text(message: dict) -> str:
    if not isinstance(message, dict):
        return ""
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            for subkey in ("content", "text", "summary"):
                subval = value.get(subkey)
                if isinstance(subval, str) and subval:
                    return subval
    details = message.get("reasoning_details")
    if isinstance(details, list):
        parts = []
        for part in details:
            if isinstance(part, dict):
                for key in ("text", "content", "summary"):
                    if isinstance(part.get(key), str) and part.get(key):
                        parts.append(part[key])
                        break
            elif isinstance(part, str):
                parts.append(part)
        return "\n\n".join(parts)
    return ""


def chat_completion_to_response_payload(body: dict, original_request: dict = None, response_id: str = None, created: int = None) -> dict:
    choices = body.get("choices") if isinstance(body, dict) else None
    if not choices:
        raise ValueError("No choices in chat response")
    choice = choices[0] or {}
    message = choice.get("message") or {}
    finish_reason = choice.get("finish_reason")
    response_id = response_id or response_id_from_chat_id(body.get("id"))
    model = str(body.get("model") or "")
    created = int(created if created is not None else body.get("created") or time.time())
    tool_context = codex_tool_context(original_request.get("tools") if isinstance(original_request, dict) else None)

    output = []
    reasoning = chat_message_reasoning_text(message)
    content = message.get("content")
    if content is None:
        content = ""
    if isinstance(content, list):
        content = response_content_text(content)
    content = str(content or "")
    think = split_leading_think_block(content)
    if think and not reasoning:
        reasoning, content = think
    if reasoning:
        output.append(reasoning_output_item(reasoning))
    if content:
        output.append(message_output_item(content))

    for tc in message.get("tool_calls") or []:
        if isinstance(tc, dict):
            tc = dict(tc)
            tc["_tool_context"] = tool_context
        output.append(function_call_output_item(tc))
    if not output:
        output.append(message_output_item(""))

    status = "incomplete" if finish_reason == "length" else "completed"
    payload = openai_response_payload(
        model,
        output,
        response_id=response_id,
        created=created,
        status=status,
        usage=chat_usage_to_response_usage(body.get("usage")),
    )
    if status == "incomplete":
        payload["incomplete_details"] = {"reason": "max_output_tokens"}
    copy_response_request_fields(payload, original_request)
    return payload


def chat_completion_from_stream_chunks(chunks, default_model: str = "", created: int = None) -> dict:
    chat_id = ""
    model = str(default_model or "")
    created_ts = int(created or time.time())
    content_parts = []
    reasoning_parts = []
    tool_call_state = {}
    finish_reason = "stop"
    usage = None

    def append_tool_call(part, fallback_index=0):
        if not isinstance(part, dict):
            return
        try:
            index = int(part.get("index", fallback_index))
        except Exception:
            index = fallback_index
        state = tool_call_state.setdefault(index, {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        })
        if part.get("id"):
            state["id"] = str(part.get("id"))
        if part.get("type"):
            state["type"] = str(part.get("type"))
        fn = part.get("function") if isinstance(part.get("function"), dict) else {}
        state_fn = state.setdefault("function", {"name": "", "arguments": ""})
        if fn.get("name"):
            state_fn["name"] = str(fn.get("name"))
        if fn.get("arguments") is not None:
            state_fn["arguments"] = str(state_fn.get("arguments") or "") + str(fn.get("arguments") or "")

    def append_legacy_function_call(function_call):
        if not isinstance(function_call, dict):
            return
        append_tool_call({
            "index": 0,
            "id": function_call.get("id") or function_call.get("call_id") or "call_0",
            "type": "function",
            "function": {
                "name": function_call.get("name") or "",
                "arguments": function_call.get("arguments") or "",
            },
        })

    for chunk in chunks or []:
        if not isinstance(chunk, dict):
            continue
        if not chat_id and chunk.get("id"):
            chat_id = str(chunk.get("id"))
        if chunk.get("model"):
            model = str(chunk.get("model"))
        try:
            if chunk.get("created"):
                created_ts = int(chunk.get("created"))
        except Exception:
            pass
        if isinstance(chunk.get("usage"), dict):
            usage = chunk.get("usage")
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                finish_reason = str(choice.get("finish_reason"))
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if content is not None:
                    content_parts.append(response_content_text(content))
                reasoning = chat_message_reasoning_text(message)
                if reasoning:
                    reasoning_parts.append(reasoning)
                for index, tc in enumerate(message.get("tool_calls") or []):
                    append_tool_call(tc, index)
                append_legacy_function_call(message.get("function_call"))
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            if delta.get("content") is not None:
                content_parts.append(response_content_text(delta.get("content")))
            reasoning = chat_message_reasoning_text(delta)
            if reasoning:
                reasoning_parts.append(reasoning)
            for index, tc in enumerate(delta.get("tool_calls") or []):
                append_tool_call(tc, index)
            append_legacy_function_call(delta.get("function_call"))

    tool_calls = []
    for index in sorted(tool_call_state):
        call = tool_call_state[index]
        if not call.get("id"):
            call["id"] = f"call_{uuid.uuid4().hex[:8]}"
        call.setdefault("type", "function")
        call.setdefault("function", {"name": "", "arguments": ""})
        tool_calls.append(call)

    message = {"role": "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": chat_id or f"chatcmpl_{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": created_ts,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def chat_stream_chunks_to_response_payload(chunks, default_model: str = "", created: int = None, original_request: dict = None) -> dict:
    return chat_completion_to_response_payload(
        chat_completion_from_stream_chunks(chunks, default_model=default_model, created=created),
        original_request=original_request,
    )


def openai_response_payload(model_name: str, output: list, prompt: str = "", response_id: str = None,
                            created: int = None, status: str = "completed", usage: dict = None) -> dict:
    created = int(created or time.time())
    response_id = response_id or f"resp_{uuid.uuid4().hex[:16]}"
    output_text = response_output_text(output)
    return {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "status": status,
        "model": model_name,
        "output": output,
        "output_text": output_text,
        "usage": usage or response_usage_from_output(prompt, output),
    }


def copy_response_request_fields(response: dict, original_request: dict = None) -> None:
    if not isinstance(original_request, dict):
        return
    for key in (
        "instructions",
        "max_output_tokens",
        "parallel_tool_calls",
        "previous_response_id",
        "reasoning",
        "temperature",
        "tool_choice",
        "tools",
        "top_p",
        "metadata",
    ):
        if key in original_request:
            response[key] = original_request[key]


# ─── HTTP Handler ────────────────────────────────────────────────────────────

class GeminiTextResponsesStreamState:
    def __init__(
        self,
        *,
        model_name: str,
        prompt: str,
        response_id: str,
        created: int,
        original_request: dict = None,
    ):
        self.model_name = str(model_name or "")
        self.prompt = str(prompt or "")
        self.response_id = str(response_id or f"resp_{uuid.uuid4().hex[:16]}")
        self.created = int(created or time.time())
        self.original_request = original_request
        self.next_output_index = 0
        self.output_items = []

        self.mode = "undecided"
        self.pending = ""
        self.reasoning_pending = ""

        self.reasoning_index = None
        self.reasoning_item_id = f"rs_{uuid.uuid4().hex[:12]}"
        self.reasoning_parts = []
        self.reasoning_added = False
        self.reasoning_done = False

        self.text_index = None
        self.text_item_id = f"msg_{uuid.uuid4().hex[:12]}"
        self.text_parts = []
        self.text_added = False
        self.text_done = False

    def _next_index(self) -> int:
        value = self.next_output_index
        self.next_output_index += 1
        return value

    def push_text_delta(self, delta: str, event) -> None:
        delta = str(delta or "")
        if not delta:
            return
        if self.mode == "text":
            self._push_output_text_delta(delta, event)
            return
        if self.mode == "reasoning":
            self.reasoning_pending += delta
            self._drain_reasoning(event)
            return

        self.pending += delta
        stripped = self.pending.lstrip()
        if not stripped:
            return
        if stripped.startswith(THINK_OPEN_TAG):
            self.mode = "reasoning"
            self.reasoning_pending = stripped[len(THINK_OPEN_TAG):]
            self.pending = ""
            self._drain_reasoning(event)
            return
        if THINK_OPEN_TAG.startswith(stripped):
            return

        text = self.pending
        self.pending = ""
        self.mode = "text"
        self._push_output_text_delta(text, event)

    def push_error_text(self, text: str, event) -> None:
        prefix = "\n" if self.text_parts else ""
        self.mode = "text"
        self._push_output_text_delta(f"{prefix}{text}", event)

    def finish(self, event) -> dict:
        if self.mode == "undecided" and self.pending:
            self.mode = "text"
            self._push_output_text_delta(self.pending, event)
            self.pending = ""
        elif self.mode == "reasoning" and self.reasoning_pending:
            text = THINK_OPEN_TAG + "".join(self.reasoning_parts) + self.reasoning_pending
            self.reasoning_parts = []
            self.reasoning_pending = ""
            self.reasoning_added = False
            self.reasoning_done = False
            self.mode = "text"
            self._push_output_text_delta(text, event)

        self._finish_reasoning(event)
        self._finish_text(event, force_empty=not self.output_items)
        output = [item for _, item in sorted(self.output_items, key=lambda pair: pair[0])]
        payload = openai_response_payload(
            self.model_name,
            output,
            self.prompt,
            response_id=self.response_id,
            created=self.created,
        )
        copy_response_request_fields(payload, self.original_request)
        return payload

    def _drain_reasoning(self, event) -> None:
        close_index = self.reasoning_pending.find(THINK_CLOSE_TAG)
        if close_index < 0:
            return
        reasoning_text = self.reasoning_pending[:close_index].strip()
        remaining = self.reasoning_pending[close_index + len(THINK_CLOSE_TAG):].lstrip()
        self.reasoning_pending = ""
        if reasoning_text:
            self._push_reasoning_delta(reasoning_text, event)
        self._finish_reasoning(event)
        self.mode = "text"
        if remaining:
            self._push_output_text_delta(remaining, event)

    def _push_output_text_delta(self, delta: str, event) -> None:
        if not delta:
            return
        if not self.text_added:
            self._start_text(event)
        self.text_parts.append(str(delta))
        event({
            "type": "response.output_text.delta",
            "response_id": self.response_id,
            "item_id": self.text_item_id,
            "output_index": self.text_index or 0,
            "content_index": 0,
            "delta": str(delta),
        })

    def _start_text(self, event) -> None:
        if self.text_added:
            return
        self.text_index = self._next_index()
        self.text_added = True
        event({
            "type": "response.output_item.added",
            "response_id": self.response_id,
            "output_index": self.text_index,
            "item": {
                "id": self.text_item_id,
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            },
        })
        event({
            "type": "response.content_part.added",
            "response_id": self.response_id,
            "item_id": self.text_item_id,
            "output_index": self.text_index,
            "content_index": 0,
            "part": {"type": "output_text", "text": "", "annotations": []},
        })

    def _finish_text(self, event, *, force_empty: bool = False) -> None:
        if self.text_done:
            return
        if force_empty and not self.text_added:
            self._start_text(event)
        if not self.text_added:
            return
        full_text = "".join(self.text_parts)
        item = {
            "id": self.text_item_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": full_text, "annotations": []}],
        }
        event({
            "type": "response.output_text.done",
            "response_id": self.response_id,
            "item_id": self.text_item_id,
            "output_index": self.text_index or 0,
            "content_index": 0,
            "text": full_text,
        })
        event({
            "type": "response.content_part.done",
            "response_id": self.response_id,
            "item_id": self.text_item_id,
            "output_index": self.text_index or 0,
            "content_index": 0,
            "part": item["content"][0],
        })
        event({
            "type": "response.output_item.done",
            "response_id": self.response_id,
            "output_index": self.text_index or 0,
            "item": item,
        })
        self.output_items.append((self.text_index or 0, item))
        self.text_done = True

    def _push_reasoning_delta(self, delta: str, event) -> None:
        if not delta:
            return
        if not self.reasoning_added:
            self.reasoning_index = self._next_index()
            self.reasoning_added = True
            event({
                "type": "response.output_item.added",
                "response_id": self.response_id,
                "output_index": self.reasoning_index,
                "item": {
                    "id": self.reasoning_item_id,
                    "type": "reasoning",
                    "status": "in_progress",
                    "summary": [],
                },
            })
            event({
                "type": "response.reasoning_summary_part.added",
                "response_id": self.response_id,
                "item_id": self.reasoning_item_id,
                "output_index": self.reasoning_index,
                "summary_index": 0,
                "part": {"type": "summary_text", "text": ""},
            })
        self.reasoning_parts.append(str(delta))
        event({
            "type": "response.reasoning_summary_text.delta",
            "response_id": self.response_id,
            "item_id": self.reasoning_item_id,
            "output_index": self.reasoning_index or 0,
            "summary_index": 0,
            "delta": str(delta),
        })

    def _finish_reasoning(self, event) -> None:
        if not self.reasoning_added or self.reasoning_done:
            return
        text = "".join(self.reasoning_parts)
        item = {
            "type": "reasoning",
            "id": self.reasoning_item_id,
            "summary": [{"type": "summary_text", "text": text}],
        }
        event({
            "type": "response.reasoning_summary_text.done",
            "response_id": self.response_id,
            "item_id": self.reasoning_item_id,
            "output_index": self.reasoning_index or 0,
            "summary_index": 0,
            "text": text,
        })
        event({
            "type": "response.reasoning_summary_part.done",
            "response_id": self.response_id,
            "item_id": self.reasoning_item_id,
            "output_index": self.reasoning_index or 0,
            "summary_index": 0,
            "part": {"type": "summary_text", "text": text},
        })
        event({
            "type": "response.output_item.done",
            "response_id": self.response_id,
            "output_index": self.reasoning_index or 0,
            "item": item,
        })
        self.output_items.append((self.reasoning_index or 0, item))
        self.reasoning_done = True


class GeminiHandler(BaseHTTPRequestHandler):
    # 客户端 socket 读写超时：挡住 slowloris 式慢请求占住服务线程
    timeout = 75

    def log_message(self, fmt, *args):
        rid = getattr(self, "_request_id", "-")
        client = getattr(self, "client_address", ("", ""))
        log(f"rid={rid} access client={client[0]}:{client[1]} " + (fmt % args))

    def _debug(self, msg: str):
        rid = getattr(self, "_request_id", "-")
        log(f"rid={rid} {msg}")

    def _check_auth(self) -> bool:
        """CONFIG["api_key"] 非空时校验客户端凭据（X-API-Key 或 Bearer）。"""
        required = str(CONFIG.get("api_key") or "")
        if not required:
            return True
        import hmac
        candidates = []
        header_key = self.headers.get("X-API-Key") or self.headers.get("x-api-key")
        if header_key:
            candidates.append(str(header_key).strip())
        auth_header = str(self.headers.get("Authorization") or "")
        if auth_header.startswith("Bearer "):
            candidates.append(auth_header[7:].strip())
        for candidate in candidates:
            if candidate and hmac.compare_digest(candidate.encode("utf-8"), required.encode("utf-8")):
                return True
        self._debug("auth rejected: missing or mismatched api key")
        self.send_json({"error": {"message": "unauthorized: invalid or missing api key"}}, 401)
        return False

    def _handle_debug_echo(self, body: bytes):
        self.send_json({
            "ok": True,
            "service": "gemini-web2api",
            "version": __version__,
            "path": self.path,
            "client": list(getattr(self, "client_address", ("", ""))),
            "body_bytes": len(body or b""),
            "body_preview": preview_text(body),
            "headers": summarize_headers(self.headers),
        })

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self._debug(f"-> JSON status={status} bytes={len(body)} preview={preview_text(body, 500)}")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_chat_stream_chunk(self, cid: str, model_name: str, text: str, finish_reason=None):
        chunk = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "delta": {"content": str(text or "")},
                "finish_reason": finish_reason,
            }],
        }
        self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _write_chat_stream_done(self, cid: str, model_name: str):
        chunk = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _write_response_sse_payload(self, payload: dict):
        event_type = payload.get("type", "") if isinstance(payload, dict) else ""
        if event_type == "response.output_text.delta":
            self._debug(f"-> SSE event={event_type} delta_len={len(str(payload.get('delta', '')))}")
        else:
            self._debug(f"-> SSE event={event_type or '(none)'}")
        data_json = json.dumps(payload, ensure_ascii=False)
        try:
            json.loads(data_json)
        except Exception as json_err:
            log(f"ERROR: Server generated invalid JSON in _write_response_sse_payload: {json_err}")
            log(f"Invalid payload: {payload}")
        if event_type:
            sse_bytes = f"event: {event_type}\ndata: {data_json}\n\n".encode("utf-8")
        else:
            sse_bytes = f"data: {data_json}\n\n".encode("utf-8")
        self._debug(f"SSE payload send: event={event_type} bytes={len(sse_bytes)} data_chars={len(data_json)}")
        self.wfile.write(sse_bytes)
        self.wfile.flush()

    def _write_response_sse_heartbeat(self):
        self._debug("-> SSE heartbeat")
        self.wfile.write(b": keep-alive\n\n")
        self.wfile.flush()

    def _write_response_sse_done(self):
        self._debug("-> SSE [DONE]")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _client_disconnected(self) -> bool:
        connection = getattr(self, "connection", None)
        if connection is None:
            return False
        try:
            readable, _, _ = select.select([connection], [], [], 0)
        except Exception:
            return False
        if not readable:
            return False
        old_timeout = connection.gettimeout()
        try:
            connection.setblocking(False)
            peek_data = connection.recv(1, socket.MSG_PEEK)
            return peek_data == b""
        except BlockingIOError:
            return False
        except (ConnectionError, OSError):
            return True
        finally:
            try:
                connection.settimeout(old_timeout)
            except Exception:
                pass

    def _run_with_response_heartbeats(self, func, *, label: str = "upstream"):
        result = {}
        done = threading.Event()
        cancel_event = threading.Event()

        def runner():
            try:
                result["value"] = func(cancel_event)
            except BaseException as exc:
                result["error"] = exc
                result["traceback"] = traceback.format_exc()
            finally:
                done.set()

        thread = threading.Thread(target=runner, name=f"Gemini{label.title()}Worker", daemon=True)
        thread.start()
        heartbeat_sec = float(CONFIG.get("response_stream_heartbeat_sec", 10) or 10)
        heartbeat_sec = max(0.05, heartbeat_sec)
        next_heartbeat_at = time.monotonic() + heartbeat_sec
        while not done.is_set():
            wait_seconds = min(0.2, max(0.0, next_heartbeat_at - time.monotonic()))
            if done.wait(wait_seconds):
                break
            if self._client_disconnected():
                cancel_event.set()
                self._debug(f"SSE client disconnected while waiting for {label}; cancelling upstream retries")
                raise ConnectionAbortedError("SSE client disconnected")
            now = time.monotonic()
            if now >= next_heartbeat_at:
                try:
                    self._write_response_sse_heartbeat()
                except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                    cancel_event.set()
                    self._debug(f"SSE heartbeat failed; client disconnected: {exception_summary(exc)}")
                    raise ConnectionAbortedError("SSE client disconnected") from exc
                next_heartbeat_at = now + heartbeat_sec
        if result.get("traceback"):
            self._debug(preview_text(result.get("traceback"), 1600))
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def _iter_gemini_stream_with_heartbeats(
        self,
        prompt: str,
        model_id: int,
        think_mode: int,
        model_hex_id: "str | None" = None,
        *,
        label: str,
    ):
        result_queue = queue.Queue()
        cancel_event = threading.Event()

        def runner():
            try:
                stream_kwargs = {"cancel_event": cancel_event}
                if model_hex_id:
                    stream_kwargs["model_hex_id"] = model_hex_id
                for delta_text in gemini_stream_generate_iter(
                    prompt,
                    model_id,
                    think_mode,
                    **stream_kwargs,
                ):
                    if cancel_event.is_set():
                        break
                    result_queue.put(("delta", str(delta_text or "")))
            except BaseException as exc:
                result_queue.put(("error", (exc, traceback.format_exc())))
            finally:
                result_queue.put(("done", None))

        thread = threading.Thread(target=runner, name=f"Gemini{label.title()}Worker", daemon=True)
        thread.start()
        heartbeat_sec = float(CONFIG.get("response_stream_heartbeat_sec", 10) or 10)
        heartbeat_sec = max(0.05, heartbeat_sec)
        next_heartbeat_at = time.monotonic() + heartbeat_sec
        try:
            while True:
                wait_seconds = min(0.2, max(0.0, next_heartbeat_at - time.monotonic()))
                try:
                    item_type, value = result_queue.get(timeout=wait_seconds)
                except queue.Empty:
                    if self._client_disconnected():
                        self._debug(f"SSE client disconnected while streaming {label}; cancelling upstream")
                        raise ConnectionAbortedError("SSE client disconnected")
                    now = time.monotonic()
                    if now >= next_heartbeat_at:
                        try:
                            self._write_response_sse_heartbeat()
                        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                            self._debug(f"SSE heartbeat failed; client disconnected: {exception_summary(exc)}")
                            raise ConnectionAbortedError("SSE client disconnected") from exc
                        next_heartbeat_at = now + heartbeat_sec
                    continue

                if item_type == "delta":
                    now = time.monotonic()
                    if now >= next_heartbeat_at:
                        try:
                            self._write_response_sse_heartbeat()
                        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                            self._debug(f"SSE heartbeat failed; client disconnected: {exception_summary(exc)}")
                            raise ConnectionAbortedError("SSE client disconnected") from exc
                        next_heartbeat_at = now + heartbeat_sec
                    if value:
                        yield value
                    continue
                if item_type == "error":
                    error, error_traceback = value
                    self._debug(preview_text(error_traceback, 1600))
                    raise error
                if item_type == "done":
                    break
        finally:
            cancel_event.set()

    def do_OPTIONS(self):
        self._request_id = uuid.uuid4().hex[:8]
        client = getattr(self, "client_address", ("", ""))
        self._debug(
            f"<- OPTIONS path={self.path} client={client[0]}:{client[1]} "
            f"headers={json.dumps(summarize_headers(self.headers), ensure_ascii=False)}"
        )
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        self._request_id = uuid.uuid4().hex[:8]
        client = getattr(self, "client_address", ("", ""))
        self._debug(
            f"<- GET path={self.path} client={client[0]}:{client[1]} "
            f"headers={json.dumps(summarize_headers(self.headers), ensure_ascii=False)}"
        )
        try:
            if self.path == "/":
                # 健康检查保持免鉴权，供本地就绪探测使用
                self._debug("route=/")
                self.send_json({"status": "ok", "version": __version__,
                                "models": list(MODELS.keys())})
                return
            if not self._check_auth():
                return
            if self.path == "/v1/models" or self.path.startswith("/v1/models?"):
                self._debug("route=/v1/models")
                data = [
                    {"id": n, "object": "model", "created": 1700000000,
                     "owned_by": "google", "description": c["desc"]}
                    for n, c in MODELS.items()
                ]
                if "include_aliases=true" in self.path.lower():
                    for alias, target in (CONFIG.get("model_aliases") or {}).items():
                        data.append({
                            "id": alias,
                            "object": "model",
                            "created": 1700000000,
                            "owned_by": "google",
                            "description": f"Alias for {target}",
                        })
                self.send_json({"object": "list", "data": data})
            elif self.path.startswith("/v1beta/models"):
                self._debug("route=/v1beta/models")
                self._handle_google_models_list()
            else:
                self._debug("route=not_found")
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            self._debug(f"GET error {exception_summary(e)}")
            self._debug(preview_text(traceback.format_exc(), 1200))

    def do_POST(self):
        self._request_id = uuid.uuid4().hex[:8]
        client = getattr(self, "client_address", ("", ""))
        try:
            if not self._check_auth():
                return
            if "chunked" in str(self.headers.get("Transfer-Encoding") or "").lower():
                # 不解析 chunked 编码：残留字节会污染连接上后续请求的解析
                self._debug("rejected: chunked transfer-encoding not supported")
                self.send_json({"error": {"message": "chunked transfer-encoding not supported; send Content-Length"}}, 411)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                length = 0
            max_body = int(CONFIG.get("max_request_body_bytes") or 0)
            if max_body and length > max_body:
                self._debug(f"rejected: body too large length={length} max={max_body}")
                self.send_json({"error": {"message": f"request body too large (max {max_body} bytes)"}}, 413)
                return
            body = self.rfile.read(length) if length > 0 else b""
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            self._debug(
                f"<- POST path={self.path} normalized={path} client={client[0]}:{client[1]} "
                f"bytes={len(body)} headers={json.dumps(summarize_headers(self.headers), ensure_ascii=False)}"
            )
            self._debug(f"body_preview={preview_text(body)}")
            if path == "/v1/chat/completions":
                self._debug("route=/v1/chat/completions")
                self.handle_chat(body)
            elif path == "/__debug/echo":
                self._debug("route=/__debug/echo")
                self._handle_debug_echo(body)
            elif path == "/v1/responses" or path == "/responses" or path.endswith("/v1/responses") or path.endswith("/responses"):
                self._debug("route=/v1/responses")
                self.handle_responses(body)
            elif ":generateContent" in self.path:
                self._debug("route=google.generateContent")
                self._handle_google_generate(body, stream=False)
            elif ":streamGenerateContent" in self.path:
                self._debug("route=google.streamGenerateContent")
                self._handle_google_generate(body, stream=True)
            else:
                self._debug("route=not_found")
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            self._debug(f"POST error {exception_summary(e)}")
            self._debug(preview_text(traceback.format_exc(), 1600))
            try:
                self.send_json({"error": {"message": str(e)}}, 500)
            except:
                pass

    def _resolve_model(self, model_name):
        requested_model = str(model_name or CONFIG["default_model"])
        model_name = requested_model
        think_override = None
        if "@think=" in model_name:
            model_name, think_str = model_name.rsplit("@think=", 1)
            think_override = int(think_str)
        cfg = MODELS.get(model_name)
        if not cfg:
            aliases = CONFIG.get("model_aliases") or {}
            alias_model = aliases.get(model_name)
            if not alias_model and (model_name.startswith(("gpt-", "o", "codex-"))):
                alias_model = CONFIG.get("default_model", "gemini-3.5-flash")
            if alias_model:
                alias_base = str(alias_model)
                if "@think=" in alias_base:
                    alias_base, alias_think = alias_base.rsplit("@think=", 1)
                    if think_override is None:
                        think_override = int(alias_think)
                cfg = MODELS.get(alias_base)
                if cfg:
                    self._debug(f"model_alias requested={requested_model} resolved={alias_base}")
                    return (
                        requested_model,
                        cfg["mode"],
                        think_override if think_override is not None else cfg["think"],
                        cfg.get("hex_id"),
                        None,
                    )
            return None, None, None, None, f"Unknown model: {model_name}"
        return (
            model_name,
            cfg["mode"],
            think_override if think_override is not None else cfg["think"],
            cfg.get("hex_id"),
            None,
        )

    def _upstream_timeout_from_headers(self) -> "float | None":
        try:
            raw = str(self.headers.get("X-Upstream-Timeout-Sec", "") or "").strip()
            if not raw:
                return None
            return max(5.0, float(raw))
        except Exception:
            return None

    def _call_gemini(
        self,
        prompt,
        model_id,
        think_mode,
        tools,
        options: dict = None,
        timeout_sec: "float | None" = None,
        cancel_event: "threading.Event | None" = None,
        model_hex_id: "str | None" = None,
    ):
        self._debug(
            f"upstream Gemini call start prompt_chars={len(prompt or '')} "
            f"model_id={model_id} think_mode={think_mode} model_hex_id={model_hex_id or '-'} "
            f"tools={len(tools or [])}"
        )
        generate_kwargs = {
            "timeout_sec": timeout_sec,
            "cancel_event": cancel_event,
        }
        if model_hex_id:
            generate_kwargs["model_hex_id"] = model_hex_id
        raw = gemini_stream_generate(prompt, model_id, think_mode, **generate_kwargs)
        self._debug(f"upstream Gemini raw_chars={len(raw or '')}")
        text = extract_response_text(raw)
        tool_calls = None
        if tools and text:
            text, tool_calls = parse_tool_calls(text, tools)
            if not tool_calls and text_looks_like_tool_intent(text, tools, options):
                self._debug("upstream Gemini returned tool-intent prose without tool_call; retrying with correction")
                retry_raw = gemini_stream_generate(
                    tool_retry_prompt(prompt, text, tools),
                    model_id,
                    think_mode,
                    **generate_kwargs,
                )
                retry_text = extract_response_text(retry_raw)
                retry_clean, retry_tool_calls = parse_tool_calls(retry_text, tools)
                self._debug(
                    f"upstream Gemini retry parsed text_chars={len(retry_clean or '')} "
                    f"tool_calls={len(retry_tool_calls or [])} text_preview={preview_text(retry_clean, 600)}"
                )
                if retry_tool_calls:
                    text, tool_calls = retry_clean, retry_tool_calls
        self._debug(
            f"upstream Gemini parsed text_chars={len(text or '')} tool_calls={len(tool_calls or [])} "
            f"text_preview={preview_text(text, 600)}"
        )
        if tool_calls:
            for i, tc in enumerate(tool_calls):
                self._debug(
                    f"-> Parsed tool_call[{i}]: name={tc.get('name')} "
                    f"args_preview={preview_text(tc.get('arguments'), 200)}"
                )
        if not text and not tool_calls:
            raw_preview = preview_text(raw, 1200)
            self._debug(f"upstream Gemini empty parsed response raw_preview={raw_preview}")
            raise RuntimeError("empty parsed response from Gemini Web")
        return text or "", tool_calls

    def _handle_official_api_chat(self, req, model_name, api_key):
        """将 OpenAI 格式的 Chat 请求无损翻译代理给官方 Gemini 接口，支持完整的多模态及流式传输。"""
        contents = []
        system_instruction = None

        for msg in req.get("messages", []):
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # system 映射为官方 systemInstruction 载荷
            if role == "system":
                if isinstance(content, str):
                    system_instruction = {"parts": [{"text": content}]}
                elif isinstance(content, list):
                    parts = []
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append({"text": c.get("text", "")})
                    system_instruction = {"parts": parts}
                continue

            official_role = "model" if role == "assistant" else "user"
            parts = []

            if isinstance(content, str):
                parts.append({"text": content})
            elif isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    c_type = c.get("type", "")
                    if c_type in ("text", "input_text"):
                        parts.append({"text": c.get("text", "")})
                    elif c_type in ("image_url", "input_image") or "image_url" in c:
                        img_url = part_url(c, "image_url")
                        if img_url.startswith("data:"):
                            try:
                                header, b64_data = img_url.split(",", 1)
                                mime_type = header.split(";")[0].split(":")[1]
                                parts.append({
                                    "inlineData": {
                                        "mimeType": mime_type,
                                        "data": b64_data
                                    }
                                })
                            except Exception as e:
                                log(f"Error parsing base64 image data: {e}")
                        elif img_url:
                            # 自动抓取网络图片链接并转化为 inlineData 发给官方 Gemini。
                            # 仅放行解析到公网的 http/https 链接（SSRF 防护），并限制体积。
                            try:
                                if not is_public_http_url(img_url):
                                    raise ValueError("blocked non-public or non-http image url")
                                log(f"Fetching network image for official API: {img_url}")
                                import urllib.request
                                import ssl
                                ctx = ssl.create_default_context()
                                req_img = urllib.request.Request(
                                    img_url,
                                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                                )
                                max_image_bytes = 20 * 1024 * 1024
                                with urllib.request.urlopen(req_img, context=ctx, timeout=12) as img_resp:
                                    img_data = img_resp.read(max_image_bytes + 1)
                                if len(img_data) > max_image_bytes:
                                    raise ValueError(f"image larger than {max_image_bytes} bytes")
                                mime_type = "image/jpeg"
                                if ".png" in img_url.lower():
                                    mime_type = "image/png"
                                elif ".webp" in img_url.lower():
                                    mime_type = "image/webp"
                                elif ".gif" in img_url.lower():
                                    mime_type = "image/gif"

                                b64_data = base64.b64encode(img_data).decode("utf-8")
                                parts.append({
                                    "inlineData": {
                                        "mimeType": mime_type,
                                        "data": b64_data
                                    }
                                })
                            except Exception as e:
                                log(f"Error fetching network image: {e}")
                                parts.append({"text": f"[Image Attachment URL: {img_url}]"})
                    elif c_type in ("file_url", "file", "input_file") or "file_url" in c or "file_data" in c:
                        file_url = part_url(c, "file_url", "file_data")
                        if file_url.startswith("data:"):
                            try:
                                header, b64_data = file_url.split(",", 1)
                                mime_type = header.split(";")[0].split(":")[1]
                                parts.append({
                                    "inlineData": {
                                        "mimeType": mime_type,
                                        "data": b64_data
                                    }
                                })
                            except Exception as e:
                                log(f"Error parsing file inlineData: {e}")
                        elif file_url:
                            parts.append({"text": f"[File Attachment URL: {file_url}]"})

            if parts:
                contents.append({"role": official_role, "parts": parts})

        # 模型名字映射，去除以 @think 开头的参数，并为官方接口格式化
        raw_model = model_name
        if "@think=" in raw_model:
            raw_model, _ = raw_model.rsplit("@think=", 1)
        google_model = raw_model
        if not google_model.startswith("models/"):
            google_model = f"models/{google_model}"

        stream = req.get("stream", False)
        method = "streamGenerateContent" if stream else "generateContent"

        # 使用 alt=sse 获得最标准的 data-stream SSE 协议流。
        # key 经 x-goog-api-key 请求头传递而非 URL query：urllib/httpx 的异常
        # 消息会原样包含完整 URL，key 放 query 会随报错泄漏到日志和客户端响应。
        url = f"https://generativelanguage.googleapis.com/v1beta/{google_model}:{method}"
        if stream:
            url += "?alt=sse"

        payload = {
            "contents": contents
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction

        generation_config = {}
        if "temperature" in req:
            generation_config["temperature"] = req["temperature"]
        if "max_tokens" in req:
            generation_config["maxOutputTokens"] = req["max_tokens"]
        if "top_p" in req:
            generation_config["topP"] = req["top_p"]
        if generation_config:
            payload["generationConfig"] = generation_config

        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        proxy = CONFIG.get("proxy")

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            sent_text = False
            try:
                if HAS_HTTPX:
                    transport = httpx.HTTPTransport(proxy=proxy) if proxy else None
                    with httpx.Client(transport=transport, timeout=upstream_httpx_timeout()) as client:
                        with client.stream("POST", url, content=body, headers=headers) as resp:
                            resp.raise_for_status()
                            for line in resp.iter_lines():
                                if not line:
                                    continue
                                if line.startswith("data:"):
                                    line_content = line[5:].strip()
                                    if not line_content:
                                        continue
                                    try:
                                        data_obj = json.loads(line_content)
                                        error_obj = data_obj.get("error") if isinstance(data_obj, dict) else None
                                        if error_obj:
                                            if isinstance(error_obj, dict):
                                                raise RuntimeError(str(error_obj.get("message") or error_obj))
                                            raise RuntimeError(str(error_obj))
                                        candidates = data_obj.get("candidates", [])
                                        if candidates:
                                            parts_list = candidates[0].get("content", {}).get("parts", [])
                                            if parts_list:
                                                delta_text = parts_list[0].get("text", "")
                                                if delta_text:
                                                    self._write_chat_stream_chunk(cid, model_name, delta_text)
                                                    sent_text = True
                                    except RuntimeError:
                                        raise
                                    except Exception as e:
                                        log(f"Error parsing official stream sse: {e}")
                else:
                    # 如果没有 httpx，走非流式的官方请求
                    log("Fallback to non-streaming since httpx is not installed for official SSE")
                    raise RuntimeError("httpx is required for official stream proxying")

                self._write_chat_stream_done(cid, model_name)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as e:
                log(f"Stream official API proxy error: {redact_api_keys(exception_summary(e))}")
                try:
                    if not sent_text:
                        self._write_chat_stream_chunk(cid, model_name, friendly_upstream_error_message(e))
                    self._write_chat_stream_done(cid, model_name)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
        else:
            # 同步模式
            try:
                import urllib.request
                import ssl
                req_obj = urllib.request.Request(url, data=body, headers=headers, method="POST")
                ctx = ssl.create_default_context()
                if proxy:
                    opener = urllib.request.build_opener(
                        urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                        urllib.request.HTTPSHandler(context=ctx)
                    )
                    resp = opener.open(req_obj, timeout=CONFIG["request_timeout_sec"])
                else:
                    resp = urllib.request.urlopen(req_obj, context=ctx, timeout=CONFIG["request_timeout_sec"])

                resp_data = json.loads(resp.read().decode("utf-8"))
                candidates = resp_data.get("candidates", [])
                text = ""
                if candidates:
                    parts_list = candidates[0].get("content", {}).get("parts", [])
                    if parts_list:
                        text = parts_list[0].get("text", "")

                self.send_json({
                    "id": cid, "object": "chat.completion", "created": int(time.time()),
                    "model": model_name,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": len(body)//4, "completion_tokens": len(text)//4,
                              "total_tokens": (len(body)+len(text))//4},
                })
            except Exception as e:
                self.send_json({"error": {"message": f"upstream official error: {redact_api_keys(str(e))}"}}, 502)

    def handle_chat(self, body: bytes):
        req = json.loads(body)
        self._debug(
            f"chat payload keys={sorted(req.keys())} model={req.get('model', CONFIG['default_model'])} "
            f"stream={bool_value(req.get('stream', False))} messages={len(req.get('messages', []) or [])} "
            f"tools={len(req.get('tools') or [])}"
        )

        # 智能劫持检测官方 API Key（支持 Request Header 或配置文件）
        auth_header = self.headers.get("Authorization", "")
        api_key = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if token.startswith("AIzaSy") or token.lower() in ("official", "google"):
                api_key = token
        if not api_key and CONFIG.get("gemini_api_key"):
            api_key = CONFIG["gemini_api_key"]

        model_name = req.get("model", CONFIG["default_model"])
        if api_key:
            self._debug(f"chat official_api_proxy enabled model={model_name}")
            # 开启官方 API 多模态双轨代理转发
            self._handle_official_api_chat(req, model_name, api_key)
            return

        model_name, model_id, think_mode, model_hex_id, err = self._resolve_model(model_name)
        if err:
            self._debug(f"chat model_error={err}")
            self.send_json({"error": {"message": err}}, 400)
            return

        tools = req.get("tools")
        model_kwargs = {"model_hex_id": model_hex_id} if model_hex_id else {}
        prompt = messages_to_prompt(req.get("messages", []), tools)
        self._debug(f"chat normalized model={model_name} prompt_chars={len(prompt or '')}")
        if not prompt.strip():
            self.send_json({"error": {"message": "empty prompt"}}, 400)
            return

        stream = req.get("stream", False)
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        if stream and not tools:
            sent_text = False
            upstream_chunks = 0
            outbound_chunks = 0
            output_chars = 0
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                for delta_text in self._iter_gemini_stream_with_heartbeats(
                    prompt,
                    model_id,
                    think_mode,
                    model_hex_id,
                    label="chat-text",
                ):
                    upstream_chunks += 1
                    output_chars += len(delta_text)
                    pieces = list(chat_stream_chunks(delta_text))
                    for piece_index, piece in enumerate(pieces):
                        self._write_chat_stream_chunk(cid, model_name, piece)
                        sent_text = True
                        outbound_chunks += 1
                        if piece_index < len(pieces) - 1:
                            delay_ms = int(CONFIG.get("chat_stream_chunk_delay_ms", 15) or 0)
                            if delay_ms > 0:
                                time.sleep(delay_ms / 1000.0)
                self._write_chat_stream_done(cid, model_name)
                self._debug(
                    f"chat stream completed upstream_chunks={upstream_chunks} "
                    f"outbound_chunks={outbound_chunks} output_chars={output_chars}"
                )
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as e:
                self._debug(f"chat stream upstream_error {exception_summary(e)}")
                self._debug(preview_text(traceback.format_exc(), 1600))
                try:
                    if not sent_text:
                        self._write_chat_stream_chunk(cid, model_name, friendly_upstream_error_message(e))
                    self._write_chat_stream_done(cid, model_name)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
            return

        if stream:
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                text, tool_calls = self._run_with_response_heartbeats(
                    lambda cancel: self._call_gemini(
                        prompt,
                        model_id,
                        think_mode,
                        tools,
                        {"tool_choice": req.get("tool_choice"), "parallel_tool_calls": req.get("parallel_tool_calls")},
                        timeout_sec=self._upstream_timeout_from_headers(),
                        cancel_event=cancel,
                        **model_kwargs,
                    ),
                    label="chat-tools",
                )
                msg = {"role": "assistant", "content": text or ""}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                finish = "tool_calls" if tool_calls else "stop"
                chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                         "model": model_name, "choices": [{"index": 0, "delta": msg, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as e:
                self._debug(f"chat stream tool upstream_error {exception_summary(e)}")
                self._debug(preview_text(traceback.format_exc(), 1600))
                try:
                    self._write_chat_stream_chunk(cid, model_name, friendly_upstream_error_message(e))
                    self._write_chat_stream_done(cid, model_name)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
            return

        # Non-streaming (or tool calling which needs full response)
        try:
            text, tool_calls = self._call_gemini(
                prompt,
                model_id,
                think_mode,
                tools,
                {"tool_choice": req.get("tool_choice"), "parallel_tool_calls": req.get("parallel_tool_calls")},
                timeout_sec=self._upstream_timeout_from_headers(),
                **model_kwargs,
            )
        except Exception as e:
            self._debug(f"chat upstream_error {exception_summary(e)}")
            self._debug(preview_text(traceback.format_exc(), 1600))
            self.send_json({"error": {"message": f"upstream error: {e}"}}, 502)
            return

        msg = {"role": "assistant", "content": text or ""}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        finish = "tool_calls" if tool_calls else "stop"

        self.send_json({
            "id": cid, "object": "chat.completion", "created": int(time.time()),
            "model": model_name,
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": len(prompt)//4, "completion_tokens": len(text)//4,
                      "total_tokens": (len(prompt)+len(text))//4},
        })

    def handle_responses(self, body: bytes):
        """OpenAI Responses API for Codex CLI compatibility."""
        req = json.loads(body or b"{}")
        self._debug(
            f"responses payload keys={sorted(req.keys())} model={req.get('model', CONFIG['default_model'])} "
            f"stream={bool_value(req.get('stream', False))} input_type={type(req.get('input')).__name__} "
            f"tools={len(req.get('tools') or [])}"
        )
        model_name, model_id, think_mode, model_hex_id, err = self._resolve_model(
            req.get("model", CONFIG["default_model"]))
        if err:
            self._debug(f"responses model_error={err}")
            self.send_json({"error": {"message": err}}, 400)
            return

        response_options = responses_request_options(req)
        model_kwargs = {"model_hex_id": model_hex_id} if model_hex_id else {}
        declared_tools = normalize_responses_tools(req.get("tools"))
        tools = tools_for_responses_options(declared_tools, response_options)
        tool_context = codex_tool_context(req.get("tools"))
        messages = responses_to_messages(req)
        self._debug(
            f"responses normalized model={model_name} messages={len(messages)} "
            f"function_tools={len(tools)} declared_tools={len(declared_tools)} "
            f"tool_choice={response_options.get('tool_choice')} "
            f"max_output_tokens={response_options.get('max_output_tokens')}"
        )

        prompt = messages_to_prompt(messages, tools, response_options)
        self._debug(f"responses prompt_chars={len(prompt or '')} prompt_preview={preview_text(prompt, 600)}")
        if not prompt.strip():
            self.send_json({"error": {"message": "empty input"}}, 400)
            return

        if bool_value(req.get("stream", False)):
            self._debug("responses stream=true")
            self._handle_responses_stream(
                prompt,
                model_name,
                model_id,
                think_mode,
                tools,
                response_options,
                tool_context=tool_context,
                original_request=req,
                model_hex_id=model_hex_id,
            )
            return

        try:
            text, tool_calls = self._call_gemini(
                prompt,
                model_id,
                think_mode,
                tools,
                response_options,
                **model_kwargs,
            )
            tool_calls = apply_responses_tool_policy(tool_calls, response_options)
        except Exception as e:
            self._debug(f"responses upstream_error {exception_summary(e)}")
            self._debug(preview_text(traceback.format_exc(), 1600))
            text, tool_calls = friendly_upstream_error_message(e), None

        output = responses_output_items(text, tool_calls, tool_context)
        self._debug(
            f"responses completed output_items={len(output)} output_text_chars={len(response_output_text(output))}"
        )
        payload = openai_response_payload(model_name, output, prompt)
        copy_response_request_fields(payload, req)
        self.send_json(payload)

    def _handle_responses_stream(
        self,
        prompt: str,
        model_name: str,
        model_id: int,
        think_mode: int,
        tools,
        response_options=None,
        tool_context=None,
        original_request: dict = None,
        model_hex_id: "str | None" = None,
    ):
        created = int(time.time())
        rid = f"resp_{uuid.uuid4().hex[:16]}"
        sequence_number = 0
        model_kwargs = {"model_hex_id": model_hex_id} if model_hex_id else {}

        def event(payload: dict):
            nonlocal sequence_number
            payload.setdefault("sequence_number", sequence_number)
            sequence_number += 1
            self._write_response_sse_payload(payload)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self._debug("responses stream headers sent status=200")

        try:
            empty_response = openai_response_payload(
                model_name, [], prompt, response_id=rid, created=created, status="in_progress")
            event({"type": "response.created", "response": empty_response})
            event({"type": "response.in_progress", "response": empty_response})

            if tools:
                try:
                    text, tool_calls = self._run_with_response_heartbeats(
                        lambda cancel: self._call_gemini(
                            prompt,
                            model_id,
                            think_mode,
                            tools,
                            response_options,
                            cancel_event=cancel,
                            **model_kwargs,
                        ),
                        label="responses-tools",
                    )
                    tool_calls = apply_responses_tool_policy(tool_calls, response_options or {})
                except Exception as e:
                    self._debug(f"responses stream upstream_error {exception_summary(e)}")
                    self._debug(preview_text(traceback.format_exc(), 1600))
                    text, tool_calls = friendly_upstream_error_message(e), None

                output = responses_output_items(text, tool_calls, tool_context)
                self._debug(
                    f"responses stream completed output_items={len(output)} "
                    f"output_text_chars={len(response_output_text(output))}"
                )
                self._write_responses_output_events(event, rid, output)
                completed = openai_response_payload(model_name, output, prompt, response_id=rid, created=created)
            else:
                state = GeminiTextResponsesStreamState(
                    model_name=model_name,
                    prompt=prompt,
                    response_id=rid,
                    created=created,
                    original_request=original_request,
                )
                try:
                    for delta_text in self._iter_gemini_stream_with_heartbeats(
                        prompt,
                        model_id,
                        think_mode,
                        model_hex_id,
                        label="responses-text",
                    ):
                        for piece in chat_stream_chunks(delta_text):
                            state.push_text_delta(piece, event)
                except Exception as e:
                    self._debug(f"responses stream upstream_error {exception_summary(e)}")
                    self._debug(preview_text(traceback.format_exc(), 1600))
                    state.push_error_text(friendly_upstream_error_message(e), event)
                completed = state.finish(event)
                output = completed.get("output") or []
                self._debug(
                    f"responses stream completed output_items={len(output)} "
                    f"output_text_chars={len(response_output_text(output))}"
                )
            copy_response_request_fields(completed, original_request)
            event({"type": "response.completed", "response": completed})
            self._write_response_sse_done()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            self._debug(f"responses stream error {exception_summary(e)}")
            self._debug(preview_text(traceback.format_exc(), 1600))
            try:
                event({"type": "error", "error": {"message": str(e), "type": "server_error"}})
                self._write_response_sse_done()
            except Exception:
                pass

    def _write_responses_output_events(self, event, response_id: str, output: list):
        for output_index, item in enumerate(output):
            if item.get("type") == "reasoning":
                summary = response_reasoning_text([item])
                added_item = dict(item)
                added_item["summary"] = []
                added_item["status"] = "in_progress"
                event({
                    "type": "response.output_item.added",
                    "response_id": response_id,
                    "output_index": output_index,
                    "item": added_item,
                })
                part = {"type": "summary_text", "text": ""}
                event({
                    "type": "response.reasoning_summary_part.added",
                    "response_id": response_id,
                    "item_id": item["id"],
                    "output_index": output_index,
                    "summary_index": 0,
                    "part": part,
                })
                for delta in text_chunks(summary):
                    if delta:
                        event({
                            "type": "response.reasoning_summary_text.delta",
                            "response_id": response_id,
                            "item_id": item["id"],
                            "output_index": output_index,
                            "summary_index": 0,
                            "delta": delta,
                        })
                event({
                    "type": "response.reasoning_summary_text.done",
                    "response_id": response_id,
                    "item_id": item["id"],
                    "output_index": output_index,
                    "summary_index": 0,
                    "text": summary,
                })
                done_part = {"type": "summary_text", "text": summary}
                event({
                    "type": "response.reasoning_summary_part.done",
                    "response_id": response_id,
                    "item_id": item["id"],
                    "output_index": output_index,
                    "summary_index": 0,
                    "part": done_part,
                })
                event({
                    "type": "response.output_item.done",
                    "response_id": response_id,
                    "output_index": output_index,
                    "item": item,
                })
                continue

            if item.get("type") == "function_call":
                added_item = dict(item)
                added_item["status"] = "in_progress"
                added_item["arguments"] = ""
                event({
                    "type": "response.output_item.added",
                    "response_id": response_id,
                    "output_index": output_index,
                    "item": added_item,
                })
                arguments = item.get("arguments", "")
                if arguments:
                    event({
                        "type": "response.function_call_arguments.delta",
                        "response_id": response_id,
                        "item_id": item["id"],
                        "output_index": output_index,
                        "delta": arguments,
                    })
                event({
                    "type": "response.function_call_arguments.done",
                    "response_id": response_id,
                    "item_id": item["id"],
                    "output_index": output_index,
                    "call_id": item["call_id"],
                    "name": item["name"],
                    "arguments": arguments,
                    **({"namespace": item["namespace"]} if item.get("namespace") else {}),
                })
                event({
                    "type": "response.output_item.done",
                    "response_id": response_id,
                    "output_index": output_index,
                    "item": item,
                })
                continue

            if item.get("type") == "custom_tool_call":
                added_item = dict(item)
                added_item["status"] = "in_progress"
                added_item["input"] = ""
                event({
                    "type": "response.output_item.added",
                    "response_id": response_id,
                    "output_index": output_index,
                    "item": added_item,
                })
                input_text = str(item.get("input") or "")
                if input_text:
                    event({
                        "type": "response.custom_tool_call_input.delta",
                        "response_id": response_id,
                        "item_id": item["id"],
                        "call_id": item["call_id"],
                        "output_index": output_index,
                        "delta": input_text,
                    })
                event({
                    "type": "response.output_item.done",
                    "response_id": response_id,
                    "output_index": output_index,
                    "item": item,
                })
                continue

            if item.get("type") != "message":
                continue
            in_progress = dict(item)
            in_progress["status"] = "in_progress"
            in_progress["content"] = []
            event({
                "type": "response.output_item.added",
                "response_id": response_id,
                "output_index": output_index,
                "item": in_progress,
            })
            for content_index, part in enumerate(item.get("content", [])):
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                empty_part = {"type": "output_text", "text": "", "annotations": []}
                event({
                    "type": "response.content_part.added",
                    "response_id": response_id,
                    "item_id": item["id"],
                    "output_index": output_index,
                    "content_index": content_index,
                    "part": empty_part,
                })
                full_text = part.get("text", "")
                for delta in text_chunks(full_text):
                    if delta:
                        event({
                            "type": "response.output_text.delta",
                            "response_id": response_id,
                            "item_id": item["id"],
                            "output_index": output_index,
                            "content_index": content_index,
                            "delta": delta,
                        })
                event({
                    "type": "response.output_text.done",
                    "response_id": response_id,
                    "item_id": item["id"],
                    "output_index": output_index,
                    "content_index": content_index,
                    "text": full_text,
                })
                event({
                    "type": "response.content_part.done",
                    "response_id": response_id,
                    "item_id": item["id"],
                    "output_index": output_index,
                    "content_index": content_index,
                    "part": part,
                })
            event({
                "type": "response.output_item.done",
                "response_id": response_id,
                "output_index": output_index,
                "item": item,
            })


    # ─── Google Native API (Gemini CLI compatible) ────────────────────────────

    def _parse_google_model_from_path(self):
        """Extract model name from /v1beta/models/{model}:method path."""
        m = re.match(r'/v1beta/models/([^:?]+)', self.path)
        if m:
            return m.group(1)
        return None

    def _handle_google_models_list(self):
        """GET /v1beta/models — Google AI format model list."""
        models = []
        for name, cfg in MODELS.items():
            models.append({
                "name": f"models/{name}",
                "displayName": name,
                "description": cfg["desc"],
                "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
            })
        self.send_json({"models": models})

    def _google_contents_to_prompt(self, req: dict) -> str:
        """Convert Google API contents format to prompt string."""
        parts = []
        sys_inst = req.get("systemInstruction")
        if sys_inst:
            sys_parts = sys_inst.get("parts", [])
            sys_text = " ".join(p.get("text", "") for p in sys_parts if p.get("text"))
            if sys_text:
                parts.append(f"[System instruction]: {sys_text}")

        for content in req.get("contents", []):
            role = content.get("role", "user")
            text_parts = []
            for p in content.get("parts", []):
                if p.get("text"):
                    text_parts.append(p["text"])
            text = " ".join(text_parts)
            if role == "model":
                parts.append(f"[Assistant]: {text}")
            else:
                parts.append(text)
        return "\n\n".join(p for p in parts if p)

    def _handle_google_generate(self, body: bytes, stream: bool):
        """Handle Google native generateContent / streamGenerateContent."""
        req = json.loads(body)
        model_name = self._parse_google_model_from_path()
        if not model_name:
            self.send_json({"error": {"message": "model not specified in path"}}, 400)
            return

        model_name, model_id, think_mode, model_hex_id, err = self._resolve_model(model_name)
        if err:
            self.send_json({"error": {"message": err}}, 400)
            return

        prompt = self._google_contents_to_prompt(req)
        if not prompt.strip():
            self.send_json({"error": {"message": "empty content"}}, 400)
            return

        try:
            model_kwargs = {"model_hex_id": model_hex_id} if model_hex_id else {}
            text, _ = self._call_gemini(
                prompt,
                model_id,
                think_mode,
                None,
                **model_kwargs,
            )
        except Exception as e:
            self.send_json({"error": {"message": f"upstream error: {e}"}}, 502)
            return

        candidate = {
            "content": {"parts": [{"text": text or ""}], "role": "model"},
            "finishReason": "STOP",
            "index": 0,
        }
        usage = {
            "promptTokenCount": len(prompt) // 4,
            "candidatesTokenCount": len(text) // 4,
            "totalTokenCount": (len(prompt) + len(text)) // 4,
        }
        response_obj = {
            "candidates": [candidate],
            "usageMetadata": usage,
            "modelVersion": model_name,
        }

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps(response_obj)}\n\n".encode())
            self.wfile.flush()
        else:
            self.send_json(response_obj)


# ─── Main ────────────────────────────────────────────────────────────────────

def load_config(path: str):
    if path and os.path.exists(path):
        with open(path) as f:
            CONFIG.update(json.load(f))
        log(f"Config loaded: {path}")


def startup_self_test(host: str, port: int):
    if not CONFIG.get("log_requests"):
        return

    def run():
        time.sleep(0.4)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f"http://127.0.0.1:{int(port)}"
        for method, path, data in (
            ("GET", "/v1/models", None),
            ("POST", "/__debug/echo", b'{"self_test":true}'),
        ):
            try:
                headers = {"Content-Type": "application/json"} if data else {}
                if CONFIG.get("api_key"):
                    headers["X-API-Key"] = str(CONFIG["api_key"])
                req = urllib.request.Request(
                    base + path,
                    data=data,
                    headers=headers,
                    method=method,
                )
                with opener.open(req, timeout=5) as resp:
                    resp_body = resp.read(200).decode("utf-8", errors="replace")
                log(f"startup self-test {method} {path} status={resp.status} preview={preview_text(resp_body, 240)}")
            except Exception as e:
                log(f"startup self-test {method} {path} failed {exception_summary(e)}")

    thread = threading.Thread(target=run, name="GeminiWeb2APISelfTest", daemon=True)
    thread.start()


def main():
    parser = argparse.ArgumentParser(description="Gemini Web to OpenAI API")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", type=str, default=None, help="Bind address (default 127.0.0.1; non-loopback requires --api-key)")
    parser.add_argument("--api-key", type=str, default=None, help="Require this key (X-API-Key or Bearer) on all /v1* endpoints")
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--cookie-file", type=str, default=None, help="Path to cookie file")
    parser.add_argument("--proxy", type=str, default=None, help="HTTP proxy, e.g. http://127.0.0.1:7890")
    parser.add_argument("--version", action="version", version=f"gemini-web2api {__version__}")
    args = parser.parse_args()

    config_path = args.config or os.environ.get("GEMINI_WEB2API_CONFIG")
    if not config_path:
        for p in ["./config.json", os.path.expanduser("~/.config/gemini-web2api/config.json")]:
            if os.path.exists(p):
                config_path = p
                break
    load_config(config_path)

    if args.port:
        CONFIG["port"] = args.port
    if args.host:
        CONFIG["host"] = args.host
    if args.api_key:
        CONFIG["api_key"] = args.api_key
    if args.cookie_file:
        CONFIG["cookie_file"] = args.cookie_file
    if args.proxy:
        CONFIG["proxy"] = args.proxy

    host = str(CONFIG.get("host") or "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1") and not CONFIG.get("api_key"):
        # 本服务持有浏览器 Cookie，端点能直接消耗上游配额，无鉴权暴露到
        # 局域网等于把账号借给同网段所有主机，因此直接拒绝启动。
        print("ERROR: 绑定非回环地址时必须配置 api_key（--api-key 或配置文件），否则局域网任意主机可借用本机 Cookie 调用 Gemini。", file=sys.stderr)
        sys.exit(2)

    class ThreadedServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    port = CONFIG["port"]
    server = ThreadedServer((host, port), GeminiHandler)
    print(f"gemini-web2api v{__version__}")
    print(f"  Listening: http://{host}:{port}")
    print(f"  Auth:      {'api key required' if CONFIG.get('api_key') else 'none (loopback only)'}")
    print(f"  Base URL:  http://localhost:{port}/v1")
    print(f"  Models:    {', '.join(MODELS.keys())}")
    print(f"  Cookie:    {'yes (' + CONFIG['cookie_file'] + ')' if CONFIG.get('cookie_file') else 'none (anonymous)'}")
    print(f"  Proxy:     {CONFIG.get('proxy') or 'none (uses system env HTTP_PROXY/HTTPS_PROXY)'}")
    print(f"  Retry:     {CONFIG['retry_attempts']}x / {CONFIG['retry_delay_sec']}s")
    print(f"  Script:    {os.path.abspath(__file__)}")
    print(f"  Logs:      {'stdout debug enabled' if CONFIG.get('log_requests') else 'disabled by config'}")
    print(f"  PID:       {os.getpid()}")
    print(f"  Proxy env: {json.dumps(proxy_env_summary(), ensure_ascii=False) or '{}'}")
    print(f"  Debug URL: http://127.0.0.1:{port}/__debug/echo")
    print(f"  Local test: curl --noproxy '*' -v http://127.0.0.1:{port}/v1/models")
    print()
    startup_self_test(CONFIG["host"], port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
