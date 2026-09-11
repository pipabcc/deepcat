from __future__ import annotations

import atexit
import json
import logging
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Optional

import httpx
from urllib.parse import urlsplit

from deepcat.settings_store import (
    DEEPLX_INFO_ADDRESS,
    get_settings_path,
    infer_translator_model_type,
    load_settings,
    normalize_anthropic_messages_url,
    normalize_openai_chat_base_url,
    normalize_openai_images_url,
    normalize_openai_responses_url,
)
from deepcat.utils.logger import get_logger
from deepcat.utils.http_client_pool import HttpClientPool


SERVICE_MODEL_ID = "deepcat-translate"
DEFAULT_SERVICE_API_KEY = "sk-deepcat-local"
STREAM_CONNECT_TIMEOUT_SECONDS = 90.0
STREAM_READ_TIMEOUT_SECONDS = 600.0
STREAM_UPSTREAM_ATTEMPTS = 2
# QA 对话允许长推理，但必须有限超时，否则上游挂起会永久占用一个服务线程
QA_UPSTREAM_TIMEOUT_SECONDS = 600.0
# 生图单次请求可能数十秒到数分钟，给大但有限的超时
IMAGE_GENERATION_TIMEOUT_SECONDS = 300.0
IMAGE_MODEL_TRANSLATE_ERROR = "当前模型是图像生成模型（/v1/images/generations），不支持文本翻译。请在翻译模型中选择文本大模型。"
logger = get_logger("deepcat.translator_engine", level=logging.INFO)
_http_client_pool = HttpClientPool()
atexit.register(_http_client_pool.close)


class TranslationError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranslatorRuntime:
    source_lang: str
    target_lang: str
    proxy_url: str
    display_name: str
    model_type: str
    base_url: str
    model_name: str
    api_key: str
    use_proxy: bool
    purpose: str = "translate"


def _default_model_for_purpose(translator: dict[str, Any], purpose: str) -> str:
    normalized = str(purpose or "").strip().lower()
    if normalized == "qa":
        return str(
            translator.get("qa_model")
            or translator.get("current_model")
            or translator.get("translate_model")
            or ""
        ).strip()
    return str(
        translator.get("translate_model")
        or translator.get("current_model")
        or translator.get("qa_model")
        or ""
    ).strip()


_translator_settings_cache_lock = threading.Lock()
_translator_settings_cache_sig: Optional[tuple[str, int, int]] = None
_translator_settings_cache_value: Optional[dict[str, Any]] = None


def _load_translator_settings_cached() -> dict[str, Any]:
    """按 (path, mtime_ns, size) 签名缓存 ui["translator"]。

    沉浸式翻译等本地服务每个请求都会调用 load_translator_runtime，
    直接 load_settings 会全量解析 ~100KB settings.json 并额外打开
    PromptStore（SQLite）——而 translator 配置只消费 ui["translator"]。
    settings.json 任何写路径都经 os.replace 落盘，mtime 变化即自动失效。
    """
    global _translator_settings_cache_sig, _translator_settings_cache_value
    sig: Optional[tuple[str, int, int]] = None
    try:
        path = get_settings_path()
        st = path.stat()
        sig = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    if sig is not None:
        with _translator_settings_cache_lock:
            if (
                _translator_settings_cache_value is not None
                and _translator_settings_cache_sig == sig
            ):
                return dict(_translator_settings_cache_value)
    settings = load_settings(validate_dirs=False)
    translator = dict((settings.ui or {}).get("translator") or {})
    if sig is not None:
        with _translator_settings_cache_lock:
            _translator_settings_cache_sig = sig
            _translator_settings_cache_value = dict(translator)
    return translator


def load_translator_runtime(model_name: Optional[str] = None, *, purpose: str = "translate") -> TranslatorRuntime:
    translator = _load_translator_settings_cached()
    configs = translator.get("model_configs")
    if not isinstance(configs, dict):
        configs = {}

    requested = str(model_name or "").strip()
    default_selected = _default_model_for_purpose(translator, purpose)
    selected = requested or default_selected
    if selected not in configs and configs:
        fallback = default_selected if default_selected in configs else str(next(iter(configs.keys())))
        if requested:
            logger.warning(
                "translator model is not configured; falling back to purpose default: requested=%s fallback=%s purpose=%s",
                requested,
                fallback,
                purpose,
            )
        selected = fallback
    cfg = dict(configs.get(selected) or {})
    if not cfg and selected:
        cfg = {"model_name": selected, "model_type": "glm"}

    model_type = str(cfg.get("model_type") or infer_translator_model_type(selected, cfg) or "glm").lower()
    return TranslatorRuntime(
        source_lang=str(translator.get("source_lang") or "auto"),
        target_lang=str(translator.get("target_lang") or "bidirectional"),
        proxy_url=str(translator.get("proxy_url") or ""),
        display_name=selected or SERVICE_MODEL_ID,
        model_type=model_type,
        base_url=str(cfg.get("base_url") or "").strip(),
        model_name=str(cfg.get("model_name") or selected or SERVICE_MODEL_ID).strip(),
        api_key=str(cfg.get("api_key") or "").strip(),
        use_proxy=bool(cfg.get("use_proxy", False)),
        purpose=purpose,
    )


def available_models() -> list[dict[str, str]]:
    translator = _load_translator_settings_cached()
    configs = translator.get("model_configs")
    data = [{"id": SERVICE_MODEL_ID, "owned_by": "deepcat"}]
    if isinstance(configs, dict):
        for name, cfg in configs.items():
            if not isinstance(cfg, dict):
                continue
            model_id = str(name or "").strip()
            if model_id:
                data.append({"id": model_id, "owned_by": "deepcat"})
    return data


def normalize_proxy_url(proxy_url: str) -> str:
    proxy = str(proxy_url or "").strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        return f"socks5h://{proxy}"
    if proxy.startswith("socks5://"):
        return proxy.replace("socks5://", "socks5h://", 1)
    return proxy


def _is_loopback_url(url: str) -> bool:
    try:
        parsed = httpx.URL(str(url or ""))
        host = str(parsed.host or "").lower()
    except Exception:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def _http_timeout(timeout: Optional[float], *, stream: bool = False) -> httpx.Timeout:
    if timeout is None:
        return httpx.Timeout(None)
    seconds = max(float(timeout), 1.0)
    if not stream:
        return httpx.Timeout(seconds)
    return httpx.Timeout(
        connect=max(seconds, STREAM_CONNECT_TIMEOUT_SECONDS),
        read=max(seconds, STREAM_READ_TIMEOUT_SECONDS),
        write=max(seconds, 60.0),
        pool=max(min(seconds, 60.0), 5.0),
    )


def _preview(value: object, limit: int = 600) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[: max(0, int(limit) - 3)] + "..."


def _redact_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = httpx.URL(raw)
        host = parsed.host or ""
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path or ""
        query = "?..." if parsed.query else ""
        return f"{parsed.scheme}://{host}{port}{path}{query}"
    except Exception:
        return raw.split("?", 1)[0] + ("?..." if "?" in raw else "")


def _timeout_summary(timeout: httpx.Timeout) -> dict[str, float | None]:
    return {
        "connect": timeout.connect,
        "read": timeout.read,
        "write": timeout.write,
        "pool": timeout.pool,
    }


