from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import inspect
import json
import logging
import queue
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import parse_qs, unquote, unquote_to_bytes, urlparse

import requests

from deepcat.utils.logger import get_logger

try:
    from curl_cffi import requests as curl_requests
    from curl_cffi.curl import CurlError as CurlBaseError
    from curl_cffi.requests.exceptions import RequestException as CurlRequestException

    HAS_CURL_CFFI = True
except Exception:
    curl_requests = None  # type: ignore[assignment]
    CurlBaseError = None  # type: ignore[assignment]
    CurlRequestException = None  # type: ignore[assignment]
    HAS_CURL_CFFI = False

try:
    from deepcat.utils.curl_dns import apply_to_session as _apply_curl_resolve
except Exception:  # 缺少工具模块时退回 libcurl 自身解析，不影响功能
    _apply_curl_resolve = None

NETWORK_REQUEST_EXCEPTION_TYPES: list[type[BaseException]] = [requests.RequestException]
if CurlRequestException is not None:
    NETWORK_REQUEST_EXCEPTION_TYPES.append(CurlRequestException)
if CurlBaseError is not None:
    NETWORK_REQUEST_EXCEPTION_TYPES.append(CurlBaseError)
NETWORK_REQUEST_EXCEPTIONS: tuple[type[BaseException], ...] = tuple(dict.fromkeys(NETWORK_REQUEST_EXCEPTION_TYPES))


DEFAULT_MODEL = "gpt-5-5-thinking"
CHATGPT_WEB_IMAGE_MODELS = {
    "gpt-image-2",
    "codex-gpt-image-2",
    "plus-codex-gpt-image-2",
    "team-codex-gpt-image-2",
    "pro-codex-gpt-image-2",
}
NON_TEXT_TYPES = {"thinking", "thoughts", "reasoning_recap", "model_editable_context"}
RICH_TEXT_CONTAINER_KEYS = (
    "parts",
    "content",
    "children",
    "items",
    "blocks",
    "nodes",
    "paragraphs",
    "segments",
    "runs",
    "spans",
    "fragments",
    "lines",
)
SENTINEL_HEADER_NAMES = {
    "openai-sentinel-chat-requirements-token",
    "openai-sentinel-turnstile-token",
    "openai-sentinel-proof-token",
    "x-conduit-token",
}
HEADER_CONTAINER_KEYS = {
    "headers",
    "requestheaders",
    "request_headers",
    "protocolheaders",
    "protocol_headers",
}
FORBIDDEN_UPSTREAM_HEADER_NAMES = {
    "accept-encoding",
    "connection",
    "content-length",
    "cookie",
    "host",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
SENTINEL_JSON_TOKEN_FIELDS = {
    "chatrequirementstoken": "openai-sentinel-chat-requirements-token",
    "openaisentinelchatrequirementstoken": "openai-sentinel-chat-requirements-token",
    "turnstiletoken": "openai-sentinel-turnstile-token",
    "openaisentinelturnstiletoken": "openai-sentinel-turnstile-token",
    "prooftoken": "openai-sentinel-proof-token",
    "openaisentinelprooftoken": "openai-sentinel-proof-token",
    "conduittoken": "x-conduit-token",
    "xconduittoken": "x-conduit-token",
}


CHATGPT_WEB_MODELS: list[dict[str, Any]] = [
    {
        "id": "gpt-5-3",
        "name": "GPT-5.3",
        "max_tokens": 34834,
        "context_window": 34834,
        "reasoning_type": "auto",
        "reasoning": False,
    },
    {
        "id": "gpt-5-2",
        "name": "GPT-5.2",
        "max_tokens": 25384,
        "context_window": 25384,
        "reasoning_type": "auto",
        "reasoning": False,
    },
    {
        "id": "gpt-5-1",
        "name": "GPT-5.1",
        "max_tokens": 35815,
        "context_window": 35815,
        "reasoning_type": "auto",
        "reasoning": False,
    },
    {
        "id": "gpt-5",
        "name": "GPT-5",
        "max_tokens": 34815,
        "context_window": 34815,
        "reasoning_type": "auto",
        "reasoning": False,
    },
    {
        "id": "gpt-5-mini",
        "name": "GPT-5 mini",
        "max_tokens": 32767,
        "context_window": 32767,
        "reasoning_type": "none",
        "reasoning": False,
    },
    {
        "id": "gpt-5-3-mini",
        "name": "GPT-5.3 Mini",
        "max_tokens": 34834,
        "context_window": 34834,
        "reasoning_type": "none",
        "reasoning": False,
    },
    {
        "id": "gpt-5-5-thinking",
        "name": "GPT-5.5 Thinking",
        "max_tokens": 262144,
        "context_window": 262144,
        "reasoning_type": "reasoning",
        "reasoning": True,
    },
    {
        "id": "auto",
        "name": "Auto",
        "max_tokens": 34834,
        "context_window": 34834,
        "reasoning_type": "auto",
        "reasoning": False,
    },
    {
        "id": "gpt-image-2",
        "name": "GPT Image 2",
        "max_tokens": 4096,
        "context_window": 32768,
        "reasoning_type": "image",
        "reasoning": False,
    },
    {
        "id": "codex-gpt-image-2",
        "name": "Codex GPT Image 2",
        "max_tokens": 4096,
        "context_window": 32768,
        "reasoning_type": "image",
        "reasoning": False,
    },
]


MODEL_ALIASES = {
    "gpt-4": DEFAULT_MODEL,
    "gpt-4o": DEFAULT_MODEL,
    "chatgpt-4o-latest": DEFAULT_MODEL,
    "gpt5.5": "gpt-5-5-thinking",
    "gpt-5.5": "gpt-5-5-thinking",
    "gpt5.5-think": "gpt-5-5-thinking",
    "gpt5.5-thinking": "gpt-5-5-thinking",
    "gpt-5.5-think": "gpt-5-5-thinking",
    "gpt-5.5-thinking": "gpt-5-5-thinking",
    "gpt-5-5-think": "gpt-5-5-thinking",
    "gpt5.4": "gpt-5-5-thinking",
    "gpt-5.4": "gpt-5-5-thinking",
    "gpt5.4-think": "gpt-5-5-thinking",
    "gpt5.4-thinking": "gpt-5-5-thinking",
    "gpt-5.4-think": "gpt-5-5-thinking",
    "gpt-5.4-thinking": "gpt-5-5-thinking",
    "gpt-5-4-thinking": "gpt-5-5-thinking",
    "gpt-5-4-t-mini": "gpt-5-3-mini",
    "gpt5.2-think": "gpt-5-2",
    "gpt5.2-thinking": "gpt-5-2",
    "gpt-5.2-think": "gpt-5-2",
    "gpt-5.2-thinking": "gpt-5-2",
    "gpt-5-2-think": "gpt-5-2",
    "gpt-5-2-thinking": "gpt-5-2",
    "gpt-5-3-instant": "gpt-5-3",
    "gpt-5-2-instant": "gpt-5-2",
}


DEFAULT_CONFIG: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8082,
    "api_key": "",
    "proxy": None,
    "base_url": "https://chatgpt.com",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "request_timeout_sec": 180,
    "request_connect_timeout_sec": 8,
    "auth_timeout_sec": 8,
    "warmup_timeout_sec": 8,
    "bootstrap_timeout_sec": 8,
    "prompt_network_error_retries": 2,
    "prompt_network_error_retry_window_sec": 15,
    "prompt_network_error_retry_delay_sec": 2,
    "fallback_fetch_timeout_sec": 8,
    "image_attachment_timeout_sec": 60,
    "image_download_timeout_sec": 60,
    "localize_generated_images": False,
    "max_request_body_bytes": 64 * 1024 * 1024,
    "log_requests": True,
    "timezone": "Asia/Shanghai",
    "timezone_offset_min": -480,
    "chatgpt_history_and_training_disabled": False,
}

CONFIG = dict(DEFAULT_CONFIG)

SERVICE_BUILD = "chatgpt_web2api_images_original_v3"

# ---------------------------------------------------------------------------
# Sentinel / PoW / Turnstile 验证相关常量
# ---------------------------------------------------------------------------

DEFAULT_POW_SCRIPT = "https://chatgpt.com/backend-api/sentinel/sdk.js"
CLIENT_VERSION = "prod-a194cd50d4416d3c0b47c740f206b12ce60f5887"
CLIENT_BUILD_NUMBER = "6708908"
SEC_CH_UA = '"Microsoft Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"'

_BOOTSTRAP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)

# 模块级 Bootstrap 资源缓存
_bootstrap_cache_lock = threading.Lock()
_bootstrap_script_sources: list[str] = []
_bootstrap_data_build: str = ""
_bootstrap_timestamp: float = 0.0
_BOOTSTRAP_TTL_SEC = 600  # 10 分钟缓存有效期


# ---------------------------------------------------------------------------
# 会话追加历史映射缓存
# ---------------------------------------------------------------------------
_conversation_context_cache: dict[str, tuple[str, str]] = {}
_conversation_cache_lock = threading.Lock()