def _payload_text_chars(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_payload_text_chars(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_payload_text_chars(child) for child in value)
    return 0


def _payload_summary(kwargs: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    payload = kwargs.get("json")
    if isinstance(payload, dict):
        messages = payload.get("messages")
        contents = payload.get("contents")
        tools = payload.get("tools")
        summary.update(
            {
                "json_keys": sorted(str(key) for key in payload.keys()),
                "payload_model": payload.get("model"),
                "payload_stream": bool(payload.get("stream", False)),
                "message_count": len(messages) if isinstance(messages, list) else None,
                "contents_count": len(contents) if isinstance(contents, list) else None,
                "tools_count": len(tools) if isinstance(tools, list) else 0,
                "text_chars": _payload_text_chars(payload),
            }
        )
    elif payload is not None:
        summary["json_type"] = type(payload).__name__
    params = kwargs.get("params")
    if isinstance(params, dict) and params:
        summary["param_keys"] = sorted(str(key) for key in params.keys())
    headers = kwargs.get("headers")
    if isinstance(headers, dict) and headers:
        summary["content_type"] = headers.get("Content-Type") or headers.get("content-type")
        summary["has_authorization"] = bool(headers.get("Authorization") or headers.get("authorization"))
        summary["has_api_key"] = bool(headers.get("x-goog-api-key") or headers.get("X-API-Key") or headers.get("x-api-key"))
    return {key: value for key, value in summary.items() if value is not None}


def _runtime_summary(runtime: TranslatorRuntime, url: str) -> dict[str, Any]:
    proxy = normalize_proxy_url(runtime.proxy_url) if runtime.use_proxy and not _is_loopback_url(url) else ""
    return {
        "display_model": runtime.display_name,
        "upstream_model": runtime.model_name,
        "model_type": runtime.model_type,
        "base_url": _redact_url(runtime.base_url),
        "proxy_enabled": bool(proxy),
    }


def _format_summary(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _response_preview(response: httpx.Response, limit: int = 900) -> str:
    try:
        return _preview(response.content, limit)
    except Exception:
        try:
            return _preview(response.text, limit)
        except Exception:
            return "(response body not available)"


def _is_retryable_upstream_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status in {408, 409, 425, 429, 500, 502, 503, 504}
    return False


def _client(url: str, runtime: TranslatorRuntime, timeout: Optional[float], *, stream: bool = False) -> httpx.Client:
    kwargs: dict[str, Any] = {"timeout": _http_timeout(timeout, stream=stream), "trust_env": False}
    transport = _inprocess_transport(url)
    if transport is not None:
        # 内置 Web2API 服务在进程内直调：不监听端口，因此不受防火墙规则影响
        return httpx.Client(transport=transport, **kwargs)
    proxy = normalize_proxy_url(runtime.proxy_url) if runtime.use_proxy and not _is_loopback_url(url) else ""
    if proxy:
        try:
            return httpx.Client(proxy=proxy, **kwargs)
        except TypeError:
            return httpx.Client(proxies=proxy, **kwargs)
    return httpx.Client(**kwargs)


def _inprocess_transport(url: str) -> Optional[httpx.BaseTransport]:
    """URL 指向内置 Web2API 服务时返回进程内传输层，否则返回 None。"""
    from deepcat.local_gemini_web2api_server import local_gemini_httpx_transport

    return local_gemini_httpx_transport(url)


@contextmanager
def _request_client(
    url: str, runtime: TranslatorRuntime, timeout: Optional[float],
    *, cancel_event: Optional[threading.Event],
) -> Iterator[httpx.Client]:
    if cancel_event is not None:
        # 取消会关闭客户端，因此可取消请求使用独立实例，不影响其他请求。
        with _client(url, runtime, timeout) as client:
            yield client
        return
    parsed = urlsplit(url)
    key = (_client, httpx.Client, parsed.scheme, parsed.netloc, runtime, timeout)
    with _http_client_pool.lease(key, lambda: _client(url, runtime, timeout)) as client:
        yield client


@contextmanager
def _stream_request_client(
    url: str, runtime: TranslatorRuntime, timeout: Optional[float],
) -> Iterator[httpx.Client]:
    """流式请求复用连接池；这些路径没有单请求取消，借用独占不互相影响。"""
    parsed = urlsplit(url)
    key = (_client, httpx.Client, "stream", parsed.scheme, parsed.netloc, runtime, timeout)
    with _http_client_pool.lease(key, lambda: _client(url, runtime, timeout, stream=True)) as client:
        yield client


def _request(
    method: str,
    url: str,
    runtime: TranslatorRuntime,
    *,
    timeout: Optional[float] = 30.0,
    cancel_event: Optional[threading.Event] = None,
    **kwargs: Any,
) -> httpx.Response:
    request_id = uuid.uuid4().hex[:8]
    method_name = str(method).upper()
    timeout_obj = _http_timeout(timeout, stream=False)
    started = time.perf_counter()
    logger.info(
        "upstream request start: id=%s method=%s url=%s timeout=%s runtime=%s payload=%s",
        request_id,
        method_name,
        _redact_url(url),
        _format_summary(_timeout_summary(timeout_obj)),
        _format_summary(_runtime_summary(runtime, url)),
        _format_summary(_payload_summary(kwargs)),
    )
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise TranslationError("Upstream request cancelled.")
        with _request_client(url, runtime, timeout, cancel_event=cancel_event) as client:
            finished = threading.Event()
            if cancel_event is not None:
                def close_when_cancelled() -> None:
                    while not finished.wait(0.1):
                        if cancel_event.is_set():
                            try:
                                client.close()
                            except Exception:
                                pass
                            return

                threading.Thread(
                    target=close_when_cancelled,
                    name="DeepCatUpstreamCancelWatcher",
                    daemon=True,
                ).start()
            try:
                response = client.request(method_name, url, **kwargs)
            finally:
                finished.set()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "upstream response received: id=%s status=%s elapsed_ms=%s bytes=%s content_type=%s",
            request_id,
            response.status_code,
            elapsed_ms,
            len(response.content or b""),
            response.headers.get("content-type", ""),
        )
        if response.is_error:
            logger.error(
                "upstream response error body: id=%s status=%s preview=%s",
                request_id,
                response.status_code,
                _response_preview(response),
            )
        response.raise_for_status()
        return response
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.error(
            "upstream request failed: id=%s method=%s url=%s elapsed_ms=%s error=%s",
            request_id,
            method_name,
            _redact_url(url),
            elapsed_ms,
            repr(exc),
        )
        raise


def _json_response(response: httpx.Response) -> Any:
    try:
        return json.loads(response.content.decode("utf-8-sig", errors="strict"))
    except Exception:
        return response.json()


def _iter_sse_json(response: httpx.Response) -> Iterator[Any]:
    buffered: list[str] = []

    def flush_buffer() -> Optional[Any]:
        if not buffered:
            return None
        data = "\n".join(buffered).strip()
        buffered.clear()
        if not data or data == "[DONE]":
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            # 多行 data 合并后仍非 JSON：按无效载荷忽略，不让整条流中断
            logger.debug("upstream SSE 缓冲载荷不是有效 JSON，已忽略：%s", _preview(data, 200))
            return None

    for raw_line in response.iter_lines():
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.strip()
        if not line:
            item = flush_buffer()
            if item is not None:
                yield item
            continue
        if line.startswith(":"):
            continue
        # SSE 事件字段（event/id/retry）不是数据载荷，不能进入 JSON 缓冲，
        # 否则 Anthropic 等提供商的流会在首个空行处解析失败。
        if line.startswith("event:") or line.startswith("id:") or line.startswith("retry:"):
            continue
        data = line[5:].strip() if line.startswith("data:") else line
        if not data:
            continue
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            buffered.append(data)

    item = flush_buffer()
    if item is not None:
        yield item


def _value(s: object) -> str:
    return str(s or "").strip()


def _is_auto_language(language: object) -> bool:
    s = _value(language).lower()
    return (
        not s
        or s in {"auto", "auto-detect", "automatic", "detect", "source", "original"}
        or s.startswith("auto")
        or s.startswith("鑷")
        or "自动" in s
    )


def _is_bidirectional_language(language: object) -> bool:
    s = _value(language).lower()
    return s in {"bidirectional", "bilingual", "中英互译", "双向", "雙向"} or s.startswith("鍙")


def _canonical_language(language: object) -> str:
    s = _value(language)
    lower = s.lower()
    if _is_auto_language(s):
        return "auto"
    if _is_bidirectional_language(s):
        return "bidirectional"
    aliases = {
        "zh": "Chinese",
        "zh-cn": "Chinese",
        "zh-hans": "Chinese",
        "chinese": "Chinese",
        "simplified chinese": "Chinese",
        "中文": "Chinese",
        "简体中文": "Chinese",
        "涓": "Chinese",
        "en": "English",
        "eng": "English",
        "english": "English",
        "英文": "English",
        "鑻": "English",
        "ja": "Japanese",
        "jp": "Japanese",
        "japanese": "Japanese",
        "日文": "Japanese",
        "日语": "Japanese",
        "鏃": "Japanese",
        "ko": "Korean",
        "kr": "Korean",
        "korean": "Korean",
        "韩文": "Korean",
        "韩语": "Korean",
        "闊": "Korean",
        "fr": "French",
        "french": "French",
        "法文": "French",
        "法语": "French",
        "娉": "French",
        "de": "German",
        "german": "German",
        "德文": "German",
        "德语": "German",
        "寰": "German",
        "es": "Spanish",
        "spanish": "Spanish",
        "西班牙文": "Spanish",
        "西班牙语": "Spanish",
        "瑗": "Spanish",
    }
    if lower in aliases:
        return aliases[lower]
    for prefix, value in aliases.items():
        if len(prefix) == 1 and s.startswith(prefix):
            return value
    return s or "Chinese"


def _is_chinese_text(text: str) -> bool:
    if not text:
        return False
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    return len(cjk) > len(text) * 0.3


def resolve_target_language(text: str, source_lang: object, target_lang: object) -> str:
    target = _canonical_language(target_lang)
    source = _canonical_language(source_lang)
    if target in {"auto", "bidirectional"}:
        return "English" if _is_chinese_text(text) else "Chinese"
    if source not in {"auto", "bidirectional"} and source == target:
        return source
    return target


def _provider_lang_code(language: str, provider: str, *, source: bool = False) -> str:
    lang = _canonical_language(language)
    if lang in {"auto", "bidirectional"}:
        return "AUTO" if provider == "deeplx" else "auto"
    maps = {
        "microsoft": {
            "Chinese": "zh-Hans",
            "English": "en",
            "Japanese": "ja",
            "Korean": "ko",
            "French": "fr",
            "German": "de",
            "Spanish": "es",
        },
        "google": {
            "Chinese": "zh-CN",
            "English": "en",
            "Japanese": "ja",
            "Korean": "ko",
            "French": "fr",
            "German": "de",
            "Spanish": "es",
        },
        "deeplx": {
            "Chinese": "ZH",
            "English": "EN",
            "Japanese": "JA",
            "Korean": "KO",
            "French": "FR",
            "German": "DE",
            "Spanish": "ES",
        },
    }
    return maps.get(provider, {}).get(lang, "AUTO" if provider == "deeplx" and source else "auto")


def _is_hunyuan_mt(runtime: TranslatorRuntime) -> bool:
    hints = " ".join(
        [
            runtime.display_name.lower(),
            runtime.model_name.lower(),
            runtime.base_url.lower(),
        ]
    )
    return any(token in hints for token in ("hy-mt", "hunyuan", "ggml-model-q4_k_m")) or runtime.display_name.startswith("鑵")


def _has_client_translation_prompt(messages: list[dict[str, Any]]) -> bool:
    """检测客户端是否已经提供翻译指令。"""
    if not isinstance(messages, list):
        return False
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip().lower()
        if role in {"system", "developer"}:
            content = msg.get("content")
            text = _message_text(content).strip() if content else ""
            if text and _looks_like_translation_prompt(text):
                return True
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip().lower()
        if role != "user":
            continue
        text = _message_text(msg.get("content")).strip()
        return bool(text and _strip_translation_prompt(text))
    return False


def _build_translation_prompt(text: str, target_lang: str, runtime: TranslatorRuntime) -> str:
    if _is_hunyuan_mt(runtime):
        return f"Translate the following segment into {target_lang}, without additional explanation.\n\n{text}"
    return (
        "You are a translation engine. Translate only the text inside "
        "<translate_input> into {target}. Keep the original formatting. "
        "Do not explain, answer questions, add notes, or include tags.\n\n"
        "<translate_input>\n{text}\n</translate_input>"
    ).format(target=target_lang, text=text)


def _chat_base_url(base_url: str) -> str:
    base = normalize_openai_chat_base_url(base_url)
    if not base:
        raise TranslationError("Missing base_url in translator settings.")
    return base


def _auth_header(runtime: TranslatorRuntime) -> dict[str, str]:
    if runtime.model_type == "chatgpt_web":
        return {}
    if not runtime.api_key:
        return {}
    return {"Authorization": f"Bearer {runtime.api_key}"}


def _clean_model_text(text: object) -> str:
    return str(text or "").strip()


@contextmanager
def _local_server_context(runtime: TranslatorRuntime) -> Iterator[None]:
    hy_active = False
    gemini_active = False
    chatgpt_active = False
    cfg = {
        "display_name": runtime.display_name,
        "model_type": runtime.model_type,
        "base_url": runtime.base_url,
        "model_name": runtime.model_name,
        "api_key": runtime.api_key,
        "use_proxy": runtime.use_proxy,
        "proxy_url": runtime.proxy_url,
    }
    try:
        try:
            from deepcat.local_hunyuan_server import ensure_server, identify_spec

            if identify_spec(cfg) is not None:
                logger.info(
                    "local upstream helper ensure start: kind=hunyuan runtime=%s",
                    _format_summary(_runtime_summary(runtime, runtime.base_url)),
                )
                ensure_server(cfg)
                hy_active = True
                logger.info("local upstream helper ensure ready: kind=hunyuan display_model=%s", runtime.display_name)
        except Exception:
            if _is_hunyuan_mt(runtime):
                logger.error("local upstream helper ensure failed: kind=hunyuan display_model=%s", runtime.display_name)
                raise

        try:
            from deepcat.local_gemini_web2api_server import ensure_server, identify_spec

            if identify_spec(cfg) is not None:
                logger.info(
                    "local upstream helper ensure start: kind=gemini_web2api runtime=%s",
                    _format_summary(_runtime_summary(runtime, runtime.base_url)),
                )
                ensure_server(cfg)
                gemini_active = True
                logger.info("local upstream helper ensure ready: kind=gemini_web2api display_model=%s", runtime.display_name)
        except Exception:
            if "8081" in runtime.base_url:
                logger.error("local upstream helper ensure failed: kind=gemini_web2api display_model=%s", runtime.display_name)
                raise

        try:
            from deepcat.local_chatgpt_web2api_server import ensure_server, identify_spec

            if identify_spec(cfg) is not None:
                logger.info(
                    "local upstream helper ensure start: kind=chatgpt_web2api runtime=%s",
                    _format_summary(_runtime_summary(runtime, runtime.base_url)),
                )
                ensure_server(cfg)
                chatgpt_active = True
                logger.info("local upstream helper ensure ready: kind=chatgpt_web2api display_model=%s", runtime.display_name)
        except Exception:
            if runtime.model_type == "chatgpt_web" or "8082" in runtime.base_url:
                logger.error("local upstream helper ensure failed: kind=chatgpt_web2api display_model=%s", runtime.display_name)
                raise

        yield
    finally:
        if hy_active:
            try:
                from deepcat.local_hunyuan_server import release_server

                release_server(cfg)
            except Exception:
                pass
        if gemini_active:
            try:
                from deepcat.local_gemini_web2api_server import release_server

                release_server(cfg)
            except Exception:
                pass
        if chatgpt_active:
            try:
                from deepcat.local_chatgpt_web2api_server import release_server

                release_server(cfg)
            except Exception:
                pass


def translate_text(
    text: str,
    *,
    source_lang: Optional[str] = None,
    target_lang: Optional[str] = None,
    model_name: Optional[str] = None,
) -> str:
    runtime = load_translator_runtime(model_name)
    source = source_lang if source_lang is not None else runtime.source_lang
    target = target_lang if target_lang is not None else runtime.target_lang
    text = str(text or "")
    if not text.strip():
        return ""
    actual_target = resolve_target_language(text, source, target)
    if _canonical_language(source) == actual_target:
        return text

    model_type = runtime.model_type
    if model_type == "microsoft_free":
        return _translate_with_microsoft(text, source, actual_target, runtime)
    if model_type == "google_free":
        return _translate_with_google(text, source, actual_target, runtime)
    if model_type == "deeplx":
        return _translate_with_deeplx(text, source, actual_target, runtime)
    if model_type == "openai_images":
        raise TranslationError(IMAGE_MODEL_TRANSLATE_ERROR)

    prompt = _build_translation_prompt(text, actual_target, runtime)
    if model_type == "gemini":
        return _chat_gemini([{"role": "user", "content": prompt}], runtime)
    if model_type == "anthropic":
        return _chat_anthropic([{"role": "user", "content": prompt}], runtime)
    if model_type == "openai_responses":
        return _chat_openai_responses([{"role": "user", "content": prompt}], runtime)
    return _chat_glm([{"role": "user", "content": prompt}], runtime)


def chat_completion(
    messages: list[dict[str, Any]],
    *,
    model_name: Optional[str] = None,
    source_lang: Optional[str] = None,
    target_lang: Optional[str] = None,
    force_translate: bool = False,
    request_options: Optional[dict[str, Any]] = None,
) -> str:
    runtime = load_translator_runtime(
        None if model_name == SERVICE_MODEL_ID else model_name,
        purpose="translate" if force_translate else "qa",
    )
    # 非 LLM 翻译引擎不支持自定义 prompt，只能使用内置逻辑
    if runtime.model_type in {"microsoft_free", "google_free", "deeplx"}:
        text = extract_text_from_messages(messages)
        return translate_text(text, source_lang=source_lang, target_lang=target_lang, model_name=runtime.display_name)
    # 当客户端 messages 包含自定义的 system/developer 提示词时，
    # 直接透传给上游 LLM，尊重客户端的翻译指令（如沉浸式翻译插件）
    if (force_translate or _is_hunyuan_mt(runtime)) and not _has_client_translation_prompt(messages):
        text = extract_text_from_messages(messages)
        return translate_text(text, source_lang=source_lang, target_lang=target_lang, model_name=runtime.display_name)
    if runtime.model_type == "openai_images":
        return _generate_image_openai(messages, runtime)
    if runtime.model_type == "gemini":
        return _chat_gemini(messages, runtime)
    if runtime.model_type == "anthropic":
        return _chat_anthropic(messages, runtime, request_options=request_options)
    if runtime.model_type == "openai_responses":
        return _chat_openai_responses(messages, runtime, request_options=request_options)
    return _chat_glm(messages, runtime, request_options=request_options)


def translate_stream(
    text: str,
    *,
    source_lang: Optional[str] = None,
    target_lang: Optional[str] = None,
    model_name: Optional[str] = None,
    request_options: Optional[dict[str, Any]] = None,
) -> Iterator[str]:
    runtime = load_translator_runtime(model_name)
    source = source_lang if source_lang is not None else runtime.source_lang
    target = target_lang if target_lang is not None else runtime.target_lang
    text = str(text or "")
    if not text.strip():
        return
    actual_target = resolve_target_language(text, source, target)
    if _canonical_language(source) == actual_target:
        yield text
        return

    model_type = runtime.model_type
    if model_type == "microsoft_free":
        yield _translate_with_microsoft(text, source, actual_target, runtime)
        return
    if model_type == "google_free":
        yield _translate_with_google(text, source, actual_target, runtime)
        return
    if model_type == "deeplx":
        yield _translate_with_deeplx(text, source, actual_target, runtime)
        return
    if model_type == "openai_images":
        raise TranslationError(IMAGE_MODEL_TRANSLATE_ERROR)

    prompt = _build_translation_prompt(text, actual_target, runtime)
    messages = [{"role": "user", "content": prompt}]
    if model_type == "gemini":
        yield from _chat_gemini_stream(messages, runtime)
        return
    if model_type == "anthropic":
        yield from _chat_anthropic_stream(messages, runtime, request_options=request_options)
        return
    if model_type == "openai_responses":
        yield from _chat_openai_responses_stream(messages, runtime, request_options=request_options)
        return
    yield from _chat_glm_stream(messages, runtime, request_options=request_options)


def chat_completion_stream(
    messages: list[dict[str, Any]],
    *,
    model_name: Optional[str] = None,
    source_lang: Optional[str] = None,
    target_lang: Optional[str] = None,
    force_translate: bool = False,
    request_options: Optional[dict[str, Any]] = None,
) -> Iterator[str]:
    runtime = load_translator_runtime(
        None if model_name == SERVICE_MODEL_ID else model_name,
        purpose="translate" if force_translate else "qa",
    )
    # 非 LLM 翻译引擎不支持自定义 prompt，只能使用内置逻辑
    if runtime.model_type in {"microsoft_free", "google_free", "deeplx"}:
        text = extract_text_from_messages(messages)
        yield from translate_stream(
            text,
            source_lang=source_lang,
            target_lang=target_lang,
            model_name=runtime.display_name,
            request_options=request_options,
        )
        return
    # 当客户端 messages 包含自定义的 system/developer 提示词时，
    # 直接透传给上游 LLM，尊重客户端的翻译指令（如沉浸式翻译插件）
    if (force_translate or _is_hunyuan_mt(runtime)) and not _has_client_translation_prompt(messages):
        text = extract_text_from_messages(messages)
        yield from translate_stream(
            text,
            source_lang=source_lang,
            target_lang=target_lang,
            model_name=runtime.display_name,
            request_options=request_options,
        )
        return
    if runtime.model_type == "openai_images":
        yield _generate_image_openai(messages, runtime)
        return
    if runtime.model_type == "gemini":
        yield from _chat_gemini_stream(messages, runtime)
        return
    if runtime.model_type == "anthropic":
        yield from _chat_anthropic_stream(messages, runtime, request_options=request_options)
        return
    if runtime.model_type == "openai_responses":
        yield from _chat_openai_responses_stream(messages, runtime, request_options=request_options)
        return
    yield from _chat_glm_stream(messages, runtime, request_options=request_options)


def chat_completion_response(
    messages: list[dict[str, Any]],
    *,
    model_name: Optional[str] = None,
    source_lang: Optional[str] = None,
    target_lang: Optional[str] = None,
    force_translate: bool = False,
    request_options: Optional[dict[str, Any]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> dict[str, Any]:
    runtime = load_translator_runtime(
        None if model_name == SERVICE_MODEL_ID else model_name,
        purpose="translate" if force_translate else "qa",
    )
    response_model = str(model_name or runtime.display_name or SERVICE_MODEL_ID)
    # 非 LLM 翻译引擎不支持自定义 prompt，只能使用内置逻辑
    if runtime.model_type in {"microsoft_free", "google_free", "deeplx"}:
        text = extract_text_from_messages(messages)
        content = translate_text(text, source_lang=source_lang, target_lang=target_lang, model_name=runtime.display_name)
        return openai_chat_response(response_model, content)
    # 当客户端 messages 包含自定义的 system/developer 提示词时，
    # 直接透传给上游 LLM，尊重客户端的翻译指令（如沉浸式翻译插件）
    if (force_translate or _is_hunyuan_mt(runtime)) and not _has_client_translation_prompt(messages):
        text = extract_text_from_messages(messages)
        content = translate_text(text, source_lang=source_lang, target_lang=target_lang, model_name=runtime.display_name)
        return openai_chat_response(response_model, content)
    if runtime.model_type == "openai_images":
        return openai_chat_response(
            response_model,
            _generate_image_openai(messages, runtime, cancel_event=cancel_event),
        )
    if runtime.model_type == "gemini":
        return openai_chat_response(response_model, _chat_gemini(messages, runtime, cancel_event=cancel_event))
    if runtime.model_type == "anthropic":
        return _chat_anthropic_response(
            messages,
            runtime,
            request_options=request_options,
            cancel_event=cancel_event,
        )
    if runtime.model_type == "openai_responses":
        return _chat_openai_responses_as_chat_response(
            messages,
            runtime,
            request_options=request_options,
            cancel_event=cancel_event,
        )
    return _chat_glm_response(messages, runtime, request_options=request_options, cancel_event=cancel_event)


def chat_completion_response_stream(
    messages: list[dict[str, Any]],
    *,
    model_name: Optional[str] = None,
    source_lang: Optional[str] = None,
    target_lang: Optional[str] = None,
    force_translate: bool = False,
    request_options: Optional[dict[str, Any]] = None,
) -> Iterator[dict[str, Any]]:
    runtime = load_translator_runtime(
        None if model_name == SERVICE_MODEL_ID else model_name,
        purpose="translate" if force_translate else "qa",
    )
    # 当客户端 messages 包含自定义的 system/developer 提示词时，
    # 即使是 force_translate 或 hunyuan-mt 模型，也应透传给上游 LLM
    client_has_prompt = _has_client_translation_prompt(messages)
    bypass_builtin = client_has_prompt and runtime.model_type not in {"microsoft_free", "google_free", "deeplx"}
    should_passthrough = (not force_translate and not _is_hunyuan_mt(runtime)) or bypass_builtin
    if runtime.model_type == "anthropic" and should_passthrough:
        yield from _chat_anthropic_stream_events(messages, runtime, request_options=request_options)
        return
    if runtime.model_type == "openai_responses" and should_passthrough:
        yield from _chat_openai_responses_stream_as_chat_chunks(messages, runtime, request_options=request_options)
        return
    # openai_images 不走 GLM 透传：生图经下方通用包装调 chat_completion_stream 分发
    if runtime.model_type not in {"microsoft_free", "google_free", "deeplx", "gemini", "anthropic", "openai_responses", "openai_images"} and should_passthrough:
        yield from _chat_glm_stream_events(messages, runtime, request_options=request_options)
        return

    created = int(time.time())
    response_model = str(model_name or runtime.display_name or SERVICE_MODEL_ID)
    chunk_id = f"chatcmpl-deepcat-{created}"
    yield {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": response_model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    for part in chat_completion_stream(
        messages,
        model_name=model_name,
        source_lang=source_lang,
        target_lang=target_lang,
        force_translate=force_translate,
        request_options=request_options,
    ):
        if not part:
            continue
        yield {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": response_model,
            "choices": [{"index": 0, "delta": {"content": str(part)}, "finish_reason": None}],
        }
    yield {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": response_model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }


def _translate_with_microsoft(text: str, source_lang: object, target_lang: str, runtime: TranslatorRuntime) -> str:
    source_code = _provider_lang_code(str(source_lang or "auto"), "microsoft", source=True)
    target_code = _provider_lang_code(target_lang, "microsoft")
    token_response = _request("GET", "https://edge.microsoft.com/translate/auth", runtime, timeout=15)
    token = token_response.text.strip()
    if not token:
        raise TranslationError("Microsoft translator did not return an auth token.")
    params: dict[str, str] = {"api-version": "3.0", "to": target_code}
    if source_code and source_code != "auto":
        params["from"] = source_code
    base = runtime.base_url.rstrip("/") or "https://api-edge.cognitive.microsofttranslator.com"
    response = _request(
        "POST",
        f"{base}/translate",
        runtime,
        params=params,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        json=[{"Text": text}],
        timeout=30,
    )
    payload = _json_response(response)
    try:
        return _clean_model_text(payload[0]["translations"][0]["text"])
    except Exception as exc:
        raise TranslationError("Unexpected Microsoft translator response.") from exc


def _translate_with_google(text: str, source_lang: object, target_lang: str, runtime: TranslatorRuntime) -> str:
    source_code = _provider_lang_code(str(source_lang or "auto"), "google", source=True)
    target_code = _provider_lang_code(target_lang, "google")
    base = runtime.base_url.rstrip("/") or "https://translate.googleapis.com"
    response = _request(
        "GET",
        f"{base}/translate_a/single",
        runtime,
        params={"client": "gtx", "sl": source_code or "auto", "tl": target_code, "dt": "t", "q": text},
        timeout=30,
    )
    payload = _json_response(response)
    try:
        return "".join(str(part[0] or "") for part in payload[0]).strip()
    except Exception as exc:
        raise TranslationError("Unexpected Google translator response.") from exc


def _translate_with_deeplx(text: str, source_lang: object, target_lang: str, runtime: TranslatorRuntime) -> str:
    source_code = _provider_lang_code(str(source_lang or "auto"), "deeplx", source=True)
    target_code = _provider_lang_code(target_lang, "deeplx")
    base = runtime.base_url.rstrip("/") or DEEPLX_INFO_ADDRESS
    if "linux.do/t/topic/111737" in base or not base.lower().startswith(("http://", "https://")):
        raise TranslationError("DeepLX base_url is not configured.")
    url = base if base.endswith("/translate") else f"{base}/translate"
    headers = {"Content-Type": "application/json"}
    headers.update(_auth_header(runtime))
    response = _request(
        "POST",
        url,
        runtime,
        headers=headers,
        json={"text": text, "source_lang": source_code or "AUTO", "target_lang": target_code},
        timeout=30,
    )
    payload = _json_response(response)
    if isinstance(payload, dict):
        for key in ("data", "translation", "translated_text", "text", "result"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        translations = payload.get("translations")
        if isinstance(translations, list) and translations:
            first = translations[0]
            if isinstance(first, dict):
                value = first.get("text") or first.get("translation")
                if isinstance(value, str) and value.strip():
                    return value.strip()
    raise TranslationError("Unexpected DeepLX response.")


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"text", "input_text", "output_text"}:
                parts.append(str(item.get("text") or item.get("input_text") or item.get("output_text") or ""))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _normalize_message_content(content: Any, strip_image: bool = False) -> Any:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        has_multimodal = False
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"image_url", "input_image"}:
                has_multimodal = True
                break
        if not has_multimodal:
            return _message_text(content)
        if strip_image:
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type in {"text", "input_text", "output_text"}:
                    parts.append(str(item.get("text") or item.get("input_text") or item.get("output_text") or ""))
                elif item_type in {"image_url", "input_image"}:
                    parts.append("[Image attachment (omitted from history)]")
            return "\n".join(part for part in parts if part)

        new_content = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {"text", "input_text", "output_text"}:
                new_content.append({
                    "type": "text",
                    "text": str(item.get("text") or item.get("input_text") or item.get("output_text") or "")
                })
            elif item_type == "image_url":
                new_content.append(item)
            elif item_type == "input_image":
                img_url = item.get("image_url") or item.get("url")
                if isinstance(img_url, dict):
                    img_url = img_url.get("url", "")
                if img_url:
                    new_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": img_url
                        }
                    })
        return new_content
    return str(content or "")


def _normalize_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    raw_list = list(messages)

    latest_img_msg_idx = -1
    for idx in range(len(raw_list) - 1, -1, -1):
        msg = raw_list[idx]
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").strip().lower()
        if role in {"user", "developer", "system"}:
            content_value = msg.get("content")
            has_img = False
            if isinstance(content_value, list):
                for part in content_value:
                    if isinstance(part, dict) and part.get("type") in {"image_url", "input_image"}:
                        has_img = True
                        break
            elif isinstance(content_value, dict) and content_value.get("type") in {"image_url", "input_image"}:
                has_img = True

            if has_img:
                latest_img_msg_idx = idx
                break

    for i, msg in enumerate(raw_list):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").strip().lower()
        if role == "developer":
            role = "system"
        if role == "function":
            role = "tool"
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        content_value = msg.get("content")
        if role == "assistant" and msg.get("tool_calls") and content_value is None:
            content = ""
        else:
            strip_image = (latest_img_msg_idx != -1 and i != latest_img_msg_idx)
            content = _normalize_message_content(content_value, strip_image=strip_image)
        item: dict[str, Any] = {"role": role, "content": content}
        if role == "assistant" and isinstance(msg.get("tool_calls"), list):
            item["tool_calls"] = msg["tool_calls"]
        if role == "assistant":
            # DeepSeek 等思考模型要求历史 assistant 消息回传 reasoning_content，
            # 缺失会被上游以 400 拒绝并中断 codex 会话。
            reasoning_content = msg.get("reasoning_content")
            if isinstance(reasoning_content, str) and reasoning_content:
                item["reasoning_content"] = reasoning_content
        if role == "tool":
            for key in ("tool_call_id", "name"):
                value = msg.get(key)
                if value is not None:
                    item[key] = str(value)
        normalized.append(item)
    return normalized or [{"role": "user", "content": ""}]


def extract_text_from_messages(messages: Iterable[dict[str, Any]]) -> str:
    normalized = _normalize_messages(messages)
    last_user = ""
    for msg in reversed(normalized):
        if msg["role"] == "user" and msg["content"].strip():
            last_user = msg["content"].strip()
            break
    if not last_user:
        return ""

    tag_match = re.search(
        r"<(?:translate_input|translation_input|source_text|source|input|text)>(.*?)</(?:translate_input|translation_input|source_text|source|input|text)>",
        last_user,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if tag_match:
        return tag_match.group(1).strip()

    fences = re.findall(r"```(?:[a-zA-Z0-9_-]+)?\s*(.*?)```", last_user, flags=re.DOTALL)
    if fences:
        return max((f.strip() for f in fences), key=len)

    prompted = _strip_translation_prompt(last_user)
    if prompted:
        return prompted

    return last_user


def _image_prompt_from_messages(messages: Iterable[dict[str, Any]]) -> str:
    """生图提示词取最后一条用户消息原文，不做翻译指令剥离。"""
    normalized = _normalize_messages(messages)
    for msg in reversed(normalized):
        if msg["role"] == "user" and msg["content"].strip():
            return msg["content"].strip()
    return ""


def _image_mime_type(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _generate_image_openai(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """调用 OpenAI Images API（…/v1/images/generations）生图，返回 Markdown 图片。

    优先返回上游给出的 url；b64_json 则内联为 data URI，保证响应对任意
    OpenAI 兼容客户端都是自包含的。
    """
    prompt = _image_prompt_from_messages(messages)
    if not prompt:
        raise TranslationError("生图提示词为空，请输入要生成的图片描述。")
    url = normalize_openai_images_url(runtime.base_url)
    if not url:
        raise TranslationError("生图模型未配置 API 地址。")
    headers = {"Content-Type": "application/json"}
    if runtime.api_key:
        headers["Authorization"] = f"Bearer {runtime.api_key}"
    payload: dict[str, Any] = {
        "model": runtime.model_name,
        "prompt": prompt,
        "n": 1,
        "response_format": "b64_json",
    }

    def _post() -> httpx.Response:
        return _request(
            "POST", url, runtime,
            headers=headers, json=payload,
            timeout=IMAGE_GENERATION_TIMEOUT_SECONDS, cancel_event=cancel_event,
        )

    try:
        response = _post()
    except httpx.HTTPStatusError as exc:
        body_text = str(getattr(exc.response, "text", "") or "")
        if "response_format" in body_text and "response_format" in payload:
            # 部分后端（如 gpt-image-1）不接受 response_format 参数，去掉后重试
            payload.pop("response_format", None)
            response = _post()
        else:
            detail = body_text.strip()
            if len(detail) > 300:
                detail = f"{detail[:300]}..."
            raise TranslationError(
                f"生图请求失败（HTTP {exc.response.status_code}）：{detail}"
            ) from exc
    try:
        result = response.json()
    except Exception as exc:
        raise TranslationError("生图服务返回的不是 JSON 数据。") from exc
    items = result.get("data") if isinstance(result, dict) else None
    if not isinstance(items, list) or not items:
        raise TranslationError("生图服务返回成功，但响应中没有图片数据。")
    lines: list[str] = []
    revised_prompt = ""
    for item in items:
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("url", "") or "").strip()
        if image_url:
            lines.append(f"![generated image]({image_url})")
        else:
            b64_data = str(item.get("b64_json", "") or "").strip()
            if not b64_data:
                continue
            import base64

            try:
                image_bytes = base64.b64decode(b64_data)
            except Exception as exc:
                raise TranslationError(f"生图服务返回的 b64_json 解码失败：{exc}") from exc
            mime = _image_mime_type(image_bytes)
            lines.append(f"![generated image](data:{mime};base64,{b64_data})")
        if not revised_prompt:
            candidate = str(item.get("revised_prompt", "") or "").strip()
            if candidate and candidate != prompt:
                revised_prompt = candidate
    if not lines:
        raise TranslationError("生图服务返回的图片数据无法解析（既无 b64_json 也无 url）。")
    if revised_prompt:
        lines.extend(["", f"> 模型修订后的提示词：{revised_prompt}"])
    return "\n".join(lines)


def _looks_like_translation_prompt(text: str) -> bool:
    lower = str(text or "").lower()
    english_tokens = (
        "translate",
        "translation",
        "target",
        "source",
        "only output",
        "translated content",
        "simplified chinese",
        "mandarin chinese",
    )
    if any(token in lower for token in english_tokens):
        return True
    chinese_tokens = (
        "\u7ffb\u8bd1",
        "\u8bd1\u6587",
        "\u7b80\u4f53\u4e2d\u6587",
        "\u76ee\u6807\u8bed\u8a00",
        "\u539f\u6587",
        "\u4ec5\u8f93\u51fa",
        "\u7981\u6b62\u89e3\u91ca",
    )
    return any(token in str(text or "") for token in chinese_tokens)


def _strip_translation_prompt(text: str) -> str:
    source = str(text or "").strip()
    if not source:
        return ""

    parts = [part.strip() for part in re.split(r"\n\s*\n", source) if part.strip()]
    if len(parts) >= 2:
        head = " ".join(parts[:-1])
        if _looks_like_translation_prompt(head):
            return parts[-1]

    lines = [line.strip() for line in source.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    if len(lines) >= 2 and _looks_like_translation_prompt(lines[0]):
        rest = "\n".join(lines[1:]).strip()
        if rest:
            return rest

    prefix = re.match(
        r"(?is)^\s*(?=.{0,240}(?:translate|translation|target|source|only output|simplified chinese|mandarin chinese|\u7ffb\u8bd1|\u8bd1\u6587|\u7b80\u4f53\u4e2d\u6587|\u4ec5\u8f93\u51fa)).{1,240}[:\uff1a]\s*(.+?)\s*$",
        source,
    )
    if prefix:
        candidate = prefix.group(1).strip()
        if candidate:
            return candidate
    return ""


def _iter_text_content(content: Any) -> Iterator[str]:
    if isinstance(content, str):
        if content:
            yield content
        return
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text:
                yield text
        return


def _openai_stream_texts(item: Any) -> Iterator[str]:
    if not isinstance(item, dict):
        return
    choices = item.get("choices")
    if not isinstance(choices, list):
        return
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if isinstance(delta, dict):
            yield from _iter_text_content(delta.get("content"))
        message = choice.get("message")
        if isinstance(message, dict):
            yield from _iter_text_content(message.get("content"))
        text = choice.get("text")
        if isinstance(text, str) and text:
            yield text


def _openai_stream_item_summary(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"item_type": type(item).__name__}
    choices = item.get("choices")
    text_chars = 0
    finish_reasons: list[str] = []
    tool_call_deltas = 0
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            finish = choice.get("finish_reason")
            if finish:
                finish_reasons.append(str(finish))
            delta = choice.get("delta")
            if isinstance(delta, dict):
                text_chars += _payload_text_chars(delta.get("content"))
                tool_calls = delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    tool_call_deltas += len(tool_calls)
            message = choice.get("message")
            if isinstance(message, dict):
                text_chars += _payload_text_chars(message.get("content"))
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    tool_call_deltas += len(tool_calls)
            text_chars += _payload_text_chars(choice.get("text"))
    return {
        "keys": sorted(str(key) for key in item.keys()),
        "choices": len(choices) if isinstance(choices, list) else None,
        "delta_chars": text_chars,
        "tool_call_deltas": tool_call_deltas,
        "finish_reasons": finish_reasons,
    }


ANTHROPIC_VERSION = "2023-06-01"


def _anthropic_messages_url(base_url: str) -> str:
    url = normalize_anthropic_messages_url(base_url)
    if not url:
        raise TranslationError("Missing Anthropic base_url in translator settings.")
    return url


def _anthropic_headers(runtime: TranslatorRuntime) -> dict[str, str]:
    if not runtime.api_key:
        raise TranslationError("Missing Anthropic api_key in translator settings.")
    return {
        "Content-Type": "application/json",
        "x-api-key": runtime.api_key,
        "anthropic-version": ANTHROPIC_VERSION,
    }


def _anthropic_content_blocks(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        text = _message_text(content)
        return [{"type": "text", "text": text}] if text else []

    blocks: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type in {"text", "input_text", "output_text"}:
            text = str(item.get("text") or item.get("input_text") or item.get("output_text") or "")
            if text:
                blocks.append({"type": "text", "text": text})
            continue
        if item_type in {"image_url", "input_image"}:
            image_url = item.get("image_url") or item.get("url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url")
            image_url = str(image_url or "")
            if image_url.startswith("data:") and "," in image_url:
                header, encoded = image_url.split(",", 1)
                media_type = header.split(";", 1)[0].replace("data:", "") or "image/png"
                blocks.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": encoded},
                    }
                )
            elif image_url.startswith(("http://", "https://")):
                blocks.append({"type": "image", "source": {"type": "url", "url": image_url}})
    if not blocks:
        text = _message_text(content)
        if text:
            blocks.append({"type": "text", "text": text})
    return blocks


def _merge_anthropic_content(left: Any, right: Any) -> list[dict[str, Any]]:
    left_blocks = left if isinstance(left, list) else _anthropic_content_blocks(left)
    right_blocks = right if isinstance(right, list) else _anthropic_content_blocks(right)
    return list(left_blocks or []) + list(right_blocks or [])


def _anthropic_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    system_parts: list[str] = []
    result: list[dict[str, Any]] = []

    for msg in _normalize_messages(messages):
        role = str(msg.get("role") or "user").strip().lower()
        content = msg.get("content", "")
        if role == "system":
            text = _message_text(content).strip()
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            name = str(msg.get("name") or msg.get("tool_call_id") or "tool").strip()
            content = f"[Tool result: {name}]\n{_message_text(content)}"
            role = "user"
        elif role not in {"user", "assistant"}:
            role = "user"

        content_blocks = _anthropic_content_blocks(content)
        if not content_blocks:
            content_blocks = [{"type": "text", "text": ""}]
        content_value: Any = content_blocks[0]["text"] if len(content_blocks) == 1 and content_blocks[0].get("type") == "text" else content_blocks
        if result and result[-1].get("role") == role:
            result[-1]["content"] = _merge_anthropic_content(result[-1].get("content"), content_value)
        else:
            result.append({"role": role, "content": content_value})

    if not result:
        result.append({"role": "user", "content": ""})
    return result, "\n\n".join(system_parts).strip()


def _anthropic_messages_payload(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
    stream: bool = False,
) -> dict[str, Any]:
    options = dict(request_options or {})
    max_tokens = options.get("max_tokens", options.get("max_output_tokens", 4096))
    try:
        max_tokens_int = int(max_tokens)
    except Exception:
        max_tokens_int = 4096
    payload: dict[str, Any] = {
        "model": runtime.model_name,
        "max_tokens": max(1, max_tokens_int),
    }
    payload_messages, system_text = _anthropic_messages(messages)
    payload["messages"] = payload_messages
    if system_text:
        payload["system"] = system_text
    for key in ("temperature", "top_p", "top_k"):
        if key in options:
            payload[key] = options[key]
    stop = options.get("stop")
    if isinstance(stop, str) and stop:
        payload["stop_sequences"] = [stop]
    elif isinstance(stop, list):
        payload["stop_sequences"] = [str(item) for item in stop if str(item or "")]
    if stream:
        payload["stream"] = True
    return payload


def _anthropic_texts(body: Any) -> Iterator[str]:
    if not isinstance(body, dict):
        return
    content = body.get("content")
    if isinstance(content, str):
        if content:
            yield content
        return
    if not isinstance(content, list):
        return
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            yield part["text"]


def _anthropic_tool_calls(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    calls: list[dict[str, Any]] = []
    content = body.get("content")
    if not isinstance(content, list):
        return calls
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "tool_use":
            continue
        tool_input = part.get("input")
        try:
            arguments = json.dumps(tool_input if tool_input is not None else {}, ensure_ascii=False)
        except Exception:
            arguments = str(tool_input or "")
        calls.append(
            {
                "id": str(part.get("id") or f"call_{uuid.uuid4().hex[:8]}"),
                "type": "function",
                "function": {"name": str(part.get("name") or ""), "arguments": arguments},
            }
        )
    return calls


def _anthropic_usage(usage: Any) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
    completion_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0),
    }


def _anthropic_finish_reason(stop_reason: object) -> str:
    reason = str(stop_reason or "end_turn")
    if reason == "max_tokens":
        return "length"
    if reason == "tool_use":
        return "tool_calls"
    return "stop"


def _anthropic_to_openai_chat_response(body: dict[str, Any], runtime: TranslatorRuntime) -> dict[str, Any]:
    created_ts = int(time.time())
    message: dict[str, Any] = {"role": "assistant", "content": "".join(_anthropic_texts(body)).strip()}
    tool_calls = _anthropic_tool_calls(body)
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": str(body.get("id") or f"chatcmpl-deepcat-{created_ts}"),
        "object": "chat.completion",
        "created": created_ts,
        "model": str(body.get("model") or runtime.model_name or runtime.display_name),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": _anthropic_finish_reason(body.get("stop_reason")),
            }
        ],
        "usage": _anthropic_usage(body.get("usage")),
    }


def _anthropic_stream_chunk(item: Any, *, chunk_id: str, model: str, created: int) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    event_type = str(item.get("type") or "")
    if event_type == "message_start":
        message = item.get("message") if isinstance(item.get("message"), dict) else {}
        return {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": str(message.get("model") or model),
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
    if event_type == "content_block_delta":
        delta = item.get("delta") if isinstance(item.get("delta"), dict) else {}
        text = str(delta.get("text") or "")
        if not text:
            return None
        return {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        }
    if event_type == "message_delta":
        delta = item.get("delta") if isinstance(item.get("delta"), dict) else {}
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else None
        chunk: dict[str, Any] = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": _anthropic_finish_reason(delta.get("stop_reason")),
                }
            ],
        }
        if usage:
            chunk["usage"] = _anthropic_usage(usage)
        return chunk
    return None


def _gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for msg in _normalize_messages(messages):
        role = "model" if msg["role"] == "assistant" else "user"
        content_str = _message_text(msg["content"])
        contents.append({"role": role, "parts": [{"text": content_str}]})
    return contents


def _gemini_stream_texts(item: Any) -> Iterator[str]:
    if isinstance(item, list):
        for child in item:
            yield from _gemini_stream_texts(child)
        return
    if not isinstance(item, dict):
        return
    candidates = item.get("candidates")
    if not isinstance(candidates, list):
        return
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                yield text