def _get_history_hash(messages: list[dict[str, Any]]) -> str | None:
    if not messages:
        return None
    simplified = []
    for msg in messages:
        role = str(msg.get("role") or "").strip().lower()
        content = str(msg.get("content") or "").strip()
        simplified.append({"role": role, "content": content})
    serialized = json.dumps(simplified, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(serialized.encode("utf-8")).hexdigest()

def _update_cache(key: str, val: tuple[str, str]) -> None:
    with _conversation_cache_lock:
        if len(_conversation_context_cache) > 2000:
            keys_to_del = list(_conversation_context_cache.keys())[:500]
            for k in keys_to_del:
                _conversation_context_cache.pop(k, None)
        _conversation_context_cache[key] = val


@dataclass
class ChatGPTWebAuth:
    cookie: str
    access_token: str | None
    session_token: str | None
    device_id: str | None
    user_agent: str | None
    protocol_headers: dict[str, str]


@dataclass
class ChatGPTWebCompletion:
    text: str
    model: str
    conversation_id: str | None = None
    message_id: str | None = None
    snapshot_text: str | None = None
    transient: bool = False


@dataclass(frozen=True)
class ChatGPTWebImageUpload:
    filename: str
    mime_type: str
    data: bytes
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class ChatGPTWebUploadedImage:
    file_id: str
    filename: str
    mime_type: str
    size_bytes: int
    width: int | None = None
    height: int | None = None


@dataclass
class PreparedChatGPTRequest:
    session: Any
    config: dict[str, Any]
    prompt_messages: list[dict[str, Any]]
    conversation_id: str | None
    parent_message_id: str | None
    access_token: str | None
    device_id: str
    dynamic_headers: dict[str, str]
    model_id: str


class ChatGPTWebError(RuntimeError):
    status_code = 502
    error_code = "chatgpt_web_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = int(status_code or 502)
        if error_code:
            self.error_code = error_code


class ChatGPTWebUpstreamError(ChatGPTWebError):
    def __init__(self, message: str, status_code: int, error_code: str = "upstream_error") -> None:
        super().__init__(message, status_code=status_code, error_code=error_code)


@dataclass(frozen=True)
class UpstreamErrorClassification:
    code: str
    message: str
    status_code: int


_api_logger = get_logger("deepcat.chatgpt_web2api", level=logging.INFO)
_handoff_stream_callback_local = threading.local()


def log(message: str) -> None:
    if not CONFIG.get("log_requests"):
        return
    try:
        print(f"[ChatGPT Web2API {time.strftime('%H:%M:%S')}] {message}", flush=True)
    except Exception:
        pass
    try:
        _api_logger.info(f"[ChatGPT Web2API] {message}")
    except Exception:
        pass


def _get_handoff_stream_snapshot_callback() -> Any:
    return getattr(_handoff_stream_callback_local, "callback", None)


def _set_handoff_stream_snapshot_callback(callback: Any) -> Any:
    previous = getattr(_handoff_stream_callback_local, "callback", None)
    _handoff_stream_callback_local.callback = callback
    return previous


def mask_secret(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 12:
        return "***"
    return f"{text[:8]}...{text[-4:]}"


def normalize_model(model: str | None) -> str:
    if not model:
        return DEFAULT_MODEL
    normalized = str(model).strip().lower().replace("_", "-").replace(" ", "-")
    return MODEL_ALIASES.get(normalized, normalized) or DEFAULT_MODEL


def openai_model_list() -> list[dict[str, Any]]:
    return [
        {
            "id": str(model["id"]),
            "object": "model",
            "created": 1700000000,
            "owned_by": "chatgpt-web",
            "metadata": {
                "name": str(model["name"]),
                "max_tokens": int(model["max_tokens"]),
                "context_window": int(model["context_window"]),
                "reasoning_type": str(model["reasoning_type"]),
                "reasoning": bool(model["reasoning"]),
            },
        }
        for model in CHATGPT_WEB_MODELS
    ]


def cookie_value(cookie_header: str, name: str) -> str | None:
    prefix = f"{name}="
    for item in str(cookie_header or "").split(";"):
        part = item.strip()
        if part.startswith(prefix):
            return part[len(prefix):]
    return None


def _compact_name(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _header_dict(value: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(value, dict):
        if "name" in value and "value" in value:
            name = str(value.get("name") or "").strip()
            if name and value.get("value") is not None:
                result[name] = str(value.get("value"))
            return result
        for key, item in value.items():
            name = str(key or "").strip()
            if name and item is not None:
                result[name] = str(item)
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("key") or "").strip()
            if not name:
                continue
            raw_value = item.get("value")
            if raw_value is None:
                raw_value = item.get("val")
            if raw_value is not None:
                result[name] = str(raw_value)
    return result


def _sanitize_protocol_headers(headers: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in headers.items():
        name = str(key or "").strip()
        if not name:
            continue
        lower_name = name.lower()
        if lower_name in FORBIDDEN_UPSTREAM_HEADER_NAMES:
            continue
        text = str(value).strip()
        if text:
            result[name] = text
    return result


def _headers_from_json_tree(value: Any, *, sanitize: bool = True) -> dict[str, str]:
    collected: dict[str, str] = {}

    def visit(item: Any, parent_key: str = "") -> None:
        if isinstance(item, dict):
            if _compact_name(parent_key) in HEADER_CONTAINER_KEYS:
                collected.update(_header_dict(item))
                return
            for key, child in item.items():
                compact = _compact_name(str(key))
                if compact in HEADER_CONTAINER_KEYS:
                    collected.update(_header_dict(child))
                if compact in SENTINEL_JSON_TOKEN_FIELDS and child is not None:
                    collected[SENTINEL_JSON_TOKEN_FIELDS[compact]] = str(child)
                if compact in {"useragent", "ua"} and child is not None:
                    collected.setdefault("User-Agent", str(child))
                visit(child, str(key))
        elif isinstance(item, list):
            if _compact_name(parent_key) in HEADER_CONTAINER_KEYS:
                collected.update(_header_dict(item))
            for child in item:
                visit(child, parent_key)

    visit(value)
    if sanitize:
        return _sanitize_protocol_headers(collected)
    return collected


def _cookie_from_value(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        return text if "=" in text else ""
    if isinstance(value, dict):
        if "name" in value and "value" in value:
            name = str(value.get("name") or "").strip()
            cookie_val = str(value.get("value") or "").strip()
            return f"{name}={cookie_val}" if name and cookie_val else ""
        parts: list[str] = []
        for key, item in value.items():
            if item is None:
                continue
            name = str(key or "").strip()
            if not name:
                continue
            if isinstance(item, dict) and "value" in item:
                item = item.get("value")
            cookie_val = str(item).strip()
            if cookie_val:
                parts.append(f"{name}={cookie_val}")
        return "; ".join(parts)
    if isinstance(value, list):
        parts = [_cookie_from_value(item) for item in value]
        return "; ".join(part for part in parts if part)
    return ""


def _cookie_from_json_tree(value: Any) -> str:
    if isinstance(value, list):
        cookie = _cookie_from_value(value)
        if cookie:
            return cookie
    direct_keys = {
        "cookie",
        "cookies",
        "cookieheader",
        "cookie_header",
        "requestcookie",
        "requestcookies",
        "request_cookie",
        "request_cookies",
    }
    seen: set[int] = set()

    def visit(item: Any) -> str:
        if isinstance(item, (dict, list)):
            item_id = id(item)
            if item_id in seen:
                return ""
            seen.add(item_id)
        if isinstance(item, dict):
            for key, child in item.items():
                if _compact_name(str(key)) in direct_keys:
                    cookie = _cookie_from_value(child)
                    if cookie:
                        return cookie
            for child in item.values():
                cookie = visit(child)
                if cookie:
                    return cookie
        elif isinstance(item, list):
            for child in item:
                cookie = visit(child)
                if cookie:
                    return cookie
        return ""

    cookie = visit(value)
    if cookie:
        return cookie
    headers = _headers_from_json_tree(value, sanitize=False)
    for key, item in headers.items():
        if key.lower() == "cookie":
            cookie = _cookie_from_value(item)
            if cookie:
                return cookie
    return ""


def _json_value(value: Any, *keys: str) -> str | None:
    targets = {_compact_name(key) for key in keys}
    seen: set[int] = set()

    def visit(item: Any) -> str | None:
        if isinstance(item, (dict, list)):
            item_id = id(item)
            if item_id in seen:
                return None
            seen.add(item_id)
        if isinstance(item, dict):
            for key, child in item.items():
                if _compact_name(str(key)) in targets and child is not None:
                    if isinstance(child, (dict, list)):
                        continue
                    text = str(child).strip()
                    if text:
                        return text
            for child in item.values():
                found = visit(child)
                if found:
                    return found
        elif isinstance(item, list):
            for child in item:
                found = visit(child)
                if found:
                    return found
        return None

    return visit(value)


def _looks_like_cookie(value: str) -> bool:
    raw = str(value or "")
    return "=" in raw and (
        ";" in raw
        or "__Secure-next-auth" in raw
        or "oai-" in raw
        or "cf_clearance" in raw
    )


def parse_auth_value(value: str) -> ChatGPTWebAuth:
    raw = str(value or "").strip()
    if not raw:
        raise ChatGPTWebError("缺少 ChatGPT Cookie/Token，请在模型管理的 API 密钥字段中粘贴登录 Cookie 或 session token。")
    raw_lower = raw.lower()
    if raw_lower.startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()
        raw_lower = raw.lower()
    if raw_lower.startswith("authorization:"):
        raw = raw.split(":", 1)[1].strip()
        raw_lower = raw.lower()

    cookie = ""
    access_token: str | None = None
    session_token: str | None = None
    device_id: str | None = None
    user_agent: str | None = None
    protocol_headers: dict[str, str] = {}

    if raw.startswith("{") or raw.startswith("["):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ChatGPTWebError(f"API 密钥字段中的 ChatGPT Web JSON 无法解析: {exc}") from exc
        if not isinstance(data, (dict, list)):
            raise ChatGPTWebError("API 密钥字段中的 ChatGPT Web JSON 必须是对象或 Cookie 数组。")
        cookie = _cookie_from_json_tree(data)
        access_token = _json_value(data, "access_token", "accessToken", "access token")
        session_token = _json_value(
            data,
            "session_token",
            "sessionToken",
            "__Secure-next-auth.session-token",
            "nextAuthSessionToken",
        )
        device_id = _json_value(data, "oaiDeviceId", "device_id", "deviceId", "oai-did")
        user_agent = _json_value(data, "user_agent", "userAgent", "User-Agent")
        protocol_headers = _headers_from_json_tree(data)
        if not access_token:
            for key, item in protocol_headers.items():
                if key.lower() != "authorization":
                    continue
                auth_header = str(item or "").strip()
                if auth_header.lower().startswith("bearer "):
                    access_token = auth_header[7:].strip() or None
                break
        protocol_headers = {k: v for k, v in protocol_headers.items() if k.lower() != "authorization"}
        if not user_agent:
            for key, item in protocol_headers.items():
                if key.lower() == "user-agent":
                    user_agent = item
                    break
    elif raw.lower().startswith("bearer "):
        access_token = raw[7:].strip() or None
    elif _looks_like_cookie(raw):
        cookie = raw
    else:
        session_token = raw
        access_token = raw

    if session_token and not cookie:
        cookie = f"__Secure-next-auth.session-token={session_token}"
    if not cookie and not access_token:
        raise ChatGPTWebError("ChatGPT Web 登录态为空，请检查 API 密钥字段内容。")
    if not device_id:
        device_id = cookie_value(cookie, "oai-did") if cookie else None
    protocol_headers = _sanitize_protocol_headers(protocol_headers)
    return ChatGPTWebAuth(
        cookie=cookie,
        access_token=access_token,
        session_token=session_token,
        device_id=device_id,
        user_agent=user_agent,
        protocol_headers=protocol_headers,
    )


def _normalize_proxy(proxy_url: str | None) -> str | None:
    proxy = str(proxy_url or "").strip()
    if not proxy:
        return None
    if "://" not in proxy:
        return f"socks5h://{proxy}"
    if proxy.startswith("socks5://"):
        return proxy.replace("socks5://", "socks5h://", 1)
    return proxy


def make_session(config: dict[str, Any], auth: ChatGPTWebAuth) -> Any:
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    if HAS_CURL_CFFI and curl_requests is not None:
        try:
            session = curl_requests.Session(impersonate="chrome120")
        except TypeError:
            session = curl_requests.Session()
    else:
        session = requests.Session()
    try:
        session.trust_env = False
    except Exception:
        pass
    session.headers.update(
        {
            "User-Agent": auth.user_agent or str(config.get("user_agent") or DEFAULT_CONFIG["user_agent"]),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
            "Origin": base_url,
            "Referer": f"{base_url}/",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Ch-Ua": SEC_CH_UA,
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "OAI-Language": "zh-CN",
            "OAI-Client-Version": CLIENT_VERSION,
            "OAI-Client-Build-Number": CLIENT_BUILD_NUMBER,
        }
    )
    if auth.cookie:
        session.headers["Cookie"] = auth.cookie
    proxy = _normalize_proxy(str(config.get("proxy") or ""))
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    if _apply_curl_resolve is not None:
        # 直连时预置 Python 解析出的地址，避免 libcurl 的解析线程被安全策略拦掉
        _apply_curl_resolve(session, base_url, proxy)
    return session


def get_session_info(
    session: Any,
    config: dict[str, Any],
    access_token: str | None,
) -> tuple[dict[str, Any] | None, str | None, str]:
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    try:
        resp = session.get(
            f"{base_url}/api/auth/session",
            timeout=float(config.get("auth_timeout_sec") or DEFAULT_CONFIG["auth_timeout_sec"]),
        )
    except Exception as exc:
        return None, access_token, str(exc)
    if not resp.ok:
        return None, access_token, f"{resp.status_code} {resp.text[:300]}"
    try:
        data = resp.json()
    except ValueError as exc:
        return None, access_token, f"session endpoint returned non-json: {exc}"
    token = data.get("accessToken") or access_token
    user = data.get("user") if isinstance(data, dict) else None
    if not token and not user:
        return None, token, "session endpoint returned no user/accessToken"
    return data, token, ""


def _append_cookie_if_missing(cookie_header: str, name: str, value: str) -> str:
    if not str(value or "").strip():
        return str(cookie_header or "")
    if cookie_value(cookie_header, name):
        return str(cookie_header or "")
    cookie = str(cookie_header or "").strip()
    pair = f"{name}={value}"
    return f"{cookie}; {pair}" if cookie else pair


def _stable_device_id_source(
    session_info: dict[str, Any] | None,
    cookie: str,
    access_token: str | None,
) -> str:
    if cookie:
        return cookie
    if access_token:
        return access_token
    if isinstance(session_info, dict):
        user = session_info.get("user")
        if isinstance(user, dict):
            user_id = str(user.get("id") or "").strip()
            if user_id:
                return user_id
        account = session_info.get("account")
        if isinstance(account, dict):
            account_id = str(account.get("id") or "").strip()
            if account_id:
                return account_id
    return ""


def get_device_id(
    session_info: dict[str, Any] | None,
    cookie: str,
    access_token: str | None = None,
) -> str:
    if isinstance(session_info, dict):
        device_id = session_info.get("oaiDeviceId")
        if isinstance(device_id, str) and device_id:
            return device_id
    device_id = cookie_value(cookie, "oai-did")
    if device_id:
        return device_id
    source = _stable_device_id_source(session_info, cookie, access_token)
    if source:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"deepcat-chatgpt-web:{source[:2048]}"))
    return str(uuid.uuid4())


def build_protocol_headers(
    config: dict[str, Any],
    access_token: str | None,
    device_id: str,
    dynamic_headers: dict[str, str],
    *,
    accept: str = "text/event-stream",
    target_path: str = "/backend-api/f/conversation",
) -> dict[str, str]:
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": accept,
        "oai-language": "en-US",
        "oai-device-id": device_id,
        "x-openai-target-path": target_path,
        "x-openai-target-route": target_path,
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if "oai-session-id" not in dynamic_headers:
        headers["oai-session-id"] = str(uuid.uuid4())
    if "x-oai-turn-trace-id" not in dynamic_headers:
        headers["x-oai-turn-trace-id"] = str(uuid.uuid4())
    headers.update(dynamic_headers)
    headers["x-openai-target-path"] = target_path
    headers["x-openai-target-route"] = target_path
    headers.setdefault("Referer", f"{base_url}/")
    return headers


# ===========================================================================
# Sentinel token 辅助函数 — PoW + Turnstile + Bootstrap
# ===========================================================================


class _ScriptSrcParser(HTMLParser):
    """解析 chatgpt.com 首页 HTML，提取 ``<script>`` 标签的 src 和 data-build。"""

    def __init__(self) -> None:
        super().__init__()
        self.script_sources: list[str] = []
        self.data_build = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attrs_dict = dict(attrs)
        src = attrs_dict.get("src")
        if not src:
            return
        self.script_sources.append(src)
        match = re.search(r"c/[^/]*/_", src)
        if match:
            self.data_build = match.group(0)


def _parse_pow_resources(html: str) -> tuple[list[str], str]:
    """从 chatgpt.com 首页 HTML 中提取 PoW 所需的 script sources 和 data_build。"""
    parser = _ScriptSrcParser()
    parser.feed(html)
    sources = parser.script_sources or [DEFAULT_POW_SCRIPT]
    data_build = parser.data_build
    if not data_build:
        m = re.search(r'<html[^>]*data-build="([^"]*)"', html)
        if m:
            data_build = m.group(1)
    return sources, data_build


def _legacy_parse_time() -> str:
    """生成 EST 时区的时间字符串，用于 PoW 配置。"""
    now = datetime.now(timezone(timedelta(hours=-5)))
    return now.strftime("%a %b %d %Y %H:%M:%S") + " GMT-0500 (Eastern Standard Time)"


def _build_pow_config(
    user_agent: str,
    script_sources: Sequence[str] | None = None,
    data_build: str = "",
) -> list[Any]:
    """构建 PoW（Proof of Work）配置参数列表。

    生成模拟浏览器环境指纹的配置数据，包含 navigator/window/document 属性、
    屏幕分辨率、CPU 核心数等信息，用于构造 PoW 挑战的输入。
    """
    navigator_keys = [
        "registerProtocolHandler−function registerProtocolHandler() { [native code] }",
        "storage−[object StorageManager]",
        "locks−[object LockManager]",
        "appCodeName−Mozilla",
        "permissions−[object Permissions]",
        "share−function share() { [native code] }",
        "webdriver−false",
        "managed−[object NavigatorManagedData]",
        "canShare−function canShare() { [native code] }",
        "vendor−Google Inc.",
        "mediaDevices−[object MediaDevices]",
        "vibrate−function vibrate() { [native code] }",
        "storageBuckets−[object StorageBucketManager]",
        "mediaCapabilities−[object MediaCapabilities]",
        "cookieEnabled−true",
        "virtualKeyboard−[object VirtualKeyboard]",
        "product−Gecko",
        "presentation−[object Presentation]",
        "onLine−true",
        "mimeTypes−[object MimeTypeArray]",
        "credentials−[object CredentialsContainer]",
        "serviceWorker−[object ServiceWorkerContainer]",
        "keyboard−[object Keyboard]",
        "gpu−[object GPU]",
        "doNotTrack",
        "serial−[object Serial]",
        "pdfViewerEnabled−true",
        "language−zh-CN",
        "geolocation−[object Geolocation]",
        "userAgentData−[object NavigatorUAData]",
        "getUserMedia−function getUserMedia() { [native code] }",
        "sendBeacon−function sendBeacon() { [native code] }",
        "hardwareConcurrency−32",
        "windowControlsOverlay−[object WindowControlsOverlay]",
    ]
    window_keys = [
        "0", "window", "self", "document", "name", "location",
        "customElements", "history", "navigation", "innerWidth", "innerHeight",
        "scrollX", "scrollY", "visualViewport", "screenX", "screenY",
        "outerWidth", "outerHeight", "devicePixelRatio", "screen", "chrome",
        "navigator", "onresize", "performance", "crypto", "indexedDB",
        "sessionStorage", "localStorage", "scheduler", "alert", "atob", "btoa",
        "fetch", "matchMedia", "postMessage", "queueMicrotask",
        "requestAnimationFrame", "setInterval", "setTimeout", "caches",
        "__NEXT_DATA__", "__BUILD_MANIFEST", "__NEXT_PRELOADREADY",
    ]
    document_keys = [
        "__reactContainer$fzelfjyxej8",
        "_reactListening5dehydibo78",
        "location",
    ]
    cores = [8, 16, 24, 32]
    screen_sizes = [[1920, 1080], [1440, 900], [2560, 1440], [3840, 2160]]
    script_source = random.choice(list(script_sources)) if script_sources else None
    return [
        sum(random.choices(screen_sizes, k=1)[0]),
        _legacy_parse_time(),
        4294705152,
        1,
        user_agent,
        script_source,
        data_build,
        "en-US",
        "en-US,es-US,en,es",
        random.random(),
        random.choice(navigator_keys),
        random.choice(document_keys),
        random.choice(window_keys),
        time.perf_counter() * 1000,
        str(uuid.uuid4()),
        "",
        random.choice(cores),
        time.time() * 1000 - (time.perf_counter() * 1000),
        0, 0, 0, 0, 0, 0,
        0,  # 0 = edge/chrome
    ]


def _pow_generate(
    seed: str,
    difficulty: str,
    config_list: list[Any],
    limit: int = 500000,
) -> tuple[str, bool]:
    """SHA3-512 工作量证明求解器。

    在 ``limit`` 次迭代内尝试找到满足 ``difficulty`` 的 hash 碰撞。
    返回 ``(answer, solved)``。
    """
    target = bytes.fromhex(difficulty)
    diff_len = len(difficulty) // 2
    seed_bytes = seed.encode()
    static_1 = (
        json.dumps(config_list[:3], separators=(",", ":"), ensure_ascii=False)[:-1] + ","
    ).encode()
    static_2 = (
        "," + json.dumps(config_list[4:9], separators=(",", ":"), ensure_ascii=False)[1:-1] + ","
    ).encode()
    static_3 = (
        "," + json.dumps(config_list[10:], separators=(",", ":"), ensure_ascii=False)[1:]
    ).encode()
    for i in range(limit):
        final_json = static_1 + str(i).encode() + static_2 + str(i >> 1).encode() + static_3
        encoded = base64.b64encode(final_json)
        digest = hashlib.sha3_512(seed_bytes + encoded).digest()
        if digest[:diff_len] <= target:
            return encoded.decode(), True
    fallback = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D" + base64.b64encode(
        f'"{seed}"'.encode()
    ).decode()
    return fallback, False


def _build_legacy_requirements_token(
    user_agent: str,
    script_sources: Sequence[str] | None = None,
    data_build: str = "",
) -> str:
    """构建旧版需求令牌（p_token），前缀 ``gAAAAAC``。"""
    config_list = _build_pow_config(
        user_agent, script_sources=script_sources, data_build=data_build,
    )
    return "gAAAAAC" + base64.b64encode(
        json.dumps(config_list, separators=(",", ":"), ensure_ascii=False).encode()
    ).decode()


def _build_proof_token(
    seed: str,
    difficulty: str,
    user_agent: str,
    script_sources: Sequence[str] | None = None,
    data_build: str = "",
) -> str:
    """构建 PoW 证明令牌，前缀 ``gAAAAAB``。

    如果在 500000 次迭代内未求解成功，仍返回一个降级 token 以避免阻塞请求。
    """
    config_list = _build_pow_config(
        user_agent, script_sources=script_sources, data_build=data_build,
    )
    answer, solved = _pow_generate(seed, difficulty, config_list)
    if not solved:
        log(f"PoW 求解超过限制: difficulty={difficulty}")
    return "gAAAAAB" + answer


# ---------------------------------------------------------------------------
# Turnstile 验证解算器
# ---------------------------------------------------------------------------


class _OrderedMap:
    """有序键值映射，模拟 JS 对象的属性插入顺序。"""

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.values: dict[str, Any] = {}

    def add(self, key: str, value: Any) -> None:
        if key not in self.values:
            self.keys.append(key)
        self.values[key] = value


def _turnstile_to_str(value: Any) -> str:
    """将 Turnstile 虚拟机中的值转换为字符串表示。"""
    if value is None:
        return "undefined"
    if isinstance(value, float):
        return str(value)
    if isinstance(value, str):
        special_values: dict[str, str] = {
            "window.Math": "[object Math]",
            "window.Reflect": "[object Reflect]",
            "window.performance": "[object Performance]",
            "window.localStorage": "[object Storage]",
            "window.Object": "function Object() { [native code] }",
            "window.Reflect.set": "function set() { [native code] }",
            "window.performance.now": "function () { [native code] }",
            "window.Object.create": "function create() { [native code] }",
            "window.Object.keys": "function keys() { [native code] }",
            "window.Math.random": "function random() { [native code] }",
        }
        return special_values.get(value, value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return ",".join(value)
    return str(value)


def _xor_string(text: str, key: str) -> str:
    """对字符串执行 XOR 编解码（对称操作）。"""
    if not key:
        return text
    return "".join(
        chr(ord(ch) ^ ord(key[i % len(key)])) for i, ch in enumerate(text)
    )


def _solve_turnstile_token(dx: str, p: str) -> str | None:
    """解算 Cloudflare Turnstile 验证 token。

    解析并执行 ChatGPT 下发的 Turnstile 指令序列（一个简化的虚拟机），
    返回验证 token。如果解算失败返回 None。
    """
    try:
        cipher_bytes = base64.b64decode(dx)
        key_bytes = p.encode("utf-8")
        decrypted_bytes = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(cipher_bytes))
        token_list = json.loads(decrypted_bytes.decode("utf-8"))
    except Exception as exc:
        log(f"Turnstile 解算错误: {exc}")
        return None

    process_map: dict[Any, Any] = {}
    start_time = time.time()
    result = ""

    def func_1(e: Any, t: Any) -> None:
        process_map[e] = _xor_string(
            _turnstile_to_str(process_map[e]),
            _turnstile_to_str(process_map[t]),
        )

    def func_2(e: Any, t: Any) -> None:
        process_map[e] = t

    def func_3(e: Any) -> None:
        nonlocal result
        result = base64.b64encode(e.encode()).decode()

    def func_5(e: Any, t: Any) -> None:
        current = process_map[e]
        incoming = process_map[t]
        if isinstance(current, (list, tuple)):
            process_map[e] = list(current) + [incoming]
            return
        if isinstance(current, (str, float)) or isinstance(incoming, (str, float)):
            process_map[e] = _turnstile_to_str(current) + _turnstile_to_str(incoming)
            return
        process_map[e] = "NaN"

    def func_6(e: Any, t: Any, n: Any) -> None:
        tv = process_map[t]
        nv = process_map[n]
        if isinstance(tv, str) and isinstance(nv, str):
            value = f"{tv}.{nv}"
            process_map[e] = (
                "https://chatgpt.com/"
                if value == "window.document.location"
                else value
            )

    def func_7(e: Any, *args: Any) -> None:
        target = process_map[e]
        values = [process_map[arg] for arg in args]
        if isinstance(target, str) and target == "window.Reflect.set":
            obj, key_name, val = values
            obj.add(str(key_name), val)
        elif callable(target):
            target(*values)

    def func_8(e: Any, t: Any) -> None:
        process_map[e] = process_map[t]

    def func_14(e: Any, t: Any) -> None:
        process_map[e] = json.loads(process_map[t])

    def func_15(e: Any, t: Any) -> None:
        process_map[e] = json.dumps(process_map[t])

    def func_17(e: Any, t: Any, *args: Any) -> None:
        call_args = [process_map[arg] for arg in args]
        target = process_map[t]
        if target == "window.performance.now":
            elapsed_ns = time.time_ns() - int(start_time * 1e9)
            process_map[e] = (elapsed_ns + random.random()) / 1e6
        elif target == "window.Object.create":
            process_map[e] = _OrderedMap()
        elif target == "window.Object.keys":
            if call_args and call_args[0] == "window.localStorage":
                process_map[e] = [
                    "STATSIG_LOCAL_STORAGE_INTERNAL_STORE_V4",
                    "STATSIG_LOCAL_STORAGE_STABLE_ID",
                    "client-correlated-secret",
                    "oai/apps/capExpiresAt",
                    "oai-did",
                    "STATSIG_LOCAL_STORAGE_LOGGING_REQUEST",
                    "UiState.isNavigationCollapsed.1",
                ]
        elif target == "window.Math.random":
            process_map[e] = random.random()
        elif callable(target):
            process_map[e] = target(*call_args)

    def func_18(e: Any) -> None:
        process_map[e] = base64.b64decode(
            _turnstile_to_str(process_map[e])
        ).decode()

    def func_19(e: Any) -> None:
        process_map[e] = base64.b64encode(
            _turnstile_to_str(process_map[e]).encode()
        ).decode()

    def func_20(e: Any, t: Any, n: Any, *args: Any) -> None:
        if process_map[e] == process_map[t]:
            target = process_map[n]
            if callable(target):
                target(*[process_map[arg] for arg in args])

    def func_21(*_: Any) -> None:
        return

    def func_23(e: Any, t: Any, *args: Any) -> None:
        if process_map[e] is not None and callable(process_map[t]):
            process_map[t](*args)

    def func_24(e: Any, t: Any, n: Any) -> None:
        tv = process_map[t]
        nv = process_map[n]
        if isinstance(tv, str) and isinstance(nv, str):
            process_map[e] = f"{tv}.{nv}"

    process_map.update({
        1: func_1, 2: func_2, 3: func_3, 5: func_5, 6: func_6,
        7: func_7, 8: func_8, 9: token_list, 10: "window",
        14: func_14, 15: func_15, 16: p, 17: func_17,
        18: func_18, 19: func_19, 20: func_20, 21: func_21,
        23: func_23, 24: func_24,
    })

    for token in token_list:
        try:
            fn = process_map.get(token[0])
            if callable(fn):
                fn(*token[1:])
        except Exception:
            continue
    return result or None


# ---------------------------------------------------------------------------
# Bootstrap — 从 chatgpt.com 首页获取 PoW 资源
# ---------------------------------------------------------------------------


def _bootstrap_pow_resources(
    session: Any,
    config: dict[str, Any],
) -> tuple[list[str], str]:
    """从 chatgpt.com 首页获取 script sources 和 data_build 信息。

    使用模块级缓存避免频繁请求首页，缓存有效期由 ``_BOOTSTRAP_TTL_SEC`` 控制。
    """
    global _bootstrap_script_sources, _bootstrap_data_build, _bootstrap_timestamp

    now = time.monotonic()
    with _bootstrap_cache_lock:
        if _bootstrap_script_sources and (now - _bootstrap_timestamp) < _BOOTSTRAP_TTL_SEC:
            return list(_bootstrap_script_sources), _bootstrap_data_build
        cached_sources = list(_bootstrap_script_sources)
        cached_data_build = _bootstrap_data_build

    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    try:
        resp = session.get(
            f"{base_url}/",
            headers={
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,image/avif,image/webp,*/*;q=0.8"
                ),
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            },
            timeout=float(config.get("bootstrap_timeout_sec") or config.get("warmup_timeout_sec") or 8),
        )
        resp.raise_for_status()
        sources, data_build = _parse_pow_resources(resp.text)
    except Exception as exc:
        if cached_sources:
            log(f"Bootstrap 获取 PoW 资源失败，将使用缓存资源继续: {exc}")
            sources = cached_sources
            data_build = cached_data_build
        else:
            log(f"Bootstrap 获取 PoW 资源失败，将使用默认 PoW SDK 继续: {exc}")
            sources = [DEFAULT_POW_SCRIPT]
            data_build = ""

    with _bootstrap_cache_lock:
        _bootstrap_script_sources = sources
        _bootstrap_data_build = data_build
        _bootstrap_timestamp = now

    return sources, data_build


# ---------------------------------------------------------------------------
# Sentinel 完整验证链 — prepare → PoW → Turnstile → finalize
# ---------------------------------------------------------------------------


def _fetch_sentinel_token(
    session: Any,
    config: dict[str, Any],
    access_token: str | None,
    device_id: str,
    dynamic_headers: dict[str, str],
) -> bool:
    """执行完整的 Sentinel 验证流程。

    流程：bootstrap → p_token → prepare → PoW → Turnstile → finalize。
    成功时将 sentinel 相关令牌注入 ``dynamic_headers``，返回 True；失败时返回 False。
    """
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    user_agent = str(
        session.headers.get("User-Agent")
        or config.get("user_agent")
        or _BOOTSTRAP_USER_AGENT
    )
    timeout_sec = float(config.get("warmup_timeout_sec") or DEFAULT_CONFIG["warmup_timeout_sec"])

    # 1) Bootstrap：获取 script sources 和 data_build
    script_sources, data_build = _bootstrap_pow_resources(session, config)

    # 2) 构建 p_token（旧版需求令牌）
    p_token = _build_legacy_requirements_token(user_agent, script_sources, data_build)

    # 3) Prepare 阶段
    prepare_headers = build_protocol_headers(
        config, access_token, device_id, dynamic_headers, accept="application/json",
    )
    prepare_headers.update({
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    prepare_path = "/backend-api/sentinel/chat-requirements/prepare"
    prepare_headers["X-OpenAI-Target-Path"] = prepare_path
    prepare_headers["X-OpenAI-Target-Route"] = prepare_path

    resp = session.post(
        f"{base_url}{prepare_path}",
        headers=prepare_headers,
        data=json.dumps({"p": p_token}),
        timeout=timeout_sec,
    )
    resp.raise_for_status()
    data = resp.json()

    prepare_token = str(data.get("prepare_token") or "")
    if not prepare_token:
        log("Sentinel prepare 阶段未返回 prepare_token")
        return False

    # Arkose 校验（暂不支持）
    if (data.get("arkose") or {}).get("required"):
        log("Sentinel 需要 Arkose 验证（暂不支持）")
        return False

    # 4) PoW 求解（如果需要）
    proof_token = ""
    proof_info = data.get("proofofwork") or {}
    if proof_info.get("required"):
        seed = str(proof_info.get("seed") or "")
        difficulty = str(proof_info.get("difficulty") or "")
        if seed and difficulty:
            proof_token = _build_proof_token(
                seed, difficulty, user_agent,
                script_sources=script_sources,
                data_build=data_build,
            )
            log(f"PoW 求解完成: difficulty={difficulty}")

    # 5) Turnstile 求解（如果需要）
    turnstile_token = ""
    turnstile_info = data.get("turnstile") or {}
    if turnstile_info.get("required") and turnstile_info.get("dx"):
        turnstile_token = _solve_turnstile_token(turnstile_info["dx"], p_token) or ""
        if turnstile_token:
            log("Turnstile 解算完成")
        else:
            log("Turnstile 解算未通过，将尝试不带 Turnstile token 完成 Sentinel 验证")

    # 6) Finalize 阶段
    finalize_path = "/backend-api/sentinel/chat-requirements/finalize"
    finalize_headers = dict(prepare_headers)
    finalize_headers["X-OpenAI-Target-Path"] = finalize_path
    finalize_headers["X-OpenAI-Target-Route"] = finalize_path

    resp = session.post(
        f"{base_url}{finalize_path}",
        headers=finalize_headers,
        data=json.dumps({
            "prepare_token": prepare_token,
            "proof_token": proof_token,
            "turnstile_token": turnstile_token,
        }),
        timeout=timeout_sec,
    )
    resp.raise_for_status()
    data = resp.json()

    # 7) 提取 sentinel token 并注入 dynamic_headers
    sentinel_token = str(data.get("token") or "")
    if not sentinel_token:
        log("Sentinel finalize 阶段未返回 token")
        return False

    dynamic_headers["openai-sentinel-chat-requirements-token"] = sentinel_token
    if proof_token:
        dynamic_headers["openai-sentinel-proof-token"] = proof_token
    if turnstile_token:
        dynamic_headers["openai-sentinel-turnstile-token"] = turnstile_token
    so_token = str(data.get("so_token") or "")
    if so_token:
        dynamic_headers["openai-sentinel-so-token"] = so_token
    conduit_token = str(data.get("conduit_token") or "")
    if conduit_token:
        dynamic_headers["x-conduit-token"] = conduit_token

    if turnstile_info.get("required") and not turnstile_token:
        log("Sentinel 验证完成（服务端接受了无 Turnstile token 的兜底结果）")
    else:
        log("Sentinel 验证完成")
    return True


def _legacy_warmup_chat_requirements(
    session: Any,
    config: dict[str, Any],
    access_token: str | None,
    device_id: str,
    dynamic_headers: dict[str, str],
) -> None:
    """旧版简化预热：对多个端点发送空 POST，从响应中收集 sentinel 令牌。"""
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    headers = build_protocol_headers(
        config, access_token, device_id, dynamic_headers, accept="application/json",
    )
    headers.update({"Content-Type": "application/json", "Accept": "application/json"})
    timeout_sec = float(config.get("warmup_timeout_sec") or DEFAULT_CONFIG["warmup_timeout_sec"])
    for path in (
        "/backend-api/f/conversation/prepare",
        "/backend-api/sentinel/heartbeat",
        "/backend-api/sentinel/chat-requirements/prepare",
        "/backend-api/sentinel/chat-requirements/finalize",
        "/backend-api/sentinel/req",
    ):
        try:
            resp = session.post(
                f"{base_url}{path}", headers=headers, data="{}", timeout=timeout_sec,
            )
        except NETWORK_REQUEST_EXCEPTIONS as exc:
            if is_upstream_connectivity_error(exc):
                raise_upstream_network_error(exc)
            continue
        except Exception:
            continue
        _merge_dynamic_headers_from_response(dynamic_headers, resp)


def warmup_chat_requirements(
    session: Any,
    config: dict[str, Any],
    access_token: str | None,
    device_id: str,
    dynamic_headers: dict[str, str],
) -> None:
    """获取 ChatGPT Sentinel 验证令牌。

    必须完成 prepare → PoW → Turnstile → finalize 验证链。
    旧版简化预热在当前 ChatGPT Web 流程中已经无法可靠兜底，失败时直接抛出友好错误。
    """
    try:
        if _fetch_sentinel_token(session, config, access_token, device_id, dynamic_headers):
            return
        raise ChatGPTWebError(
            "ChatGPT Web Sentinel 验证失败，未取得有效验证令牌。请检查代理/网络，或刷新浏览器后重新导出 Cookie/请求头。",
            status_code=502,
            error_code="challenge_required",
        )
    except ChatGPTWebError as exc:
        if is_upstream_connectivity_error(exc):
            raise_upstream_network_error(exc)
        raise
    except Exception as exc:
        if is_upstream_connectivity_error(exc):
            raise_upstream_network_error(exc)
        raise ChatGPTWebError(
            f"ChatGPT Web Sentinel 验证失败：{exc}",
            status_code=502,
            error_code="challenge_required",
        ) from exc


def _merge_dynamic_headers_from_response(dynamic_headers: dict[str, str], response: Any) -> None:
    for key in SENTINEL_HEADER_NAMES:
        try:
            value = response.headers.get(key)
        except Exception:
            value = None
        if value:
            dynamic_headers[key] = str(value)
    try:
        data = response.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        return
    for key, header_name in SENTINEL_JSON_TOKEN_FIELDS.items():
        value = _json_value(data, key, header_name)
        if value:
            dynamic_headers[header_name] = value


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _content_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        content_type = str(value.get("content_type") or value.get("type") or "")
        if content_type in NON_TEXT_TYPES:
            return ""
        for key in ("text", "value", "content"):
            item = value.get(key)
            if isinstance(item, str) and item:
                return item
        for key in RICH_TEXT_CONTAINER_KEYS:
            nested = value.get(key)
            if isinstance(nested, (dict, list, str)):
                text = _content_text(nested)
                if text:
                    return text
        image = value.get("image_url")
        image_url = ""
        if isinstance(image, dict):
            image_url = str(image.get("url") or "")
        elif isinstance(image, str):
            image_url = image
        if image_url:
            if image_url.lower().startswith("data:image/"):
                return ""
            return f"[image: {image_url}]"
    return ""


def _image_url_from_part(value: dict[str, Any]) -> str:
    content_type = str(value.get("content_type") or value.get("type") or "").strip().lower()
    image = value.get("image_url")
    if isinstance(image, dict):
        for key in ("url", "image_url", "file_data", "data"):
            item = image.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
    elif isinstance(image, str) and image.strip():
        return image.strip()
    if content_type not in {"image_url", "input_image"}:
        return ""
    for key in ("url", "image", "file_data", "data"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _walk_image_urls(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        text = value.strip()
        if text.lower().startswith("data:image/"):
            yield text
        return
    if isinstance(value, list):
        for item in value:
            yield from _walk_image_urls(item)
        return
    if not isinstance(value, dict):
        return
    image_url = _image_url_from_part(value)
    if image_url:
        yield image_url
    nested = value.get("content")
    if isinstance(nested, (dict, list)):
        yield from _walk_image_urls(nested)
    parts = value.get("parts")
    if isinstance(parts, list):
        yield from _walk_image_urls(parts)


_IMAGE_REQUEST_SOURCE_KEYS = (
    "attachments",
    "image",
    "images",
    "input_image",
    "input_images",
    "input",
    "content",
    "messages",
)

_IMAGE_REQUEST_NESTED_KEYS = (
    "attachments",
    "content",
    "data",
    "image",
    "image_url",
    "images",
    "input",
    "input_image",
    "input_images",
    "messages",
    "parts",
    "source",
    "url",
)


def _image_mime_from_mapping(value: dict[str, Any], fallback: str = "image/png") -> str:
    for key in ("mime_type", "mimeType", "media_type", "content_type", "type"):
        item = value.get(key)
        if isinstance(item, str):
            normalized = item.split(";", 1)[0].strip().lower()
            if normalized.startswith("image/"):
                return normalized
    return fallback


def _data_image_url_from_base64_field(value: dict[str, Any]) -> str:
    for key in ("b64_json", "base64", "base64_data"):
        item = value.get(key)
        if not isinstance(item, str):
            continue
        encoded = re.sub(r"\s+", "", item.strip())
        if encoded:
            return f"data:{_image_mime_from_mapping(value)};base64,{encoded}"
    return ""


def _iter_request_image_urls(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        text = value.strip()
        if text.lower().startswith("data:image/"):
            yield text
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_request_image_urls(item)
        return
    if not isinstance(value, dict):
        return

    image_url = _image_url_from_part(value)
    if image_url:
        yield image_url

    data_url = _data_image_url_from_base64_field(value)
    if data_url:
        yield data_url

    for key in _IMAGE_REQUEST_NESTED_KEYS:
        nested = value.get(key)
        if isinstance(nested, (dict, list, str)):
            yield from _iter_request_image_urls(nested)


def _image_uploads_from_url_candidates(image_urls: Iterable[str]) -> list[ChatGPTWebImageUpload]:
    uploads: list[ChatGPTWebImageUpload] = []
    seen_images: set[tuple[str, str]] = set()
    next_index = 1
    for image_url in image_urls:
        parsed = _parse_data_image_url(image_url)
        if parsed is None:
            continue
        mime_type, data = parsed
        image_key = (mime_type, hashlib.sha256(data).hexdigest())
        if image_key in seen_images:
            continue
        seen_images.add(image_key)
        width, height = _sniff_image_size(mime_type, data)
        uploads.append(
            ChatGPTWebImageUpload(
                filename=f"image-{next_index}{_mime_extension(mime_type)}",
                mime_type=mime_type,
                data=data,
                width=width,
                height=height,
            )
        )
        next_index += 1
    return uploads


def _image_uploads_from_request_body(body: dict[str, Any]) -> list[ChatGPTWebImageUpload]:
    if not isinstance(body, dict):
        return []
    image_urls: list[str] = []
    for key in _IMAGE_REQUEST_SOURCE_KEYS:
        if key in body:
            image_urls.extend(_iter_request_image_urls(body.get(key)))
    return _image_uploads_from_url_candidates(image_urls)


def _image_request_source_fields(body: dict[str, Any]) -> list[str]:
    if not isinstance(body, dict):
        return []
    return [key for key in _IMAGE_REQUEST_SOURCE_KEYS if key in body]


def _parse_data_image_url(url: str) -> tuple[str, bytes] | None:
    text = str(url or "").strip()
    if not text.lower().startswith("data:image/") or "," not in text:
        return None
    header, payload = text.split(",", 1)
    if ";base64" not in header.lower():
        raise ChatGPTWebError("暂不支持非 base64 的图片 data URL。")
    mime_type = header[5:].split(";", 1)[0].strip().lower() or "image/png"
    encoded = payload.strip()
    if "%" in encoded:
        try:
            encoded = unquote_to_bytes(encoded).decode("ascii")
        except Exception as exc:
            raise ChatGPTWebError(f"图片 data URL URL 解码失败: {exc}") from exc
    encoded = re.sub(r"\s+", "", encoded)
    try:
        data = base64.b64decode(encoded, validate=False)
    except Exception as exc:
        raise ChatGPTWebError(f"图片 data URL Base64 解码失败: {exc}") from exc
    if not data:
        raise ChatGPTWebError("图片 data URL 解码后为空。")
    return mime_type, data


def _mime_extension(mime_type: str) -> str:
    normalized = str(mime_type or "").strip().lower()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/svg+xml": ".svg",
    }
    return mapping.get(normalized, ".bin")


def _sniff_png_size(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def _sniff_gif_size(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 10 or data[:6] not in {b"GIF87a", b"GIF89a"}:
        return None, None
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    return width, height


def _sniff_jpeg_size(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None, None
    index = 2
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3,
        0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB,
        0xCD, 0xCE, 0xCF,
    }
    while index + 1 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            break
        block_length = int.from_bytes(data[index:index + 2], "big")
        index += 2
        if block_length < 2 or index + block_length - 2 > len(data):
            break
        if marker in sof_markers and index + 5 <= len(data):
            height = int.from_bytes(data[index + 1:index + 3], "big")
            width = int.from_bytes(data[index + 3:index + 5], "big")
            return width, height
        index += block_length - 2
    return None, None


def _sniff_webp_size(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None, None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8 " and len(data) >= 30:
        start = 20
        if data[start + 3:start + 6] == b"\x9d\x01\x2a":
            width = int.from_bytes(data[start + 6:start + 8], "little") & 0x3FFF
            height = int.from_bytes(data[start + 8:start + 10], "little") & 0x3FFF
            return width, height
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return width, height
    return None, None


def _sniff_image_size(mime_type: str, data: bytes) -> tuple[int | None, int | None]:
    normalized = str(mime_type or "").strip().lower()
    if normalized == "image/png":
        return _sniff_png_size(data)
    if normalized in {"image/jpeg", "image/jpg"}:
        return _sniff_jpeg_size(data)
    if normalized == "image/gif":
        return _sniff_gif_size(data)
    if normalized == "image/webp":
        return _sniff_webp_size(data)
    return None, None


def _extract_message_images(messages: list[dict[str, Any]]) -> list[ChatGPTWebImageUpload]:
    uploads: list[ChatGPTWebImageUpload] = []
    next_index = 1
    for message in messages:
        if not isinstance(message, dict):
            continue
        for image_url in _walk_image_urls(message.get("content")):
            parsed = _parse_data_image_url(image_url)
            if parsed is None:
                continue
            mime_type, data = parsed
            width, height = _sniff_image_size(mime_type, data)
            uploads.append(
                ChatGPTWebImageUpload(
                    filename=f"image-{next_index}{_mime_extension(mime_type)}",
                    mime_type=mime_type,
                    data=data,
                    width=width,
                    height=height,
                )
            )
            next_index += 1
    return uploads


def messages_to_prompt(messages: list[dict[str, Any]], *, allow_empty: bool = False) -> str:
    normalized: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").strip().lower() or "user"
        text = _content_text(message.get("content")).strip()
        if not text:
            continue
        normalized.append((role, text))
    if not normalized:
        if allow_empty:
            return ""
        raise ChatGPTWebError("请求中没有可发送给 ChatGPT Web 的文本内容。")
    if len(normalized) == 1 and normalized[0][0] == "user":
        return normalized[0][1]

    labels = {
        "system": "System",
        "developer": "Developer",
        "assistant": "Assistant",
        "tool": "Tool",
        "user": "User",
    }
    return "\n\n".join(f"{labels.get(role, role.title())}: {text}" for role, text in normalized)


def build_body(
    prompt: str,
    config: dict[str, Any],
    *,
    conversation_id: str | None = None,
    parent_message_id: str | None = None,
    model: str | None = None,
    uploaded_images: Sequence[ChatGPTWebUploadedImage] | None = None,
) -> dict[str, Any]:
    model_id = normalize_model(model or str(config.get("model") or DEFAULT_MODEL))
    user_message: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "author": {"role": "user"},
        "content": {"content_type": "text", "parts": [prompt]},
    }
    if uploaded_images:
        image_parts: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        for item in uploaded_images:
            image_part = {
                "asset_pointer": f"file-service://{item.file_id}",
                "size_bytes": int(item.size_bytes),
            }
            if item.width is not None:
                image_part["width"] = int(item.width)
            if item.height is not None:
                image_part["height"] = int(item.height)
            image_parts.append(image_part)
            attachment = {
                "id": item.file_id,
                "mimeType": item.mime_type,
                "name": item.filename,
                "size": int(item.size_bytes),
            }
            if item.width is not None:
                attachment["width"] = int(item.width)
            if item.height is not None:
                attachment["height"] = int(item.height)
            attachments.append(attachment)
        parts: list[Any] = list(image_parts)
        if prompt:
            parts.append(prompt)
        user_message["content"] = {"content_type": "multimodal_text", "parts": parts}
        user_message["metadata"] = {
            "attachments": attachments,
            "serialization_metadata": {"custom_symbol_offsets": []},
        }
        user_message["create_time"] = time.time()
    body: dict[str, Any] = {
        "action": "next",
        "messages": [user_message],
        "conversation_id": conversation_id,
        "parent_message_id": parent_message_id or str(uuid.uuid4()),
        "model": model_id,
        "timezone_offset_min": int(config.get("timezone_offset_min", -480)),
        "timezone": str(config.get("timezone") or "Asia/Shanghai"),
        "history_and_training_disabled": bool(config.get("chatgpt_history_and_training_disabled", False)),
        "conversation_mode": {"kind": "primary_assistant", "plugin_ids": None},
        "force_paragen": False,
        "force_paragen_model_slug": "",
        "force_rate_limit": False,
        "reset_rate_limits": False,
        "force_use_sse": True,
    }
    if model_id == "gpt-5-5-thinking":
        body.update(
            {
                "thinking_effort": "extended",
                "supports_buffering": True,
                "supported_encodings": ["v1"],
                "enable_message_followups": True,
                "paragen_cot_summary_display_override": "allow",
                "force_parallel_switch": "auto",
                "client_contextual_info": {
                    "is_dark_mode": False,
                    "time_since_loaded": 10,
                    "page_height": 900,
                    "page_width": 1440,
                    "pixel_ratio": 1,
                    "screen_height": 1080,
                    "screen_width": 1920,
                    "app_name": "chatgpt.com",
                },
            }
        )
    return body


def _is_chatgpt_web_image_model(model: str | None) -> bool:
    return str(model or "").strip().lower() in CHATGPT_WEB_IMAGE_MODELS


def _chatgpt_web_image_model_slug(model: str | None) -> str:
    normalized = str(model or "").strip().lower()
    if normalized.endswith("codex-gpt-image-2"):
        return "codex-gpt-image-2"
    return DEFAULT_MODEL


def _prepare_image_conversation(
    session: Any,
    config: dict[str, Any],
    prompt: str,
    access_token: str | None,
    device_id: str,
    dynamic_headers: dict[str, str],
    model: str | None,
    uploaded_images: Sequence[ChatGPTWebUploadedImage] | None = None,
) -> str:
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    path = "/backend-api/f/conversation/prepare"
    headers = build_protocol_headers(
        config,
        access_token,
        device_id,
        dynamic_headers,
        accept="application/json",
        target_path=path,
    )
    content, metadata = _image_generation_message_content_and_metadata(prompt, uploaded_images)
    partial_query: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "author": {"role": "user"},
        "content": content,
    }
    if metadata:
        partial_query["metadata"] = metadata
    payload = {
        "action": "next",
        "fork_from_shared_post": False,
        "parent_message_id": str(uuid.uuid4()),
        "model": _chatgpt_web_image_model_slug(model),
        "client_prepare_state": "success",
        "timezone_offset_min": int(config.get("timezone_offset_min", -480)),
        "timezone": str(config.get("timezone") or "Asia/Shanghai"),
        "conversation_mode": {"kind": "primary_assistant"},
        "system_hints": ["picture_v2"],
        "partial_query": partial_query,
        "supports_buffering": True,
        "supported_encodings": ["v1"],
        "client_contextual_info": {"app_name": "chatgpt.com"},
    }
    response = session.post(
        f"{base_url}{path}",
        headers=headers,
        data=json.dumps(payload, ensure_ascii=False),
        timeout=float(config.get("image_prepare_timeout_sec") or 60),
    )
    _merge_dynamic_headers_from_response(dynamic_headers, response)
    data = _json_response_object(response, "ChatGPT 图片 prepare")
    conduit_token = str(data.get("conduit_token") or "").strip()
    if not conduit_token:
        raise ChatGPTWebError("ChatGPT 图片 prepare 未返回 conduit_token。")
    return conduit_token


def _image_generation_message_content_and_metadata(
    prompt: str,
    uploaded_images: Sequence[ChatGPTWebUploadedImage] | None = None,
    *,
    metadata_base: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    uploaded_images = uploaded_images or []
    metadata = dict(metadata_base or {})
    if not uploaded_images:
        return {"content_type": "text", "parts": [prompt]}, metadata

    image_parts: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    for item in uploaded_images:
        image_part: dict[str, Any] = {
            "asset_pointer": f"file-service://{item.file_id}",
            "size_bytes": int(item.size_bytes),
        }
        if item.width is not None:
            image_part["width"] = int(item.width)
        if item.height is not None:
            image_part["height"] = int(item.height)
        image_parts.append(image_part)

        attachment: dict[str, Any] = {
            "id": item.file_id,
            "mimeType": item.mime_type,
            "name": item.filename,
            "size": int(item.size_bytes),
        }
        if item.width is not None:
            attachment["width"] = int(item.width)
        if item.height is not None:
            attachment["height"] = int(item.height)
        attachments.append(attachment)

    parts: list[Any] = list(image_parts)
    if prompt:
        parts.append(prompt)
    metadata["attachments"] = attachments
    metadata.setdefault("serialization_metadata", {"custom_symbol_offsets": []})
    return {"content_type": "multimodal_text", "parts": parts}, metadata


def _build_image_generation_body(
    prompt: str,
    config: dict[str, Any],
    model: str | None,
    uploaded_images: Sequence[ChatGPTWebUploadedImage] | None = None,
) -> dict[str, Any]:
    metadata_base: dict[str, Any] = {
        "developer_mode_connector_ids": [],
        "selected_github_repos": [],
        "selected_all_github_repos": False,
        "system_hints": ["picture_v2"],
        "serialization_metadata": {"custom_symbol_offsets": []},
    }
    content, metadata = _image_generation_message_content_and_metadata(
        prompt,
        uploaded_images,
        metadata_base=metadata_base,
    )
    return {
        "action": "next",
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "author": {"role": "user"},
                "create_time": time.time(),
                "content": content,
                "metadata": metadata,
            }
        ],
        "parent_message_id": str(uuid.uuid4()),
        "model": _chatgpt_web_image_model_slug(model),
        "client_prepare_state": "sent",
        "timezone_offset_min": int(config.get("timezone_offset_min", -480)),
        "timezone": str(config.get("timezone") or "Asia/Shanghai"),
        "conversation_mode": {"kind": "primary_assistant"},
        "enable_message_followups": True,
        "system_hints": ["picture_v2"],
        "supports_buffering": True,
        "supported_encodings": ["v1"],
        "client_contextual_info": {
            "is_dark_mode": False,
            "time_since_loaded": 1200,
            "page_height": 1072,
            "page_width": 1724,
            "pixel_ratio": 1.2,
            "screen_height": 1440,
            "screen_width": 2560,
            "app_name": "chatgpt.com",
        },
        "paragen_cot_summary_display_override": "allow",
        "force_parallel_switch": "auto",
    }


def _request_image_generation(
    session: Any,
    config: dict[str, Any],
    body: dict[str, Any],
    access_token: str | None,
    device_id: str,
    dynamic_headers: dict[str, str],
    conduit_token: str,
) -> Any:
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    path = "/backend-api/f/conversation"
    headers = build_protocol_headers(
        config,
        access_token,
        device_id,
        dynamic_headers,
        accept="text/event-stream",
        target_path=path,
    )
    headers["x-conduit-token"] = conduit_token
    headers["X-Conduit-Token"] = conduit_token
    headers["x-oai-turn-trace-id"] = str(uuid.uuid4())
    return session.post(
        f"{base_url}{path}",
        headers=headers,
        data=json.dumps(body, ensure_ascii=False),
        timeout=_upstream_request_timeout(config),
        stream=True,
    )


_DATA_IMAGE_REFERENCE_RE = re.compile(r"(data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+)")
_MARKDOWN_IMAGE_REFERENCE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def _image_prompt_from_messages(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or item.get("content_type") or "").strip().lower() in {"text", "input_text"}:
                    text = str(item.get("text") or item.get("content") or "").strip()
                    if text:
                        parts.append(text)
            if parts:
                return "\n".join(parts).strip()
    return messages_to_prompt(messages)


def _file_uri_to_bytes(url: str) -> bytes | None:
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() != "file":
            return None
        path_text = unquote(parsed.path or "")
        if re.match(r"^/[A-Za-z]:/", path_text):
            path_text = path_text[1:]
        path = Path(path_text)
        if path.is_file():
            return path.read_bytes()
    except Exception:
        return None
    return None


def _image_items_from_markdown(text: str, prompt: str, response_format: str) -> list[dict[str, str]]:
    candidates: list[str] = []
    for match in _MARKDOWN_IMAGE_REFERENCE_RE.finditer(str(text or "")):
        src = match.group(1).strip()
        if src:
            _add_unique_text(candidates, src)
    for match in _DATA_IMAGE_REFERENCE_RE.finditer(str(text or "")):
        _add_unique_text(candidates, re.sub(r"\s+", "", match.group(1)))

    items: list[dict[str, str]] = []
    for src in candidates:
        image_bytes: bytes | None = None
        data_url = src
        if src.lower().startswith("data:image/"):
            parsed = _parse_data_image_url(src)
            if parsed is None:
                continue
            _mime, image_bytes = parsed
        elif src.lower().startswith("file://"):
            image_bytes = _file_uri_to_bytes(src)
            if image_bytes:
                data_url = _data_image_url(_image_mime_type_from_bytes(image_bytes), image_bytes)
        if not image_bytes:
            continue
        b64_json = base64.b64encode(image_bytes).decode("ascii")
        item = {"revised_prompt": prompt}
        if response_format == "url":
            item["url"] = data_url
        else:
            item["b64_json"] = b64_json
            item["url"] = data_url
        items.append(item)
    return items


def generate_chatgpt_web_image(
    prompt: str,
    model: str | None = None,
    request_options: dict[str, Any] | None = None,
    *,
    image_uploads: Sequence[ChatGPTWebImageUpload] | None = None,
) -> ChatGPTWebCompletion:
    if not str(prompt or "").strip():
        raise ChatGPTWebError("生图提示词为空。", status_code=400, error_code="invalid_request_error")
    options = dict(request_options or {})
    options.setdefault("request_timeout_sec", max(float(options.get("request_timeout_sec") or 0), 300.0))
    image_model = str(model or options.get("model") or "gpt-image-2").strip() or "gpt-image-2"
    if not _is_chatgpt_web_image_model(image_model):
        image_model = "gpt-image-2"
    if image_uploads is None:
        image_uploads = _image_uploads_from_request_body(options)

    prepared = _prepare_chatgpt_request([{"role": "user", "content": prompt}], image_model, options)
    session = prepared.session
    config = prepared.config
    access_token = prepared.access_token
    device_id = prepared.device_id
    dynamic_headers = prepared.dynamic_headers
    response: Any | None = None
    try:
        warmup_chat_requirements(session, config, access_token, device_id, dynamic_headers)
        uploaded_images = _upload_chatgpt_images(
            session,
            config,
            access_token,
            device_id,
            dynamic_headers,
            list(image_uploads or []),
        )
        log(
            "ChatGPT Web picture_v2 附件上传完成: "
            f"image_uploads={len(image_uploads or [])}, uploaded_images={len(uploaded_images)}"
        )
        conduit_token = _prepare_image_conversation(
            session,
            config,
            prompt,
            access_token,
            device_id,
            dynamic_headers,
            image_model,
            uploaded_images,
        )
        body = _build_image_generation_body(prompt, config, image_model, uploaded_images)
        log(
            "准备请求 ChatGPT Web picture_v2 生图: "
            f"model={image_model}, tool_model={_chatgpt_web_image_model_slug(image_model)}, prompt_len={len(prompt)}"
        )
        response = _request_image_generation(
            session,
            config,
            body,
            access_token,
            device_id,
            dynamic_headers,
            conduit_token,
        )
        if not getattr(response, "ok", False):
            raise _upstream_error_from_response(response)
        context: dict[str, Any] = {}
        text, conversation_id, message_id, _handoff = parse_sse_events(iter_sse_lines(response), context)
        if text:
            text = _inline_chatgpt_file_images(text, session, config, access_token, dynamic_headers, conversation_id=conversation_id)
        if not _contains_inline_image(text) and conversation_id:
            fetched_text, fetched_msg_id = _fetch_conversation_with_retry(
                session,
                config,
                conversation_id,
                access_token,
                dynamic_headers,
                user_message_id=body["messages"][0].get("id"),
                attempts=max(_fallback_fetch_attempts(config), 60),
                interval_sec=max(_fallback_fetch_interval_sec(config), 5.0),
                stable_after_text_attempts=1,
            )
            if fetched_text:
                text = fetched_text
                message_id = fetched_msg_id or message_id
        if not _contains_inline_image(text):
            raise ChatGPTWebError("ChatGPT Web 生图完成，但未能解析到原图。", status_code=502, error_code="image_result_missing")
        return ChatGPTWebCompletion(
            text=str(text or ""),
            model=image_model,
            conversation_id=conversation_id,
            message_id=message_id,
        )
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        session.close()


def openai_image_generation_response(body: dict[str, Any]) -> dict[str, Any]:
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ChatGPTWebError("缺少 prompt。", status_code=400, error_code="invalid_request_error")
    response_format = str(body.get("response_format") or "b64_json").strip().lower()
    if response_format not in {"b64_json", "url"}:
        response_format = "b64_json"
    try:
        n = max(1, min(4, int(float(body.get("n") or 1))))
    except (TypeError, ValueError):
        n = 1
    model = str(body.get("model") or "gpt-image-2").strip() or "gpt-image-2"

    image_uploads = _image_uploads_from_request_body(body)
    log(
        "Images Generations 附件图片提取: "
        f"fields={','.join(_image_request_source_fields(body)) or '-'}, uploads={len(image_uploads)}"
    )

    request_options = dict(body)
    data: list[dict[str, str]] = []
    created = int(time.time())
    for _index in range(n):
        result = generate_chatgpt_web_image(prompt, model, request_options, image_uploads=image_uploads)
        data.extend(_image_items_from_markdown(result.text, prompt, response_format))
    if not data:
        raise ChatGPTWebError("生图服务返回成功，但响应中没有图片数据。", status_code=502, error_code="image_result_missing")
    return {"created": created, "data": data}


def _upstream_request_timeout(config: dict[str, Any]) -> tuple[float, float]:
    total = float(config.get("request_timeout_sec") or DEFAULT_CONFIG["request_timeout_sec"])
    connect = float(config.get("request_connect_timeout_sec") or DEFAULT_CONFIG["request_connect_timeout_sec"])
    connect = max(0.5, min(connect, total))
    return connect, total


def request_conversation(
    session: Any,
    config: dict[str, Any],
    body: dict[str, Any],
    access_token: str | None,
    device_id: str,
    dynamic_headers: dict[str, str],
) -> requests.Response:
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    headers = build_protocol_headers(config, access_token, device_id, dynamic_headers)

    # 临时覆盖移除客户端版本头，让网关退回到传统的 HTTP SSE 模式，避免触发 stream_handoff
    for key in (
        "OAI-Client-Version",
        "OAI-Client-Build-Number",
        "oai-client-version",
        "oai-client-build-number",
    ):
        headers.pop(key, None)

    return session.post(
        f"{base_url}/backend-api/f/conversation",
        headers=headers,
        data=json.dumps(body, ensure_ascii=False),
        timeout=_upstream_request_timeout(config),
        stream=True,
    )


def request_conversation_with_requirements(
    session: Any,
    config: dict[str, Any],
    body: dict[str, Any],
    access_token: str | None,
    device_id: str,
    dynamic_headers: dict[str, str],
) -> Any:
    response = request_conversation(session, config, body, access_token, device_id, dynamic_headers)
    if response.ok:
        return response
    before = dict(dynamic_headers)
    _merge_dynamic_headers_from_response(dynamic_headers, response)
    if dynamic_headers == before:
        return response
    try:
        response.close()
    except Exception:
        pass
    return request_conversation(session, config, body, access_token, device_id, dynamic_headers)


def iter_sse_lines(resp: Any) -> Iterable[str]:
    try:
        iterator = resp.iter_lines(chunk_size=1, decode_unicode=False)
    except TypeError:
        iterator = resp.iter_lines(decode_unicode=False)
    for raw in iterator:
        if raw is None:
            continue
        if isinstance(raw, bytes):
            line = raw.decode("utf-8", errors="replace").strip()
        else:
            line = str(raw).strip()
        if line.startswith("data: "):
            yield line[6:].strip()


_STREAM_SUCCESS_TERMINAL_TYPES = {
    "done",
    "complete",
    "message_stream_complete",
    "conversation_turn_complete",
    "conversation_turn_finished",
}


def _is_stream_success_terminal(value: Any, *, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if isinstance(value, list):
        return any(_is_stream_success_terminal(item, depth=depth + 1) for item in value)
    if not isinstance(value, dict):
        return False
    event_type = str(value.get("type") or "").strip().lower()
    if event_type in _STREAM_SUCCESS_TERMINAL_TYPES:
        return True
    status = str(value.get("status") or "").strip().lower()
    if status == "finished_successfully":
        # 必须排除 thinking、thoughts 等非正文类型
        content = value.get("content") or {}
        content_type = content.get("content_type")
        if content_type not in NON_TEXT_TYPES:
            return True
    elif value.get("end_turn") is True:
        return True
    for key in ("message", "v", "payload", "data", "body", "event", "reply"):
        child = value.get(key)
        if child is not None and _is_stream_success_terminal(child, depth=depth + 1):
            return True
    return False


def _record_stream_context(event: dict[str, Any], context: dict[str, Any] | None) -> None:
    if context is None or not isinstance(event, dict):
        return
    conversation_id = event.get("conversation_id")
    if isinstance(conversation_id, str) and conversation_id:
        context["conversation_id"] = conversation_id
    event_type = str(event.get("type") or "")
    if event_type == "server_ste_metadata":
        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            if isinstance(metadata.get("tool_invoked"), bool):
                context["tool_invoked"] = metadata["tool_invoked"]
            turn_use_case = metadata.get("turn_use_case")
            if isinstance(turn_use_case, str) and turn_use_case.strip():
                context["turn_use_case"] = turn_use_case.strip()
    if _is_stream_success_terminal(event):
        context["stream_terminal_seen"] = True
        context["message_finished_successfully"] = True
    for candidate in (event, event.get("v")):
        if not isinstance(candidate, dict):
            continue
        msg = candidate.get("message")
        if not isinstance(msg, dict):
            continue
        status = str(msg.get("status") or "").strip().lower()
        if status:
            context["message_status"] = status
            if status == "finished_successfully":
                # 只有非思维链等类型才算真正完成
                msg_content = msg.get("content") or {}
                if msg_content.get("content_type") not in NON_TEXT_TYPES:
                    context["message_finished_successfully"] = True
            elif msg.get("end_turn") is True:
                context["message_finished_successfully"] = True
    if event_type not in ("resume_conversation_token", "stream_handoff"):
        return
    context["handoff"] = True
    context["handoff_event_type"] = event_type
    if event_type == "resume_conversation_token":
        token = event.get("token")
        if isinstance(token, str) and token:
            context["handoff_token"] = token
        kind = event.get("kind")
        if isinstance(kind, str) and kind:
            context["handoff_token_kind"] = kind
    options = event.get("options")
    if isinstance(options, list):
        context["handoff_options"] = options
    turn_exchange_id = event.get("turn_exchange_id")
    if isinstance(turn_exchange_id, str) and turn_exchange_id:
        context["turn_exchange_id"] = turn_exchange_id


def _context_indicates_image_generation(context: dict[str, Any] | None) -> bool:
    if not isinstance(context, dict):
        return False
    turn_use_case = str(context.get("turn_use_case") or "").strip().lower()
    return turn_use_case == "image gen" or (
        bool(context.get("tool_invoked")) and "image" in turn_use_case
    )


def extract_visible_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if (value.strip() or ("\n" in value or "\r" in value)) else []
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            chunks.extend(extract_visible_text(item))
        return chunks
    if not isinstance(value, dict):
        return []

    content_type = str(value.get("content_type") or value.get("type") or value.get("kind") or "")
    if content_type in NON_TEXT_TYPES:
        return []

    chunks: list[str] = []
    for key in ("text", "value"):
        item = value.get(key)
        if isinstance(item, str) and (item.strip() or ("\n" in item or "\r" in item)):
            chunks.append(item)
    for key in RICH_TEXT_CONTAINER_KEYS:
        nested = value.get(key)
        if isinstance(nested, str) and (nested.strip() or ("\n" in nested or "\r" in nested)):
            chunks.append(nested)
        elif isinstance(nested, (dict, list)):
            chunks.extend(extract_visible_text(nested))
    return chunks


_CITATION_MARKER_RE = re.compile(r"\ue200cite\ue202([^\ue201]+)\ue201")
_MANGLED_CITATION_MARKER_RE = re.compile(r"(?:■|\u25a0)?cite(?:☆|\u2606)([a-zA-Z0-9☆\u2606]+)(?:↩|\u21a9)?")
_ENTITY_RE = re.compile(r"(?:■|\u25a0|\ue200)entity(?:☆|\u2606|\ue202)(.*?)(?:↩|\u21a9|\ue201)")
_URL_RE = re.compile(r"(?:■|\u25a0|\ue200)url(?:☆|\u2606|\ue202)([^\ue202\u2606\n]*?)(?:☆|\u2606|\ue202)([^\ue201\u21a9\n]*?)(?:↩|\u21a9|\ue201)")
_ANY_MANGLED_MARKER_RE = re.compile(r"(?:■|\u25a0|\ue200)[a-zA-Z_]+(?:☆|\u2606|\ue202)(.*?)(?:↩|\u21a9|\ue201)")


def _clean_entities_and_urls(text: str) -> str:
    if not text:
        return text

    def _replace_entity(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        try:
            data = json.loads(content)
            if isinstance(data, list):
                if len(data) >= 2:
                    return str(data[1])
                elif len(data) == 1:
                    return str(data[0])
        except Exception:
            pass
        names = re.findall(r'"([^"]+)"', content)
        if names:
            if len(names) >= 2:
                return names[1]
            return names[0]
        return content

    def _replace_url(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        url = match.group(2).strip()
        if label and url:
            return f"[{label} ↗]({url})"
        elif url:
            return f"[{url} ↗]({url})"
        return label or url

    result = _ENTITY_RE.sub(_replace_entity, text)
    result = _URL_RE.sub(_replace_url, result)
    result = _ANY_MANGLED_MARKER_RE.sub("", result)
    return result


_CITATION_TOKEN_RE = re.compile(r"\bturn\d+[a-zA-Z0-9]+\b")
_REFERENCE_URL_KEYS = ("url", "href", "link", "uri", "source_url", "target_url")
_REFERENCE_TITLE_KEYS = ("title", "name", "label", "site_name", "domain", "display_name")
_REFERENCE_ID_KEYS = ("id", "ref_id", "reference_id", "citation_id", "source_id", "marker")
_REFERENCE_MARKER_KEYS = ("matched_text", "marker", "citation_marker", "text")


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_dicts(item)


def _first_string_by_keys(value: dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _citation_token(value: str) -> str:
    text = str(value or "")
    match = _CITATION_MARKER_RE.search(text) or _MANGLED_CITATION_MARKER_RE.search(text)
    if match:
        return match.group(1).strip()
    match = _CITATION_TOKEN_RE.search(text)
    return match.group(0).strip() if match else ""


def _markdown_link(label: str, url: str) -> str:
    clean_label = re.sub(r"\s+", " ", str(label or "").strip()) or str(url or "").strip()
    clean_label = clean_label.replace("[", "(").replace("]", ")")
    clean_url = str(url or "").strip().replace(" ", "%20").replace(")", "%29")
    return f"[{clean_label}]({clean_url})" if clean_url else clean_label


def _collect_citation_references(value: Any) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for item in _walk_dicts(value):
        url = _first_string_by_keys(item, _REFERENCE_URL_KEYS)
        if not url:
            continue
        label = _first_string_by_keys(item, _REFERENCE_TITLE_KEYS) or url
        markers: set[str] = set()
        for key in _REFERENCE_MARKER_KEYS:
            marker = item.get(key)
            if isinstance(marker, str) and marker.strip():
                markers.add(marker.strip())
                token = _citation_token(marker)
                if token:
                    markers.add(token)
        for key in _REFERENCE_ID_KEYS:
            token = _citation_token(str(item.get(key) or ""))
            if token:
                markers.add(token)
        if markers:
            refs.append({"url": url, "label": label, "markers": "\n".join(sorted(markers))})
    return refs


def _apply_citation_references(text: str, references: Sequence[dict[str, str]]) -> str:
    result = str(text or "")
    if not result:
        return result
    token_links: dict[str, str] = {}
    exact_links: dict[str, str] = {}
    for ref in references:
        link = _markdown_link(str(ref.get("label") or ""), str(ref.get("url") or ""))
        if not link:
            continue
        for marker in str(ref.get("markers") or "").splitlines():
            marker = marker.strip()
            if not marker:
                continue
            token = _citation_token(marker)
            if token:
                token_links[token] = link
            if "\ue200cite\ue202" in marker or "cite" in marker:
                exact_links[marker] = link
    for marker, link in sorted(exact_links.items(), key=lambda item: len(item[0]), reverse=True):
        result = result.replace(marker, link)

    def _replace_marker(match: re.Match[str]) -> str:
        token = match.group(1).strip()
        token = token.replace("\u2606", "☆")
        if "☆" in token:
            sub_tokens = [t.strip() for t in token.split("☆") if t.strip()]
            parts = []
            for t in sub_tokens:
                link = token_links.get(t)
                if link:
                    parts.append(link)
            return " ".join(parts) if parts else ""
        return token_links.get(token) or ""

    result = _CITATION_MARKER_RE.sub(_replace_marker, result)
    result = _MANGLED_CITATION_MARKER_RE.sub(_replace_marker, result)
    result = _clean_entities_and_urls(result)
    return result


_IMAGE_URL_EXT_RE = re.compile(r"\.(?:png|jpe?g|webp|gif|bmp|svg)(?:[?#].*)?$", re.IGNORECASE)


def _file_id_from_asset_pointer(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("file-service://"):
        text = text[len("file-service://"):]
    if not text.startswith("file-"):
        return ""
    return text.split("/", 1)[0].split("?", 1)[0].strip()


def _estuary_file_id_from_asset_pointer(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("sediment://"):
        text = text[len("sediment://"):]
    if not text.startswith("file_"):
        return ""
    return text.split("/", 1)[0].split("?", 1)[0].strip()


def _download_clean_url(session: Any, url: str, timeout: float) -> Any:
    # 构造一个临时的、不带默认 headers 和 cookies 的 session 来发起 GET 请求
    # 这样可以防止 Origin, Referer, Cookie 等敏感头被发送到 oaiusercontent.com
    # 从而导致 400 Bad Request 或者 403 Forbidden
    if HAS_CURL_CFFI and curl_requests is not None and isinstance(session, curl_requests.Session):
        try:
            temp_session = curl_requests.Session(impersonate="chrome120")
        except TypeError:
            temp_session = curl_requests.Session()
    else:
        temp_session = requests.Session()
    try:
        temp_session.trust_env = False
    except Exception:
        pass
    if hasattr(session, "proxies") and session.proxies:
        temp_session.proxies.update(session.proxies)
    if _apply_curl_resolve is not None:
        source_proxies = dict(getattr(session, "proxies", {}) or {})
        _apply_curl_resolve(
            temp_session,
            url,
            source_proxies.get("https") or source_proxies.get("http") or "",
        )
    session_headers = getattr(session, "headers", {}) or {}
    ua = session_headers.get("User-Agent")
    if ua:
        temp_session.headers["User-Agent"] = ua
    lang = session_headers.get("Accept-Language")
    if lang:
        temp_session.headers["Accept-Language"] = lang
    return temp_session.get(url, timeout=timeout)


def _download_with_original_session(session: Any, url: str, timeout: float) -> Any:
    # ChatGPT 的附件接口返回的签名地址有时仍依赖当前 web 会话上下文。
    # 参考项目也是直接复用 self.session 下载，避免被上游判定为 file stream access denied。
    return session.get(url, timeout=timeout)


def _chatgpt_file_download_url(file_id: str, base_url: str = "https://chatgpt.com") -> str:
    clean_id = _file_id_from_asset_pointer(file_id)
    if not clean_id:
        return ""
    root = str(base_url or "https://chatgpt.com").rstrip("/")
    return f"{root}/backend-api/files/{clean_id}/download"


def _chatgpt_asset_download_url(file_id: str, base_url: str = "https://chatgpt.com") -> str:
    clean_file_id = _file_id_from_asset_pointer(file_id)
    if clean_file_id:
        return _chatgpt_file_download_url(clean_file_id, base_url)
    clean_estuary_id = _estuary_file_id_from_asset_pointer(file_id)
    if clean_estuary_id:
        return f"sediment://{clean_estuary_id}"
    return ""


_CHATGPT_FILE_DOWNLOAD_RE = re.compile(r"https?://[^)\s]+/backend-api/files/(file-[^/\s)]+)/download")
_CHATGPT_ESTUARY_CONTENT_RE = re.compile(r"(https?://[^)\s]+/backend-api/estuary/content\?[^)\s]*\bid=(file_[^&\s)]+)[^)\s]*)")
_CHATGPT_SEDIMENT_POINTER_RE = re.compile(r"sediment://(file_[A-Za-z0-9_-]+)")
_CHATGPT_FILE_SERVICE_POINTER_RE = re.compile(r"file-service://(file-[A-Za-z0-9_-]+)")


def _image_mime_type_from_bytes(data: bytes, fallback: str = "image/png") -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
        return "image/avif"
    return fallback


def _image_dimensions_from_bytes(data: bytes) -> tuple[int, int] | None:
    body = bytes(data or b"")
    if len(body) >= 24 and body.startswith(b"\x89PNG\r\n\x1a\n"):
        return int.from_bytes(body[16:20], "big"), int.from_bytes(body[20:24], "big")
    if len(body) >= 10 and body.startswith((b"GIF87a", b"GIF89a")):
        return int.from_bytes(body[6:8], "little"), int.from_bytes(body[8:10], "little")
    if len(body) >= 30 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        chunk = body[12:16]
        if chunk == b"VP8 " and len(body) >= 30:
            return int.from_bytes(body[26:28], "little") & 0x3FFF, int.from_bytes(body[28:30], "little") & 0x3FFF
        if chunk == b"VP8L" and len(body) >= 25:
            bits = int.from_bytes(body[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8X" and len(body) >= 30:
            return int.from_bytes(body[24:27], "little") + 1, int.from_bytes(body[27:30], "little") + 1
    if len(body) >= 4 and body.startswith(b"\xff\xd8"):
        idx = 2
        while idx + 9 < len(body):
            if body[idx] != 0xFF:
                idx += 1
                continue
            marker = body[idx + 1]
            idx += 2
            if marker in {0xD8, 0xD9}:
                continue
            if idx + 2 > len(body):
                return None
            segment_len = int.from_bytes(body[idx:idx + 2], "big")
            if segment_len < 2 or idx + segment_len > len(body):
                return None
            if 0xC0 <= marker <= 0xC3 or 0xC5 <= marker <= 0xC7 or 0xC9 <= marker <= 0xCB or 0xCD <= marker <= 0xCF:
                return int.from_bytes(body[idx + 5:idx + 7], "big"), int.from_bytes(body[idx + 3:idx + 5], "big")
            idx += segment_len
    return None


def _looks_like_thumbnail_url(url: str) -> bool:
    decoded = unquote(str(url or "")).lower()
    return any(token in decoded for token in ("thumbnail", "thumb", "#icon", "%23icon", "icon_url"))


def _is_probably_thumbnail_image(data: bytes, url: str = "") -> bool:
    dims = _image_dimensions_from_bytes(data)
    if not dims:
        return False
    width, height = dims
    if width <= 96 and height <= 96:
        return True
    return _looks_like_thumbnail_url(url) and width <= 256 and height <= 256


def _convert_to_png(mime: str, data: bytes) -> tuple[str, bytes]:
    if not data:
        return mime, data
    if mime.lower() == "image/png":
        return mime, data
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        if img.mode not in {"RGB", "RGBA"}:
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        out = io.BytesIO()
        img.save(out, format="PNG")
        return "image/png", out.getvalue()
    except Exception as exc:
        log(f"图片转换为 PNG 失败: {exc}")
        return mime, data


def _image_suffix_from_mime_or_bytes(mime: str, data: bytes) -> str:
    normalized = str(mime or "").split(";", 1)[0].strip().lower()
    if normalized in {"image/jpeg", "image/jpg"} or data.startswith(b"\xff\xd8"):
        return ".jpg"
    if normalized == "image/webp" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return ".webp"
    if normalized == "image/gif" or data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if normalized == "image/bmp" or data.startswith(b"BM"):
        return ".bmp"
    if normalized == "image/avif" or (len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}):
        return ".avif"
    return ".png"


def _generated_image_output_dir(config: dict[str, Any]) -> Path:
    configured = str(config.get("generated_images_dir") or "").strip()
    if configured:
        return Path(configured)
    from deepcat.utils.paths import get_app_dir

    return get_app_dir() / "generated-images"


def _data_image_url(mime: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    safe_mime = str(mime or "image/png").split(";", 1)[0].strip().lower() or "image/png"
    return f"data:{safe_mime};base64,{encoded}"


def _config_bool(config: dict[str, Any], key: str, *, default: bool = False) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "y"}:
            return True
        if normalized in {"0", "false", "no", "off", "n"}:
            return False
    return bool(default)


def _generated_image_url_from_bytes(
    config: dict[str, Any],
    mime: str,
    data: bytes,
    *,
    source_id: str = "",
) -> str:
    if not _config_bool(config, "localize_generated_images", default=False):
        return _data_image_url(mime, data)
    try:
        out_dir = _generated_image_output_dir(config)
        out_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(data).hexdigest()[:16]
        clean_source = re.sub(r"[^0-9A-Za-z_-]+", "_", str(source_id or "").strip()).strip("_")
        stem = f"chatgpt_web_{clean_source}_{digest}" if clean_source else f"chatgpt_web_{digest}"
        path = out_dir / f"{stem}{_image_suffix_from_mime_or_bytes(mime, data)}"
        if not path.exists():
            path.write_bytes(data)
        return path.resolve().as_uri()
    except Exception as exc:
        log(f"保存 ChatGPT 生成图片到本地失败，将回退 data URI: {exc}")
        return _data_image_url(mime, data)


def _image_attachment_timeout_sec(config: dict[str, Any]) -> float:
    fallback = float(config.get("fallback_fetch_timeout_sec") or DEFAULT_CONFIG["fallback_fetch_timeout_sec"])
    configured = float(config.get("image_attachment_timeout_sec") or DEFAULT_CONFIG["image_attachment_timeout_sec"])
    return max(fallback, configured)


def _image_download_timeout_sec(config: dict[str, Any]) -> float:
    fallback = float(config.get("fallback_fetch_timeout_sec") or DEFAULT_CONFIG["fallback_fetch_timeout_sec"])
    configured = float(config.get("image_download_timeout_sec") or DEFAULT_CONFIG["image_download_timeout_sec"])
    return max(fallback, configured)


def _download_chatgpt_file_image(
    session: Any,
    config: dict[str, Any],
    file_id: str,
    access_token: str | None,
    dynamic_headers: dict[str, str] | None,
) -> tuple[str, bytes] | None:
    clean_id = _file_id_from_asset_pointer(file_id)
    if not clean_id:
        return None
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    path = f"/backend-api/files/{clean_id}/download"
    headers = {
        "Accept": "image/png,image/jpeg,image/webp,image/*;q=0.8,*/*;q=0.5",
        "x-openai-target-path": path,
        "x-openai-target-route": path,
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if dynamic_headers:
        headers.update(dynamic_headers)
    headers["x-openai-target-path"] = path
    headers["x-openai-target-route"] = path
    try:
        resp = session.get(
            f"{base_url}{path}",
            headers=headers,
            timeout=_image_attachment_timeout_sec(config),
        )
    except Exception as exc:
        log(f"下载 ChatGPT 生成图片失败: file_id={clean_id}, error={exc}")
        return None
    if not getattr(resp, "ok", False):
        log(f"下载 ChatGPT 生成图片失败: HTTP {getattr(resp, 'status_code', '')} - {str(getattr(resp, 'text', ''))[:300]}")
        return None
    content_type = ""
    try:
        content_type = str(resp.headers.get("content-type") or resp.headers.get("Content-Type") or "")
    except Exception:
        content_type = ""
    body = bytes(getattr(resp, "content", b"") or b"")
    if not body:
        return None
    if "application/json" in content_type.lower():
        try:
            data = resp.json()
        except Exception:
            data = None
        if isinstance(data, dict):
            download_url = str(data.get("download_url") or data.get("url") or "").strip()
            if download_url:
                try:
                    download_resp = _download_with_original_session(session, download_url, _image_download_timeout_sec(config))
                except Exception as exc:
                    log(f"下载 ChatGPT 生成图片签名地址失败: file_id={clean_id}, error={exc}")
                    return None
                if getattr(download_resp, "ok", False):
                    body = bytes(getattr(download_resp, "content", b"") or b"")
                    try:
                        content_type = str(download_resp.headers.get("content-type") or download_resp.headers.get("Content-Type") or "")
                    except Exception:
                        content_type = ""
                else:
                    log(f"下载 ChatGPT 生成图片签名地址响应不OK: HTTP {getattr(download_resp, 'status_code', '')}")
                    return None
            else:
                log(f"下载 ChatGPT 生成图片签名地址解析为空: file_id={clean_id}")
                return None
        else:
            log(f"下载 ChatGPT 生成图片响应 JSON 解析失败: file_id={clean_id}")
            return None
    if not body:
        return None
    mime = content_type.split(";", 1)[0].strip().lower()
    if not mime.startswith("image/"):
        mime = _image_mime_type_from_bytes(body)
    mime, body = _convert_to_png(mime, body)
    return mime, body


def _download_chatgpt_attachment_image(
    session: Any,
    config: dict[str, Any],
    conversation_id: str | None,
    file_id: str,
    access_token: str | None,
    dynamic_headers: dict[str, str] | None,
) -> tuple[str, bytes] | None:
    clean_conversation_id = str(conversation_id or "").strip()
    clean_id = _estuary_file_id_from_asset_pointer(file_id)
    if not clean_conversation_id or not clean_id:
        return None
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    path = f"/backend-api/conversation/{clean_conversation_id}/attachment/{clean_id}/download"
    headers = {
        "Accept": "application/json",
        "x-openai-target-path": path,
        "x-openai-target-route": path,
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if dynamic_headers:
        headers.update(dynamic_headers)
    headers["Accept"] = "application/json"
    headers["x-openai-target-path"] = path
    headers["x-openai-target-route"] = path
    try:
        resp = session.get(
            f"{base_url}{path}",
            headers=headers,
            timeout=_image_attachment_timeout_sec(config),
        )
    except Exception as exc:
        log(f"获取 ChatGPT 附件原图下载地址失败: conversation_id={clean_conversation_id}, file_id={clean_id}, error={exc}")
        return None
    if not getattr(resp, "ok", False):
        log(
            "获取 ChatGPT 附件原图下载地址失败: "
            f"conversation_id={clean_conversation_id}, file_id={clean_id}, "
            f"HTTP {getattr(resp, 'status_code', '')} - {str(getattr(resp, 'text', ''))[:300]}"
        )
        return None
    try:
        content_type = str(resp.headers.get("content-type") or resp.headers.get("Content-Type") or "")
    except Exception:
        content_type = ""
    body = bytes(getattr(resp, "content", b"") or b"")
    if "application/json" in content_type.lower():
        try:
            data = resp.json()
        except Exception as exc:
            log(f"解析 ChatGPT 附件原图下载地址失败: conversation_id={clean_conversation_id}, file_id={clean_id}, error={exc}")
            return None
        if not isinstance(data, dict):
            return None
        download_url = str(data.get("download_url") or data.get("url") or "").strip()
        if not download_url:
            log(f"ChatGPT 附件原图下载地址为空: conversation_id={clean_conversation_id}, file_id={clean_id}")
            return None
        downloaded = _download_chatgpt_download_url_with_session(session, download_url, _image_download_timeout_sec(config))
        if downloaded is None:
            return None
        mime, data_bytes = downloaded
        if _is_probably_thumbnail_image(data_bytes, download_url):
            dims = _image_dimensions_from_bytes(data_bytes)
            log(f"ChatGPT 附件接口返回疑似缩略图，继续尝试其他来源: file_id={clean_id}, size={dims}, url={download_url[:160]}")
            return None
        log(f"已下载 ChatGPT 附件原图: file_id={clean_id}, size={_image_dimensions_from_bytes(data_bytes)}")
        return _convert_to_png(mime, data_bytes)
    if not body:
        return None
    mime = content_type.split(";", 1)[0].strip().lower()
    if not mime.startswith("image/"):
        mime = _image_mime_type_from_bytes(body)
    if _is_probably_thumbnail_image(body, f"{base_url}{path}"):
        dims = _image_dimensions_from_bytes(body)
        log(f"ChatGPT 附件接口直接返回疑似缩略图，继续尝试其他来源: file_id={clean_id}, size={dims}")
        return None
    log(f"已下载 ChatGPT 附件原图: file_id={clean_id}, size={_image_dimensions_from_bytes(body)}")
    return _convert_to_png(mime, body)


def _download_image_url(
    session: Any,
    url: str,
    headers: dict[str, str] | None,
    timeout: float,
) -> tuple[str, bytes] | None:
    try:
        if headers is None:
            resp = _download_clean_url(session, url, timeout)
        else:
            resp = session.get(url, headers=headers, timeout=timeout)
    except Exception as exc:
        log(f"下载 ChatGPT 图片 URL 失败: url={url[:160]}, error={exc}")
        return None
    if not getattr(resp, "ok", False):
        log(f"下载 ChatGPT 图片 URL 失败: HTTP {getattr(resp, 'status_code', '')} - {str(getattr(resp, 'text', ''))[:300]}")
        return None
    body = bytes(getattr(resp, "content", b"") or b"")
    if not body:
        return None
    try:
        content_type = str(resp.headers.get("content-type") or resp.headers.get("Content-Type") or "")
    except Exception:
        content_type = ""
    if "application/json" in content_type.lower():
        return None
    mime = content_type.split(";", 1)[0].strip().lower()
    if not mime.startswith("image/"):
        mime = _image_mime_type_from_bytes(body)
    return mime, body


def _download_chatgpt_download_url_with_session(
    session: Any,
    url: str,
    timeout: float,
) -> tuple[str, bytes] | None:
    try:
        resp = _download_with_original_session(session, url, timeout)
    except Exception as exc:
        log(f"下载 ChatGPT 附件签名图片失败: url={url[:160]}, error={exc}")
        return None
    if not getattr(resp, "ok", False):
        log(f"下载 ChatGPT 附件签名图片失败: HTTP {getattr(resp, 'status_code', '')} - {str(getattr(resp, 'text', ''))[:300]}")
        return None
    body = bytes(getattr(resp, "content", b"") or b"")
    if not body:
        return None
    try:
        content_type = str(resp.headers.get("content-type") or resp.headers.get("Content-Type") or "")
    except Exception:
        content_type = ""
    if "application/json" in content_type.lower():
        log(f"下载 ChatGPT 附件签名图片返回 JSON，未得到图片: url={url[:160]}")
        return None
    mime = content_type.split(";", 1)[0].strip().lower()
    if not mime.startswith("image/"):
        mime = _image_mime_type_from_bytes(body)
    return mime, body


def _estuary_content_url_rank(url: str, clean_id: str) -> tuple[int, int, int, int, int, str]:
    text = str(url or "").strip()
    decoded = unquote(text).lower()
    parsed_id = ""
    profile = ""
    try:
        parsed = urlparse(text)
        query = parse_qs(parsed.query)
        parsed_id = unquote(str((query.get("id") or [""])[0] or ""))
        profile = str((query.get("p") or [""])[0] or "").lower()
    except Exception:
        parsed_id = ""
        profile = ""

    thumbnail_rank = 1 if _looks_like_thumbnail_url(text) else 0
    profile_rank = 0 if profile == "fs" else 1 if profile else 2
    exact_id_rank = 0 if parsed_id == clean_id else 1
    signed_rank = 0 if ("sig=" in decoded and "ts=" in decoded) else 1
    return thumbnail_rank, profile_rank, exact_id_rank, signed_rank, len(text), text


def _collect_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _collect_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _collect_strings(item)


def _signed_estuary_urls_from_images_bootstrap(
    session: Any,
    config: dict[str, Any],
    file_id: str,
    access_token: str | None,
    dynamic_headers: dict[str, str] | None,
    *,
    conversation_id: str | None = None,
) -> list[str]:
    clean_id = _estuary_file_id_from_asset_pointer(file_id)
    if not clean_id:
        return []
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    path = "/backend-api/images/bootstrap"
    headers = {
        "Accept": "application/json",
        "x-openai-target-path": path,
        "x-openai-target-route": path,
    }
    clean_conversation_id = str(conversation_id or "").strip()
    if clean_conversation_id:
        headers["Referer"] = f"{base_url}/c/{clean_conversation_id}"
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if dynamic_headers:
        headers.update(dynamic_headers)
    if clean_conversation_id:
        headers["Referer"] = f"{base_url}/c/{clean_conversation_id}"
    try:
        resp = session.get(
            f"{base_url}{path}",
            headers=headers,
            timeout=float(config.get("fallback_fetch_timeout_sec") or 8),
        )
    except Exception as exc:
        log(f"获取 ChatGPT 图片签名 bootstrap 失败: file_id={clean_id}, error={exc}")
        return []
    if not getattr(resp, "ok", False):
        log(f"获取 ChatGPT 图片签名 bootstrap 失败: HTTP {getattr(resp, 'status_code', '')} - {str(getattr(resp, 'text', ''))[:300]}")
        return []
    try:
        data = resp.json()
    except Exception as exc:
        log(f"解析 ChatGPT 图片签名 bootstrap 失败: file_id={clean_id}, error={exc}")
        return []
    seen: set[str] = set()
    ranked_urls: list[tuple[tuple[int, int, int, int, int, str], str]] = []
    fallback_urls: list[tuple[tuple[int, int, int, int, int, str], str]] = []
    for text in _collect_strings(data):
        if "/backend-api/estuary/content" not in text:
            continue
        url = text.strip()
        if url.startswith("/"):
            url = f"{base_url}{url}"
        if not url.startswith("https://") or url in seen:
            continue
        seen.add(url)
        rank = _estuary_content_url_rank(url, clean_id)
        if clean_id in unquote(url):
            ranked_urls.append((rank, url))
            continue
        decoded = unquote(url).lower()
        if "p=fs" in decoded and "sig=" in decoded and not _looks_like_thumbnail_url(url):
            fallback_urls.append((rank, url))
    urls: list[str] = []
    for _rank, url in sorted(ranked_urls, key=lambda item: item[0]):
        urls.append(url)
    for _rank, url in sorted(fallback_urls, key=lambda item: item[0]):
        urls.append(url)
    return urls


def _download_chatgpt_estuary_image(
    session: Any,
    config: dict[str, Any],
    file_id: str,
    access_token: str | None,
    dynamic_headers: dict[str, str] | None,
    *,
    conversation_id: str | None = None,
    signed_url: str | None = None,
) -> tuple[str, bytes] | None:
    clean_id = _estuary_file_id_from_asset_pointer(file_id)
    if not clean_id:
        return None
    attachment = _download_chatgpt_attachment_image(
        session,
        config,
        conversation_id,
        clean_id,
        access_token,
        dynamic_headers,
    )
    if attachment is not None:
        return attachment
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    estuary_headers = {
        "Accept": "image/png,image/jpeg,image/webp,image/*;q=0.8,*/*;q=0.5",
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "same-origin",
    }
    clean_conversation_id = str(conversation_id or "").strip()
    if clean_conversation_id:
        estuary_headers["Referer"] = f"{base_url}/c/{clean_conversation_id}"
    timeout = _image_download_timeout_sec(config)
    candidate_urls: list[str] = []
    if signed_url and "p=" in signed_url and "ts=" in signed_url:
        candidate_urls.append(signed_url)
    candidate_urls.extend(
        _signed_estuary_urls_from_images_bootstrap(
            session,
            config,
            clean_id,
            access_token,
            dynamic_headers,
            conversation_id=conversation_id,
        )
    )
    candidate_urls = sorted(dict.fromkeys(candidate_urls), key=lambda url: _estuary_content_url_rank(url, clean_id))
    for url in candidate_urls:
        downloaded = _download_image_url(session, url, estuary_headers, timeout)
        if downloaded is not None:
            if _is_probably_thumbnail_image(downloaded[1], url):
                dims = _image_dimensions_from_bytes(downloaded[1])
                log(f"跳过 ChatGPT Estuary 缩略图候选: file_id={clean_id}, size={dims}, url={url[:160]}")
                continue
            return _convert_to_png(downloaded[0], downloaded[1])
    log(f"下载 ChatGPT Estuary 图片失败: file_id={clean_id}, 未找到可用签名 URL")
    return None


def _inline_chatgpt_file_images(
    text: str,
    session: Any,
    config: dict[str, Any],
    access_token: str | None,
    dynamic_headers: dict[str, str] | None,
    conversation_id: str | None = None,
) -> str:
    source = str(text or "")
    if "/backend-api/files/" not in source and "/backend-api/estuary/content" not in source and "sediment://file_" not in source:
        return source

    def replace_file(match: re.Match[str]) -> str:
        url = match.group(0)
        file_id = match.group(1)
        downloaded = _download_chatgpt_file_image(session, config, file_id, access_token, dynamic_headers)
        if downloaded is None:
            return url
        mime, data = downloaded
        return _generated_image_url_from_bytes(config, mime, data, source_id=file_id)

    def replace_estuary(match: re.Match[str]) -> str:
        url = match.group(0)
        file_id = match.group(2)
        downloaded = _download_chatgpt_estuary_image(
            session,
            config,
            file_id,
            access_token,
            dynamic_headers,
            conversation_id=conversation_id,
            signed_url=url,
        )
        if downloaded is None:
            return url
        mime, data = downloaded
        return _generated_image_url_from_bytes(config, mime, data, source_id=file_id)

    def replace_sediment(match: re.Match[str]) -> str:
        file_id = match.group(1)
        downloaded = _download_chatgpt_estuary_image(
            session,
            config,
            file_id,
            access_token,
            dynamic_headers,
            conversation_id=conversation_id,
        )
        if downloaded is None:
            return ""
        mime, data = downloaded
        return _generated_image_url_from_bytes(config, mime, data, source_id=file_id)

    source = _CHATGPT_FILE_DOWNLOAD_RE.sub(replace_file, source)
    source = _CHATGPT_ESTUARY_CONTENT_RE.sub(replace_estuary, source)
    source = _CHATGPT_SEDIMENT_POINTER_RE.sub(replace_sediment, source)
    source = re.sub(r"!\[generated image\]\(\)\s*", "", source)
    return source


def _contains_inline_image(text: str | None) -> bool:
    source = str(text or "")
    lower = source.lower()
    if "data:image/" in lower:
        return True
    for match in _MARKDOWN_IMAGE_REFERENCE_RE.finditer(source):
        src = match.group(1).strip().lower()
        if src.startswith("file://"):
            return True
        if src.startswith(("http://", "https://")):
            if "backend-api/estuary/content" in src or "/backend-api/files/" in src:
                continue
            return True
    return False


def _has_unresolved_chatgpt_image_pointer(text: str | None) -> bool:
    source = str(text or "")
    return (
        "sediment://file_" in source
        or "/backend-api/estuary/content" in source
        or "/backend-api/files/" in source
    )


def _add_unique_text(items: list[str], value: str) -> None:
    clean = str(value or "").strip()
    if clean and clean not in items:
        items.append(clean)


def _image_reference_markdown_from_value(value: Any, *, base_url: str = "https://chatgpt.com") -> str:
    urls = _collect_image_urls(value, base_url=base_url)
    for text in _collect_strings(value):
        for file_id in _CHATGPT_FILE_SERVICE_POINTER_RE.findall(text):
            _add_unique_text(urls, _chatgpt_file_download_url(file_id, base_url))
        for sediment_id in _CHATGPT_SEDIMENT_POINTER_RE.findall(text):
            _add_unique_text(urls, f"sediment://{sediment_id}")
    return "\n".join(f"![generated image]({url})" for url in urls if url)


def _message_has_image_generation_context(message: dict[str, Any], markdown: str) -> bool:
    metadata = message.get("metadata") or {}
    content = message.get("content") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    if isinstance(content, dict) and content.get("content_type") == "image_asset_pointer":
        return True
    if metadata.get("async_task_type") == "image_gen":
        return True
    if any(key in metadata for key in ("image_gen_title", "image_generation", "generated_image")):
        return True
    return bool(markdown)


def _conversation_image_markdown_from_mapping(
    mapping: dict[str, Any],
    *,
    user_message_id: str | None = None,
    base_url: str = "https://chatgpt.com",
) -> tuple[str, str | None]:
    candidates: list[tuple[tuple[int, float, str], str, str | None]] = []
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        if user_message_id and not _is_descendant_of(str(node_id), user_message_id, mapping):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        role = str((message.get("author") or {}).get("role") or "").strip().lower()
        if role == "user":
            continue
        content = message.get("content") or {}
        metadata = message.get("metadata") or {}
        markdown = _image_reference_markdown_from_value(
            {"content": content, "metadata": metadata},
            base_url=base_url,
        )
        if not markdown or not _message_has_image_generation_context(message, markdown):
            continue
        create_time = float(message.get("create_time") or 0.0)
        is_tool_or_image_gen = role == "tool" or (
            isinstance(metadata, dict) and metadata.get("async_task_type") == "image_gen"
        )
        priority = 0 if is_tool_or_image_gen else 1
        msg_id = str(message.get("id") or node_id or "").strip() or None
        candidates.append(((priority, -create_time, str(node_id)), markdown, msg_id))

    lines: list[str] = []
    selected_msg_id: str | None = None
    for _rank, markdown, msg_id in sorted(candidates, key=lambda item: item[0]):
        if selected_msg_id is None and msg_id:
            selected_msg_id = msg_id
        for line in markdown.splitlines():
            _add_unique_text(lines, line)
    return "\n".join(lines), selected_msg_id


def _image_context_hint(value: dict[str, Any]) -> bool:
    content_type = str(value.get("content_type") or value.get("type") or value.get("mimeType") or value.get("mime_type") or "")
    if "image" in content_type.lower():
        return True
    if value.get("asset_pointer") or value.get("image_url") or value.get("download_url"):
        return True
    metadata = value.get("metadata")
    if isinstance(metadata, dict) and any(key in metadata for key in ("dalle", "image", "generated_image", "generation")):
        return True
    return any(key in value for key in ("gen_id", "dalle", "image_asset_pointer", "assetPointer"))


def _collect_image_urls(value: Any, *, base_url: str = "https://chatgpt.com") -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add_url(raw: Any, *, image_hint: bool = False) -> None:
        text = str(raw or "").strip()
        if not text:
            return
        if (
            text.startswith("file-service://")
            or text.startswith("sediment://")
            or text.startswith("file-")
            or text.startswith("file_")
        ):
            text = _chatgpt_asset_download_url(text, base_url)
        if not text:
            return
        lower = text.lower()
        if not (
            lower.startswith("data:image/")
            or lower.startswith("sediment://")
            or lower.startswith(("http://", "https://"))
        ):
            return
        if not (image_hint or lower.startswith(("data:image/", "sediment://")) or _IMAGE_URL_EXT_RE.search(text)):
            return
        if text not in seen:
            seen.add(text)
            found.append(text)

    def visit(item: Any, *, image_hint: bool = False) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child, image_hint=image_hint)
            return
        if not isinstance(item, dict):
            return
        current_hint = image_hint or _image_context_hint(item)
        for key in ("asset_pointer", "assetPointer", "image_asset_pointer", "file_id", "fileId"):
            if key in item:
                add_url(item.get(key), image_hint=True)
        image_url = item.get("image_url")
        if isinstance(image_url, dict):
            visit(image_url, image_hint=True)
        else:
            add_url(image_url, image_hint=True)
        for key in ("download_url", "downloadUrl", "url", "content_url", "contentUrl"):
            if key in item:
                add_url(item.get(key), image_hint=current_hint or key.lower().startswith(("download", "content")))
        for key, child in item.items():
            if key in {
                "asset_pointer",
                "assetPointer",
                "image_asset_pointer",
                "file_id",
                "fileId",
                "image_url",
                "download_url",
                "downloadUrl",
                "url",
                "content_url",
                "contentUrl",
            }:
                continue
            visit(child, image_hint=current_hint)

    visit(value)
    return found


def _image_markdown_from_value(value: Any, *, base_url: str = "https://chatgpt.com") -> str:
    urls = _collect_image_urls(value, base_url=base_url)
    if not urls:
        return ""
    return "\n".join(f"![generated image]({url})" for url in urls)


def extract_content(event: dict[str, Any]) -> str | None:
    message = event.get("message", {})
    if not isinstance(message, dict):
        return None
    content = message.get("content", {})
    if not isinstance(content, dict) or content.get("content_type") in NON_TEXT_TYPES:
        return None
    chunks = extract_visible_text(content.get("parts", [])) or extract_visible_text(content)
    image_markdown = _image_markdown_from_value(message)
    if not chunks and not image_markdown:
        return None
    text = _apply_citation_references("".join(chunks), _collect_citation_references(message)) if chunks else ""
    if image_markdown:
        return f"{text}\n\n{image_markdown}".strip()
    return text


def _is_renderable_chatgpt_message(message: dict[str, Any]) -> bool:
    role = (message.get("author") or {}).get("role")
    if role == "user":
        return False
    if role == "assistant":
        return True
    if role == "tool":
        return bool(_image_markdown_from_value(message))
    return bool(_image_markdown_from_value(message))


def _render_stream_part_value(value: Any, *, base_url: str = "https://chatgpt.com") -> str:
    if isinstance(value, str):
        return value
    image_markdown = _image_markdown_from_value(value, base_url=base_url)
    chunks = extract_visible_text(value)
    text = "".join(chunks)
    if image_markdown:
        return f"{text}\n\n{image_markdown}".strip() if text.strip() else image_markdown
    return text


def _append_stream_part(current: str, part_text: str) -> str:
    if not part_text:
        return current
    if not current:
        return part_text
    if "![generated image]" in part_text:
        return f"{current}\n\n{part_text}"
    return current + part_text


def _merge_stream_snapshot(current: str, snapshot: str) -> str:
    if not snapshot:
        return current
    if not current:
        return snapshot
    if snapshot == current or snapshot in current or current.startswith(snapshot):
        return current
    if snapshot.startswith(current) or current in snapshot:
        return snapshot
    if _looks_like_prefix_suffix_gap(current, snapshot):
        return snapshot
    max_overlap = min(len(current), len(snapshot))
    for size in range(max_overlap, 0, -1):
        if current[-size:] == snapshot[:size]:
            return current + snapshot[size:]
    return current + snapshot


def _append_only_delta(current_text: str, snapshot_text: str) -> str:
    current = str(current_text or "")
    snapshot = str(snapshot_text or "")
    if not snapshot:
        return ""
    if not current:
        return snapshot
    if snapshot.startswith(current):
        return snapshot[len(current):]
    clean_current = _strip_all_markers(current)
    clean_snapshot = _strip_all_markers(snapshot)
    if clean_snapshot.startswith(clean_current) and len(clean_snapshot) >= len(clean_current):
        clean_char_count = len(clean_current)
        clean_idx = 0
        phys_idx = 0
        marker_regexes = (
            _INCOMPLETE_MARKER_RE,
            _CITATION_MARKER_RE,
            _MANGLED_CITATION_MARKER_RE,
            _ENTITY_RE,
            _URL_RE,
            _ANY_MANGLED_MARKER_RE
        )
        while clean_idx < clean_char_count and phys_idx < len(snapshot):
            char = snapshot[phys_idx]
            for regex in marker_regexes:
                match = regex.match(snapshot, phys_idx)
                if match:
                    phys_idx = match.end()
                    break
            else:
                if not char.isspace():
                    clean_idx += 1
                phys_idx += 1
        return snapshot[phys_idx:]
    return ""


_INCOMPLETE_MARKER_RE = re.compile(
    r"(?:"
    r"■[^↩]*|"
    r"\ue200[^\ue201]*|"
    r"■|"
    r"\ue200|"
    r"\b(?:cite|entity|url)\b"
    r")$",
    re.IGNORECASE
)


def _strip_all_markers(text: str) -> str:
    if not text:
        return ""
    res = _INCOMPLETE_MARKER_RE.sub("", text)
    res = _CITATION_MARKER_RE.sub("", res)
    res = _MANGLED_CITATION_MARKER_RE.sub("", res)
    res = _ENTITY_RE.sub("", res)
    res = _URL_RE.sub("", res)
    res = _ANY_MANGLED_MARKER_RE.sub("", res)
    return "".join(res.split())


def _common_prefix_len(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    idx = 0
    while idx < limit and left[idx] == right[idx]:
        idx += 1
    return idx


def _common_suffix_len(left: str, right: str, *, prefix_len: int = 0) -> int:
    limit = min(len(left), len(right)) - max(0, prefix_len)
    idx = 0
    while idx < limit and left[len(left) - 1 - idx] == right[len(right) - 1 - idx]:
        idx += 1
    return idx


def _looks_like_prefix_suffix_gap(current_text: str, snapshot_text: str) -> bool:
    """识别“已输出文本 = 完整文本开头 + 完整文本结尾”的缺口形态。"""
    current_clean = _strip_all_markers(str(current_text or ""))
    snapshot_clean = _strip_all_markers(str(snapshot_text or ""))
    if not current_clean or not snapshot_clean:
        return False
    if len(snapshot_clean) <= len(current_clean):
        return False
    prefix_len = _common_prefix_len(current_clean, snapshot_clean)
    suffix_len = _common_suffix_len(current_clean, snapshot_clean, prefix_len=prefix_len)
    if prefix_len <= 0 or suffix_len <= 0:
        return False
    covered = prefix_len + suffix_len
    min_edge = max(4, min(8, len(current_clean) // 4))
    min_covered = max(12, int(len(current_clean) * 0.65))
    gap_len = len(snapshot_clean) - covered
    return prefix_len >= min_edge and suffix_len >= min_edge and covered >= min_covered and gap_len > 0


def _stream_text_corruption_score(text: str | None) -> int:
    raw = str(text or "")
    if not raw:
        return 0
    score = 0
    if re.search(r"(?<!\n)#\s", raw):
        score += 18
    if "cite" in raw or "turn" in raw:
        score += 8
    normalized_lines = [
        re.sub(r"\s+", " ", line.strip())
        for line in raw.splitlines()
        if line and line.strip()
    ]
    line_counts: dict[str, int] = {}
    for line in normalized_lines:
        if len(line) < 10:
            continue
        line_counts[line] = line_counts.get(line, 0) + 1
    for line, count in line_counts.items():
        if count > 1:
            score += min(80, (count - 1) * max(12, min(len(line), 36)))
    return score


def _is_clean_subsequence(needle: str, haystack: str) -> bool:
    if not needle:
        return True
    if not haystack or len(needle) > len(haystack):
        return False
    idx = 0
    limit = len(needle)
    for ch in haystack:
        if ch == needle[idx]:
            idx += 1
            if idx >= limit:
                return True
    return False


def _should_validate_short_handoff_text(text: str, model_id: str) -> bool:
    if model_id != "gpt-5-5-thinking":
        return False
    value = str(text or "").strip()
    if not value or _contains_inline_image(value):
        return False
    clean = _strip_all_markers(value)
    if len(clean) >= 120 or "\n" in value:
        return False
    return any(token in value for token in ("学习", "AI", "ai", "利用AI", "能力"))


def _should_validate_handoff_text_against_conversation(text: str, model_id: str) -> bool:
    if model_id != "gpt-5-5-thinking":
        return False
    value = str(text or "").strip()
    if not value or _contains_inline_image(value):
        return False
    if _should_validate_short_handoff_text(value, model_id):
        return True
    clean = _strip_all_markers(value)
    if len(clean) < 40:
        return False
    return _stream_text_corruption_score(value) >= 30


def _prefer_more_complete_text(current_text: str | None, fetched_text: str | None) -> str | None:
    if not fetched_text:
        return current_text
    if not current_text:
        return fetched_text
    current_clean = _strip_all_markers(str(current_text))
    fetched_clean = _strip_all_markers(str(fetched_text))
    if not fetched_clean:
        return current_text
    if not current_clean:
        return fetched_text
    current_corruption = _stream_text_corruption_score(str(current_text))
    fetched_corruption = _stream_text_corruption_score(str(fetched_text))
    prefix_len = _common_prefix_len(current_clean, fetched_clean)
    if (
        current_corruption >= fetched_corruption + 20
        and len(fetched_clean) >= max(48, int(len(current_clean) * 0.35))
        and _is_clean_subsequence(fetched_clean, current_clean)
    ):
        return fetched_text
    if (
        current_corruption >= max(36, fetched_corruption + 20)
        and fetched_corruption <= 8
        and len(fetched_clean) >= max(72, int(len(current_clean) * 0.6))
        and prefix_len >= max(40, int(len(fetched_clean) * 0.35))
    ):
        return fetched_text
    if fetched_clean == current_clean:
        return fetched_text if len(str(fetched_text)) >= len(str(current_text)) else current_text
    if _looks_like_prefix_suffix_gap(str(current_text), str(fetched_text)):
        return fetched_text
    min_prefix_growth = max(8, int(len(current_clean) * 0.45))
    if prefix_len >= min_prefix_growth and len(fetched_clean) >= len(current_clean) + max(12, int(len(current_clean) * 0.15)):
        return fetched_text
    if current_clean in fetched_clean:
        return fetched_text
    min_growth = max(8, int(len(current_clean) * 0.05))
    large_growth = len(fetched_clean) - len(current_clean)
    if len(fetched_clean) >= len(current_clean) + min_growth and _is_clean_subsequence(current_clean, fetched_clean):
        return fetched_text
    if len(current_clean) <= 12 and len(fetched_clean) > len(current_clean):
        return fetched_text
    if large_growth >= max(24, int(len(current_clean) * 0.2)):
        return fetched_text
    return current_text


def _should_replace_with_snapshot(current_text: str | None, fetched_text: str | None) -> bool:
    preferred = _prefer_more_complete_text(current_text, fetched_text)
    if preferred != fetched_text:
        return False
    current = str(current_text or "")
    fetched = str(fetched_text or "")
    if not current or not fetched or fetched == current:
        return False
    if fetched.startswith(current):
        return False
    return True


def _physical_index_after_clean_chars(text: str, clean_char_count: int) -> int:
    clean_idx = 0
    phys_idx = 0
    marker_regexes = (_CITATION_MARKER_RE, _MANGLED_CITATION_MARKER_RE, _ENTITY_RE, _URL_RE, _ANY_MANGLED_MARKER_RE)
    while clean_idx < clean_char_count and phys_idx < len(text):
        char = text[phys_idx]
        for regex in marker_regexes:
            match = regex.match(text, phys_idx)
            if match:
                phys_idx = match.end()
                break
        else:
            if not char.isspace():
                clean_idx += 1
            phys_idx += 1
    return phys_idx


def _minimum_contained_overlap_size(clean_text: str) -> int:
    length = len(clean_text)
    if length <= 0:
        return 0
    return min(length, max(16, min(64, length // 3)))


def _max_yielded_tail_skip(clean_text: str) -> int:
    length = len(clean_text)
    if length <= 0:
        return 0
    return min(length, max(32, min(256, length // 5)))


def _looks_like_skipped_stream_marker_tail(text: str, clean_end: int) -> bool:
    tail = text[_physical_index_after_clean_chars(text, clean_end):].strip()
    if not tail:
        return False
    if any(marker in tail for marker in ("■", "\u25a0", "\ue200", "☆", "\u2606", "\ue202")):
        return True
    normalized_tail = re.sub(r"^[\s\.,;:!?\-_/\\，。！？、；：）】》\])}\"']+", "", tail).lower()
    return normalized_tail.startswith(("cite", "entity", "url"))


def _delta_after_yielded_text(
    yielded_text: str,
    fetched_text: str,
    *,
    allow_full_replacement: bool = False,
) -> str:
    fetched = str(fetched_text or "")
    yielded = str(yielded_text or "")

    log(
        f"[_delta_after_yielded_text] 差量计算调用开始: "
        f"yielded_len={len(yielded)}, fetched_len={len(fetched)}, allow_full_replacement={allow_full_replacement}. "
        f"yielded_preview={repr(yielded[:40])}...{repr(yielded[-40:]) if len(yielded) > 40 else ''}, "
        f"fetched_preview={repr(fetched[:40])}...{repr(fetched[-40:]) if len(fetched) > 40 else ''}"
    )

    if not fetched:
        log("[_delta_after_yielded_text] fetched为空，返回空串。")
        return ""
    if allow_full_replacement:
        log("[_delta_after_yielded_text] 允许完全替换，返回全部fetched。")
        return fetched
    if not yielded:
        log("[_delta_after_yielded_text] yielded为空，返回全部fetched。")
        return fetched

    if fetched.startswith(yielded):
        delta = fetched[len(yielded):]
        log(f"[_delta_after_yielded_text] 命中首部直接包含, 返回delta长度={len(delta)}")
        return delta

    fetched_clean = _strip_all_markers(fetched)
    yielded_clean = _strip_all_markers(yielded)

    if not fetched_clean:
        log("[_delta_after_yielded_text] fetched_clean为空，返回全部fetched。")
        return fetched
    if not yielded_clean:
        log("[_delta_after_yielded_text] yielded_clean为空，返回全部fetched。")
        return fetched

    if _looks_like_prefix_suffix_gap(yielded, fetched):
        log("[_delta_after_yielded_text] 识别到首尾拼接缺口，但当前不允许全量替换，返回空增量。")
        return ""

    # 在 clean 文本上寻找最长重合 (overlap)
    max_clean_overlap = min(len(yielded_clean), len(fetched_clean))
    clean_overlap_size = 0
    for size in range(max_clean_overlap, 0, -1):
        if yielded_clean[-size:] == fetched_clean[:size]:
            clean_overlap_size = size
            break

    if clean_overlap_size > 0:
        idx = _physical_index_after_clean_chars(fetched, clean_overlap_size)
        delta = fetched[idx:]
        log(f"[_delta_after_yielded_text] 命中头部重合 overlap_size={clean_overlap_size}, physical_idx={idx}, 返回delta长度={len(delta)}")
        return delta

    contained_idx = fetched_clean.find(yielded_clean)
    if contained_idx >= 0:
        clean_end = contained_idx + len(yielded_clean)
        idx = _physical_index_after_clean_chars(fetched, clean_end)
        delta = fetched[idx:]
        log(f"[_delta_after_yielded_text] 命中完全包含 contained_idx={contained_idx}, clean_end={clean_end}, physical_idx={idx}, 返回delta长度={len(delta)}")
        return delta

    min_overlap = _minimum_contained_overlap_size(yielded_clean)
    min_yielded_end = max(min_overlap, len(yielded_clean) - _max_yielded_tail_skip(yielded_clean))

    log(f"[_delta_after_yielded_text] 准备进行后缀滑动匹配: min_overlap={min_overlap}, len(yielded_clean)={len(yielded_clean)}, min_yielded_end={min_yielded_end}")

    for yielded_end in range(len(yielded_clean), min_yielded_end - 1, -1):
        if yielded_end < len(yielded_clean) and not _looks_like_skipped_stream_marker_tail(yielded, yielded_end):
            continue
        max_window = min(yielded_end, len(fetched_clean))
        for size in range(max_window, min_overlap - 1, -1):
            suffix = yielded_clean[yielded_end - size:yielded_end]
            best_idx = -1
            best_dist = float("inf")
            start_pos = 0
            max_tol = 48
            while True:
                idx = fetched_clean.find(suffix, start_pos)
                if idx < 0:
                    break
                clean_end = idx + size
                dist = abs(clean_end - yielded_end)
                if dist <= max_tol and dist < best_dist:
                    best_dist = dist
                    best_idx = idx
                start_pos = idx + 1
            if best_idx >= 0:
                clean_end = best_idx + size
                idx = _physical_index_after_clean_chars(fetched, clean_end)
                delta = fetched[idx:]
                log(
                    f"[_delta_after_yielded_text] 命中后缀滑动匹配: "
                    f"yielded_end={yielded_end}, suffix_size={size}, "
                    f"best_idx={best_idx}, clean_end={clean_end}, dist={best_dist}, physical_idx={idx}, "
                    f"suffix={repr(suffix)}, 返回delta长度={len(delta)}"
                )
                return delta

    log(
        f"[_delta_after_yielded_text] 匹配彻底失败，返回空字符串。 "
        f"yielded_clean_tail_40={repr(yielded_clean[-40:]) if len(yielded_clean) > 40 else repr(yielded_clean)}, "
        f"fetched_clean_head_40={repr(fetched_clean[:40]) if len(fetched_clean) > 40 else repr(fetched_clean)}"
    )
    return ""



def _join_stream_parts(parts_by_index: dict[int, str]) -> str:
    return "".join(parts_by_index[index] for index in sorted(parts_by_index) if parts_by_index.get(index))


def _stream_content_part_index(path: Any) -> tuple[bool, int | None, str]:
    if isinstance(path, str):
        match = re.match(r"^/(?:message/)?content/parts/(\d+)(?:/(.*))?$", path)
        if not match:
            return False, None, ""
        suffix = match.group(2) or ""
        return True, int(match.group(1)), suffix
    if isinstance(path, list):
        parts = [str(item) for item in path]
        if parts[:3] == ["message", "content", "parts"] and len(parts) >= 4:
            try:
                return True, int(parts[3]), "/".join(parts[4:])
            except (TypeError, ValueError):
                return True, None, "/".join(parts[4:])
        if parts[:2] == ["content", "parts"] and len(parts) >= 3:
            try:
                return True, int(parts[2]), "/".join(parts[3:])
            except (TypeError, ValueError):
                return True, None, "/".join(parts[3:])
    return False, None, ""


def _render_stream_patch_value(path_suffix: str, value: Any) -> str:
    key = str(path_suffix or "").split("/", 1)[0]
    image_keys = {"asset_pointer", "assetPointer", "image_asset_pointer", "file_id", "fileId", "image_url", "download_url", "downloadUrl"}
    if key in image_keys and isinstance(value, str):
        return _image_markdown_from_value({key: value})
    return _render_stream_part_value(value)


def _has_stream_content_patch(event: dict[str, Any]) -> bool:
    is_target_path, _part_index, _path_suffix = _stream_content_part_index(event.get("p"))
    if is_target_path:
        return True
    if event.get("o") == "patch" and isinstance(event.get("v"), list):
        return any(_has_stream_content_patch(item) for item in event["v"] if isinstance(item, dict))
    return False


def _extract_text_incremental(
    event: dict[str, Any],
    current: str,
    parts_by_index: dict[int, str] | None = None,
) -> str:
    for candidate in (event, event.get("v")):
        if not isinstance(candidate, dict):
            continue
        msg = candidate.get("message")
        if not isinstance(msg, dict):
            continue
        if not _is_renderable_chatgpt_message(msg):
            continue
        content = msg.get("content") or {}
        if not isinstance(content, dict) or content.get("content_type") in NON_TEXT_TYPES:
            continue
        parts = content.get("parts") or []
        had_indexed_parts = False
        if parts_by_index is not None and isinstance(parts, list):
            had_indexed_parts = bool(parts_by_index)
            for index, part in enumerate(parts):
                part_text = _render_stream_part_value(part)
                if part_text:
                    parts_by_index[int(index)] = _merge_stream_snapshot(parts_by_index.get(int(index), ""), part_text)
            text = _join_stream_parts(parts_by_index)
        else:
            chunks = extract_visible_text(parts) or extract_visible_text(content)
            text = "".join(chunks)
        image_markdown = _image_markdown_from_value(msg)
        if image_markdown and image_markdown in text:
            image_markdown = ""
        if text.strip() or image_markdown:
            text = _apply_citation_references(text, _collect_citation_references(msg)) if text.strip() else ""
            snapshot = f"{text}\n\n{image_markdown}".strip() if image_markdown else text
            if parts_by_index is not None and had_indexed_parts:
                return snapshot
            return _merge_stream_snapshot(current, snapshot)

    p = event.get("p")
    is_target_path, part_index, path_suffix = _stream_content_part_index(p)

    if is_target_path:
        op, v = event.get("o"), event.get("v")
        part_text = _render_stream_patch_value(path_suffix, v)
        if parts_by_index is not None and part_index is not None:
            existing = parts_by_index.get(part_index, "")
            if op == "append":
                parts_by_index[part_index] = _append_stream_part(existing, part_text)
                return _join_stream_parts(parts_by_index)
            if op == "replace":
                parts_by_index[part_index] = part_text
                return _join_stream_parts(parts_by_index)
            if op == "add":
                parts_by_index[part_index] = part_text if not existing else _merge_stream_snapshot(existing, part_text)
                return _join_stream_parts(parts_by_index)
        if op == "append":
            return _append_stream_part(current, part_text)
        if op == "replace":
            if part_index in (None, 0):
                return part_text
            return _append_stream_part(current, part_text)
        if op == "add":
            if part_index in (None, 0):
                return _merge_stream_snapshot(current, part_text)
            return _append_stream_part(current, part_text)

    if isinstance(event.get("v"), list) and not event.get("p"):
        text = current
        for item in event["v"]:
            if isinstance(item, dict):
                text = _extract_text_incremental(item, text, parts_by_index)
        return text

    v = event.get("v")
    if isinstance(v, str) and not event.get("p") and not event.get("o") and current:
        # ChatGPT 增量编码：裸 {"v": "..."} 表示延续上一条 append 的路径（通常是
        # /message/content/parts/N）。parts_by_index 必须同步累加，否则后续
        # {"p": "", "o": "patch"} 事件按 parts 重新 join 时，这些续写内容会整段
        # 丢失，快照会塌缩成"首个 part + 末尾 patch"的拼接。
        if parts_by_index:
            last_index = max(parts_by_index)
            parts_by_index[last_index] = _append_stream_part(parts_by_index[last_index], v)
        return current + v

    return current


def parse_sse_events(
    events: Iterable[str],
    context: dict[str, Any] | None = None,
) -> tuple[str, str | None, str | None, bool]:
    accumulated = ""
    current_message_id = None
    current_message_allowed = False
    current_message_ignored = None
    conversation_id = None
    assistant_message_id = None
    handoff = False
    debug_lines = []
    citation_refs: list[dict[str, str]] = []
    parts_by_index: dict[int, str] = {}
    for data in events:
        if not data or data == "[DONE]":
            continue
        debug_lines.append(data)
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("error"):
            _raise_classified_upstream_error(event.get("error"))
        citation_refs.extend(_collect_citation_references(event))
        _record_stream_context(event, context)
        event_type = event.get("type", "")
        if event_type in ("resume_conversation_token", "stream_handoff"):
            handoff = True
            conversation_id = conversation_id or event.get("conversation_id")
            continue
        conversation_id = conversation_id or event.get("conversation_id")

        for candidate in (event, event.get("v")):
            if not isinstance(candidate, dict):
                continue
            msg = candidate.get("message")
            if isinstance(msg, dict) and _is_renderable_chatgpt_message(msg):
                status = msg.get("status")
                metadata = msg.get("metadata") or {}
                err_detail = ""
                if isinstance(metadata.get("error"), str) and metadata.get("error").strip():
                    err_detail = metadata.get("error").strip()
                elif isinstance(metadata.get("error_message"), str) and metadata.get("error_message").strip():
                    err_detail = metadata.get("error_message").strip()
                if status == "finished_with_error" or err_detail:
                    error_payload = {
                        "message": err_detail or "Something went wrong. Please try again.",
                        "status": status
                    }
                    _raise_classified_upstream_error(
                        payload=error_payload,
                        detail=str(err_detail or "Something went wrong. Please try again."),
                    )
                content = msg.get("content") or {}
                content_type = content.get("content_type")
                recipient = msg.get("recipient")
                is_tool = (content_type == "code" or (recipient and recipient != "all"))
                is_ignored = (content_type in NON_TEXT_TYPES or is_tool)

                msg_id = msg.get("id")
                if msg_id and (msg_id != current_message_id or is_ignored != current_message_ignored):
                    current_message_ignored = is_ignored
                    if is_ignored:
                        current_message_id = msg_id
                        current_message_allowed = False
                    else:
                        had_existing_content = bool(accumulated)
                        current_message_id = msg_id
                        current_message_allowed = True
                        if not had_existing_content:
                            accumulated = ""
                            parts_by_index.clear()
                if msg_id and not is_ignored:
                    assistant_message_id = msg_id
                if content_type == "model_editable_context":
                    parts = content.get("parts")
                    if isinstance(parts, list):
                        historical_text = "".join(str(p) for p in parts if p is not None)
                        if historical_text:
                            accumulated = historical_text
                            for idx, part in enumerate(parts):
                                parts_by_index[idx] = str(part)

        if current_message_allowed or (current_message_id is None and _has_stream_content_patch(event)):
            accumulated = _extract_text_incremental(event, accumulated, parts_by_index)

    if not accumulated and debug_lines:
        _api_logger.debug(f"[parse_sse_events] 未解析出有效文本，接收到 {len(debug_lines)} 条事件")

    if accumulated:
        accumulated = _apply_citation_references(accumulated, citation_refs)
        accumulated = _INCOMPLETE_MARKER_RE.sub("", accumulated)
    return accumulated, conversation_id, assistant_message_id, handoff


def iter_delta_events(events: Iterable[str], context: dict[str, Any] | None = None) -> Iterator[ChatGPTWebCompletion]:
    accumulated = ""
    current_message_id = None
    current_message_allowed = False
    current_message_ignored = None
    conversation_id = None
    assistant_message_id = None
    parts_by_index: dict[int, str] = {}
    for data in events:
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("error"):
            _raise_classified_upstream_error(event.get("error"))
        event_type = str(event.get("type") or "")
        _record_stream_context(event, context)
        conversation_id = conversation_id or event.get("conversation_id")
        if context is not None and conversation_id:
            context["conversation_id"] = conversation_id

        for candidate in (event, event.get("v")):
            if not isinstance(candidate, dict):
                continue
            msg = candidate.get("message")
            if isinstance(msg, dict) and _is_renderable_chatgpt_message(msg):
                status = msg.get("status")
                metadata = msg.get("metadata") or {}
                err_detail = ""
                if isinstance(metadata.get("error"), str) and metadata.get("error").strip():
                    err_detail = metadata.get("error").strip()
                elif isinstance(metadata.get("error_message"), str) and metadata.get("error_message").strip():
                    err_detail = metadata.get("error_message").strip()
                if status == "finished_with_error" or err_detail:
                    error_payload = {
                        "message": err_detail or "Something went wrong. Please try again.",
                        "status": status
                    }
                    _raise_classified_upstream_error(
                        payload=error_payload,
                        detail=str(err_detail or "Something went wrong. Please try again."),
                    )
                content = msg.get("content") or {}
                content_type = content.get("content_type")
                recipient = msg.get("recipient")
                is_tool = (content_type == "code" or (recipient and recipient != "all"))
                is_ignored = (content_type in NON_TEXT_TYPES or is_tool)

                msg_id = msg.get("id")
                if msg_id and (msg_id != current_message_id or is_ignored != current_message_ignored):
                    current_message_ignored = is_ignored
                    if is_ignored:
                        current_message_id = msg_id
                        current_message_allowed = False
                    else:
                        had_existing_content = bool(accumulated)
                        current_message_id = msg_id
                        current_message_allowed = True
                        if not had_existing_content:
                            accumulated = ""
                            parts_by_index.clear()
                if msg_id and not is_ignored:
                    assistant_message_id = msg_id
                    if context is not None:
                        context["message_id"] = assistant_message_id
                if content_type == "model_editable_context":
                    parts = content.get("parts")
                    if isinstance(parts, list):
                        historical_text = "".join(str(p) for p in parts if p is not None)
                        if historical_text:
                            accumulated = historical_text
                            for idx, part in enumerate(parts):
                                parts_by_index[idx] = str(part)

        if current_message_allowed or (current_message_id is None and _has_stream_content_patch(event)):
            new_text = _extract_text_incremental(event, accumulated, parts_by_index)
            if new_text != accumulated:
                delta = new_text[len(accumulated):] if new_text.startswith(accumulated) else new_text
                accumulated = new_text
                if delta:
                    yield ChatGPTWebCompletion(delta, DEFAULT_MODEL, conversation_id, assistant_message_id)


def _is_descendant_of(node_id: str, target_parent_id: str, mapping: dict[str, Any]) -> bool:
    current_id = node_id
    visited = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        node = mapping.get(current_id)
        if not node:
            break
        parent_id = node.get("parent")
        if parent_id == target_parent_id:
            return True
        current_id = parent_id
    return False


def _copy_session_state(source: Any, target: Any) -> None:
    try:
        target.trust_env = False
    except Exception:
        pass
    try:
        target.headers.update(dict(getattr(source, "headers", {}) or {}))
    except Exception:
        pass
    try:
        target.proxies.update(dict(getattr(source, "proxies", {}) or {}))
    except Exception:
        pass
    source_options = getattr(source, "curl_options", None)
    if source_options:
        try:
            target.curl_options = dict(source_options)
        except Exception:
            pass


def _make_curl_retry_session(source: Any) -> Any | None:
    if not HAS_CURL_CFFI or curl_requests is None:
        return None
    try:
        retry = curl_requests.Session(impersonate="chrome120")
    except TypeError:
        try:
            retry = curl_requests.Session()
        except Exception:
            return None
    except Exception:
        return None
    _copy_session_state(source, retry)
    return retry


def _make_requests_retry_session(source: Any) -> requests.Session:
    retry = requests.Session()
    _copy_session_state(source, retry)
    return retry


def _conversation_response_json(
    session: Any,
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any] | None:
    retry_clients: list[tuple[str, Any, bool]] = [("当前会话", session, False)]
    curl_retry = _make_curl_retry_session(session)
    if curl_retry is not None:
        retry_clients.append(("新 curl_cffi 会话", curl_retry, True))
    retry_clients.append(("requests 降级会话", _make_requests_retry_session(session), True))
    last_connectivity_error: BaseException | None = None

    for label, client, should_close in retry_clients:
        try:
            resp = client.get(url, headers=headers, timeout=timeout)
        except Exception as exc:
            log(f"fetch_conversation {label}请求失败: {exc}")
            if is_upstream_connectivity_error(exc):
                last_connectivity_error = exc
            if should_close:
                try:
                    client.close()
                except Exception:
                    pass
            continue
        try:
            if not resp.ok:
                log(f"fetch_conversation 响应失败: HTTP {resp.status_code} - {resp.text[:500]}")
                upstream_exc = _upstream_error_from_response(resp)
                if getattr(upstream_exc, "error_code", "") in {
                    "model_limited",
                    "quota_or_rate_limited",
                    "permission_error",
                    "auth_error",
                    "challenge_required",
                }:
                    raise upstream_exc
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
        except ChatGPTWebError:
            raise
        except Exception as exc:
            log(f"fetch_conversation 解析响应失败: {exc}")
            return None
        finally:
            if should_close:
                try:
                    client.close()
                except Exception:
                    pass

    if last_connectivity_error is not None:
        raise_upstream_network_error(last_connectivity_error)
    return None


def fetch_conversation(
    session: Any,
    config: dict[str, Any],
    conversation_id: str,
    access_token: str | None,
    dynamic_headers: dict[str, str] | None = None,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
    suppress_transient_logs: bool = False,
) -> tuple[str | None, str | None]:
    def _transient_log(message: str) -> None:
        # 并行轮询等高频场景下抑制每次尝试的过程性日志，避免刷屏
        if not suppress_transient_logs:
            log(message)

    _transient_log(
        f"[fetch_conversation] 开始拉取: conversation_id={conversation_id}, "
        f"user_message_id={user_message_id}, assistant_message_id={assistant_message_id}"
    )
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    timeout = float(config.get("fallback_fetch_timeout_sec") or 30.0)
    fetch_paths = [
        f"/backend-api/conversation/{conversation_id}",
        f"/backend-api/f/conversation/{conversation_id}",
    ]
    data = None
    last_inaccessible_error: ChatGPTWebUpstreamError | None = None
    for path in fetch_paths:
        headers = {
            "Accept": "application/json",
            "x-openai-target-path": path,
            "x-openai-target-route": path,
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        if dynamic_headers:
            headers.update(dynamic_headers)
        headers["x-openai-target-path"] = path
        headers["x-openai-target-route"] = path
        try:
            data = _conversation_response_json(
                session,
                f"{base_url}{path}",
                headers,
                timeout,
            )
        except ChatGPTWebUpstreamError as exc:
            if _is_conversation_inaccessible_error(exc):
                last_inaccessible_error = exc
                continue
            raise
        if data is not None:
            break
    if data is None and last_inaccessible_error is not None:
        raise last_inaccessible_error
    if data is None:
        _transient_log(f"[fetch_conversation] 获取会话数据返回 None: conversation_id={conversation_id}")
        return None, None
    if isinstance(data.get("conversation"), dict):
        data = data["conversation"]
    mapping = data.get("mapping") or {}
    _transient_log(f"[fetch_conversation] 解析 mapping 树，节点数={len(mapping)}")
    target_msg = None
    latest = None
    latest_time = -1
    for node_id, node in mapping.items():
        msg = node.get("message") if isinstance(node, dict) else None
        if not isinstance(msg, dict):
            continue
        if not _is_renderable_chatgpt_message(msg):
            continue
        content = msg.get("content") or {}
        content_type = content.get("content_type")
        if content_type in NON_TEXT_TYPES or content_type == "code":
            continue
        recipient = msg.get("recipient")
        if recipient and recipient != "all":
            continue
        if assistant_message_id and (node_id == assistant_message_id or msg.get("id") == assistant_message_id):
            target_msg = msg
        if user_message_id and not _is_descendant_of(node_id, user_message_id, mapping):
            continue
        create_time = msg.get("create_time") or 0
        if create_time > latest_time:
            latest = msg
            latest_time = create_time

    latest = target_msg if target_msg else latest

    _transient_log(
        f"[fetch_conversation] 遍历消息完成. "
        f"assistant_message_id={assistant_message_id}, user_message_id={user_message_id}, "
        f"是否定位到最新节点: {'是' if latest else '否'}"
    )
    if latest:
        _transient_log(
            f"[fetch_conversation] 定位到最新消息节点: msg_id={latest.get('id')}, "
            f"status={latest.get('status')}, create_time={latest.get('create_time')}, "
            f"content_type={latest.get('content', {}).get('content_type')}"
        )
        status = latest.get("status")
        metadata = latest.get("metadata") or {}
        err_detail = ""
        if isinstance(metadata.get("error"), str) and metadata.get("error").strip():
            err_detail = metadata.get("error").strip()
        elif isinstance(metadata.get("error_message"), str) and metadata.get("error_message").strip():
            err_detail = metadata.get("error_message").strip()
        if status == "finished_with_error" or err_detail:
            error_payload = {
                "message": err_detail or "Something went wrong. Please try again.",
                "status": status
            }
            _raise_classified_upstream_error(
                payload=error_payload,
                detail=str(err_detail or "Something went wrong. Please try again."),
            )
    if not latest:
        return None, None
    raw_text = extract_content({"message": latest})
    _transient_log(f"[fetch_conversation] 提取节点内容: 字符长度={len(raw_text) if raw_text else 0}, 预览={repr(raw_text[:40]) if raw_text else ''}")
    raw_had_image_pointer = _has_unresolved_chatgpt_image_pointer(raw_text)
    text = raw_text
    if text:
        text = _inline_chatgpt_file_images(text, session, config, access_token, dynamic_headers, conversation_id=conversation_id)
    if raw_had_image_pointer and not _contains_inline_image(text):
        fallback_markdown, fallback_msg_id = _conversation_image_markdown_from_mapping(
            mapping,
            user_message_id=user_message_id,
            base_url=base_url,
        )
        if fallback_markdown:
            fallback_text = _inline_chatgpt_file_images(
                fallback_markdown,
                session,
                config,
                access_token,
                dynamic_headers,
                conversation_id=conversation_id,
            )
            if _contains_inline_image(fallback_text):
                prefix = str(text or "").strip()
                resolved = f"{prefix}\n\n{fallback_text}".strip() if prefix else fallback_text
                log(
                    "[fetch_conversation] 已从图片工具记录解析到原图: "
                    f"fallback_msg_id={fallback_msg_id or ''}, 字符长度={len(resolved)}"
                )
                return resolved, fallback_msg_id or latest.get("id")
        log(
            "[fetch_conversation] 当前节点只包含未解析图片占位，尚未找到原图，继续轮询: "
            f"conversation_id={conversation_id}, msg_id={latest.get('id')}"
        )
        return "", latest.get("id")
    return text, latest.get("id")


def _is_conversation_inaccessible_error(exc: BaseException) -> bool:
    if not isinstance(exc, ChatGPTWebUpstreamError):
        return False
    detail = str(exc).lower()
    return "conversation_inaccessible" in detail or "无权访问此对话" in detail


def _fetch_conversation_with_retry(
    session: Any,
    config: dict[str, Any],
    conversation_id: str,
    access_token: str | None,
    dynamic_headers: dict[str, str] | None = None,
    *,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
    attempts: int = 15,
    interval_sec: float = 2.0,
    stable_after_text_attempts: int = 0,
    suppress_transient_logs: bool = False,
) -> tuple[str | None, str | None]:
    last_inaccessible_error: ChatGPTWebUpstreamError | None = None
    latest_text: str | None = None
    latest_msg_id: str | None = None
    unchanged_after_text = 0
    max_attempts = max(1, int(attempts))
    stable_attempts = max(0, int(stable_after_text_attempts))
    # 仅在需要抑制日志时才传该参数，兼容旧签名的 fetch_conversation 替身/包装
    fetch_extra_kwargs: dict[str, Any] = {"suppress_transient_logs": True} if suppress_transient_logs else {}
    for retry in range(max_attempts):
        if retry:
            time.sleep(max(0.0, float(interval_sec)))
        try:
            fetched_text, fetched_msg_id = fetch_conversation(
                session,
                config,
                conversation_id,
                access_token,
                dynamic_headers,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                **fetch_extra_kwargs,
            )
        except ChatGPTWebUpstreamError as exc:
            if not _is_conversation_inaccessible_error(exc):
                raise
            last_inaccessible_error = exc
            if not suppress_transient_logs:
                log(
                    "fetch_conversation 暂时无法访问会话，将继续轮询: "
                    f"attempt={retry + 1}/{max_attempts}, conversation_id={conversation_id}"
                )
            continue
        except Exception as exc:
            if not suppress_transient_logs:
                log(
                    "fetch_conversation 遇到异常，将继续轮询: "
                    f"attempt={retry + 1}/{max_attempts}, conversation_id={conversation_id}, error={exc}"
                )
            continue
        if fetched_text and fetched_text.strip():
            preferred_text = _prefer_more_complete_text(latest_text, fetched_text)
            if preferred_text != latest_text:
                latest_text = preferred_text
                latest_msg_id = fetched_msg_id or latest_msg_id
                unchanged_after_text = 0
            else:
                unchanged_after_text += 1
            if _contains_inline_image(latest_text):
                return latest_text, latest_msg_id
            if stable_attempts <= 0 or unchanged_after_text >= stable_attempts:
                return latest_text, latest_msg_id
    if latest_text and latest_text.strip():
        return latest_text, latest_msg_id
    if last_inaccessible_error is not None:
        raise last_inaccessible_error
    return None, None


_HANDOFF_TERMINAL_TYPES = _STREAM_SUCCESS_TERMINAL_TYPES
_HANDOFF_ORDER_KEYS = (
    "offset",
    "sequence",
    "seq",
    "index",
    "idx",
    "cursor",
    "message_offset",
    "stream_offset",
)


def _handoff_topic_id(context: dict[str, Any] | None) -> str | None:
    if not isinstance(context, dict):
        return None
    options = context.get("handoff_options")
    if not isinstance(options, list):
        return None
    preferred_types = ("subscribe_ws_topic", "resume_sse_endpoint")
    for wanted in preferred_types:
        for option in options:
            if not isinstance(option, dict) or option.get("type") != wanted:
                continue
            topic_id = option.get("topic_id")
            if isinstance(topic_id, str) and topic_id.strip():
                return topic_id.strip()
    return None


def _coerce_handoff_order_value(value: Any) -> tuple[int, int | str] | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value), 0
    if isinstance(value, (int, float)):
        return 0, int(value)
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d+", text):
        return 0, int(text)
    match = re.search(r"-?\d+", text)
    if match:
        return 1, int(match.group(0))
    return 2, text


def _handoff_order_value(value: Any, *, depth: int = 0) -> tuple[int, int | str] | None:
    if depth > 6 or not isinstance(value, dict):
        return None
    for key in _HANDOFF_ORDER_KEYS:
        order = _coerce_handoff_order_value(value.get(key))
        if order is not None:
            return order
    for key in ("payload", "data", "body", "event", "message", "msg", "reply"):
        child = value.get(key)
        if child is not value:
            order = _handoff_order_value(child, depth=depth + 1)
            if order is not None:
                return order
    return None


def _sort_handoff_items(items: Iterable[Any]) -> tuple[list[Any], bool]:
    indexed = list(enumerate(items))
    if not any(_handoff_order_value(item) is not None for _index, item in indexed):
        return [item for _index, item in indexed], False
    sorted_indexed = sorted(
        indexed,
        key=lambda pair: (
            _handoff_order_value(pair[1]) is None,
            _handoff_order_value(pair[1]) or (3, pair[0]),
            pair[0],
        ),
    )
    return [item for _index, item in sorted_indexed], [index for index, _item in sorted_indexed] != [index for index, _item in indexed]


def _handoff_timeout_sec(config: dict[str, Any]) -> float:
    configured = config.get("handoff_stream_timeout_sec")
    if configured not in (None, ""):
        try:
            return max(30.0, min(180.0, float(configured)))
        except (TypeError, ValueError):
            pass
    attempts = _fallback_fetch_attempts(config, handoff=True)
    interval = _fallback_fetch_interval_sec(config)
    return max(60.0, min(180.0, attempts * interval + 30.0))


def _handoff_parallel_poll_delay_sec(config: dict[str, Any]) -> float:
    configured = config.get("handoff_parallel_poll_delay_sec")
    if configured not in (None, ""):
        try:
            return max(0.0, min(120.0, float(configured)))
        except (TypeError, ValueError):
            pass
    return 15.0


def _handoff_parallel_poll_attempts(config: dict[str, Any]) -> int:
    configured = config.get("handoff_parallel_poll_attempts")
    if configured not in (None, ""):
        try:
            return max(0, min(60, int(float(configured))))
        except (TypeError, ValueError):
            pass
    return 12


def _request_celsius_ws_url(
    session: Any,
    config: dict[str, Any],
    access_token: str | None,
    dynamic_headers: dict[str, str] | None,
) -> str | None:
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    path = "/backend-api/celsius/ws/user"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-openai-target-path": path,
        "x-openai-target-route": path,
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if dynamic_headers:
        headers.update(dynamic_headers)
    try:
        resp = session.get(
            f"{base_url}{path}",
            headers=headers,
            timeout=float(config.get("fallback_fetch_timeout_sec") or 8),
        )
    except Exception as exc:
        log(f"stream_handoff 获取 Celsius WebSocket 地址失败: {exc}")
        return None
    if not getattr(resp, "ok", False):
        classification = _upstream_error_classification_from_response(resp)
        if classification.code in ("quota_or_rate_limited", "model_limited", "auth_error", "challenge_required", "permission_error"):
            raise _upstream_error_from_response(resp)
        status_code = getattr(resp, "status_code", "?")
        body = str(getattr(resp, "text", ""))[:300]
        log(f"stream_handoff 获取 Celsius WebSocket 地址失败: HTTP {status_code} - {body}")
        return None
    try:
        data = resp.json()
    except Exception as exc:
        log(f"stream_handoff 解析 Celsius WebSocket 地址失败: {exc}")
        return None
    if not isinstance(data, dict):
        return None
    for key in ("websocket_url", "websocketUrl", "url", "ws_url", "wsUrl"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("ws://", "wss://")):
            return value
    return None


def _session_cookie_header(session: Any) -> str:
    headers = getattr(session, "headers", {}) or {}
    for key in ("Cookie", "cookie"):
        value = headers.get(key) if hasattr(headers, "get") else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    cookies = getattr(session, "cookies", None)
    try:
        cookie_dict = cookies.get_dict() if cookies is not None else {}
    except Exception:
        cookie_dict = {}
    if isinstance(cookie_dict, dict) and cookie_dict:
        return "; ".join(f"{name}={value}" for name, value in cookie_dict.items())
    return ""


def _has_python_socks() -> bool:
    try:
        import python_socks  # noqa: F401
    except Exception:
        return False
    return True


def _is_socks_proxy(proxy: str | None) -> bool:
    return str(proxy or "").strip().lower().startswith(("socks4://", "socks4a://", "socks5://", "socks5h://"))


def _websocket_connect_kwargs(
    websockets_mod: Any,
    session: Any,
    config: dict[str, Any],
    access_token: str | None,
) -> dict[str, Any]:
    try:
        params = inspect.signature(websockets_mod.connect).parameters
    except Exception:
        params = {}
    user_agent = str(
        (getattr(session, "headers", {}) or {}).get("User-Agent")
        or config.get("user_agent")
        or DEFAULT_CONFIG["user_agent"]
    )
    headers = {
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    cookie_header = _session_cookie_header(session)
    if cookie_header:
        headers["Cookie"] = cookie_header
    kwargs: dict[str, Any] = {
        "open_timeout": 20,
        "close_timeout": 5,
        "max_size": 16 * 1024 * 1024,
    }
    if "origin" in params:
        kwargs["origin"] = "https://chatgpt.com"
    if "user_agent_header" in params:
        kwargs["user_agent_header"] = user_agent
    else:
        headers["User-Agent"] = user_agent
    if "additional_headers" in params:
        kwargs["additional_headers"] = headers
    elif "extra_headers" in params:
        kwargs["extra_headers"] = headers
    proxy = _normalize_proxy(str(config.get("proxy") or ""))
    if "proxy" in params:
        if proxy and (not _is_socks_proxy(proxy) or _has_python_socks()):
            kwargs["proxy"] = proxy
        else:
            if proxy and _is_socks_proxy(proxy):
                log("stream_handoff 当前环境缺少 python-socks，Celsius WebSocket 将跳过 SOCKS 代理尝试直连。")
            # websockets 会默认读取系统代理；显式禁用可避免缺少 python-socks 时直接失败。
            kwargs["proxy"] = None
    return kwargs


def _json_items(raw: Any) -> list[Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(str(raw or ""))
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def _sse_data_values_from_encoded_item(encoded_item: Any) -> list[Any]:
    values: list[Any] = []
    for raw_line in str(encoded_item or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data_text = line[5:].strip()
        if not data_text:
            continue
        if data_text == "[DONE]":
            values.append({"type": "done"})
            continue
        try:
            values.append(json.loads(data_text))
        except json.JSONDecodeError:
            values.append(data_text)
    return values


def _encoded_item_payloads(value: Any, *, depth: int = 0) -> Iterable[dict[str, Any]]:
    if depth > 8:
        return
    if isinstance(value, list):
        ordered, _changed = _sort_handoff_items(value)
        for item in ordered:
            yield from _encoded_item_payloads(item, depth=depth + 1)
        return
    if not isinstance(value, dict):
        return
    if value.get("encoded_item"):
        yield value
    for key in ("payload", "data", "body", "event", "message", "msg"):
        child = value.get(key)
        if child is not None and child is not value:
            yield from _encoded_item_payloads(child, depth=depth + 1)


def _is_handoff_terminal_event(value: Any, *, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if isinstance(value, list):
        return any(_is_handoff_terminal_event(item, depth=depth + 1) for item in value)
    if not isinstance(value, dict):
        return False
    event_type = value.get("type")
    if isinstance(event_type, str) and event_type in _HANDOFF_TERMINAL_TYPES:
        return True
    if value.get("end_turn") is True:
        return True
    if value.get("status") == "finished_successfully":
        # 必须排除 thinking、thoughts 等非正文类型
        content = value.get("content") or {}
        content_type = content.get("content_type")
        if content_type not in NON_TEXT_TYPES:
            return True
    for key in ("message", "v", "payload", "data", "body", "event", "reply"):
        child = value.get(key)
        if child is not None and _is_handoff_terminal_event(child, depth=depth + 1):
            return True
    return False


def _handoff_event_strings_from_ws_message(message: Any, topic_id: str) -> tuple[list[str], bool]:
    if not isinstance(message, dict):
        return [], False
    message_topic = message.get("topic_id")
    if isinstance(message_topic, str) and message_topic and message_topic != topic_id:
        return [], False
    events: list[str] = []
    terminal = _is_handoff_terminal_event(message)
    for inner in _encoded_item_payloads(message):
        if inner.get("type") == "heartbeat":
            continue
        encoded_item = inner.get("encoded_item")
        if not encoded_item:
            continue
        conversation_id = inner.get("conversation_id") or message.get("conversation_id")
        terminal = terminal or _is_handoff_terminal_event(inner)
        for data_value in _sse_data_values_from_encoded_item(encoded_item):
            if isinstance(data_value, dict):
                if conversation_id and not data_value.get("conversation_id"):
                    data_value = dict(data_value)
                    data_value["conversation_id"] = conversation_id
                terminal = terminal or _is_handoff_terminal_event(data_value)
            events.append(json.dumps(data_value, ensure_ascii=False))
    return events, terminal


def _handoff_messages_from_ws_item(item: Any, topic_id: str) -> tuple[list[Any], bool, bool]:
    messages: list[Any] = []
    should_subscribe = False
    terminal = False
    if not isinstance(item, dict):
        return messages, should_subscribe, terminal
    reply = item.get("reply")
    if item.get("type") == "reply" and isinstance(reply, dict):
        reply_type = reply.get("type")
        if reply_type == "connect":
            should_subscribe = True
        if reply_type == "subscribe" and isinstance(reply.get("catchups"), list):
            catchups, changed = _sort_handoff_items(reply["catchups"])
            if changed:
                log(f"stream_handoff 已按 offset/sequence 重排 catchups: count={len(catchups)}")
            messages.extend(catchups)
        terminal = terminal or _is_handoff_terminal_event(reply)
    if item.get("type") == "message":
        messages.append(item)
    return messages, should_subscribe, terminal


async def _consume_handoff_ws_topic(
    ws_url: str,
    topic_id: str,
    session: Any,
    config: dict[str, Any],
    access_token: str | None,
    timeout_sec: float,
    on_snapshot: Any | None = None,
    first_event_timeout: float | None = None,
) -> tuple[str | None, str | None, str | None]:
    try:
        import websockets
    except Exception as exc:  # pragma: no cover - depends on optional runtime packaging
        raise ChatGPTWebError("缺少 websockets 依赖，无法订阅 ChatGPT handoff topic。") from exc

    event_strings: list[str] = []
    context: dict[str, Any] = {}
    deadline = time.monotonic() + max(30.0, float(timeout_sec))
    terminal_seen = False
    last_event_at: float | None = None
    streamed_text = ""
    terminal_quiet_sec = max(1.0, min(10.0, float(config.get("handoff_terminal_quiet_sec") or 3.0)))
    idle_return_sec = max(8.0, min(45.0, float(config.get("handoff_idle_return_sec") or 18.0)))
    if first_event_timeout is None:
        first_event_timeout = idle_return_sec
    else:
        first_event_timeout = max(3.0, min(45.0, float(first_event_timeout)))
    connect_kwargs = _websocket_connect_kwargs(websockets, session, config, access_token)
    async with websockets.connect(ws_url, **connect_kwargs) as ws:
        log(f"stream_handoff Celsius WebSocket 已连接，准备订阅 topic: topic_id={topic_id}")
        await ws.send(json.dumps([{"id": 0, "command": {"type": "connect"}}], ensure_ascii=False))
        subscribed = False
        frame_count = 0
        start_time = time.monotonic()
        while time.monotonic() < deadline:
            remaining = max(1.0, deadline - time.monotonic())
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(1.0, remaining))
            except asyncio.TimeoutError:
                now = time.monotonic()
                idle_sec = now - (last_event_at or start_time)
                text, conversation_id, message_id = None, None, None
                if event_strings:
                    try:
                        text, conversation_id, message_id, _handoff = parse_sse_events(event_strings, context)
                        conversation_id = conversation_id or context.get("conversation_id")
                    except Exception:
                        pass
                has_valid_text = bool(text and text.strip())
                if terminal_seen and idle_sec >= terminal_quiet_sec:
                    if has_valid_text:
                        return text, conversation_id, message_id
                is_timeout = False
                if not has_valid_text:
                    if idle_sec >= first_event_timeout:
                        is_timeout = True
                else:
                    if idle_sec >= idle_return_sec:
                        is_timeout = True
                if is_timeout:
                    if has_valid_text:
                        log(
                            "stream_handoff Celsius WebSocket 未收到明确完成帧，"
                            f"空闲 {idle_sec:.1f}s 后返回已收内容: topic_id={topic_id}, length={len(text)}"
                        )
                        return text, conversation_id, message_id
                    else:
                        log(
                            "stream_handoff Celsius WebSocket 未收到有效文本事件，"
                            f"空闲 {idle_sec:.1f}s 后触发超时退出以回退到轮询: topic_id={topic_id}"
                        )
                        return None, None, None
                continue
            frame_count += 1
            frame_terminal = False
            for item in _json_items(raw):
                messages, should_subscribe, item_terminal = _handoff_messages_from_ws_item(item, topic_id)
                frame_terminal = frame_terminal or item_terminal
                if should_subscribe and not subscribed:
                    await ws.send(
                        json.dumps(
                            [
                                {
                                    "id": 1,
                                    "command": {
                                        "type": "subscribe",
                                        "topic_id": topic_id,
                                        "offset": "0",
                                    },
                                }
                            ],
                            ensure_ascii=False,
                        )
                    )
                    subscribed = True
                    log(f"stream_handoff Celsius WebSocket 已订阅 topic: topic_id={topic_id}")
                for message in messages:
                    new_events, message_terminal = _handoff_event_strings_from_ws_message(message, topic_id)
                    if new_events:
                        event_strings.extend(new_events)
                        last_event_at = time.monotonic()
                        if on_snapshot is not None:
                            try:
                                text, conversation_id, message_id, _handoff = parse_sse_events(event_strings, context)
                            except Exception:
                                pass
                            else:
                                snapshot = _prefer_more_complete_text(streamed_text, text)
                                if snapshot and snapshot.strip() and snapshot != streamed_text:
                                    try:
                                        on_snapshot(snapshot, conversation_id or context.get("conversation_id"), message_id)
                                    except Exception as exc:
                                        log(f"stream_handoff 快照回调失败: {type(exc).__name__}: {exc}")
                                    else:
                                        streamed_text = snapshot
                    frame_terminal = frame_terminal or message_terminal
            if frame_terminal and event_strings:
                terminal_seen = True
        if event_strings:
            return parse_sse_events(event_strings, context)[:3]
        log(
            "stream_handoff Celsius WebSocket 超时但没有解析到事件: "
            f"topic_id={topic_id}, frames={frame_count}"
        )
    return None, None, None


def _run_async_blocking(coro_factory: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro_factory())
        except BaseException as exc:  # pragma: no cover - defensive bridge for GUI event loops
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _fetch_handoff_topic_text(
    session: Any,
    config: dict[str, Any],
    access_token: str | None,
    dynamic_headers: dict[str, str] | None,
    context: dict[str, Any] | None,
) -> tuple[str | None, str | None, str | None]:
    topic_id = _handoff_topic_id(context)
    if not topic_id:
        if isinstance(context, dict):
            context["handoff_error"] = "handoff 事件没有携带可订阅的 topic_id"
        return None, None, None
    log(f"stream_handoff 检测到 topic，尝试通过 Celsius WebSocket 续流: topic_id={topic_id}")
    ws_url = _request_celsius_ws_url(session, config, access_token, dynamic_headers)
    if not ws_url:
        if isinstance(context, dict):
            context["handoff_error"] = "无法获取 Celsius WebSocket 地址"
        return None, None, None
    try:
        snapshot_callback = _get_handoff_stream_snapshot_callback()
        first_event_timeout = None
        if _context_indicates_image_generation(context):
            first_event_timeout = 8.0
        text, conversation_id, message_id = _run_async_blocking(
            lambda: _consume_handoff_ws_topic(
                ws_url,
                topic_id,
                session,
                config,
                access_token,
                _handoff_timeout_sec(config),
                on_snapshot=snapshot_callback,
                first_event_timeout=first_event_timeout,
            )
        )
    except Exception as exc:
        if isinstance(exc, ChatGPTWebUpstreamError):
            raise exc
        error_text = f"{type(exc).__name__}: {exc}"
        log(f"stream_handoff Celsius WebSocket 续流失败: {error_text}")
        if isinstance(context, dict):
            context["handoff_error"] = error_text
        return None, None, None
    if text and text.strip():
        log(f"stream_handoff Celsius WebSocket 续流成功: topic_id={topic_id}, length={len(text)}")
        return text, conversation_id, message_id
    if isinstance(context, dict):
        context["handoff_error"] = "Celsius WebSocket 没有返回有效文本"
    return None, None, None


@dataclass
class HandoffStreamResult:
    text: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None


def _iter_handoff_topic_chunks(
    session: Any,
    config: dict[str, Any],
    access_token: str | None,
    dynamic_headers: dict[str, str] | None,
    context: dict[str, Any] | None,
    *,
    initial_text: str = "",
    model_id: str = DEFAULT_MODEL,
    conversation_id_for_poll: str | None = None,
    user_message_id: str | None = None,
    result: HandoffStreamResult | None = None,
) -> Iterator[ChatGPTWebCompletion]:
    events: "queue.Queue[tuple[str, Any]]" = queue.Queue()
    poll_stop = threading.Event()

    def _on_snapshot(snapshot_text: str, conversation_id: str | None, message_id: str | None) -> None:
        events.put(("snapshot", (snapshot_text, conversation_id, message_id)))

    def _runner() -> None:
        previous_callback = _set_handoff_stream_snapshot_callback(_on_snapshot)
        try:
            fetched = _fetch_handoff_topic_text(
                session,
                config,
                access_token,
                dynamic_headers,
                context,
            )
            events.put(("done", fetched))
        except BaseException as exc:
            events.put(("error", exc))
        finally:
            _set_handoff_stream_snapshot_callback(previous_callback)

    def _poll_runner() -> None:
        # WS 续流的并行保险：延迟一段时间后轮询会话详情接口，把拿到的文本
        # 作为快照送入同一队列（经过与 WS 快照一致的追加式校验后再对外发送），
        # 避免 WS 通道卡死时长时间无输出。
        delay = _handoff_parallel_poll_delay_sec(config)
        attempts = _handoff_parallel_poll_attempts(config)
        interval = _fallback_fetch_interval_sec(config, handoff=True)
        if attempts <= 0 or not conversation_id_for_poll:
            return
        if delay > 0 and poll_stop.wait(delay):
            return
        last_polled_text = ""
        for attempt in range(attempts):
            if attempt and poll_stop.wait(max(0.1, interval)):
                return
            if poll_stop.is_set():
                return
            assistant_msg_id = None
            if isinstance(context, dict):
                assistant_msg_id = context.get("message_id")
            try:
                polled_text, polled_msg_id = fetch_conversation(
                    session,
                    config,
                    conversation_id_for_poll,
                    access_token,
                    dynamic_headers,
                    user_message_id=user_message_id,
                    assistant_message_id=assistant_msg_id,
                    suppress_transient_logs=True,
                )
            except Exception:
                continue
            text = str(polled_text or "")
            if text.strip() and text != last_polled_text:
                last_polled_text = text
                events.put(("snapshot", (text, conversation_id_for_poll, polled_msg_id)))

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    poll_worker: threading.Thread | None = None
    if conversation_id_for_poll:
        poll_worker = threading.Thread(target=_poll_runner, daemon=True)
        poll_worker.start()

    emitted_text = str(initial_text or "")
    last_conversation_id = ""
    last_message_id = ""
    if isinstance(context, dict):
        last_conversation_id = str(context.get("conversation_id") or "")
        last_message_id = str(context.get("message_id") or "")

    try:
        while True:
            kind, payload = events.get()
            if kind == "snapshot":
                snapshot_text, conversation_id, message_id = payload
                snapshot = str(snapshot_text or "")
                if conversation_id:
                    last_conversation_id = str(conversation_id)
                if message_id:
                    last_message_id = str(message_id)
                preferred = _prefer_more_complete_text(emitted_text, snapshot)
                if preferred != snapshot or not snapshot.strip() or snapshot == emitted_text:
                    if (
                        snapshot.strip()
                        and snapshot != emitted_text
                        and not _append_only_delta(emitted_text, snapshot)
                        and isinstance(context, dict)
                    ):
                        context["handoff_rewrite_seen"] = True
                        log(
                            "stream_handoff 收到不可追加且未被采纳的重写快照，"
                            f"等待最终会话校验: emitted_len={len(emitted_text)}, snapshot_len={len(snapshot)}"
                        )
                    continue
                delta = _append_only_delta(emitted_text, snapshot)
                if not delta:
                    if isinstance(context, dict):
                        context["handoff_rewrite_seen"] = True
                    log(
                        "stream_handoff 收到会改写已发送前文的快照，"
                        f"跳过对外追加并等待最终会话校验: emitted_len={len(emitted_text)}, snapshot_len={len(snapshot)}"
                    )
                    continue
                emitted_text = snapshot
                yield ChatGPTWebCompletion(
                    text=delta,
                    model=model_id,
                    conversation_id=last_conversation_id or None,
                    message_id=last_message_id or None,
                    snapshot_text=snapshot,
                )
                continue

            if kind == "done":
                fetched_text, conversation_id, message_id = payload
                final_text = str(fetched_text or "")
                if conversation_id:
                    last_conversation_id = str(conversation_id)
                if message_id:
                    last_message_id = str(message_id)
                preferred = _prefer_more_complete_text(emitted_text, final_text)
                if preferred == final_text and final_text and final_text != emitted_text:
                    delta = _append_only_delta(emitted_text, final_text)
                    if delta:
                        emitted_text = final_text
                        yield ChatGPTWebCompletion(
                            text=delta,
                            model=model_id,
                            conversation_id=last_conversation_id or None,
                            message_id=last_message_id or None,
                            snapshot_text=final_text,
                        )
                    elif isinstance(context, dict):
                        context["handoff_rewrite_seen"] = True
                if result is not None:
                    result.text = final_text or emitted_text or None
                    result.conversation_id = last_conversation_id or None
                    result.message_id = last_message_id or None
                break

            exc = payload
            if result is not None:
                result.text = emitted_text or None
                result.conversation_id = last_conversation_id or None
                result.message_id = last_message_id or None
            if isinstance(exc, ChatGPTWebUpstreamError):
                raise exc
            log(f"stream_handoff 后台续流失败，将回退到 conversation 轮询: {type(exc).__name__}: {exc}")
            break
    finally:
        poll_stop.set()
        worker.join(timeout=0.2)
        if poll_worker is not None:
            poll_worker.join(timeout=0.2)


def _raise_handoff_unavailable_if_inaccessible(
    exc: ChatGPTWebUpstreamError,
    context: dict[str, Any] | None,
) -> None:
    if not (isinstance(context, dict) and context.get("handoff") and _is_conversation_inaccessible_error(exc)):
        raise exc
    handoff_error = str(context.get("handoff_error") or "").strip()
    detail = f" 续流详情：{handoff_error}。" if handoff_error else ""
    raise ChatGPTWebUpstreamError(
        "ChatGPT Web 已将本次生成切换到 stream_handoff topic，但本地未能从 Celsius WebSocket 续流取得结果；"
        "随后会话详情接口返回 conversation_inaccessible。网页端可能已经生成成功，问题在本地续流通道或登录态。"
        f"{detail}",
        getattr(exc, "status_code", 502),
        error_code="handoff_stream_unavailable",
    ) from exc


def _fallback_fetch_attempts(config: dict[str, Any], *, handoff: bool = False) -> int:
    configured = config.get("fallback_fetch_attempts")
    if configured not in (None, ""):
        try:
            return max(1, min(300, int(float(configured))))
        except (TypeError, ValueError):
            pass
    return 36 if handoff else 15


def _fallback_fetch_interval_sec(config: dict[str, Any], *, handoff: bool = False) -> float:
    configured = config.get("fallback_fetch_interval_sec")
    if configured not in (None, ""):
        try:
            return max(0.1, min(10.0, float(configured)))
        except (TypeError, ValueError):
            pass
    return 5.0 if handoff else 2.0


def _fallback_fetch_stable_after_text_attempts(config: dict[str, Any], *, stream: bool = False) -> int:
    configured = config.get("fallback_fetch_stable_after_text_attempts")
    if configured not in (None, ""):
        try:
            return max(0, min(30, int(float(configured))))
        except (TypeError, ValueError):
            pass
    return 3 if stream else 0


def _buffer_stream_until_handoff_decision(config: dict[str, Any], model_id: str) -> bool:
    configured = config.get("buffer_stream_until_handoff")
    if isinstance(configured, bool):
        return configured
    if configured not in (None, ""):
        return str(configured).strip().lower() in {"1", "true", "yes", "on", "y"}
    return model_id == "gpt-5-5-thinking"


def _final_fetch_after_stream_completion(config: dict[str, Any], model_id: str) -> bool:
    configured = config.get("final_fetch_after_stream_completion")
    if isinstance(configured, bool):
        return configured
    if configured not in (None, ""):
        return str(configured).strip().lower() in {"1", "true", "yes", "on", "y"}
    return model_id == "gpt-5-5-thinking"


def _final_stream_fetch_attempts(config: dict[str, Any]) -> int:
    configured = config.get("final_stream_fetch_attempts")
    if configured not in (None, ""):
        try:
            return max(1, min(12, int(float(configured))))
        except (TypeError, ValueError):
            pass
    return 4


def _final_stream_fetch_interval_sec(config: dict[str, Any]) -> float:
    configured = config.get("final_stream_fetch_interval_sec")
    if configured not in (None, ""):
        try:
            return max(0.5, min(6.0, float(configured)))
        except (TypeError, ValueError):
            pass
    return 1.5


def _request_options_value(options: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = options.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _request_options_bool(
    options: dict[str, Any],
    *keys: str,
    default: bool = False,
) -> bool:
    truthy = {"1", "true", "yes", "on", "y"}
    falsy = {"0", "false", "no", "off", "n"}
    for key in keys:
        if key not in options:
            continue
        value = options.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in truthy:
                return True
            if normalized in falsy:
                return False
    return bool(default)


def _conversation_append_enabled(options: dict[str, Any] | None) -> bool:
    return _request_options_bool(
        dict(options or {}),
        "enable_conversation_append",
        "conversation_append",
        default=False,
    )


def _request_options_float(
    options: dict[str, Any],
    *keys: str,
    minimum: float = 1.0,
    maximum: float = 300.0,
) -> float | None:
    for key in keys:
        value = options.get(key)
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        return max(float(minimum), min(float(maximum), number))
    return None


def _request_options_int(
    options: dict[str, Any],
    *keys: str,
    minimum: int = 1,
    maximum: int = 300,
) -> int | None:
    for key in keys:
        value = options.get(key)
        if value in (None, ""):
            continue
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        return max(int(minimum), min(int(maximum), number))
    return None


def _prepare_chatgpt_request(
    messages: list[dict[str, Any]],
    model: str | None,
    request_options: dict[str, Any] | None,
) -> PreparedChatGPTRequest:
    options = dict(request_options or {})
    conversation_append_enabled = _conversation_append_enabled(options)
    config = dict(CONFIG)
    request_timeout = _request_options_float(options, "upstream_timeout_sec", minimum=1.0, maximum=600.0)
    if request_timeout is not None:
        config["request_timeout_sec"] = request_timeout
    request_connect_timeout = _request_options_float(
        options, "upstream_connect_timeout_sec", minimum=0.5, maximum=60.0
    )
    if request_connect_timeout is not None:
        config["request_connect_timeout_sec"] = request_connect_timeout
    auth_timeout = _request_options_float(options, "upstream_auth_timeout_sec", minimum=1.0, maximum=60.0)
    if auth_timeout is not None:
        config["auth_timeout_sec"] = auth_timeout
    warmup_timeout = _request_options_float(options, "upstream_warmup_timeout_sec", minimum=0.5, maximum=60.0)
    if warmup_timeout is not None:
        config["warmup_timeout_sec"] = warmup_timeout
    bootstrap_timeout = _request_options_float(options, "upstream_bootstrap_timeout_sec", minimum=0.5, maximum=60.0)
    if bootstrap_timeout is not None:
        config["bootstrap_timeout_sec"] = bootstrap_timeout
    fallback_fetch_timeout = _request_options_float(
        options, "fallback_fetch_timeout_sec", minimum=0.5, maximum=60.0
    )
    if fallback_fetch_timeout is not None:
        config["fallback_fetch_timeout_sec"] = fallback_fetch_timeout
    fallback_fetch_attempts = _request_options_int(
        options, "fallback_fetch_attempts", minimum=1, maximum=300
    )
    if fallback_fetch_attempts is not None:
        config["fallback_fetch_attempts"] = fallback_fetch_attempts
    fallback_fetch_interval = _request_options_float(
        options, "fallback_fetch_interval_sec", minimum=0.1, maximum=10.0
    )
    if fallback_fetch_interval is not None:
        config["fallback_fetch_interval_sec"] = fallback_fetch_interval
    fallback_fetch_stable = _request_options_int(
        options, "fallback_fetch_stable_after_text_attempts", minimum=0, maximum=30
    )
    if fallback_fetch_stable is not None:
        config["fallback_fetch_stable_after_text_attempts"] = fallback_fetch_stable
    config["localize_generated_images"] = _request_options_bool(
        options,
        "localize_generated_images",
        "local_image_output",
        default=bool(config.get("localize_generated_images", False)),
    )
    generated_images_dir = _request_options_value(options, "generated_images_dir")
    if generated_images_dir:
        config["generated_images_dir"] = generated_images_dir
    auth = parse_auth_value(str(config.get("api_key") or ""))
    model_id = normalize_model(model or options.get("model") or DEFAULT_MODEL)
    session = make_session(config, auth)
    session_info, access_token, auth_error = get_session_info(session, config, auth.access_token)
    if not session_info and not access_token:
        raise ChatGPTWebError(f"ChatGPT 登录态校验失败: {auth_error or '未取得会话信息'}")
    device_id = auth.device_id or get_device_id(session_info, auth.cookie, access_token)
    if auth.cookie and not cookie_value(auth.cookie, "oai-did"):
        session.headers["Cookie"] = _append_cookie_if_missing(auth.cookie, "oai-did", device_id)
    conv_id = _request_options_value(options, "conversation_id")
    parent_msg_id = _request_options_value(options, "parent_message_id", "response_id")
    if conversation_append_enabled and not conv_id and len(messages) > 1:
        history_hash = _get_history_hash(messages[:-1])
        if history_hash:
            with _conversation_cache_lock:
                cached = _conversation_context_cache.get(history_hash)
            if cached:
                conv_id, parent_msg_id = cached
                log(f"[会话追加] 命中历史缓存! 关联到网页端会话: conversation_id={conv_id}, parent_message_id={parent_msg_id}")

    # 已关联会话ID时，只发送最新的追问消息；否则发送全部上下文
    if conv_id and len(messages) > 0:
        prompt_messages = messages[-1:]
    else:
        prompt_messages = messages

    return PreparedChatGPTRequest(
        session=session,
        config=config,
        prompt_messages=prompt_messages,
        conversation_id=conv_id,
        parent_message_id=parent_msg_id,
        access_token=access_token,
        device_id=device_id,
        dynamic_headers=dict(auth.protocol_headers),
        model_id=model_id,
    )


def _response_preview(response: Any, limit: int = 300) -> str:
    text = str(getattr(response, "text", "") or "").strip()
    return text[:limit]


def _json_response_object(response: Any, error_prefix: str) -> dict[str, Any]:
    if not getattr(response, "ok", False):
        raise _upstream_error_from_response(response)
    try:
        data = response.json()
    except Exception as exc:
        raise ChatGPTWebError(f"{error_prefix} 返回了非 JSON 响应: {exc}") from exc
    if not isinstance(data, dict):
        raise ChatGPTWebError(f"{error_prefix} 返回的 JSON 不是对象。")
    return data


def _build_upload_blob_headers(config: dict[str, Any], session: Any, mime_type: str) -> dict[str, str]:
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    user_agent = str(session.headers.get("User-Agent") or config.get("user_agent") or DEFAULT_CONFIG["user_agent"])
    return {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
        "Content-Type": str(mime_type or "application/octet-stream"),
        "Origin": base_url,
        "Referer": f"{base_url}/",
        "Sec-Ch-Ua": SEC_CH_UA,
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
        "User-Agent": user_agent,
        "x-ms-blob-type": "BlockBlob",
        "x-ms-version": "2020-04-08",
    }


def _upload_chatgpt_images(
    session: Any,
    config: dict[str, Any],
    access_token: str | None,
    device_id: str,
    dynamic_headers: dict[str, str],
    images: Sequence[ChatGPTWebImageUpload],
) -> list[ChatGPTWebUploadedImage]:
    if not images:
        return []
    base_url = str(config.get("base_url") or "https://chatgpt.com").rstrip("/")
    timeout = _upstream_request_timeout(config)
    uploaded: list[ChatGPTWebUploadedImage] = []
    for image in images:
        init_path = "/backend-api/files"
        init_headers = build_protocol_headers(
            config,
            access_token,
            device_id,
            dynamic_headers,
            accept="application/json",
            target_path=init_path,
        )
        init_payload = {
            "file_name": image.filename,
            "file_size": len(image.data),
            "use_case": "multimodal",
        }
        response = session.post(
            f"{base_url}{init_path}",
            headers=init_headers,
            data=json.dumps(init_payload, ensure_ascii=False),
            timeout=timeout,
        )
        _merge_dynamic_headers_from_response(dynamic_headers, response)
        upload_meta = _json_response_object(response, "ChatGPT Web 文件登记")
        file_id = str(upload_meta.get("file_id") or upload_meta.get("id") or "").strip()
        upload_url = str(upload_meta.get("upload_url") or "").strip()
        if not file_id or not upload_url:
            raise ChatGPTWebError(
                f"ChatGPT Web 文件登记缺少 file_id/upload_url: {_response_preview(response)}"
            )

        put_response = session.put(
            upload_url,
            headers=_build_upload_blob_headers(config, session, image.mime_type),
            data=image.data,
            timeout=timeout,
        )
        if not getattr(put_response, "ok", False):
            raise ChatGPTWebError(
                f"ChatGPT Web 图片二进制上传失败，HTTP {getattr(put_response, 'status_code', 502)}: "
                f"{_response_preview(put_response)}",
                status_code=502,
                error_code="upload_failed",
            )

        finalize_path = f"/backend-api/files/{file_id}/uploaded"
        finalize_headers = build_protocol_headers(
            config,
            access_token,
            device_id,
            dynamic_headers,
            accept="application/json",
            target_path=finalize_path,
        )
        finalize_response = session.post(
            f"{base_url}{finalize_path}",
            headers=finalize_headers,
            data="{}",
            timeout=timeout,
        )
        _merge_dynamic_headers_from_response(dynamic_headers, finalize_response)
        _json_response_object(finalize_response, "ChatGPT Web 文件确认")
        uploaded.append(
            ChatGPTWebUploadedImage(
                file_id=file_id,
                filename=image.filename,
                mime_type=image.mime_type,
                size_bytes=len(image.data),
                width=image.width,
                height=image.height,
            )
        )
    return uploaded


MODEL_LIMIT_KEYWORDS = (
    "model_cap",
    "message_cap",
    "model cap",
    "message cap",
    "model_limit",
    "message_limit",
    "thinking limit",
    "limit for this model",
    "model usage",
    "模型额度",
    "模型上限",
)
MODEL_HINT_KEYWORDS = (
    "gpt-5",
    "gpt-4",
    "thinking",
    "reasoning",
    "模型",
)
LIMIT_KEYWORDS = (
    "quota",
    "rate_limit",
    "rate limit",
    "usage_limit",
    "usage limit",
    "limit_reached",
    "too_many_requests",
    "too many requests",
    "you've reached",
    "you have reached",
    "reached your limit",
    "try again later",
    "try again after",
    "额度",
    "限流",
    "次数",
    "稍后重试",
)
AUTH_KEYWORDS = (
    "unauthorized",
    "authentication",
    "access token",
    "session token",
    "force_login",
    "login required",
    "sign in",
    "重新登录",
    "登录态",
)
CHALLENGE_KEYWORDS = (
    "arkose",
    "turnstile",
    "captcha",
    "challenge",
    "cloudflare",
    "cf_clearance",
    "sentinel",
    "verification",
    "校验",
    "验证",
    "风控",
)
PERMISSION_KEYWORDS = (
    "forbidden",
    "permission",
    "not allowed",
    "not available",
    "not eligible",
    "unsupported model",
    "model_not_found",
    "模型不可用",
    "无权限",
)
CONVERSATION_ACCESS_LIMIT_KEYWORDS = (
    "conversation_inaccessible",
    "无权访问此对话",
    "请对话所有者向你发送共享链接",
    "conversation owner",
)


def _truncate_text(value: Any, limit: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _collect_error_signal_parts(value: Any, parts: list[str], *, depth: int = 0) -> None:
    if depth > 6 or len(parts) > 240:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            parts.append(str(key))
            _collect_error_signal_parts(item, parts, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_error_signal_parts(item, parts, depth=depth + 1)
        return
    if isinstance(value, (str, int, float, bool)) and value is not None:
        parts.append(str(value))


def _loads_json_maybe(text: str) -> Any | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_status_code(value: Any) -> int | None:
    if isinstance(value, dict):
        for key in ("status_code", "statusCode", "status"):
            raw = value.get(key)
            if isinstance(raw, int):
                return raw
            if isinstance(raw, str) and raw.strip().isdigit():
                return int(raw.strip())
        for item in value.values():
            status_code = _extract_status_code(item)
            if status_code:
                return status_code
    if isinstance(value, list):
        for item in value:
            status_code = _extract_status_code(item)
            if status_code:
                return status_code
    return None


def _header_value(headers: Any, name: str) -> str:
    if not headers:
        return ""
    try:
        value = headers.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    try:
        for key, value in dict(headers).items():
            if str(key).lower() == name.lower() and value:
                return str(value)
    except Exception:
        pass
    return ""


def _contains_any(signal: str, keywords: Sequence[str]) -> bool:
    return any(keyword in signal for keyword in keywords)


def classify_upstream_error(
    *,
    status_code: int | None = None,
    payload: Any | None = None,
    detail: str = "",
    headers: Any | None = None,
) -> UpstreamErrorClassification:
    parsed_detail = _loads_json_maybe(detail)
    signal_parts: list[str] = []
    if payload is not None:
        _collect_error_signal_parts(payload, signal_parts)
    if parsed_detail is not None:
        _collect_error_signal_parts(parsed_detail, signal_parts)
    if detail:
        signal_parts.append(detail)
    retry_after = _header_value(headers, "Retry-After")
    if retry_after:
        signal_parts.extend(["retry-after", retry_after])

    payload_status = _extract_status_code(payload) or _extract_status_code(parsed_detail)
    upstream_status = int(status_code or payload_status or 502)
    signal = "\n".join(signal_parts).lower()
    detail_text = _truncate_text(detail, 1200)

    has_model_limit = _contains_any(signal, MODEL_LIMIT_KEYWORDS)
    has_model_hint = _contains_any(signal, MODEL_HINT_KEYWORDS)
    has_limit = _contains_any(signal, LIMIT_KEYWORDS)
    has_auth = _contains_any(signal, AUTH_KEYWORDS)
    has_challenge = _contains_any(signal, CHALLENGE_KEYWORDS)
    has_permission = _contains_any(signal, PERMISSION_KEYWORDS)
    has_conversation_access_limit = _contains_any(signal, CONVERSATION_ACCESS_LIMIT_KEYWORDS)

    if upstream_status in {401, 419} or (has_auth and upstream_status in {401, 403, 502}):
        code = "auth_error"
        local_status = 401
        base = "ChatGPT Web 登录态可能已失效，请重新导出完整 Cookie/Token 后重试。"
    elif has_challenge:
        code = "challenge_required"
        local_status = 403
        base = "ChatGPT Web 要求浏览器校验或风控验证，请在浏览器完成验证后重新导出 Cookie/请求头。"
    elif has_conversation_access_limit:
        code = "model_limited"
        local_status = 429
        base = (
            "当前账号无法继续访问该 ChatGPT Web 会话，可能是账号/模型额度或权限受限。"
            "请切换 auto/其他模型，或刷新浏览器后重新导出 Cookie 再试。"
        )
    elif upstream_status == 403 or has_permission:
        code = "permission_error"
        local_status = 403
        base = (
            "ChatGPT Web 拒绝了当前请求，可能是登录态权限、模型权限或动态校验头不完整。"
            "请重新导出完整 Cookie/请求头，或切换 auto/其他模型。"
        )
    elif has_model_limit or (has_model_hint and has_limit):
        code = "model_limited"
        local_status = 429
        base = "当前账号对所选模型可能已达到使用额度或暂时无权限；如果 auto 可用，请先切换 auto/其他模型或稍后重试。"
    elif upstream_status == 429 or has_limit:
        code = "quota_or_rate_limited"
        local_status = 429
        base = "账号、模型或请求频率可能触发额度/限流，请稍后重试，或切换 auto/其他可用模型。"
    else:
        code = "upstream_error"
        local_status = upstream_status if upstream_status >= 400 else 502
        if "something went wrong" in signal:
            base = "ChatGPT Web 返回通用错误 Something went wrong，未能确认具体原因。请稍后重试，或切换 auto/其他模型验证。"
        else:
            base = f"ChatGPT Web 上游请求失败，HTTP {upstream_status}"

    suffix_parts = []
    if retry_after:
        suffix_parts.append(f"Retry-After: {retry_after}")
    if detail_text:
        suffix_parts.append(f"详情：{detail_text}")
    if suffix_parts:
        base = f"{base} {'；'.join(suffix_parts)}"
    return UpstreamErrorClassification(code=code, message=base, status_code=local_status)


def _upstream_error_classification_from_response(response: Any) -> UpstreamErrorClassification:
    detail = _truncate_text(getattr(response, "text", ""), 1200)
    payload = None
    try:
        payload = response.json()
    except Exception:
        payload = _loads_json_maybe(detail)
    return classify_upstream_error(
        status_code=int(getattr(response, "status_code", 502) or 502),
        payload=payload,
        detail=detail,
        headers=getattr(response, "headers", None),
    )


def _upstream_error_from_response(response: Any) -> ChatGPTWebUpstreamError:
    classification = _upstream_error_classification_from_response(response)
    return ChatGPTWebUpstreamError(
        classification.message,
        classification.status_code,
        error_code=classification.code,
    )


def _raise_classified_upstream_error(
    payload: Any,
    *,
    status_code: int | None = None,
    detail: str = "",
    headers: Any | None = None,
) -> None:
    classification = classify_upstream_error(
        status_code=status_code,
        payload=payload,
        detail=detail,
        headers=headers,
    )
    raise ChatGPTWebUpstreamError(
        classification.message,
        classification.status_code,
        error_code=classification.code,
    )


def _upstream_error_message(response: Any) -> str:
    return _upstream_error_classification_from_response(response).message


def _complete_chatgpt_web_once(
    messages: list[dict[str, Any]],
    model: str | None = None,
    request_options: dict[str, Any] | None = None,
) -> ChatGPTWebCompletion:
    conversation_append_enabled = _conversation_append_enabled(request_options)
    prepared = _prepare_chatgpt_request(messages, model, request_options)
    session = prepared.session
    config = prepared.config
    access_token = prepared.access_token
    device_id = prepared.device_id
    dynamic_headers = prepared.dynamic_headers
    model_id = prepared.model_id
    response: requests.Response | None = None
    try:
        warmup_chat_requirements(session, config, access_token, device_id, dynamic_headers)
        image_uploads = _extract_message_images(prepared.prompt_messages)
        uploaded_images = _upload_chatgpt_images(
            session,
            config,
            access_token,
            device_id,
            dynamic_headers,
            image_uploads,
        )
        prompt = messages_to_prompt(prepared.prompt_messages, allow_empty=bool(uploaded_images))
        body = build_body(
            prompt,
            config,
            conversation_id=prepared.conversation_id,
            parent_message_id=prepared.parent_message_id,
            model=model_id,
            uploaded_images=uploaded_images,
        )
        log(
            "准备请求 ChatGPT 网页 conversation: "
            f"model={model_id}, conversation_id={prepared.conversation_id}, "
            f"parent_message_id={prepared.parent_message_id}, prompt_len={len(prompt)}"
        )
        response = request_conversation_with_requirements(session, config, body, access_token, device_id, dynamic_headers)
        if not response.ok:
            raise _upstream_error_from_response(response)
        handoff_context: dict[str, Any] = {}
        text, conversation_id, message_id, _handoff = parse_sse_events(iter_sse_lines(response), handoff_context)
        if text:
            text = _inline_chatgpt_file_images(text, session, config, access_token, dynamic_headers, conversation_id=conversation_id)
        needs_image_fallback = _context_indicates_image_generation(handoff_context) and not _contains_inline_image(text)
        user_msg_id = None
        if body and isinstance(body.get("messages"), list) and len(body["messages"]) > 0:
            user_msg_id = body["messages"][0].get("id")
        if _handoff and conversation_id:
            response.close()
            response = None
            fetched_text, handoff_conversation_id, fetched_msg_id = _fetch_handoff_topic_text(
                session,
                config,
                access_token,
                dynamic_headers,
                handoff_context,
            )
            conversation_id = handoff_conversation_id or conversation_id
            if fetched_text:
                text = _inline_chatgpt_file_images(
                    fetched_text,
                    session,
                    config,
                    access_token,
                    dynamic_headers,
                    conversation_id=conversation_id,
                )
                citation_refs = _collect_citation_references(handoff_context)
                text = _apply_citation_references(text, citation_refs)
                message_id = message_id or fetched_msg_id
            if text and _should_validate_short_handoff_text(text, model_id):
                try:
                    polled_text, polled_msg_id = _fetch_conversation_with_retry(
                        session,
                        config,
                        conversation_id,
                        access_token,
                        dynamic_headers,
                        user_message_id=user_msg_id,
                        assistant_message_id=message_id,
                        attempts=_fallback_fetch_attempts(config, handoff=True),
                        interval_sec=_fallback_fetch_interval_sec(config, handoff=True),
                        stable_after_text_attempts=_fallback_fetch_stable_after_text_attempts(config, stream=False),
                    )
                except ChatGPTWebUpstreamError as exc:
                    _raise_handoff_unavailable_if_inaccessible(exc, handoff_context)
                else:
                    text = _prefer_more_complete_text(text, polled_text)
                    message_id = message_id or polled_msg_id
        needs_image_fallback = _context_indicates_image_generation(handoff_context) and not _contains_inline_image(text)
        if (not text or not text.strip() or needs_image_fallback) and conversation_id:
            if response is not None:
                response.close()
                response = None
            fetched_text = None
            fetched_msg_id = None
            if _handoff:
                fetched_text, handoff_conversation_id, fetched_msg_id = _fetch_handoff_topic_text(
                    session,
                    config,
                    access_token,
                    dynamic_headers,
                    handoff_context,
                )
                conversation_id = handoff_conversation_id or conversation_id
                if fetched_text:
                    fetched_text = _inline_chatgpt_file_images(
                        fetched_text,
                        session,
                        config,
                        access_token,
                        dynamic_headers,
                        conversation_id=conversation_id,
                    )
            if needs_image_fallback and text and text.strip() and not fetched_text:
                log(
                    "[图片兜底] SSE 标记为 image gen，但当前内容未解析到图片，继续拉取 conversation: "
                    f"conversation_id={conversation_id}"
                )
            if not fetched_text:
                try:
                    fetched_text, fetched_msg_id = _fetch_conversation_with_retry(
                        session,
                        config,
                        conversation_id,
                        access_token,
                        dynamic_headers,
                        user_message_id=user_msg_id,
                        attempts=_fallback_fetch_attempts(config, handoff=_handoff),
                    interval_sec=_fallback_fetch_interval_sec(config, handoff=_handoff),
                        stable_after_text_attempts=_fallback_fetch_stable_after_text_attempts(config, stream=False),
                    )
                except ChatGPTWebUpstreamError as exc:
                    _raise_handoff_unavailable_if_inaccessible(exc, handoff_context)
            if fetched_text:
                text = fetched_text
                message_id = message_id or fetched_msg_id
        if not text or not text.strip():
            raise ChatGPTWebError("ChatGPT Web 没有返回有效文本。")
        if conversation_append_enabled and conversation_id and message_id:
            history = list(messages)
            history.append({"role": "assistant", "content": text})
            history_hash = _get_history_hash(history)
            if history_hash:
                _update_cache(history_hash, (conversation_id, message_id))
                log(f"[会话追加] 已缓存历史映射: hash={history_hash} -> ({conversation_id}, {message_id})")
        return ChatGPTWebCompletion(text=text, model=model_id, conversation_id=conversation_id, message_id=message_id)
    finally:
        if response is not None:
            response.close()
        session.close()


def complete_chatgpt_web(
    messages: list[dict[str, Any]],
    model: str | None = None,
    request_options: dict[str, Any] | None = None,
) -> ChatGPTWebCompletion:
    retries = _prompt_network_error_retries(request_options)
    max_attempts = retries + 1
    for attempt in range(max_attempts):
        started_at = time.monotonic()
        try:
            return _complete_chatgpt_web_once(messages, model, request_options)
        except Exception as exc:
            elapsed = time.monotonic() - started_at
            if attempt >= retries or not _should_retry_prompt_network_error(
                exc,
                elapsed_sec=elapsed,
                request_options=request_options,
            ):
                raise
            log(
                "ChatGPT Web 发送提示词遇到快速可重试错误，将重试: "
                f"attempt={attempt + 1}/{max_attempts}, elapsed={elapsed:.2f}s, error={exc}"
            )
            _sleep_before_prompt_retry(request_options)
    raise ChatGPTWebError("ChatGPT Web 重试流程异常结束。")


def _stream_chatgpt_web_once(
    messages: list[dict[str, Any]],
    model: str | None = None,
    request_options: dict[str, Any] | None = None,
) -> Iterator[ChatGPTWebCompletion]:
    conversation_append_enabled = _conversation_append_enabled(request_options)
    prepared = _prepare_chatgpt_request(messages, model, request_options)
    session = prepared.session
    config = prepared.config
    access_token = prepared.access_token
    device_id = prepared.device_id
    dynamic_headers = prepared.dynamic_headers
    model_id = prepared.model_id
    response: requests.Response | None = None
    try:
        warmup_chat_requirements(session, config, access_token, device_id, dynamic_headers)
        image_uploads = _extract_message_images(prepared.prompt_messages)
        uploaded_images = _upload_chatgpt_images(
            session,
            config,
            access_token,
            device_id,
            dynamic_headers,
            image_uploads,
        )
        prompt = messages_to_prompt(prepared.prompt_messages, allow_empty=bool(uploaded_images))
        body = build_body(
            prompt,
            config,
            conversation_id=prepared.conversation_id,
            parent_message_id=prepared.parent_message_id,
            model=model_id,
            uploaded_images=uploaded_images,
        )
        log(
            "准备请求 ChatGPT 网页 conversation: "
            f"model={model_id}, conversation_id={prepared.conversation_id}, "
            f"parent_message_id={prepared.parent_message_id}, prompt_len={len(prompt)}, stream=True"
        )
        response = request_conversation_with_requirements(session, config, body, access_token, device_id, dynamic_headers)
        if not response.ok:
            raise _upstream_error_from_response(response)


        yielded_len = 0
        yielded_text = ""
        context = {"conversation_id": None, "message_id": None}
        sse_completed = False
        buffer_until_handoff_decision = _buffer_stream_until_handoff_decision(config, model_id)
        pending_chunks: list[ChatGPTWebCompletion] = []
        # 客户端是否已实际收到正文（缓冲中的 pending_chunks 不算）。
        # 一旦为 True，就绝不允许全量替换，否则客户端会看到开头重复。
        client_sent_any = False
        try:
            for event in iter_delta_events(iter_sse_lines(response), context):

                if event.text and (event.text.strip() or yielded_len > 0):
                    event_text = _inline_chatgpt_file_images(
                        event.text,
                        session,
                        config,
                        access_token,
                        dynamic_headers,
                        conversation_id=event.conversation_id or context.get("conversation_id"),
                    )
                    yielded_len += len(event_text)
                    yielded_text += event_text
                    chunk = ChatGPTWebCompletion(
                        text=event_text,
                        model=model_id,
                        conversation_id=event.conversation_id,
                        message_id=event.message_id,
                        # 缓冲模式下 chunk 会长期滞留在 pending_chunks：
                        # 逐个保留前缀快照会让内存按"前缀长度之和"累积（O(n²)）。
                        # 消费端对 snapshot_text 为空的 chunk 会用小 delta 合并回退，成本线性。
                        snapshot_text=None if buffer_until_handoff_decision else yielded_text,
                    )
                    if buffer_until_handoff_decision:
                        pending_chunks.append(chunk)
                    else:
                        client_sent_any = True
                        yield chunk
            sse_completed = True
        except ChatGPTWebError:
            raise
        except Exception as sse_exc:
            if is_upstream_connectivity_error(sse_exc):
                raise_upstream_network_error(sse_exc)
            log(f"[流式传输] 迭代流事件中途发生异常: {sse_exc}，将尝试通过轮询拉取完整内容。")

        if (
            sse_completed
            and not context.get("handoff")
            and context.get("conversation_id")
            and _final_fetch_after_stream_completion(config, model_id)
        ):
            conversation_id = context.get("conversation_id")
            message_id = context.get("message_id")
            user_msg_id = None
            if body and isinstance(body.get("messages"), list) and len(body["messages"]) > 0:
                user_msg_id = body["messages"][0].get("id")
            log(
                "[流式传输] SSE 已结束，执行最终会话校验: "
                f"model={model_id}, 当前长度={yielded_len}, 会话ID={conversation_id}"
            )
            try:
                fetched_text, fetched_msg_id = _fetch_conversation_with_retry(
                    session,
                    config,
                    str(conversation_id),
                    access_token,
                    dynamic_headers,
                    user_message_id=user_msg_id,
                    assistant_message_id=message_id,
                    attempts=_final_stream_fetch_attempts(config),
                    interval_sec=_final_stream_fetch_interval_sec(config),
                    stable_after_text_attempts=_fallback_fetch_stable_after_text_attempts(config, stream=True),
                )
            except ChatGPTWebUpstreamError as exc:
                log(f"[流式传输] 最终会话校验失败，将使用 SSE 已收内容: {exc}")
                fetched_text = None
                fetched_msg_id = None
            if fetched_text and _prefer_more_complete_text(yielded_text, fetched_text) == fetched_text:
                fetched_text = _inline_chatgpt_file_images(
                    fetched_text,
                    session,
                    config,
                    access_token,
                    dynamic_headers,
                    conversation_id=conversation_id,
                )
                citation_refs = _collect_citation_references(context)
                fetched_text = _apply_citation_references(fetched_text, citation_refs)
                applied_yielded_text = _apply_citation_references(yielded_text, citation_refs)
                replacement_needed = (
                    (
                        _context_indicates_image_generation(context)
                        and not _contains_inline_image(applied_yielded_text)
                        and _contains_inline_image(fetched_text)
                    )
                    or _looks_like_prefix_suffix_gap(
                        applied_yielded_text,
                        fetched_text,
                    )
                    or _should_replace_with_snapshot(applied_yielded_text, fetched_text)
                )
                allow_full_replacement = bool(buffer_until_handoff_decision) and replacement_needed
                if replacement_needed and not allow_full_replacement:
                    log("[流式传输] 最终会话校验得到重写快照；已发送正文，跳过全量替换以避免重复。")
                    delta = _append_only_delta(applied_yielded_text, fetched_text)
                else:
                    delta = _delta_after_yielded_text(
                        applied_yielded_text,
                        fetched_text,
                        allow_full_replacement=allow_full_replacement,
                    )
                # 缓冲中的 SSE 正文尚未发给客户端：全量替换时直接丢弃（fetched 已覆盖全部），
                # 追加式对账时必须先冲刷，否则客户端会缺失 delta 之前的整段开头。
                if pending_chunks and not allow_full_replacement:
                    for chunk in pending_chunks:
                        yield chunk
                    client_sent_any = True
                pending_chunks.clear()
                if delta:
                    yield ChatGPTWebCompletion(
                        text=delta,
                        model=model_id,
                        conversation_id=str(conversation_id),
                        message_id=message_id or fetched_msg_id,
                    )
                return

        if sse_completed and buffer_until_handoff_decision and not context.get("handoff"):
            if pending_chunks:
                client_sent_any = True
            for chunk in pending_chunks:
                yield chunk
            pending_chunks.clear()

        if (
            yielded_len > 0
            and bool(context.get("message_finished_successfully"))
            and (model_id != "gpt-5-5-thinking" or not context.get("handoff"))
            and not (_context_indicates_image_generation(context) and not _contains_inline_image(yielded_text))
        ):
            if pending_chunks:
                for chunk in pending_chunks:
                    yield chunk
                pending_chunks.clear()
            return

        # 检查是否需要进行原地兜底（连接未完成、没有输出过内容，或者上游切到了 handoff）
        if (
            context.get("handoff")
            or not sse_completed
            or yielded_len == 0
            or (_context_indicates_image_generation(context) and not _contains_inline_image(yielded_text))
        ):
            if response is not None:
                response.close()
                response = None
            conversation_id = context.get("conversation_id")
            if conversation_id:
                message_id = context.get("message_id")
                user_msg_id = None
                if body and isinstance(body.get("messages"), list) and len(body["messages"]) > 0:
                    user_msg_id = body["messages"][0].get("id")
                log(f"[流式传输] 触发断线兜底。当前已发送长度: {yielded_len}，会话ID: {conversation_id}，提问ID: {user_msg_id}")
                fetched_text = None
                fetched_msg_id = None
                if context.get("handoff"):
                    handoff_result = HandoffStreamResult()
                    for handoff_chunk in _iter_handoff_topic_chunks(
                        session,
                        config,
                        access_token,
                        dynamic_headers,
                        context,
                        initial_text="" if buffer_until_handoff_decision else yielded_text,
                        model_id=model_id,
                        conversation_id_for_poll=str(conversation_id) if conversation_id else None,
                        user_message_id=user_msg_id,
                        result=handoff_result,
                    ):
                        conversation_id = handoff_chunk.conversation_id or conversation_id
                        message_id = handoff_chunk.message_id or message_id
                        yielded_text = handoff_chunk.snapshot_text or _merge_stream_snapshot(yielded_text, handoff_chunk.text)
                        yielded_len = len(yielded_text)
                        client_sent_any = True
                        yield handoff_chunk
                    fetched_text = handoff_result.text
                    handoff_conversation_id = handoff_result.conversation_id
                    fetched_msg_id = handoff_result.message_id
                    conversation_id = handoff_conversation_id or conversation_id
                    if fetched_text:
                        fetched_text = _inline_chatgpt_file_images(
                            fetched_text,
                            session,
                            config,
                            access_token,
                            dynamic_headers,
                            conversation_id=conversation_id,
                        )
                    if fetched_text and (
                        bool(context.get("handoff_rewrite_seen"))
                        or _should_validate_handoff_text_against_conversation(fetched_text, model_id)
                    ):
                        try:
                            polled_text, polled_msg_id = _fetch_conversation_with_retry(
                                session,
                                config,
                                conversation_id,
                                access_token,
                                dynamic_headers,
                                user_message_id=user_msg_id,
                                assistant_message_id=fetched_msg_id or message_id,
                                attempts=_final_stream_fetch_attempts(config),
                                interval_sec=_final_stream_fetch_interval_sec(config),
                                stable_after_text_attempts=_fallback_fetch_stable_after_text_attempts(config, stream=True),
                            )
                        except ChatGPTWebUpstreamError as exc:
                            _raise_handoff_unavailable_if_inaccessible(exc, context)
                            polled_text = None
                            polled_msg_id = None
                        if polled_text and _prefer_more_complete_text(fetched_text, polled_text) == polled_text:
                            fetched_text = polled_text
                            fetched_msg_id = polled_msg_id or fetched_msg_id
                if not fetched_text:
                    try:
                        fetched_text, fetched_msg_id = _fetch_conversation_with_retry(
                            session,
                            config,
                            conversation_id,
                            access_token,
                            dynamic_headers,
                            user_message_id=user_msg_id,
                            assistant_message_id=message_id,
                            attempts=_fallback_fetch_attempts(config, handoff=bool(context.get("handoff"))),
                            interval_sec=_fallback_fetch_interval_sec(config, handoff=bool(context.get("handoff"))),
                            stable_after_text_attempts=_fallback_fetch_stable_after_text_attempts(config, stream=True),
                        )
                    except ChatGPTWebUpstreamError as exc:
                        _raise_handoff_unavailable_if_inaccessible(exc, context)
                if fetched_text:
                    citation_refs = _collect_citation_references(context)
                    fetched_text = _apply_citation_references(fetched_text, citation_refs)
                    applied_yielded_text = _apply_citation_references(yielded_text, citation_refs)
                    replacement_needed = (
                        (
                            _context_indicates_image_generation(context)
                            and not _contains_inline_image(applied_yielded_text)
                            and _contains_inline_image(fetched_text)
                        )
                        or _looks_like_prefix_suffix_gap(
                            applied_yielded_text,
                            fetched_text,
                        )
                        or _should_replace_with_snapshot(applied_yielded_text, fetched_text)
                    )
                    fetched_preferred = (
                        _prefer_more_complete_text(applied_yielded_text, fetched_text) == fetched_text
                    )
                    if not replacement_needed and not fetched_preferred:
                        # 兜底拉到的文本比已收内容更短/更差（例如快照塌缩后的残片），
                        # 保留已收内容，不能让它反向覆盖 yielded_text。
                        log(
                            "[流式传输] 兜底拉取文本不如已收内容完整，保留已收内容: "
                            f"yielded_len={len(applied_yielded_text)}, fetched_len={len(fetched_text)}"
                        )
                        message_id = message_id or fetched_msg_id
                    else:
                        # 全量替换仅限缓冲模式下的"损坏恢复"场景（如上游快照被改写）：
                        # 即使 handoff 已流出部分正文，也宁可整段重发干净文本，保证完整性。
                        allow_full_replacement = bool(buffer_until_handoff_decision) and replacement_needed
                        if replacement_needed and not allow_full_replacement:
                            log("[流式传输] 兜底拉取得到重写快照；已发送正文，跳过全量替换以避免重复。")
                            delta = _append_only_delta(applied_yielded_text, fetched_text)
                        else:
                            delta = _delta_after_yielded_text(
                                applied_yielded_text,
                                fetched_text,
                                allow_full_replacement=allow_full_replacement,
                            )
                        # 客户端已收到过正文（如 handoff 续流）时，缓冲的 SSE 前缀已包含在
                        # 续流重放里，直接丢弃；客户端还没收到任何内容且非全量替换时，
                        # 必须先冲刷缓冲，否则 delta 之前的开头会缺失。
                        if pending_chunks and not allow_full_replacement and not client_sent_any:
                            for chunk in pending_chunks:
                                yield chunk
                            client_sent_any = True
                        pending_chunks.clear()
                        yielded_len = len(fetched_text)
                        yielded_text = fetched_text
                        message_id = message_id or fetched_msg_id
                        if delta:
                            client_sent_any = True
                            yield ChatGPTWebCompletion(
                                text=delta,
                                model=model_id,
                                conversation_id=conversation_id,
                                message_id=message_id,
                            )
                if yielded_len > 0:
                    if pending_chunks:
                        if not client_sent_any:
                            for chunk in pending_chunks:
                                yield chunk
                        pending_chunks.clear()
                    return
            if pending_chunks:
                for chunk in pending_chunks:
                    yield chunk
                pending_chunks.clear()
                return
            if yielded_len == 0:
                raise ChatGPTWebError("ChatGPT Web 没有返回有效文本。")
    finally:
        if response is not None:
            response.close()
        session.close()


def stream_chatgpt_web(
    messages: list[dict[str, Any]],
    model: str | None = None,
    request_options: dict[str, Any] | None = None,
) -> Iterator[ChatGPTWebCompletion]:
    retries = _prompt_network_error_retries(request_options)
    max_attempts = retries + 1
    for attempt in range(max_attempts):
        started_at = time.monotonic()
        yielded_chunk = False
        try:
            for chunk in _stream_chatgpt_web_once(messages, model, request_options):
                yielded_chunk = True
                yield chunk
            return
        except Exception as exc:
            elapsed = time.monotonic() - started_at
            if yielded_chunk or attempt >= retries or not _should_retry_prompt_network_error(
                exc,
                elapsed_sec=elapsed,
                request_options=request_options,
            ):
                raise
            log(
                "ChatGPT Web 流式发送提示词遇到快速可重试错误，将重试: "
                f"attempt={attempt + 1}/{max_attempts}, elapsed={elapsed:.2f}s, error={exc}"
            )
            _sleep_before_prompt_retry(request_options)


def openai_chat_response(result: ChatGPTWebCompletion, *, created: int | None = None) -> dict[str, Any]:
    timestamp = int(created or time.time())
    payload = {
        "id": f"chatcmpl-chatgpt-web-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": timestamp,
        "model": result.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.text},
                "finish_reason": "stop",
            }
        ],
    }
    if result.conversation_id:
        payload["conversation_id"] = result.conversation_id
    if result.message_id:
        payload["parent_message_id"] = result.message_id
        payload["response_id"] = result.message_id
    return payload


def _image_chat_content_from_result(result: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in result.get("data") or []:
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("url") or "").strip()
        if not image_url:
            b64_json = str(item.get("b64_json") or "").strip()
            if b64_json:
                image_url = f"data:image/png;base64,{b64_json}"
        if image_url:
            lines.append(f"![generated image]({image_url})")
    return "\n".join(lines)


def openai_error(message: str, *, code: str = "chatgpt_web_error") -> dict[str, Any]:
    return {
        "error": {
            "message": str(message),
            "type": "api_error",
            "code": code,
        }
    }


def upstream_network_error_message(exc: BaseException) -> str:
    detail = str(exc or "").strip()
    if len(detail) > 500:
        detail = f"{detail[:500]}..."
    base = "上游网络连接失败，请检查代理/网络后重试。"
    return f"{base} 详情：{detail}" if detail else base


NETWORK_CONNECTIVITY_KEYWORDS = (
    "curl: (28)",
    "curl: (35)",
    "connect timeout",
    "connect timed out",
    "connection timeout",
    "connection timed out",
    "read timed out",
    "operation timed out",
    "timed out",
    "timeout",
    "failed to connect",
    "could not connect",
    "couldn't connect",
    "connection refused",
    "connection reset",
    "connection aborted",
    "tls connect error",
    "ssl connect",
    "proxy",
    "name resolution",
    "could not resolve",
    "resolve host",
    "network is unreachable",
    "no route to host",
)


def _exception_text_chain(exc: BaseException) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(str(current or ""))
        for arg in getattr(current, "args", ()) or ():
            if arg is not current:
                parts.append(str(arg or ""))
        next_exc = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        current = next_exc if isinstance(next_exc, BaseException) else None
    return "\n".join(part for part in parts if part).lower()


def is_upstream_connectivity_error(exc: BaseException) -> bool:
    detail = _exception_text_chain(exc)
    if detail and any(keyword in detail for keyword in NETWORK_CONNECTIVITY_KEYWORDS):
        return True
    response = getattr(exc, "response", None)
    if response is not None:
        return False
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if not isinstance(exc, NETWORK_REQUEST_EXCEPTIONS):
        return False
    if not detail:
        return True
    return False


def raise_upstream_network_error(exc: BaseException) -> None:
    raise ChatGPTWebError(
        upstream_network_error_message(exc),
        status_code=502,
        error_code="upstream_network_error",
    ) from exc


def _merged_retry_options(request_options: dict[str, Any] | None) -> dict[str, Any]:
    options = dict(CONFIG)
    options.update(dict(request_options or {}))
    return options


def _int_retry_option(
    request_options: dict[str, Any] | None,
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = _merged_retry_options(request_options).get(key, default)
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = int(default)
    return max(int(minimum), min(int(maximum), number))


def _float_retry_option(
    request_options: dict[str, Any] | None,
    key: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = _merged_retry_options(request_options).get(key, default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(float(minimum), min(float(maximum), number))


def _prompt_network_error_retries(request_options: dict[str, Any] | None) -> int:
    return _int_retry_option(
        request_options,
        "prompt_network_error_retries",
        default=2,
        minimum=0,
        maximum=5,
    )


def _prompt_network_error_retry_window_sec(request_options: dict[str, Any] | None) -> float:
    return _float_retry_option(
        request_options,
        "prompt_network_error_retry_window_sec",
        default=15.0,
        minimum=0.0,
        maximum=120.0,
    )


def _prompt_network_error_retry_delay_sec(request_options: dict[str, Any] | None) -> float:
    return _float_retry_option(
        request_options,
        "prompt_network_error_retry_delay_sec",
        default=2.0,
        minimum=0.0,
        maximum=10.0,
    )


def _is_prompt_retryable_network_error(exc: BaseException) -> bool:
    detail = _exception_text_chain(exc)
    if "curl: (28)" not in detail:
        return False
    return is_upstream_connectivity_error(exc)


def _is_prompt_retryable_upstream_502(exc: BaseException) -> bool:
    return isinstance(exc, ChatGPTWebUpstreamError) and int(getattr(exc, "status_code", 0) or 0) == 502


def _should_retry_prompt_network_error(
    exc: BaseException,
    *,
    elapsed_sec: float,
    request_options: dict[str, Any] | None,
) -> bool:
    if elapsed_sec > _prompt_network_error_retry_window_sec(request_options):
        return False
    return _is_prompt_retryable_network_error(exc) or _is_prompt_retryable_upstream_502(exc)


def _sleep_before_prompt_retry(request_options: dict[str, Any] | None) -> None:
    delay = _prompt_network_error_retry_delay_sec(request_options)
    if delay > 0:
        time.sleep(delay)


def chatgpt_error_from_exception(exc: BaseException) -> ChatGPTWebError:
    if isinstance(exc, ChatGPTWebError):
        return exc
    if isinstance(exc, NETWORK_REQUEST_EXCEPTIONS):
        response = getattr(exc, "response", None)
        if response is not None:
            return _upstream_error_from_response(response)
        return ChatGPTWebError(
            upstream_network_error_message(exc),
            status_code=502,
            error_code="upstream_network_error",
        )
    return ChatGPTWebError(str(exc), status_code=502, error_code="chatgpt_web_error")


def _is_client_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
        return True
    if isinstance(exc, OSError):
        return exc.errno in {errno.EPIPE, errno.ECONNABORTED, errno.ECONNRESET, 10053, 10054}
    return False


class ChatGPTWeb2APIHandler(BaseHTTPRequestHandler):
    server_version = "DeepCatChatGPTWeb2API/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        log(fmt % args if args else fmt)

    def _write_json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError) as exc:
            if not _is_client_disconnect(exc):
                raise
            log(f"客户端在响应写入前已断开连接: {exc}")

    def _read_json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        max_bytes = int(CONFIG.get("max_request_body_bytes") or DEFAULT_CONFIG["max_request_body_bytes"])
        if length <= 0:
            return {}
        if length > max_bytes:
            raise ChatGPTWebError(f"请求体过大，超过限制 {max_bytes} 字节。")
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ChatGPTWebError(f"请求体不是有效 JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise ChatGPTWebError("请求体必须是 JSON 对象。")
        return body

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/":
            self._write_json(
                200,
                {
                    "status": "ok",
                    "service": "chatgpt_web2api",
                    "build": SERVICE_BUILD,
                    "models": [model["id"] for model in CHATGPT_WEB_MODELS],
                    "has_auth": bool(str(CONFIG.get("api_key") or "").strip()),
                },
            )
            return
        if path in {"/v1/models", "/models"}:
            self._write_json(200, {"object": "list", "data": openai_model_list()})
            return
        self._write_json(404, openai_error(f"Unknown route: {self.path}", code="not_found"))

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in {"/v1/images/generations", "/images/generations"}:
            try:
                body = self._read_json_body()
                log(
                    "收到 Images Generations 请求: "
                    f"path={path}, model={body.get('model') or 'gpt-image-2'}, n={body.get('n') or 1}"
                )
                result = openai_image_generation_response(body)
                self._write_json(200, result)
            except ChatGPTWebError as exc:
                self._write_json(
                    getattr(exc, "status_code", 502),
                    openai_error(str(exc), code=getattr(exc, "error_code", "chatgpt_web_error")),
                )
            except NETWORK_REQUEST_EXCEPTIONS as exc:
                response = getattr(exc, "response", None)
                if response is not None:
                    upstream_exc = _upstream_error_from_response(response)
                    self._write_json(
                        getattr(upstream_exc, "status_code", 502),
                        openai_error(str(upstream_exc), code=getattr(upstream_exc, "error_code", "upstream_error")),
                    )
                    return
                log(f"上游网络连接失败: {exc}")
                self._write_json(502, openai_error(upstream_network_error_message(exc), code="upstream_network_error"))
            except Exception as exc:
                import traceback
                tb = traceback.format_exc()
                log(f"Images Generations 请求内部错误: {exc}\n{tb}")
                self._write_json(500, openai_error(f"{type(exc).__name__}: {exc}\n{tb}", code="internal_error"))
            return
        if path not in {"/v1/chat/completions", "/chat/completions"}:
            self._write_json(404, openai_error(f"Unknown route: {self.path}", code="not_found"))
            return
        try:
            body = self._read_json_body()
            messages = body.get("messages")
            if not isinstance(messages, list):
                raise ChatGPTWebError("缺少 messages 数组。")
            model = normalize_model(str(body.get("model") or DEFAULT_MODEL))
            log(
                "收到 Chat Completions 请求: "
                f"path={path}, model={model}, stream={bool(body.get('stream'))}, messages={len(messages)}"
            )
            if _is_chatgpt_web_image_model(model) or (
                isinstance(body.get("modalities"), list)
                and "image" in {str(item or "").strip().lower() for item in body.get("modalities") or []}
            ):
                prompt = _image_prompt_from_messages(messages)
                image_model = model if _is_chatgpt_web_image_model(model) else "gpt-image-2"
                image_options = dict(body)
                image_options.setdefault("localize_generated_images", True)
                image_result = generate_chatgpt_web_image(
                    prompt,
                    image_model,
                    image_options,
                    image_uploads=_image_uploads_from_request_body(image_options),
                )
                completion = ChatGPTWebCompletion(
                    text=image_result.text,
                    model=str(image_model),
                    conversation_id=image_result.conversation_id,
                    message_id=image_result.message_id,
                )
                if bool(body.get("stream")):
                    self._handle_static_stream(completion)
                    return
                self._write_json(200, openai_chat_response(completion))
                return
            if bool(body.get("stream")):
                self._handle_stream(messages, model, body)
                return
            result = complete_chatgpt_web(messages, model, body)
            self._write_json(200, openai_chat_response(result))
        except ChatGPTWebError as exc:
            self._write_json(
                getattr(exc, "status_code", 502),
                openai_error(str(exc), code=getattr(exc, "error_code", "chatgpt_web_error")),
            )
        except NETWORK_REQUEST_EXCEPTIONS as exc:
            response = getattr(exc, "response", None)
            if response is not None:
                upstream_exc = _upstream_error_from_response(response)
                self._write_json(
                    getattr(upstream_exc, "status_code", 502),
                    openai_error(str(upstream_exc), code=getattr(upstream_exc, "error_code", "upstream_error")),
                )
                return
            log(f"上游网络连接失败: {exc}")
            self._write_json(502, openai_error(upstream_network_error_message(exc), code="upstream_network_error"))
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            log(f"API 请求内部错误: {exc}\n{tb}")
            self._write_json(500, openai_error(f"{type(exc).__name__}: {exc}\n{tb}", code="internal_error"))

    def _write_sse(self, payload: dict[str, Any] | str) -> None:
        if isinstance(payload, str):
            data = payload
        else:
            data = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _write_stream_error_content(self, chunk_id: str, created: int, model: str, message: str) -> None:
        self._write_sse(
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": message}, "finish_reason": None}],
            }
        )
        self._write_sse(
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
        self._write_sse("[DONE]")

    def _handle_static_stream(self, result: ChatGPTWebCompletion) -> None:
        created = int(time.time())
        chunk_id = f"chatcmpl-chatgpt-web-{uuid.uuid4().hex}"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self._write_sse(
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": result.model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
        )
        if result.text:
            self._write_sse(
                {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": result.model,
                    "choices": [{"index": 0, "delta": {"content": result.text}, "finish_reason": None}],
                }
            )
        self._write_sse(
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": result.model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
        self._write_sse("[DONE]")

    def _handle_stream(self, messages: list[dict[str, Any]], model: str, body: dict[str, Any]) -> None:
        conversation_append_enabled = _conversation_append_enabled(body)
        created = int(time.time())
        chunk_id = f"chatcmpl-chatgpt-web-{uuid.uuid4().hex}"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self._write_sse(
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
        )
        try:
            last: ChatGPTWebCompletion | None = None
            accumulated_text = ""
            for event in stream_chatgpt_web(messages, model, body):
                last = event
                if not event.text:
                    continue
                accumulated_text = event.snapshot_text or _merge_stream_snapshot(accumulated_text, event.text)
                self._write_sse(
                    {
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": event.text}, "finish_reason": None}],
                    }
                )
            final_chunk: dict[str, Any] = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            if last and last.conversation_id:
                final_chunk["conversation_id"] = last.conversation_id
            if last and last.message_id:
                final_chunk["parent_message_id"] = last.message_id
                final_chunk["response_id"] = last.message_id
            self._write_sse(final_chunk)

            if conversation_append_enabled and last and last.conversation_id and last.message_id:
                history = list(messages)
                history.append({"role": "assistant", "content": accumulated_text})
                history_hash = _get_history_hash(history)
                if history_hash:
                    _update_cache(history_hash, (last.conversation_id, last.message_id))
                    log(f"[会话追加] 流式结束，已缓存历史映射: hash={history_hash} -> ({last.conversation_id}, {last.message_id})")
        except Exception as exc:
            friendly_exc = chatgpt_error_from_exception(exc)
            log(f"流式响应失败: {friendly_exc}")
            try:
                self._write_stream_error_content(chunk_id, created, model, str(friendly_exc))
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError) as write_exc:
                if not _is_client_disconnect(write_exc):
                    raise
                log(f"客户端在流式错误写回前已断开连接: {write_exc}")