def _gemini_stream_item_summary(item: Any, texts: list[str]) -> dict[str, Any]:
    candidates = item.get("candidates") if isinstance(item, dict) else None
    return {
        "item_type": type(item).__name__,
        "keys": sorted(str(key) for key in item.keys()) if isinstance(item, dict) else [],
        "candidates": len(candidates) if isinstance(candidates, list) else None,
        "delta_chars": sum(len(text) for text in texts),
    }


def _stream_error_message(item: dict[str, Any]) -> str:
    error = item.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or item)


def _chat_anthropic_response(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> dict[str, Any]:
    payload = _anthropic_messages_payload(messages, runtime, request_options=request_options)
    url = _anthropic_messages_url(runtime.base_url)
    headers = _anthropic_headers(runtime)
    timeout = QA_UPSTREAM_TIMEOUT_SECONDS if getattr(runtime, "purpose", "translate") == "qa" else 60
    response: Optional[httpx.Response] = None
    for attempt in range(STREAM_UPSTREAM_ATTEMPTS):
        try:
            logger.info(
                "upstream Anthropic messages attempt: attempt=%s/%s url=%s runtime=%s payload=%s",
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                _redact_url(url),
                _format_summary(_runtime_summary(runtime, url)),
                _format_summary(_payload_summary({"headers": headers, "json": payload})),
            )
            response = _request(
                "POST",
                url,
                runtime,
                headers=headers,
                json=payload,
                timeout=timeout,
                cancel_event=cancel_event,
            )
            break
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise TranslationError("Upstream request cancelled.") from exc
            retryable = _is_retryable_upstream_error(exc)
            logger.error(
                "upstream Anthropic messages attempt failed: attempt=%s/%s retryable=%s error=%s",
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                retryable,
                repr(exc),
            )
            if attempt >= STREAM_UPSTREAM_ATTEMPTS - 1 or not retryable:
                raise
            time.sleep(min(1.0 + attempt, 3.0))
    if response is None:
        raise TranslationError("Anthropic messages upstream did not return a response.")
    body = _json_response(response)
    if not isinstance(body, dict):
        raise TranslationError("Unexpected Anthropic messages response.")
    return _anthropic_to_openai_chat_response(body, runtime)


def _chat_anthropic(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
) -> str:
    payload = _chat_anthropic_response(messages, runtime, request_options=request_options)
    try:
        return _clean_model_text(payload["choices"][0]["message"]["content"])
    except Exception as exc:
        raise TranslationError("Unexpected Anthropic messages response.") from exc


def _chat_anthropic_stream_events(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
) -> Iterator[dict[str, Any]]:
    payload = _anthropic_messages_payload(messages, runtime, request_options=request_options, stream=True)
    url = _anthropic_messages_url(runtime.base_url)
    headers = _anthropic_headers(runtime)
    timeout = QA_UPSTREAM_TIMEOUT_SECONDS if getattr(runtime, "purpose", "translate") == "qa" else 60
    stream_id = uuid.uuid4().hex[:8]
    chunk_id = f"chatcmpl-deepcat-{int(time.time())}-{stream_id}"
    created = int(time.time())
    for attempt in range(STREAM_UPSTREAM_ATTEMPTS):
        yielded = False
        item_count = 0
        total_delta_chars = 0
        started = time.perf_counter()
        try:
            logger.info(
                "upstream stream start: id=%s provider=anthropic_messages attempt=%s/%s url=%s timeout=%s runtime=%s payload=%s",
                stream_id,
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                _redact_url(url),
                _format_summary(_timeout_summary(_http_timeout(timeout, stream=True))),
                _format_summary(_runtime_summary(runtime, url)),
                _format_summary(_payload_summary({"headers": headers, "json": payload})),
            )
            with _stream_request_client(url, runtime, timeout) as client:
                with client.stream("POST", url, headers=headers, json=payload) as response:
                    header_elapsed_ms = int((time.perf_counter() - started) * 1000)
                    logger.info(
                        "upstream stream headers: id=%s attempt=%s status=%s elapsed_ms=%s content_type=%s",
                        stream_id,
                        attempt + 1,
                        response.status_code,
                        header_elapsed_ms,
                        response.headers.get("content-type", ""),
                    )
                    if response.is_error:
                        error_body = response.read()
                        logger.error(
                            "upstream stream http error body: id=%s attempt=%s status=%s preview=%s",
                            stream_id,
                            attempt + 1,
                            response.status_code,
                            _preview(error_body),
                        )
                    response.raise_for_status()
                    for item in _iter_sse_json(response):
                        if isinstance(item, dict) and (item.get("type") == "error" or "error" in item):
                            logger.error(
                                "upstream stream error event: id=%s attempt=%s chunk_index=%s error=%s",
                                stream_id,
                                attempt + 1,
                                item_count + 1,
                                _preview(_stream_error_message(item), 900),
                            )
                            raise TranslationError(_stream_error_message(item))
                        chunk = _anthropic_stream_chunk(item, chunk_id=chunk_id, model=runtime.model_name, created=created)
                        if chunk is None:
                            continue
                        item_count += 1
                        item_summary = _openai_stream_item_summary(chunk)
                        total_delta_chars += int(item_summary.get("delta_chars") or 0)
                        if item_count <= 3 or item_count % 20 == 0:
                            logger.info(
                                "upstream stream chunk: id=%s attempt=%s chunk_index=%s summary=%s elapsed_ms=%s",
                                stream_id,
                                attempt + 1,
                                item_count,
                                _format_summary(item_summary),
                                int((time.perf_counter() - started) * 1000),
                            )
                        yielded = True
                        yield chunk
            logger.info(
                "upstream stream completed: id=%s attempt=%s chunks=%s delta_chars=%s elapsed_ms=%s",
                stream_id,
                attempt + 1,
                item_count,
                total_delta_chars,
                int((time.perf_counter() - started) * 1000),
            )
            return
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.HTTPStatusError) as exc:
            retryable = _is_retryable_upstream_error(exc)
            logger.error(
                "upstream stream attempt failed: id=%s attempt=%s/%s yielded=%s chunks=%s delta_chars=%s retryable=%s elapsed_ms=%s error=%s",
                stream_id,
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                yielded,
                item_count,
                total_delta_chars,
                retryable,
                int((time.perf_counter() - started) * 1000),
                repr(exc),
            )
            if yielded or attempt >= STREAM_UPSTREAM_ATTEMPTS - 1 or not retryable:
                raise
            time.sleep(min(1.0 + attempt, 3.0))


def _chat_anthropic_stream(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
) -> Iterator[str]:
    for item in _chat_anthropic_stream_events(messages, runtime, request_options=request_options):
        for text in _openai_stream_texts(item):
            if text:
                yield text


def _chat_glm_payload(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
    stream: bool = False,
) -> dict[str, Any]:
    options = dict(request_options or {})
    if "max_output_tokens" in options and "max_tokens" not in options:
        options["max_tokens"] = options["max_output_tokens"]
    payload: dict[str, Any] = {
        "model": runtime.model_name,
        "messages": _normalize_messages(messages),
        "temperature": options.get("temperature", 0.1),
    }
    if stream:
        payload["stream"] = True
    generation_keys = ["max_tokens", "top_p", "frequency_penalty", "presence_penalty"]
    if stream:
        generation_keys.append("top_k")
    for key in generation_keys:
        if key in options:
            payload[key] = options[key]
    for key in ("tools", "tool_choice", "response_format", "stop", "seed", "user"):
        if key in options:
            payload[key] = options[key]
    # parallel_tool_calls 不传递给上游（部分模型不支持会导致 400 错误）
    # 该策略由 translation_server._apply_tool_policy 在本地响应侧强制执行
    if _is_hunyuan_mt(runtime):
        payload.update(
            {
                "temperature": options.get("temperature", 0.7),
                "top_p": options.get("top_p", 0.6),
                "top_k": options.get("top_k", 20),
                "repetition_penalty": 1.05,
                "repeat_penalty": 1.05,
                "max_tokens": options.get("max_tokens", 4096),
            }
        )
    return payload


def _chat_glm_response(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> dict[str, Any]:
    payload = _chat_glm_payload(messages, runtime, request_options=request_options)
    base = _chat_base_url(runtime.base_url)
    url = f"{base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    headers.update(_auth_header(runtime))
    if getattr(runtime, "purpose", "translate") == "qa":
        timeout = QA_UPSTREAM_TIMEOUT_SECONDS
    else:
        timeout = 600 if _is_hunyuan_mt(runtime) else 60
    response: Optional[httpx.Response] = None
    for attempt in range(STREAM_UPSTREAM_ATTEMPTS):
        try:
            logger.info(
                "upstream chat completion attempt: attempt=%s/%s url=%s runtime=%s payload=%s",
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                _redact_url(url),
                _format_summary(_runtime_summary(runtime, url)),
                _format_summary(_payload_summary({"headers": headers, "json": payload})),
            )
            with _local_server_context(runtime):
                response = _request(
                    "POST",
                    url,
                    runtime,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                    cancel_event=cancel_event,
                )
            break
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise TranslationError("Upstream request cancelled.") from exc
            retryable = _is_retryable_upstream_error(exc)
            logger.error(
                "upstream chat completion attempt failed: attempt=%s/%s retryable=%s error=%s",
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                retryable,
                repr(exc),
            )
            if attempt >= STREAM_UPSTREAM_ATTEMPTS - 1 or not retryable:
                raise
            time.sleep(min(1.0 + attempt, 3.0))
    if response is None:
        raise TranslationError("Chat completion upstream did not return a response.")
    body = _json_response(response)
    if not isinstance(body, dict):
        raise TranslationError("Unexpected chat completion response.")
    return body


def _chat_glm(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
) -> str:
    payload = _chat_glm_response(messages, runtime, request_options=request_options)
    try:
        return _clean_model_text(payload["choices"][0]["message"]["content"])
    except Exception as exc:
        raise TranslationError("Unexpected chat completion response.") from exc


def _chat_glm_stream_events(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
) -> Iterator[dict[str, Any]]:
    payload = _chat_glm_payload(messages, runtime, request_options=request_options, stream=True)
    base = _chat_base_url(runtime.base_url)
    url = f"{base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    headers.update(_auth_header(runtime))
    if getattr(runtime, "purpose", "translate") == "qa":
        timeout = QA_UPSTREAM_TIMEOUT_SECONDS
    else:
        timeout = 600 if _is_hunyuan_mt(runtime) else 60
    stream_id = uuid.uuid4().hex[:8]
    for attempt in range(STREAM_UPSTREAM_ATTEMPTS):
        yielded = False
        item_count = 0
        total_delta_chars = 0
        started = time.perf_counter()
        try:
            logger.info(
                "upstream stream start: id=%s provider=openai_compatible attempt=%s/%s url=%s timeout=%s runtime=%s payload=%s",
                stream_id,
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                _redact_url(url),
                _format_summary(_timeout_summary(_http_timeout(timeout, stream=True))),
                _format_summary(_runtime_summary(runtime, url)),
                _format_summary(_payload_summary({"headers": headers, "json": payload})),
            )
            with _local_server_context(runtime):
                with _stream_request_client(url, runtime, timeout) as client:
                    with client.stream("POST", url, headers=headers, json=payload) as response:
                        header_elapsed_ms = int((time.perf_counter() - started) * 1000)
                        logger.info(
                            "upstream stream headers: id=%s attempt=%s status=%s elapsed_ms=%s content_type=%s",
                            stream_id,
                            attempt + 1,
                            response.status_code,
                            header_elapsed_ms,
                            response.headers.get("content-type", ""),
                        )
                        if response.is_error:
                            error_body = response.read()
                            logger.error(
                                "upstream stream http error body: id=%s attempt=%s status=%s preview=%s",
                                stream_id,
                                attempt + 1,
                                response.status_code,
                                _preview(error_body),
                            )
                        response.raise_for_status()
                        for item in _iter_sse_json(response):
                            if isinstance(item, dict) and "error" in item:
                                logger.error(
                                    "upstream stream error event: id=%s attempt=%s chunk_index=%s error=%s",
                                    stream_id,
                                    attempt + 1,
                                    item_count + 1,
                                    _preview(_stream_error_message(item), 900),
                                )
                                raise TranslationError(_stream_error_message(item))
                            if isinstance(item, dict):
                                item_count += 1
                                item_summary = _openai_stream_item_summary(item)
                                total_delta_chars += int(item_summary.get("delta_chars") or 0)
                                if item_count <= 3 or item_count % 20 == 0:
                                    logger.info(
                                        "upstream stream chunk: id=%s attempt=%s chunk_index=%s summary=%s elapsed_ms=%s",
                                        stream_id,
                                        attempt + 1,
                                        item_count,
                                        _format_summary(item_summary),
                                        int((time.perf_counter() - started) * 1000),
                                    )
                                yielded = True
                                yield item
            logger.info(
                "upstream stream completed: id=%s attempt=%s chunks=%s delta_chars=%s elapsed_ms=%s",
                stream_id,
                attempt + 1,
                item_count,
                total_delta_chars,
                int((time.perf_counter() - started) * 1000),
            )
            return
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.HTTPStatusError) as exc:
            retryable = _is_retryable_upstream_error(exc)
            logger.error(
                "upstream stream attempt failed: id=%s attempt=%s/%s yielded=%s chunks=%s delta_chars=%s retryable=%s elapsed_ms=%s error=%s",
                stream_id,
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                yielded,
                item_count,
                total_delta_chars,
                retryable,
                int((time.perf_counter() - started) * 1000),
                repr(exc),
            )
            if yielded or attempt >= STREAM_UPSTREAM_ATTEMPTS - 1 or not retryable:
                raise
            time.sleep(min(1.0 + attempt, 3.0))


def _chat_glm_stream(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
) -> Iterator[str]:
    for item in _chat_glm_stream_events(messages, runtime, request_options=request_options):
        for text in _openai_stream_texts(item):
            if text:
                yield text


def _chat_gemini(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    base = runtime.base_url.rstrip("/")
    if not base:
        raise TranslationError("Missing Gemini base_url in translator settings.")
    if not runtime.api_key:
        raise TranslationError("Missing Gemini api_key in translator settings.")
    contents = _gemini_contents(messages)
    url = f"{base}/v1beta/models/{runtime.model_name}:generateContent"
    response = _request(
        "POST",
        url,
        runtime,
        headers={"Content-Type": "application/json", "x-goog-api-key": runtime.api_key},
        json={"contents": contents},
        timeout=60,
        cancel_event=cancel_event,
    )
    payload = _json_response(response)
    try:
        return _clean_model_text(payload["candidates"][0]["content"]["parts"][0]["text"])
    except Exception as exc:
        raise TranslationError("Unexpected Gemini response.") from exc


def _chat_gemini_stream(messages: list[dict[str, Any]], runtime: TranslatorRuntime) -> Iterator[str]:
    base = runtime.base_url.rstrip("/")
    if not base:
        raise TranslationError("Missing Gemini base_url in translator settings.")
    if not runtime.api_key:
        raise TranslationError("Missing Gemini api_key in translator settings.")
    url = f"{base}/v1beta/models/{runtime.model_name}:streamGenerateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": runtime.api_key}
    params = {"alt": "sse"}
    payload = {"contents": _gemini_contents(messages)}
    stream_id = uuid.uuid4().hex[:8]
    for attempt in range(STREAM_UPSTREAM_ATTEMPTS):
        yielded = False
        item_count = 0
        total_delta_chars = 0
        started = time.perf_counter()
        try:
            logger.info(
                "upstream stream start: id=%s provider=gemini_native attempt=%s/%s url=%s timeout=%s runtime=%s payload=%s",
                stream_id,
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                _redact_url(url),
                _format_summary(_timeout_summary(_http_timeout(60, stream=True))),
                _format_summary(_runtime_summary(runtime, url)),
                _format_summary(_payload_summary({"headers": headers, "params": params, "json": payload})),
            )
            with _local_server_context(runtime):
                with _stream_request_client(url, runtime, 60) as client:
                    with client.stream(
                        "POST",
                        url,
                        params=params,
                        headers=headers,
                        json=payload,
                    ) as response:
                        header_elapsed_ms = int((time.perf_counter() - started) * 1000)
                        logger.info(
                            "upstream stream headers: id=%s attempt=%s status=%s elapsed_ms=%s content_type=%s",
                            stream_id,
                            attempt + 1,
                            response.status_code,
                            header_elapsed_ms,
                            response.headers.get("content-type", ""),
                        )
                        if response.is_error:
                            error_body = response.read()
                            logger.error(
                                "upstream stream http error body: id=%s attempt=%s status=%s preview=%s",
                                stream_id,
                                attempt + 1,
                                response.status_code,
                                _preview(error_body),
                            )
                        response.raise_for_status()
                        for item in _iter_sse_json(response):
                            if isinstance(item, dict) and "error" in item:
                                logger.error(
                                    "upstream stream error event: id=%s attempt=%s chunk_index=%s error=%s",
                                    stream_id,
                                    attempt + 1,
                                    item_count + 1,
                                    _preview(_stream_error_message(item), 900),
                                )
                                raise TranslationError(_stream_error_message(item))
                            texts = list(_gemini_stream_texts(item))
                            if isinstance(item, dict):
                                item_count += 1
                                item_summary = _gemini_stream_item_summary(item, texts)
                                total_delta_chars += int(item_summary.get("delta_chars") or 0)
                                if item_count <= 3 or item_count % 20 == 0:
                                    logger.info(
                                        "upstream stream chunk: id=%s attempt=%s chunk_index=%s summary=%s elapsed_ms=%s",
                                        stream_id,
                                        attempt + 1,
                                        item_count,
                                        _format_summary(item_summary),
                                        int((time.perf_counter() - started) * 1000),
                                    )
                            for text in texts:
                                if text:
                                    yielded = True
                                    yield text
            logger.info(
                "upstream stream completed: id=%s attempt=%s chunks=%s delta_chars=%s elapsed_ms=%s",
                stream_id,
                attempt + 1,
                item_count,
                total_delta_chars,
                int((time.perf_counter() - started) * 1000),
            )
            return
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.HTTPStatusError) as exc:
            retryable = _is_retryable_upstream_error(exc)
            logger.error(
                "upstream stream attempt failed: id=%s attempt=%s/%s yielded=%s chunks=%s delta_chars=%s retryable=%s elapsed_ms=%s error=%s",
                stream_id,
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                yielded,
                item_count,
                total_delta_chars,
                retryable,
                int((time.perf_counter() - started) * 1000),
                repr(exc),
            )
            if yielded or attempt >= STREAM_UPSTREAM_ATTEMPTS - 1 or not retryable:
                raise
            time.sleep(min(1.0 + attempt, 3.0))


def _openai_responses_url(base_url: str) -> str:
    url = normalize_openai_responses_url(base_url)
    if not url:
        raise TranslationError("Missing base_url in translator settings.")
    return url


def _openai_responses_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")

    blocks: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in {"text", "input_text", "output_text"}:
            text_parts.append(str(item.get("text") or item.get("input_text") or item.get("output_text") or ""))
            continue
        if item_type in {"image_url", "input_image"}:
            image_url = item.get("image_url") or item.get("url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url", "")
            if isinstance(image_url, str) and image_url:
                block: dict[str, Any] = {"type": "input_image", "image_url": image_url}
                detail = item.get("detail")
                if detail:
                    block["detail"] = detail
                blocks.append(block)
            continue
        if item_type == "file_url":
            file_url_data = item.get("file_url")
            name = "tool"
            url = ""
            if isinstance(file_url_data, dict):
                name = str(file_url_data.get("name") or "未命名文件")
                url = str(file_url_data.get("url") or "")
            if url.startswith("data:") and "," in url:
                try:
                    import base64

                    header, b64 = url.split(",", 1)
                    mime_type = header.split(";", 1)[0].split(":", 1)[1]
                    if any(token in mime_type for token in ("text", "plain", "markdown", "json", "xml", "javascript")):
                        decoded = base64.b64decode(b64).decode("utf-8", errors="ignore")
                        text_parts.append(f"\n\n[关联附件: {name}]\n--- {name} 内容开始 ---\n{decoded}\n--- {name} 内容结束 ---\n")
                except Exception:
                    text_parts.append(f"\n\n[关联附件 (读取失败): {name}]\n")

    text = "\n".join(part for part in text_parts if part).strip()
    if blocks:
        if text:
            blocks.insert(0, {"type": "input_text", "text": text})
        return blocks
    return text


def _openai_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for msg in _normalize_messages(messages):
        role = str(msg.get("role") or "user").strip().lower()
        content = msg.get("content", "")
        if role == "tool":
            name = str(msg.get("name") or msg.get("tool_call_id") or "tool")
            content = f"[Tool result: {name}]\n{_message_text(content)}"
            role = "user"
        if role == "system":
            role = "developer"
        if role not in {"system", "developer", "user", "assistant"}:
            role = "user"
        items.append({"role": role, "content": _openai_responses_content(content)})
    return items or [{"role": "user", "content": ""}]


def _openai_responses_payload(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
    stream: bool = False,
) -> dict[str, Any]:
    options = dict(request_options or {})
    if "max_tokens" in options and "max_output_tokens" not in options:
        options["max_output_tokens"] = options["max_tokens"]
    payload: dict[str, Any] = {
        "model": runtime.model_name,
        "input": _openai_responses_input(messages),
        "temperature": options.get("temperature", 0.1),
    }
    if stream:
        payload["stream"] = True
    for key in (
        "instructions",
        "max_output_tokens",
        "top_p",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "previous_response_id",
        "reasoning",
        "metadata",
        "store",
        "include",
        "truncation",
        "user",
    ):
        if key in options:
            payload[key] = options[key]
    if "response_format" in options and "text" not in payload:
        payload["text"] = {"format": options["response_format"]}
    return payload


def _openai_responses_usage(usage: Any) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _openai_responses_text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return _clean_model_text(output_text)
    chunks: list[str] = []
    output = body.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str):
                chunks.append(content)
                continue
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in {"output_text", "input_text", "text"}:
                    text = str(part.get("text") or part.get("output_text") or part.get("input_text") or "")
                    if text:
                        chunks.append(text)
    return _clean_model_text("".join(chunks))


def _openai_responses_tool_calls(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    output = body.get("output")
    if not isinstance(output, list):
        return []
    tool_calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        arguments = item.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
        call_id = str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:12]}")
        tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return tool_calls


def _openai_responses_to_chat_response(body: dict[str, Any], runtime: TranslatorRuntime) -> dict[str, Any]:
    created = int(body.get("created_at") or time.time())
    response_model = str(body.get("model") or runtime.display_name or SERVICE_MODEL_ID)
    content = _openai_responses_text(body)
    payload = openai_chat_response(response_model, content, created=created)
    tool_calls = _openai_responses_tool_calls(body)
    if tool_calls:
        payload["choices"][0]["message"]["tool_calls"] = tool_calls
        payload["choices"][0]["finish_reason"] = "tool_calls"
    elif body.get("status") == "incomplete":
        payload["choices"][0]["finish_reason"] = "length"
    payload["usage"] = _openai_responses_usage(body.get("usage"))
    return payload


def _openai_responses_stream_delta(item: Any) -> tuple[str, str]:
    if not isinstance(item, dict):
        return "", ""
    if item.get("type") == "error" or "error" in item:
        raise TranslationError(_stream_error_message(item))
    event_type = str(item.get("type") or item.get("event") or "")
    delta = item.get("delta")
    if isinstance(delta, str) and delta:
        kind = "reasoning" if "reasoning" in event_type else "text"
        return kind, _clean_model_text(delta)
    text = item.get("text")
    if isinstance(text, str) and text and event_type.endswith(".delta"):
        kind = "reasoning" if "reasoning" in event_type else "text"
        return kind, _clean_model_text(text)
    for text_part in _openai_stream_texts(item):
        if text_part:
            return "text", _clean_model_text(text_part)
    return "", ""


def _chat_openai_responses_response(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> dict[str, Any]:
    payload = _openai_responses_payload(messages, runtime, request_options=request_options)
    url = _openai_responses_url(runtime.base_url)
    headers = {"Content-Type": "application/json"}
    headers.update(_auth_header(runtime))
    timeout = QA_UPSTREAM_TIMEOUT_SECONDS if getattr(runtime, "purpose", "translate") == "qa" else 60
    response: Optional[httpx.Response] = None
    for attempt in range(STREAM_UPSTREAM_ATTEMPTS):
        try:
            logger.info(
                "upstream responses attempt: attempt=%s/%s url=%s runtime=%s payload=%s",
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                _redact_url(url),
                _format_summary(_runtime_summary(runtime, url)),
                _format_summary(_payload_summary({"headers": headers, "json": payload})),
            )
            response = _request(
                "POST",
                url,
                runtime,
                headers=headers,
                json=payload,
                timeout=timeout,
                cancel_event=cancel_event,
            )
            break
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise TranslationError("Upstream request cancelled.") from exc
            retryable = _is_retryable_upstream_error(exc)
            logger.error(
                "upstream responses attempt failed: attempt=%s/%s retryable=%s error=%s",
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                retryable,
                repr(exc),
            )
            if attempt >= STREAM_UPSTREAM_ATTEMPTS - 1 or not retryable:
                raise
            time.sleep(min(1.0 + attempt, 3.0))
    if response is None:
        raise TranslationError("OpenAI Responses upstream did not return a response.")
    body = _json_response(response)
    if not isinstance(body, dict):
        raise TranslationError("Unexpected OpenAI Responses response.")
    return body


def _chat_openai_responses(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
) -> str:
    body = _chat_openai_responses_response(messages, runtime, request_options=request_options)
    text = _openai_responses_text(body)
    if not text and not _openai_responses_tool_calls(body):
        raise TranslationError("Unexpected OpenAI Responses response.")
    return text


def _chat_openai_responses_as_chat_response(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> dict[str, Any]:
    body = _chat_openai_responses_response(
        messages,
        runtime,
        request_options=request_options,
        cancel_event=cancel_event,
    )
    return _openai_responses_to_chat_response(body, runtime)


def _chat_openai_responses_stream_events(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
) -> Iterator[dict[str, Any]]:
    payload = _openai_responses_payload(messages, runtime, request_options=request_options, stream=True)
    url = _openai_responses_url(runtime.base_url)
    headers = {"Content-Type": "application/json"}
    headers.update(_auth_header(runtime))
    timeout = QA_UPSTREAM_TIMEOUT_SECONDS if getattr(runtime, "purpose", "translate") == "qa" else 60
    stream_id = uuid.uuid4().hex[:8]
    for attempt in range(STREAM_UPSTREAM_ATTEMPTS):
        yielded = False
        item_count = 0
        total_delta_chars = 0
        started = time.perf_counter()
        try:
            logger.info(
                "upstream stream start: id=%s provider=openai_responses attempt=%s/%s url=%s timeout=%s runtime=%s payload=%s",
                stream_id,
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                _redact_url(url),
                _format_summary(_timeout_summary(_http_timeout(timeout, stream=True))),
                _format_summary(_runtime_summary(runtime, url)),
                _format_summary(_payload_summary({"headers": headers, "json": payload})),
            )
            with _local_server_context(runtime):
                with _stream_request_client(url, runtime, timeout) as client:
                    with client.stream("POST", url, headers=headers, json=payload) as response:
                        header_elapsed_ms = int((time.perf_counter() - started) * 1000)
                        logger.info(
                            "upstream stream headers: id=%s attempt=%s status=%s elapsed_ms=%s content_type=%s",
                            stream_id,
                            attempt + 1,
                            response.status_code,
                            header_elapsed_ms,
                            response.headers.get("content-type", ""),
                        )
                        if response.is_error:
                            error_body = response.read()
                            logger.error(
                                "upstream stream http error body: id=%s attempt=%s status=%s preview=%s",
                                stream_id,
                                attempt + 1,
                                response.status_code,
                                _preview(error_body),
                            )
                        response.raise_for_status()
                        for item in _iter_sse_json(response):
                            if isinstance(item, dict) and (item.get("type") == "error" or "error" in item):
                                logger.error(
                                    "upstream stream error event: id=%s attempt=%s chunk_index=%s error=%s",
                                    stream_id,
                                    attempt + 1,
                                    item_count + 1,
                                    _preview(_stream_error_message(item), 900),
                                )
                                raise TranslationError(_stream_error_message(item))
                            if isinstance(item, dict):
                                item_count += 1
                                _, text = _openai_responses_stream_delta(item)
                                total_delta_chars += len(text)
                                if item_count <= 3 or item_count % 20 == 0:
                                    logger.info(
                                        "upstream stream chunk: id=%s attempt=%s chunk_index=%s type=%s delta_chars=%s elapsed_ms=%s",
                                        stream_id,
                                        attempt + 1,
                                        item_count,
                                        item.get("type") or item.get("event") or "",
                                        len(text),
                                        int((time.perf_counter() - started) * 1000),
                                    )
                                yielded = True
                                yield item
            logger.info(
                "upstream stream completed: id=%s attempt=%s chunks=%s delta_chars=%s elapsed_ms=%s",
                stream_id,
                attempt + 1,
                item_count,
                total_delta_chars,
                int((time.perf_counter() - started) * 1000),
            )
            return
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.HTTPStatusError) as exc:
            retryable = _is_retryable_upstream_error(exc)
            logger.error(
                "upstream stream attempt failed: id=%s attempt=%s/%s yielded=%s chunks=%s delta_chars=%s retryable=%s elapsed_ms=%s error=%s",
                stream_id,
                attempt + 1,
                STREAM_UPSTREAM_ATTEMPTS,
                yielded,
                item_count,
                total_delta_chars,
                retryable,
                int((time.perf_counter() - started) * 1000),
                repr(exc),
            )
            if yielded or attempt >= STREAM_UPSTREAM_ATTEMPTS - 1 or not retryable:
                raise
            time.sleep(min(1.0 + attempt, 3.0))


def _chat_openai_responses_stream(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
) -> Iterator[str]:
    for item in _chat_openai_responses_stream_events(messages, runtime, request_options=request_options):
        kind, text = _openai_responses_stream_delta(item)
        if text and kind == "text":
            yield text


def _chat_openai_responses_stream_as_chat_chunks(
    messages: list[dict[str, Any]],
    runtime: TranslatorRuntime,
    *,
    request_options: Optional[dict[str, Any]] = None,
) -> Iterator[dict[str, Any]]:
    created = int(time.time())
    chunk_id = f"chatcmpl-deepcat-{created}"
    model = runtime.display_name or runtime.model_name or SERVICE_MODEL_ID
    yield {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    for item in _chat_openai_responses_stream_events(messages, runtime, request_options=request_options):
        kind, text = _openai_responses_stream_delta(item)
        if not text:
            continue
        delta_key = "reasoning_content" if kind == "reasoning" else "content"
        yield {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {delta_key: text}, "finish_reason": None}],
        }
    yield {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }


def openai_chat_response(model: str, content: str, *, created: Optional[int] = None) -> dict[str, Any]:
    created_ts = int(created if created is not None else time.time())
    return {
        "id": f"chatcmpl-deepcat-{created_ts}",
        "object": "chat.completion",
        "created": created_ts,
        "model": str(model or SERVICE_MODEL_ID),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": str(content or "")},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
