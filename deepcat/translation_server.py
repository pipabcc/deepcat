from __future__ import annotations

import atexit
import dataclasses
import errno
import json
import logging
import os
import re
import select
import shlex
import socket
import socketserver
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urlsplit

from deepcat.translator_engine import (
    DEFAULT_SERVICE_API_KEY,
    SERVICE_MODEL_ID,
    available_models,
    chat_completion,
    chat_completion_response,
    chat_completion_response_stream,
    chat_completion_stream,
    extract_text_from_messages,
    openai_chat_response,
    translate_stream,
    translate_text,
    _has_client_translation_prompt,
)
from deepcat.utils.logger import get_logger
from deepcat.utils.log_redaction import redact_query, redact_url


logger = get_logger("deepcat.local_api_service", level=logging.INFO)
_background_lock = threading.RLock()
_background_server: Optional["TranslationHTTPServer"] = None
_background_thread: Optional[threading.Thread] = None
_background_server_v6: Optional["TranslationHTTPServer"] = None
_background_thread_v6: Optional[threading.Thread] = None
LOCAL_API_SSE_HEARTBEAT_SEC = 3.0

# 每条流式请求都会占用一个阻塞等待上游的 daemon 工作线程；
# 无上限时上游 hang + 高并发会无限堆积线程。超限时新请求立即得到明确错误。
SSE_WORKER_LIMIT = 32
_sse_worker_slots = threading.BoundedSemaphore(SSE_WORKER_LIMIT)


class _ClientDisconnectedError(ConnectionError):
    pass


class _SSEWorkerLimitError(RuntimeError):
    """并发流式请求数超过本地服务的 worker 上限。"""


# 防止超大 body / slowloris 慢请求耗尽内存与服务线程
MAX_REQUEST_BODY_BYTES = 64 * 1024 * 1024
SOCKET_TIMEOUT_SECONDS = 75


class TranslationHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], *, api_key: str) -> None:
        super().__init__(server_address, handler_class)
        self.api_key = str(api_key or "")


class TranslationHTTPServerV6(TranslationHTTPServer):
    """IPv6 回环监听，让把 localhost 解析成 ::1 的客户端也能连上。

    Windows 的 hosts 默认含「::1 localhost」，Chromium 系客户端会优先走 IPv6。
    只监听 IPv4 时这些连接会停在 SYN_SENT 直到超时（ERR_CONNECTION_TIMED_OUT）。
    """

    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        # 跳过 HTTPServer.server_bind 的 getfqdn：它对字面量 "::1" 只会多一次解析开销
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class TranslationRequestHandler(BaseHTTPRequestHandler):
    server: TranslationHTTPServer
    # 客户端 socket 读写超时：挡住 slowloris 式慢请求占住服务线程
    timeout = SOCKET_TIMEOUT_SECONDS

    def log_message(self, fmt: str, *args: Any) -> None:
        try:
            host, port = _client_host_port(getattr(self, "client_address", None))
            logger.info("local API access: client=%s:%s " + str(fmt), host, port, *args)
        except Exception:
            pass

    def do_OPTIONS(self) -> None:
        self._log_inbound_request()
        self._send_empty(HTTPStatus.NO_CONTENT)

    def do_GET(self) -> None:
        self._log_inbound_request()
        parsed = urlsplit(self.path)
        path = _normalized_path(parsed.path)
        if path in {"/health", "/ready"}:
            self._send_json({"ok": True, "service": "deepcat-translate"})
            return
        if path == "/":
            self._send_json(_service_info_payload(self))
            return
        if _is_models_path(path):
            if not self._is_authorized():
                self._send_error(HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return
            self._send_json({"object": "list", "data": [_model_payload(item["id"]) for item in available_models()]})
            return
        if _is_translate_path(path):
            self._handle_translate(initial_payload=_payload_from_query(parsed.query))
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        self._log_inbound_request()
        path = _normalized_path(urlsplit(self.path).path)
        if _is_chat_completion_path(path):
            self._handle_chat_completion()
            return
        if _is_responses_path(path):
            self._handle_responses(force_translate=_is_translate_responses_path(path))
            return
        if _is_translate_path(path):
            self._handle_translate()
            return
        self._log_unmatched_post_body(path)
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _handle_chat_completion(self) -> None:
        if not self._is_authorized():
            self._send_error(HTTPStatus.UNAUTHORIZED, "Unauthorized")
            return
        try:
            payload = self._read_json()
            messages = payload.get("messages")
            debug_text = ""
            if isinstance(messages, list):
                try:
                    debug_text = extract_text_from_messages(messages)
                except Exception:
                    debug_text = ""
            self._log_payload("chat", payload, text=debug_text, text_source="messages" if debug_text else "")
            if not isinstance(messages, list):
                raise ValueError("messages must be a list")
            model = _first_optional_str(payload, "model", "model_name", "modelName") or SERVICE_MODEL_ID
            stream = _bool_value(payload.get("stream", payload.get("streaming", False)))
            source_lang = _language_option(payload, ("source_lang", "sourceLang", "source", "from", "from_lang", "fromLang", "sl"))
            target_lang = _language_option(payload, ("target_lang", "targetLang", "target", "to", "to_lang", "toLang", "tl"))
            force_translate = _bool_value(payload.get("force_translate", payload.get("forceTranslate", False)))
            request_options = {
                key: payload[key]
                for key in (
                    "temperature",
                    "max_tokens",
                    "max_output_tokens",
                    "top_p",
                    "top_k",
                    "frequency_penalty",
                    "presence_penalty",
                    "response_format",
                    "stop",
                    "seed",
                    "user",
                )
                if key in payload
            }
            tools = _normalize_responses_tools(payload.get("tools"))
            if tools:
                request_options["tools"] = tools
                messages = _messages_with_apply_patch_policy(messages, tools)
            if "tool_choice" in payload:
                request_options["tool_choice"] = _normalize_tool_choice(payload.get("tool_choice"))
            if "parallel_tool_calls" in payload:
                request_options["parallel_tool_calls"] = payload["parallel_tool_calls"]
            active_tools = _tools_for_request_options(request_options.get("tools"), request_options)
            if stream:
                if active_tools:
                    self._send_openai_chat_response_stream(
                        model,
                        lambda cancel_event: _chat_completion_response_with_tool_support(
                            messages,
                            model_name=model,
                            source_lang=source_lang,
                            target_lang=target_lang,
                            force_translate=force_translate,
                            request_options=request_options,
                            cancel_event=cancel_event,
                        ),
                    )
                    return
                self._send_openai_stream(
                    model,
                    chat_completion_stream(
                        messages,
                        model_name=model,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        force_translate=force_translate,
                        request_options=request_options,
                    ),
                )
                return
            if active_tools:
                chat_payload = _chat_completion_response_with_tool_support(
                    messages,
                    model_name=model,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    force_translate=force_translate,
                    request_options=request_options,
                )
                logger.info(
                    "local API chat tool result: model=%s tool_calls=%s content_len=%s preview=%s",
                    model,
                    len((_first_chat_message(chat_payload) or {}).get("tool_calls") or []),
                    len(_chat_payload_content_text(chat_payload)),
                    _preview(_chat_payload_content_text(chat_payload)),
                )
                self._send_json(chat_payload)
                return
            content = chat_completion(
                messages,
                model_name=model,
                source_lang=source_lang,
                target_lang=target_lang,
                force_translate=force_translate,
                request_options=request_options,
            )
            logger.info("local API chat result: model=%s content_len=%s preview=%s", model, len(content or ""), _preview(content))
            self._send_json(openai_chat_response(model, content))
        except Exception as exc:
            logger.error("translation chat completion failed: %s", repr(exc))
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc) or repr(exc))

    def _handle_responses(self, *, force_translate: bool = False) -> None:
        if not self._is_authorized():
            self._send_error(HTTPStatus.UNAUTHORIZED, "Unauthorized")
            return
        try:
            payload = self._read_json()
            model = _first_optional_str(payload, "model", "model_name", "modelName") or SERVICE_MODEL_ID
            stream = _bool_value(payload.get("stream", payload.get("streaming", False)))
            source_lang = _language_option(payload, ("source_lang", "sourceLang", "source", "from", "from_lang", "fromLang", "sl"))
            target_lang = _language_option(payload, ("target_lang", "targetLang", "target", "to", "to_lang", "toLang", "tl"))
            force_translate = force_translate or _bool_value(payload.get("force_translate", payload.get("forceTranslate", False)))
            messages = _responses_messages(payload)
            direct_text, direct_source = _extract_translate_text(payload)
            message_text = ""
            try:
                message_text = extract_text_from_messages(messages)
            except Exception:
                message_text = ""
            structured_input = isinstance(payload.get("input"), (dict, list)) or isinstance(payload.get("messages"), list)
            if structured_input and message_text:
                text, text_source = message_text, "responses.input"
            elif direct_text:
                text, text_source = direct_text, direct_source
            else:
                text, text_source = message_text, "responses.input" if message_text else ""
            self._log_payload("responses", payload, text=text, text_source=text_source)
            request_options = _responses_request_options(payload)
            active_tools = _tools_for_request_options(request_options.get("tools"), request_options)
            messages = _messages_with_apply_patch_policy(messages, request_options.get("tools"))
            codex_client = _is_codex_responses_client(self.headers, payload)
            logger.info(
                "local API protocol bridge: client_protocol=responses upstream_contract=%s response_protocol=responses "
                "codex_client=%s model=%s stream=%s",
                "translate" if force_translate else "chat_completions",
                codex_client,
                model,
                stream,
            )
            if force_translate:
                # 沉浸式翻译等 OpenAI 兼容客户端会自带翻译提示词。
                # 此时仍使用翻译模型，但不再注入 DeepCat 内置翻译 prompt。
                client_has_prompt = _has_client_translation_prompt(messages)
                if client_has_prompt and messages:
                    logger.info(
                        "local API force_translate with client translation prompt detected; "
                        "bypassing built-in translation to respect client instructions"
                    )
                    if stream:
                        self._send_responses_chat_stream(
                            model,
                            chunks=chat_completion_response_stream(
                                messages,
                                model_name=model,
                                source_lang=source_lang,
                                target_lang=target_lang,
                                force_translate=True,
                                request_options=request_options,
                            ),
                            original_request=payload,
                        )
                        return
                    chat_payload = chat_completion_response(
                        messages,
                        model_name=model,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        force_translate=True,
                        request_options=request_options,
                    )
                    response_payload = _chat_completion_to_response_payload(chat_payload, original_request=payload)
                    logger.info(
                        "local API responses chat result (client prompt): model=%s output_items=%s output_len=%s preview=%s",
                        response_payload.get("model") or model,
                        len(response_payload.get("output") or []),
                        len(str(response_payload.get("output_text") or "")),
                        _preview(response_payload.get("output_text")),
                    )
                    self._send_json(response_payload)
                    return
                if not text:
                    keys = ", ".join(sorted(str(key) for key in payload.keys())) or "(empty)"
                    raise ValueError(
                        "text is required for Responses API translation; accepted fields include input, text, q, "
                        f"query, sourceText, messages, or data.text. Received keys: {keys}"
                    )
                if stream:
                    self._send_responses_stream(
                        model,
                        translate_stream(
                            text,
                            source_lang=source_lang,
                            target_lang=target_lang,
                            model_name=None if model == SERVICE_MODEL_ID else model,
                            request_options=request_options,
                        ),
                    )
                    return
                content = translate_text(
                    text,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    model_name=None if model == SERVICE_MODEL_ID else model,
                )
            else:
                if stream:
                    if active_tools:
                        self._send_responses_chat_stream(
                            model,
                            response_factory=lambda cancel_event: _chat_completion_response_with_tool_support(
                                messages,
                                model_name=model,
                                source_lang=source_lang,
                                target_lang=target_lang,
                                force_translate=False,
                                request_options=request_options,
                                cancel_event=cancel_event,
                            ),
                            original_request=payload,
                        )
                    else:
                        self._send_responses_chat_stream(
                            model,
                            chunks=chat_completion_response_stream(
                                messages,
                                model_name=model,
                                source_lang=source_lang,
                                target_lang=target_lang,
                                force_translate=False,
                                request_options=request_options,
                            ),
                            original_request=payload,
                        )
                    return
                if active_tools:
                    chat_payload = _chat_completion_response_with_tool_support(
                        messages,
                        model_name=model,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        force_translate=False,
                        request_options=request_options,
                    )
                else:
                    chat_payload = chat_completion_response(
                        messages,
                        model_name=model,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        force_translate=False,
                        request_options=request_options,
                    )
                response_payload = _chat_completion_to_response_payload(chat_payload, original_request=payload)
                logger.info(
                    "local API responses chat result: model=%s output_items=%s output_len=%s preview=%s",
                    response_payload.get("model") or model,
                    len(response_payload.get("output") or []),
                    len(str(response_payload.get("output_text") or "")),
                    _preview(response_payload.get("output_text")),
                )
                self._send_json(response_payload)
                return
            logger.info("local API responses result: model=%s content_len=%s preview=%s", model, len(content or ""), _preview(content))
            self._send_json(_openai_response_payload(model, content))
        except ValueError as exc:
            logger.error("translation responses request failed: %s", repr(exc))
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc) or repr(exc))
        except Exception as exc:
            logger.error("translation responses request failed: %s", repr(exc))
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc) or repr(exc))

    def _handle_translate(self, initial_payload: Optional[dict[str, Any]] = None) -> None:
        if not self._is_authorized():
            self._send_error(HTTPStatus.UNAUTHORIZED, "Unauthorized")
            return
        try:
            payload = initial_payload if initial_payload is not None else self._read_json()
            text, text_source = _extract_translate_text(payload)
            self._log_payload("translate", payload, text=text, text_source=text_source)
            if not text:
                keys = ", ".join(sorted(str(key) for key in payload.keys())) or "(empty)"
                raise ValueError(
                    "text is required; accepted fields include text, q, query, input, sourceText, "
                    f"source_text, text_list, texts, sentences, messages, or data.text. Received keys: {keys}"
                )
            source_lang = _language_option(payload, ("source_lang", "sourceLang", "source", "from", "from_lang", "fromLang", "sl"))
            target_lang = _language_option(payload, ("target_lang", "targetLang", "target", "to", "to_lang", "toLang", "tl"))
            model = _first_optional_str(payload, "model", "model_name", "modelName")
            request_options = {
                key: payload[key]
                for key in ("temperature", "max_tokens", "top_p", "top_k", "frequency_penalty", "presence_penalty")
                if key in payload
            }
            if _bool_value(payload.get("stream", payload.get("streaming", False))):
                self._send_translate_stream(
                    translate_stream(
                        text,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        model_name=model,
                        request_options=request_options,
                    ),
                    source_lang=source_lang,
                    target_lang=target_lang,
                    model=model or SERVICE_MODEL_ID,
                )
                return
            translated = translate_text(text, source_lang=source_lang, target_lang=target_lang, model_name=model)
            logger.info("local API translate result: model=%s text_len=%s preview=%s", model or SERVICE_MODEL_ID, len(translated or ""), _preview(translated))
            response_payload = {
                "text": translated,
                "translation": translated,
                "translated_text": translated,
                "translatedText": translated,
                "result": translated,
                "source_lang": source_lang or "auto",
                "target_lang": target_lang or "",
                "model": model or SERVICE_MODEL_ID,
                "data": {
                    "text": translated,
                    "translation": translated,
                    "translated_text": translated,
                    "translatedText": translated,
                },
                "translations": [{"text": translated, "translatedText": translated}],
            }
            chat_payload = openai_chat_response(model or SERVICE_MODEL_ID, translated)
            for choice in chat_payload.get("choices", []):
                if isinstance(choice, dict):
                    choice["text"] = translated
            response_payload.update(chat_payload)
            self._send_json(response_payload)
        except ValueError as exc:
            logger.error("translation request failed: %s", repr(exc))
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc) or repr(exc))
        except Exception as exc:
            logger.error("translation request failed: %s", repr(exc))
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc) or repr(exc))

    def _read_json(self) -> dict[str, Any]:
        if "chunked" in str(self.headers.get("Transfer-Encoding") or "").lower():
            # 不解析 chunked 编码：残留字节会污染连接上后续请求的解析
            raise ValueError("chunked transfer-encoding not supported; send Content-Length")
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except Exception:
            length = 0
        if length > MAX_REQUEST_BODY_BYTES:
            raise ValueError(f"request body too large (max {MAX_REQUEST_BODY_BYTES} bytes)")
        raw = self.rfile.read(max(0, length)) if length else b"{}"
        if not raw:
            return {}
        body = raw.decode("utf-8-sig", errors="replace")
        content_type = str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        logger.info(
            "local API body: path=%s bytes=%s content_type=%s preview=%s",
            _normalized_path(urlsplit(self.path).path),
            len(raw),
            content_type or "(none)",
            _preview(body),
        )
        if content_type == "application/x-www-form-urlencoded":
            return _payload_from_query(body)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            if body.lstrip().startswith(("{", "[")) or content_type == "application/json":
                raise
            return {"text": body}
        return _payload_from_value(data)

    def _is_authorized(self) -> bool:
        required = str(getattr(self.server, "api_key", "") or "")
        if not required:
            return True
        auth = str(self.headers.get("Authorization") or "").strip()
        if auth == required:
            return True
        if auth.lower().startswith("bearer "):
            if auth[7:].strip() == required:
                return True
        if str(self.headers.get("X-API-Key") or "").strip() == required:
            return True
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        for key in ("api_key", "apikey", "key", "token"):
            values = query.get(key) or []
            if values and str(values[0] or "").strip() == required:
                return True
        return False

    def _log_inbound_request(self) -> None:
        host, port = _client_host_port(getattr(self, "client_address", None))
        logger.info(
            "local API request: client=%s:%s method=%s path=%s query=%s content_length=%s content_type=%s origin=%s referer=%s ua=%s",
            host,
            port,
            getattr(self, "command", ""),
            _normalized_path(urlsplit(self.path).path),
            _preview(redact_query(urlsplit(self.path).query), 160) or "(none)",
            str(self.headers.get("Content-Length") or "0"),
            str(self.headers.get("Content-Type") or "(none)"),
            _preview(redact_url(self.headers.get("Origin")), 120) or "(none)",
            _preview(redact_url(self.headers.get("Referer")), 120) or "(none)",
            _preview(str(self.headers.get("User-Agent") or ""), 160) or "(none)",
        )

    def _log_payload(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        text: str = "",
        text_source: str = "",
    ) -> None:
        keys = sorted(str(key) for key in payload.keys())
        logger.info(
            "local API payload: endpoint=%s keys=%s text_source=%s text_len=%s stream=%s model=%s target=%s",
            endpoint,
            keys,
            text_source or "(n/a)",
            len(text or ""),
            payload.get("stream", payload.get("streaming", False)),
            _first_optional_str(payload, "model", "model_name", "modelName") or "(default)",
            _language_option(payload, ("target_lang", "targetLang", "target", "to", "to_lang", "toLang", "tl")) or "(default)",
        )

    def _log_unmatched_post_body(self, path: str) -> None:
        try:
            payload = self._read_json()
            text, text_source = _extract_translate_text(payload)
            logger.info(
                "local API unmatched POST body: path=%s keys=%s text_source=%s text_len=%s",
                path,
                sorted(str(key) for key in payload.keys()),
                text_source or "(n/a)",
                len(text or ""),
            )
        except Exception as exc:
            logger.info("local API unmatched POST body parse failed: path=%s error=%s", path, repr(exc))

    def _send_openai_stream(self, model: str, chunks: Iterable[str] | str) -> None:
        created = int(time.time())
        logger.info(
            "local API response: method=%s path=%s status=%s content_type=text/event-stream model=%s",
            getattr(self, "command", ""),
            _normalized_path(urlsplit(self.path).path),
            int(HTTPStatus.OK),
            str(model or SERVICE_MODEL_ID),
        )
        self.send_response(HTTPStatus.OK)
        self._send_common_headers("text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            role_chunk = {
                "id": f"chatcmpl-deepcat-{created}",
                "object": "chat.completion.chunk",
                "created": created,
                "model": str(model or SERVICE_MODEL_ID),
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            self._write_sse_payload(role_chunk)
            for part in _iter_outbound_text_chunks(chunks):
                item = {
                    "id": f"chatcmpl-deepcat-{created}",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": str(model or SERVICE_MODEL_ID),
                    "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}],
                }
                self._write_sse_payload(item)
            done_chunk = {
                "id": f"chatcmpl-deepcat-{created}",
                "object": "chat.completion.chunk",
                "created": created,
                "model": str(model or SERVICE_MODEL_ID),
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            self._write_sse_payload(done_chunk)
            self._write_sse_done()
        except (ConnectionError, OSError, _ClientDisconnectedError):
            return
        except Exception as exc:
            logger.error("translation chat stream failed: %s", repr(exc))
            try:
                self._write_sse_payload({"error": {"message": str(exc) or repr(exc), "type": "deepcat_error"}})
                self._write_sse_done()
            except Exception:
                pass

    def _send_openai_chat_response_stream(self, model: str, response_factory) -> None:
        created = int(time.time())
        logger.info(
            "local API response: method=%s path=%s status=%s content_type=text/event-stream model=%s mode=chat_tool_payload",
            getattr(self, "command", ""),
            _normalized_path(urlsplit(self.path).path),
            int(HTTPStatus.OK),
            str(model or SERVICE_MODEL_ID),
        )
        self.send_response(HTTPStatus.OK)
        self._send_common_headers("text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        try:
            try:
                def heartbeat() -> None:
                    self._write_sse_payload(
                        {
                            "id": f"chatcmpl-deepcat-{created}",
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": str(model or SERVICE_MODEL_ID),
                            "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
                        }
                    )

                payload = self._run_with_sse_heartbeats(response_factory, label="chat-tools", heartbeat=heartbeat)
            except _ClientDisconnectedError:
                raise
            except Exception as exc:
                logger.error("translation chat tool stream upstream failed: %s", repr(exc))
                payload = openai_chat_response(model, _stream_error_text(exc), created=created)

            choice = _first_chat_choice(payload) or {}
            message = _first_chat_message(payload) or {}
            chunk_id = str(payload.get("id") or f"chatcmpl-deepcat-{created}") if isinstance(payload, dict) else f"chatcmpl-deepcat-{created}"
            try:
                created_ts = int(payload.get("created") or created) if isinstance(payload, dict) else created
            except Exception:
                created_ts = created
            response_model = str((payload.get("model") if isinstance(payload, dict) else None) or model or SERVICE_MODEL_ID)

            def chunk(delta: dict[str, Any], finish_reason: Optional[str] = None) -> None:
                self._write_sse_payload(
                    {
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": response_model,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                    }
                )

            chunk({"role": "assistant"})
            content = _chat_content_text(message.get("content"))
            if content:
                for part in _stream_text_chunks(content):
                    if part:
                        chunk({"content": part})

            tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
            for index, tool_call in enumerate(tool_calls or []):
                if not isinstance(tool_call, dict):
                    continue
                fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                chunk(
                    {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": str(tool_call.get("id") or tool_call.get("call_id") or f"call_{index}"),
                                "type": str(tool_call.get("type") or "function"),
                                "function": {
                                    "name": str(fn.get("name") or tool_call.get("name") or ""),
                                    "arguments": _json_string(fn.get("arguments", tool_call.get("arguments", "{}"))),
                                },
                            }
                        ]
                    }
                )

            finish_reason = "tool_calls" if tool_calls else str(choice.get("finish_reason") or "stop")
            chunk({}, finish_reason)
            self._write_sse_done()
        except (ConnectionError, OSError, _ClientDisconnectedError):
            return
        except Exception as exc:
            logger.error("translation chat tool stream failed: %s", repr(exc))
            try:
                self._write_sse_payload({"error": {"message": str(exc) or repr(exc), "type": "deepcat_error"}})
                self._write_sse_done()
            except Exception:
                pass

    def _send_responses_stream(self, model: str, chunks: Iterable[str] | str) -> None:
        created = int(time.time())
        response_id = f"resp-deepcat-{created}"
        item_id = f"msg-deepcat-{created}"
        sequence_number = 0
        logger.info(
            "local API response: method=%s path=%s status=%s content_type=text/event-stream model=%s",
            getattr(self, "command", ""),
            _normalized_path(urlsplit(self.path).path),
            int(HTTPStatus.OK),
            str(model or SERVICE_MODEL_ID),
        )
        self.send_response(HTTPStatus.OK)
        self._send_common_headers("text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        accumulated: list[str] = []

        def event(payload: dict[str, Any]) -> None:
            nonlocal sequence_number
            payload.setdefault("sequence_number", sequence_number)
            sequence_number += 1
            self._write_sse_response_event(payload)

        try:
            event(
                {
                    "type": "response.created",
                    "response": _openai_response_payload(
                        model,
                        "",
                        created=created,
                        response_id=response_id,
                        status="in_progress",
                        include_output=False,
                    ),
                }
            )
            event(
                {
                    "type": "response.in_progress",
                    "response": _openai_response_payload(
                        model,
                        "",
                        created=created,
                        response_id=response_id,
                        status="in_progress",
                        include_output=False,
                    ),
                }
            )
            event(
                {
                    "type": "response.output_item.added",
                    "response_id": response_id,
                    "output_index": 0,
                    "item": {
                        "id": item_id,
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                }
            )
            event(
                {
                    "type": "response.content_part.added",
                    "response_id": response_id,
                    "item_id": item_id,
                    "output_index": 0,
                    "content_index": 0,
                    "part": {
                        "type": "output_text",
                        "text": "",
                        "annotations": [],
                    },
                }
            )
            try:
                for part in _iter_outbound_text_chunks(chunks):
                    accumulated.append(part)
                    event(
                        {
                            "type": "response.output_text.delta",
                            "response_id": response_id,
                            "item_id": item_id,
                            "output_index": 0,
                            "content_index": 0,
                            "delta": part,
                        }
                    )
            except Exception as exc:
                logger.error("translation responses stream upstream failed: %s", repr(exc))
                part = _stream_error_text(exc)
                accumulated.append(part)
                event(
                    {
                        "type": "response.output_text.delta",
                        "response_id": response_id,
                        "item_id": item_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": part,
                    }
                )
            output_text = "".join(accumulated)
            event(
                {
                    "type": "response.output_text.done",
                    "response_id": response_id,
                    "item_id": item_id,
                    "output_index": 0,
                    "content_index": 0,
                    "text": output_text,
                }
            )
            output_part = {
                "type": "output_text",
                "text": output_text,
                "annotations": [],
            }
            output_item = {
                "id": item_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [output_part],
            }
            event(
                {
                    "type": "response.content_part.done",
                    "response_id": response_id,
                    "item_id": item_id,
                    "output_index": 0,
                    "content_index": 0,
                    "part": output_part,
                }
            )
            event(
                {
                    "type": "response.output_item.done",
                    "response_id": response_id,
                    "output_index": 0,
                    "item": output_item,
                }
            )
            event(
                {
                    "type": "response.completed",
                    "response": _openai_response_payload(model, output_text, created=created, response_id=response_id),
                }
            )
            self._write_sse_done()
        except (ConnectionError, OSError, _ClientDisconnectedError):
            return
        except Exception as exc:
            logger.error("translation responses stream failed: %s", repr(exc))
            try:
                self._write_sse_payload({"type": "error", "error": {"message": str(exc) or repr(exc), "type": "deepcat_error"}})
                self._write_sse_done()
            except Exception:
                pass

    def _send_responses_chat_stream(
        self,
        model: str,
        chunks: Optional[Iterable[dict[str, Any]]] = None,
        response_factory=None,
        original_request: Optional[dict[str, Any]] = None,
    ) -> None:
        created = int(time.time())
        response_id = f"resp-deepcat-{created}-{uuid.uuid4().hex[:8]}"
        sequence_number = 0
        logger.info(
            "local API response: method=%s path=%s status=%s content_type=text/event-stream model=%s mode=chat_to_responses",
            getattr(self, "command", ""),
            _normalized_path(urlsplit(self.path).path),
            int(HTTPStatus.OK),
            str(model or SERVICE_MODEL_ID),
        )
        logger.info(
            "local API responses chat stream start: response_id=%s model=%s source=%s request_keys=%s",
            response_id,
            str(model or SERVICE_MODEL_ID),
            "response_factory" if response_factory is not None else "chunks",
            sorted(str(key) for key in original_request.keys()) if isinstance(original_request, dict) else [],
        )
        self.send_response(HTTPStatus.OK)
        self._send_common_headers("text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def event(payload: dict[str, Any]) -> None:
            nonlocal sequence_number
            payload.setdefault("sequence_number", sequence_number)
            sequence_number += 1
            self._write_sse_response_event(payload)

        try:
            event(
                {
                    "type": "response.created",
                    "response": _openai_response_payload(
                        model,
                        "",
                        created=created,
                        response_id=response_id,
                        status="in_progress",
                        include_output=False,
                    ),
                }
            )
            event(
                {
                    "type": "response.in_progress",
                    "response": _openai_response_payload(
                        model,
                        "",
                        created=created,
                        response_id=response_id,
                        status="in_progress",
                        include_output=False,
                    ),
                }
            )

            if response_factory is not None:
                try:
                    logger.info("local API responses chat stream upstream call start: response_id=%s mode=response_factory", response_id)
                    def progress_heartbeat() -> None:
                        event(
                            {
                                "type": "response.in_progress",
                                "response": _openai_response_payload(
                                    model,
                                    "",
                                    created=created,
                                    response_id=response_id,
                                    status="in_progress",
                                    include_output=False,
                                ),
                            }
                        )

                    chat_payload = self._run_with_sse_heartbeats(
                        response_factory,
                        label="responses-tools",
                        heartbeat=progress_heartbeat,
                    )
                    response_payload = _chat_completion_to_response_payload(
                        chat_payload,
                        response_id=response_id,
                        created=created,
                        original_request=original_request,
                    )
                    logger.info(
                        "local API responses chat stream upstream call completed: response_id=%s output_items=%s output_len=%s "
                        "output_summary=%s output_preview=%s",
                        response_id,
                        len(response_payload.get("output") or []),
                        len(str(response_payload.get("output_text") or "")),
                        json.dumps(_response_output_summary(response_payload.get("output") or []), ensure_ascii=False, sort_keys=True),
                        _preview(response_payload.get("output_text")),
                    )
                except _ClientDisconnectedError:
                    raise
                except Exception as exc:
                    logger.error("translation responses chat stream upstream failed: response_id=%s error=%s", response_id, repr(exc))
                    response_payload = _openai_response_payload(
                        model,
                        _stream_error_text(exc),
                        created=created,
                        response_id=response_id,
                    )
                self._write_responses_output_events(event, response_id, response_payload.get("output") or [])
            else:
                state = _ChatToResponsesStreamState(
                    model=model,
                    created=created,
                    response_id=response_id,
                    tool_context=_codex_tool_context(original_request.get("tools") if isinstance(original_request, dict) else None),
                    original_request=original_request,
                )
                upstream_chunks = 0
                try:
                    for chunk in chunks or []:
                        upstream_chunks += 1
                        if upstream_chunks <= 3 or upstream_chunks % 20 == 0:
                            logger.info(
                                "local API responses chat stream upstream chunk: response_id=%s chunk_index=%s keys=%s",
                                response_id,
                                upstream_chunks,
                                sorted(str(key) for key in chunk.keys()) if isinstance(chunk, dict) else type(chunk).__name__,
                            )
                        state.apply_chunk(chunk, event)
                except (ConnectionError, OSError):
                    raise
                except Exception as exc:
                    logger.error(
                        "translation responses chat stream upstream failed: response_id=%s chunks=%s error=%s",
                        response_id,
                        upstream_chunks,
                        repr(exc),
                    )
                    state.push_error_text(_stream_error_text(exc), event)
                response_payload = state.finish(event)
                logger.info(
                    "local API responses chat stream upstream iteration completed: response_id=%s chunks=%s output_items=%s output_len=%s "
                    "output_summary=%s output_preview=%s",
                    response_id,
                    upstream_chunks,
                    len(response_payload.get("output") or []),
                    len(str(response_payload.get("output_text") or "")),
                    json.dumps(_response_output_summary(response_payload.get("output") or []), ensure_ascii=False, sort_keys=True),
                    _preview(response_payload.get("output_text")),
                )
            event({"type": "response.completed", "response": response_payload})
            self._write_sse_done()
            logger.info(
                "local API responses chat stream completed: response_id=%s events=%s output_items=%s output_len=%s "
                "output_summary=%s output_preview=%s",
                response_id,
                sequence_number,
                len(response_payload.get("output") or []),
                len(str(response_payload.get("output_text") or "")),
                json.dumps(_response_output_summary(response_payload.get("output") or []), ensure_ascii=False, sort_keys=True),
                _preview(response_payload.get("output_text")),
            )
        except (ConnectionError, OSError):
            return
        except Exception as exc:
            logger.error("translation responses chat stream failed: %s", repr(exc))
            # 流已经以 response.created 开头，必须以 response.completed 收尾，
            # 否则 Codex 等客户端会按"流提前断开"处理并中断/重试整个会话。
            try:
                failure_payload = _openai_response_payload(
                    model,
                    _stream_error_text(exc),
                    created=created,
                    response_id=response_id,
                )
                event({"type": "response.completed", "response": failure_payload})
                self._write_sse_done()
            except Exception:
                try:
                    self._write_sse_response_event(
                        {"type": "error", "code": "deepcat_error", "message": str(exc) or repr(exc)}
                    )
                    self._write_sse_done()
                except Exception:
                    pass

    def _write_responses_output_events(self, event, response_id: str, output: list[dict[str, Any]]) -> None:
        for output_index, item in enumerate(output or []):
            if item.get("type") == "reasoning":
                summary = _response_reasoning_text([item])
                added_item = dict(item)
                added_item["summary"] = []
                added_item["status"] = "in_progress"
                event(
                    {
                        "type": "response.output_item.added",
                        "response_id": response_id,
                        "output_index": output_index,
                        "item": added_item,
                    }
                )
                part = {"type": "summary_text", "text": ""}
                event(
                    {
                        "type": "response.reasoning_summary_part.added",
                        "response_id": response_id,
                        "item_id": item["id"],
                        "output_index": output_index,
                        "summary_index": 0,
                        "part": part,
                    }
                )
                for delta in _stream_text_chunks(summary):
                    if delta:
                        event(
                            {
                                "type": "response.reasoning_summary_text.delta",
                                "response_id": response_id,
                                "item_id": item["id"],
                                "output_index": output_index,
                                "summary_index": 0,
                                "delta": delta,
                            }
                        )
                event(
                    {
                        "type": "response.reasoning_summary_text.done",
                        "response_id": response_id,
                        "item_id": item["id"],
                        "output_index": output_index,
                        "summary_index": 0,
                        "text": summary,
                    }
                )
                done_part = {"type": "summary_text", "text": summary}
                event(
                    {
                        "type": "response.reasoning_summary_part.done",
                        "response_id": response_id,
                        "item_id": item["id"],
                        "output_index": output_index,
                        "summary_index": 0,
                        "part": done_part,
                    }
                )
                event(
                    {
                        "type": "response.output_item.done",
                        "response_id": response_id,
                        "output_index": output_index,
                        "item": item,
                    }
                )
                continue

            if item.get("type") == "function_call":
                added_item = dict(item)
                added_item["status"] = "in_progress"
                added_item["arguments"] = ""
                event(
                    {
                        "type": "response.output_item.added",
                        "response_id": response_id,
                        "output_index": output_index,
                        "item": added_item,
                    }
                )
                arguments = str(item.get("arguments") or "")
                if arguments:
                    event(
                        {
                            "type": "response.function_call_arguments.delta",
                            "response_id": response_id,
                            "item_id": item["id"],
                            "output_index": output_index,
                            "delta": arguments,
                        }
                    )
                event(
                    {
                        "type": "response.function_call_arguments.done",
                        "response_id": response_id,
                        "item_id": item["id"],
                        "output_index": output_index,
                        "call_id": item["call_id"],
                        "name": item["name"],
                        "arguments": arguments,
                        **({"namespace": item["namespace"]} if item.get("namespace") else {}),
                    }
                )
                event(
                    {
                        "type": "response.output_item.done",
                        "response_id": response_id,
                        "output_index": output_index,
                        "item": item,
                    }
                )
                continue

            if item.get("type") == "custom_tool_call":
                added_item = dict(item)
                added_item["status"] = "in_progress"
                added_item["input"] = ""
                event(
                    {
                        "type": "response.output_item.added",
                        "response_id": response_id,
                        "output_index": output_index,
                        "item": added_item,
                    }
                )
                input_text = str(item.get("input") or "")
                if input_text:
                    event(
                        {
                            "type": "response.custom_tool_call_input.delta",
                            "response_id": response_id,
                            "item_id": item["id"],
                            "call_id": item["call_id"],
                            "output_index": output_index,
                            "delta": input_text,
                        }
                    )
                event(
                    {
                        "type": "response.custom_tool_call_input.done",
                        "response_id": response_id,
                        "item_id": item["id"],
                        "call_id": item["call_id"],
                        "output_index": output_index,
                        "input": input_text,
                    }
                )
                event(
                    {
                        "type": "response.output_item.done",
                        "response_id": response_id,
                        "output_index": output_index,
                        "item": item,
                    }
                )
                continue

            if item.get("type") != "message":
                continue
            in_progress = dict(item)
            in_progress["status"] = "in_progress"
            in_progress["content"] = []
            event(
                {
                    "type": "response.output_item.added",
                    "response_id": response_id,
                    "output_index": output_index,
                    "item": in_progress,
                }
            )
            for content_index, part in enumerate(item.get("content", [])):
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                empty_part = {"type": "output_text", "text": "", "annotations": []}
                event(
                    {
                        "type": "response.content_part.added",
                        "response_id": response_id,
                        "item_id": item["id"],
                        "output_index": output_index,
                        "content_index": content_index,
                        "part": empty_part,
                    }
                )
                full_text = str(part.get("text") or "")
                for delta in _stream_text_chunks(full_text):
                    if delta:
                        event(
                            {
                                "type": "response.output_text.delta",
                                "response_id": response_id,
                                "item_id": item["id"],
                                "output_index": output_index,
                                "content_index": content_index,
                                "delta": delta,
                            }
                        )
                event(
                    {
                        "type": "response.output_text.done",
                        "response_id": response_id,
                        "item_id": item["id"],
                        "output_index": output_index,
                        "content_index": content_index,
                        "text": full_text,
                    }
                )
                event(
                    {
                        "type": "response.content_part.done",
                        "response_id": response_id,
                        "item_id": item["id"],
                        "output_index": output_index,
                        "content_index": content_index,
                        "part": part,
                    }
                )
            event(
                {
                    "type": "response.output_item.done",
                    "response_id": response_id,
                    "output_index": output_index,
                    "item": item,
                }
            )

    def _send_translate_stream(
        self,
        chunks: Iterable[str] | str,
        *,
        source_lang: Optional[str],
        target_lang: Optional[str],
        model: str,
    ) -> None:
        created = int(time.time())
        logger.info(
            "local API response: method=%s path=%s status=%s content_type=text/event-stream model=%s",
            getattr(self, "command", ""),
            _normalized_path(urlsplit(self.path).path),
            int(HTTPStatus.OK),
            str(model or SERVICE_MODEL_ID),
        )
        self.send_response(HTTPStatus.OK)
        self._send_common_headers("text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for part in _iter_outbound_text_chunks(chunks):
                self._write_sse_payload(
                    {
                        "object": "translation.chunk",
                        "created": created,
                        "model": str(model or SERVICE_MODEL_ID),
                        "delta": part,
                        "text": part,
                        "source_lang": source_lang or "auto",
                        "target_lang": target_lang or "",
                        "finish_reason": None,
                    }
                )
            self._write_sse_payload(
                {
                    "object": "translation.chunk",
                    "created": created,
                    "model": str(model or SERVICE_MODEL_ID),
                    "delta": "",
                    "text": "",
                    "source_lang": source_lang or "auto",
                    "target_lang": target_lang or "",
                    "finish_reason": "stop",
                }
            )
            self._write_sse_done()
        except (ConnectionError, OSError):
            return
        except Exception as exc:
            logger.error("translation stream failed: %s", repr(exc))
            try:
                self._write_sse_payload({"error": {"message": str(exc) or repr(exc), "type": "deepcat_error"}})
                self._write_sse_done()
            except Exception:
                pass

    def _write_sse_payload(self, payload: dict[str, Any]) -> None:
        self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _write_sse_response_event(self, payload: dict[str, Any]) -> None:
        """写入符合 OpenAI Responses API 规范的 SSE 事件。

        格式: ``event: <type>\ndata: <json>\n\n``
        与 ``_write_sse_payload``（仅 ``data:`` 行）不同，此方法在
        ``data`` 行前插入 ``event:`` 行，使客户端能通过标准 EventSource
        的 event 字段路由事件。仅用于 ``/v1/responses`` 端点的 SSE 流。
        """
        event_type = payload.get("type", "")
        data_json = json.dumps(payload, ensure_ascii=False)
        if event_type:
            sse_bytes = f"event: {event_type}\ndata: {data_json}\n\n".encode("utf-8")
        else:
            sse_bytes = f"data: {data_json}\n\n".encode("utf-8")
        logger.debug(
            "SSE event write: type=%s seq=%s bytes=%s preview=%s",
            event_type or "(none)",
            payload.get("sequence_number", "?"),
            len(sse_bytes),
            data_json[:200],
        )
        self.wfile.write(sse_bytes)
        self.wfile.flush()

    def _write_sse_heartbeat(self) -> None:
        self.wfile.write(b": keep-alive\n\n")
        self.wfile.flush()

    def _write_sse_done(self) -> None:
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

    def _run_with_sse_heartbeats(self, func, *, label: str = "upstream", heartbeat=None) -> Any:
        result: dict[str, Any] = {}
        done = threading.Event()
        cancel_event = threading.Event()
        slots = _sse_worker_slots

        def runner() -> None:
            try:
                result["value"] = func(cancel_event)
            except BaseException as exc:
                result["error"] = exc
            finally:
                # 客户端退出不等于上游退出，名额必须跟随真实的工作线程。
                slots.release()
                done.set()

        if not slots.acquire(blocking=False):
            raise _SSEWorkerLimitError(
                f"并发流式请求数已达上限（{SSE_WORKER_LIMIT}），请稍后重试"
            )
        try:
            thread = threading.Thread(target=runner, name=f"DeepCatLocalAPI{label.title()}Worker", daemon=True)
            thread.start()
        except BaseException:
            slots.release()
            raise
        try:
            heartbeat_sec = max(0.05, float(LOCAL_API_SSE_HEARTBEAT_SEC or 10.0))
            next_heartbeat_at = time.monotonic() + heartbeat_sec
            while not done.is_set():
                now = time.monotonic()
                wait_seconds = min(0.2, max(0.0, next_heartbeat_at - now))
                if done.wait(wait_seconds):
                    break
                if self._client_disconnected():
                    cancel_event.set()
                    logger.info(
                        "local API SSE client disconnected while waiting for upstream: label=%s path=%s",
                        label,
                        _normalized_path(urlsplit(self.path).path),
                    )
                    raise _ClientDisconnectedError("SSE client disconnected")
                now = time.monotonic()
                if now >= next_heartbeat_at:
                    try:
                        self._write_sse_heartbeat()
                        if heartbeat is not None:
                            heartbeat()
                    except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                        cancel_event.set()
                        logger.info(
                            "local API SSE heartbeat failed because client disconnected: label=%s path=%s error=%s",
                            label,
                            _normalized_path(urlsplit(self.path).path),
                            repr(exc),
                        )
                        raise _ClientDisconnectedError("SSE client disconnected") from exc
                    next_heartbeat_at = now + heartbeat_sec
        finally:
            if not done.is_set():
                cancel_event.set()
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        logger.info(
            "local API response: method=%s path=%s status=%s bytes=%s",
            getattr(self, "command", ""),
            _normalized_path(urlsplit(self.path).path),
            int(status),
            len(body),
        )
        self.send_response(status)
        self._send_common_headers("application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": {"message": str(message or status.phrase), "type": "deepcat_error"}}, status)

    def _send_empty(self, status: HTTPStatus) -> None:
        logger.info(
            "local API response: method=%s path=%s status=%s bytes=0",
            getattr(self, "command", ""),
            _normalized_path(urlsplit(self.path).path),
            int(status),
        )
        self.send_response(status)
        self._send_common_headers("text/plain; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_common_headers(self, content_type: str) -> None:
        self.send_header("Content-Type", content_type)
        origin = str(self.headers.get("Origin") or "").strip()
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        if origin:
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        requested_headers = str(self.headers.get("Access-Control-Request-Headers") or "").strip()
        self.send_header(
            "Access-Control-Allow-Headers",
            requested_headers or "accept, authorization, content-type, x-api-key, x-requested-with",
        )
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "86400")


def _model_payload(model_id: str) -> dict[str, Any]:
    return {
        "id": str(model_id or SERVICE_MODEL_ID),
        "object": "model",
        "created": 0,
        "owned_by": "deepcat",
    }


def _json_string(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False)
    except Exception:
        return str(value or "{}")


def _normalize_tool_schema(schema: object) -> dict[str, Any]:
    normalized = dict(schema) if isinstance(schema, dict) else {}
    normalized.setdefault("type", "object")
    if not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    if not isinstance(normalized.get("required"), list):
        normalized["required"] = []
    return normalized


def _flatten_namespace_tool_name(namespace: object, name: object) -> str:
    namespace_text = str(namespace or "")
    name_text = str(name or "")
    if not namespace_text:
        return name_text
    if not name_text:
        return namespace_text
    if namespace_text.endswith("__") or name_text.startswith("__"):
        return f"{namespace_text}{name_text}"
    return f"{namespace_text}__{name_text}"


def _combine_tool_description(namespace_description: object, child_description: object) -> str:
    namespace_text = str(namespace_description or "").strip()
    child_text = str(child_description or "").strip()
    if namespace_text and child_text:
        return f"{namespace_text}\n\n{child_text}"
    return child_text or namespace_text


def _tool_names_from_tools(tools: object) -> set[str]:
    names: set[str] = set()
    if not isinstance(tools, list):
        return names
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = fn.get("name") or tool.get("name")
        if name:
            names.add(str(name))
    return names


def _tool_name_tail(name: object) -> str:
    parts = re.split(r"(?:__|\.)", str(name or "").strip())
    return parts[-1] if parts else ""


def _tool_name_canonical(name: object) -> str:
    return str(name or "").strip().replace(".", "__")


def _normalize_tool_name(name: object, allowed_names: Optional[set[str]] = None) -> str:
    text = str(name or "").strip()
    if allowed_names:
        if text in allowed_names:
            return text
        canonical = _tool_name_canonical(text)
        canonical_matches = {
            _tool_name_canonical(allowed): allowed
            for allowed in sorted(allowed_names)
        }
        if canonical in canonical_matches:
            return canonical_matches[canonical]
        short = _tool_name_tail(text)
        if short in allowed_names:
            return short
        tail_matches = [
            allowed
            for allowed in sorted(allowed_names)
            if _tool_name_tail(allowed) == short
        ]
        if len(tail_matches) == 1:
            return tail_matches[0]
    return text


def _normalize_tool_arguments(value: object) -> str:
    if value is None or value == "":
        return "{}"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "{}"
        try:
            return json.dumps(json.loads(text), ensure_ascii=False)
        except Exception:
            return json.dumps({"input": value}, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def _load_jsonish(value: object) -> object:
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


def _parse_loose_tool_call_block(block: str) -> object:
    try:
        return _load_jsonish(block)
    except Exception:
        pass

    name_match = re.search(
        r'(?im)(?:^|[,{]\s*)["\']?(?:name|tool_name|recipient_name)["\']?\s*[:=]\s*["`\']?([A-Za-z0-9_.-]+)',
        block,
    )
    if not name_match:
        return None
    data: dict[str, Any] = {"name": name_match.group(1)}
    args_match = re.search(r'(?is)(?:^|[,{]\s*)["\']?(?:arguments|args|parameters|input)["\']?\s*[:=]\s*(.+)$', block)
    if args_match:
        raw_args = args_match.group(1).strip().strip("`")
        if block.strip().startswith("{") and raw_args.startswith("{") and raw_args.endswith("}"):
            raw_args = raw_args[:-1].rstrip()
        try:
            data["arguments"] = _load_jsonish(raw_args)
        except Exception:
            data["arguments"] = _parse_loose_argument_object(raw_args)
    return data


def _parse_loose_argument_object(raw_args: str) -> object:
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


def _coerce_tool_calls(payload: object, allowed_names: Optional[set[str]] = None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if payload is None:
        return calls
    if isinstance(payload, list):
        for item in payload:
            calls.extend(_coerce_tool_calls(item, allowed_names))
        return calls
    if not isinstance(payload, dict):
        return calls

    for key in ("tool_calls", "function_calls", "calls"):
        nested = payload.get(key)
        if isinstance(nested, list):
            calls.extend(_coerce_tool_calls(nested, allowed_names))
    for key in ("tool_call", "function_call", "call"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            calls.extend(_coerce_tool_calls(nested, allowed_names))

    fn = payload.get("function") if isinstance(payload.get("function"), dict) else {}
    raw_name = fn.get("name") or payload.get("name") or payload.get("tool_name") or payload.get("recipient_name")
    name = _normalize_tool_name(raw_name, allowed_names)
    if not name or (allowed_names and name not in allowed_names):
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
    calls.append(
        {
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": _normalize_tool_arguments(args),
            },
        }
    )
    return calls


def _parse_tool_calls_from_text(text: object, tools: object = None) -> tuple[str, list[dict[str, Any]]]:
    source = str(text or "")
    allowed_names = _tool_names_from_tools(tools)
    tool_calls: list[dict[str, Any]] = []
    consumed_spans: list[tuple[int, int]] = []

    def consume_payload(payload: object, span: Optional[tuple[int, int]] = None) -> None:
        calls = _coerce_tool_calls(payload, allowed_names)
        if not calls:
            return
        tool_calls.extend(calls)
        if span:
            consumed_spans.append(span)

    def span_consumed(span: tuple[int, int]) -> bool:
        start, end = span
        return any(start >= used_start and end <= used_end for used_start, used_end in consumed_spans)

    fence_pattern = re.compile(r'```([A-Za-z0-9_-]*)\s*\n(.*?)```', re.DOTALL)
    for match in fence_pattern.finditer(source):
        language = (match.group(1) or "").strip().lower()
        if language and language not in {"tool_call", "tool", "json", "jsonc"}:
            continue
        consume_payload(_parse_loose_tool_call_block(match.group(2)), match.span())

    tag_pattern = re.compile(
        r'<(?:tool_call|function_call)>\s*(.*?)\s*</(?:tool_call|function_call)>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in tag_pattern.finditer(source):
        consume_payload(_parse_loose_tool_call_block(match.group(1)), match.span())

    stripped = source.strip()
    if not tool_calls and stripped.startswith(("{", "[")):
        try:
            consume_payload(_load_jsonish(stripped), (0, len(source)))
        except Exception:
            pass

    decoder = json.JSONDecoder()
    for index, ch in enumerate(source):
        if ch not in "[{":
            continue
        try:
            parsed, end = decoder.raw_decode(source[index:])
        except json.JSONDecodeError:
            continue
        span = (index, index + end)
        if not span_consumed(span):
            consume_payload(parsed, span)

    if allowed_names:
        name_pattern = re.compile(r'(?<![\w.])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\(')
        for match in name_pattern.finditer(source):
            name = _normalize_tool_name(match.group(1), allowed_names)
            if name not in allowed_names:
                continue
            index = match.end()
            while index < len(source) and source[index].isspace():
                index += 1
            if index >= len(source):
                continue
            if source[index] == ")":
                consume_payload({"name": name, "arguments": {}}, (match.start(), index + 1))
                continue
            if source[index] not in "[{":
                continue
            try:
                parsed_args, end = decoder.raw_decode(source[index:])
            except json.JSONDecodeError:
                continue
            close = index + end
            while close < len(source) and source[close].isspace():
                close += 1
            if close < len(source) and source[close] == ")":
                close += 1
            span = (match.start(), close)
            if not span_consumed(span):
                consume_payload({"name": name, "arguments": parsed_args}, span)

    clean = source
    for start, end in sorted(consumed_spans, reverse=True):
        clean = clean[:start] + clean[end:]
    return clean.strip(), tool_calls


def _tool_choice_function_name(tool_choice: object) -> str:
    if not isinstance(tool_choice, dict):
        return ""
    fn = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
    return str(fn.get("name") or tool_choice.get("name") or "").strip()


def _tools_for_request_options(tools: object, request_options: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    options = request_options or {}
    tool_choice = options.get("tool_choice")
    if tool_choice == "none":
        return []
    required_name = _tool_choice_function_name(tool_choice)
    if not required_name:
        return list(tools)
    return [
        tool
        for tool in tools
        if isinstance(tool, dict)
        and _normalize_tool_name(
            (tool.get("function") if isinstance(tool.get("function"), dict) else tool).get("name"),
            {required_name},
        )
        == required_name
    ]


def _apply_tool_policy(tool_calls: object, request_options: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    calls = list(tool_calls or []) if isinstance(tool_calls, list) else []
    options = request_options or {}
    if options.get("tool_choice") == "none":
        return []
    required_name = _tool_choice_function_name(options.get("tool_choice"))
    if required_name:
        calls = [
            call
            for call in calls
            if isinstance(call, dict)
            and _normalize_tool_name((call.get("function") or {}).get("name") or call.get("name"), {required_name})
            == required_name
        ]
    if options.get("parallel_tool_calls") is False and len(calls) > 1:
        return calls[:1]
    return calls


def _tool_instruction_text(tools: object, request_options: Optional[dict[str, Any]] = None) -> str:
    if not isinstance(tools, list) or not tools:
        return ""
    tool_defs: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = fn.get("name") or tool.get("name")
        if not name:
            continue
        params = fn.get("parameters") or fn.get("input_schema") or fn.get("schema") or {"type": "object"}
        tool_defs.append(
            {
                "name": str(name),
                "description": str(fn.get("description") or tool.get("description") or "")[:4000],
                "parameters": params,
            }
        )
    if not tool_defs:
        return ""
    tool_names = ", ".join(item["name"] for item in tool_defs)
    lines = [
        "You have access to the following tools. Use them directly to fulfill user requests.",
        "CRITICAL: Do NOT describe what you plan to do in prose. Instead, call the appropriate tool immediately.",
        "CRITICAL: All listed tools are available and functional. NEVER claim a tool is missing, unavailable, or cannot be found.",
        f"Allowed tool names: {tool_names}",
        "To call a tool, respond ONLY with one or more blocks in this exact format (no surrounding prose):",
        '```tool_call\n{"name": "tool_name", "arguments": {...}}\n```',
        "The arguments value must be a valid JSON object matching the tool's parameter schema.",
    ]
    options = request_options or {}
    if options.get("tool_choice") == "none":
        lines.append("Tool choice is none: do not call tools in this turn; answer in text only.")
    elif options.get("tool_choice") == "required":
        lines.append("Tool choice is required: call at least one available tool before giving a final answer.")
    else:
        required_name = _tool_choice_function_name(options.get("tool_choice"))
        if required_name:
            lines.append(f"Tool choice requires function `{required_name}`: call only this function for the next tool action.")
    if options.get("parallel_tool_calls") is False:
        lines.append("parallel_tool_calls is false: return at most one tool call in this response.")
    lines.append(f"Available tools:\n{json.dumps(tool_defs, ensure_ascii=False, indent=2)}")
    return "\n".join(lines)


def _messages_with_tool_instruction(
    messages: list[dict[str, Any]],
    tools: object,
    request_options: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    instruction = _tool_instruction_text(tools, request_options)
    if not instruction:
        return list(messages or [])
    return [{"role": "system", "content": instruction}] + list(messages or [])


_APPLY_PATCH_EDIT_POLICY_TEXT = (
    "MANDATORY FILE-EDIT POLICY (overrides any conflicting habit):\n"
    "- To create, overwrite, modify, move or delete ANY file you MUST call one of these tools: "
    "apply_patch_add_file, apply_patch_replace_file, apply_patch_update_file, apply_patch_delete_file "
    "(or apply_patch with a raw patch).\n"
    "- NEVER write file content through shell commands. Redirection or here-docs such as echo/printf/cat <<EOF/"
    "tee/Set-Content/Out-File/New-Item used to create or change a file are FORBIDDEN.\n"
    "- shell_command is ONLY for reading files, listing directories and running programs or tests.\n"
    '- Example: to create index.html call apply_patch_add_file with {"path": "index.html", "content": "<!DOCTYPE html>..."}.'
)


def _has_apply_patch_proxy_tools(tools: object) -> bool:
    if not isinstance(tools, list):
        return False
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        if str(fn.get("name") or "").startswith("apply_patch_"):
            return True
    return False


def _messages_with_apply_patch_policy(
    messages: list[dict[str, Any]],
    tools: object,
) -> list[dict[str, Any]]:
    """注入强制 apply_patch 文件编辑策略。

    仅靠代理工具描述不足以扭转部分 chat 上游模型偏好 shell 写文件的习惯，
    在客户端 instructions 之后追加一条 system 策略消息加强约束，
    否则 codex 无法将文件改动渲染为可审阅的编辑卡片。
    """
    if not _has_apply_patch_proxy_tools(tools):
        return list(messages or [])
    result = list(messages or [])
    insert_at = 0
    while insert_at < len(result) and str(result[insert_at].get("role") or "") == "system":
        insert_at += 1
    result.insert(insert_at, {"role": "system", "content": _APPLY_PATCH_EDIT_POLICY_TEXT})
    return result


def _text_looks_like_tool_intent(
    text: object,
    tools: object = None,
    request_options: Optional[dict[str, Any]] = None,
) -> bool:
    value = str(text or "")
    lowered = value.lower()
    options = request_options or {}
    if options.get("tool_choice") == "required" or _tool_choice_function_name(options.get("tool_choice")):
        return True
    tool_names = _tool_names_from_tools(tools)
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
    # 中文意图短语检测
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
        "我来看看",
        "我来试试",
        "我来重新",
        "我来修复",
        "我来生成",
        "我来创建",
        "我来运行",
        "我来执行",
        "我来修改",
        "我需要检查",
        "我需要搜索",
        "我需要查看",
        "我将检查",
        "我将搜索",
        "我将查看",
        "我将重新",
        "我将修复",
        "我将生成",
        "现在让我",
        "现在我来",
        "接下来我",
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
    if any(phrase in value for phrase in chinese_intent_phrases):
        return True
    # 末尾冒号：模型宣告即将执行动作（如"让我重新生成正确的文件："）却没有发出工具调用，
    # 这类"话说一半"的回复几乎总以冒号收尾，是最通用的意图信号。
    stripped = value.rstrip().rstrip("*`")
    return bool(stripped) and stripped.endswith(("：", ":"))


def _tool_retry_messages(
    messages: list[dict[str, Any]],
    previous_text: object,
    tools: object,
    request_options: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    tool_name_set = _tool_names_from_tools(tools)
    names = ", ".join(sorted(tool_name_set))
    first_name = sorted(tool_name_set)[0] if tool_name_set else "tool_name"
    correction = (
        "ERROR: Your previous response described an action in prose instead of calling a tool. "
        "The client cannot execute prose descriptions — only valid tool_call blocks are executable.\n\n"
        f"All of these tools ARE available and functional: {names}\n\n"
        "You MUST respond with ONLY a tool_call block. Example:\n"
        f'```tool_call\n{{"name": "{first_name}", "arguments": {{"...": "..."}}}}\n```\n\n'
        "Rules:\n"
        "- Do NOT include any text outside the tool_call block\n"
        "- Do NOT apologize or explain\n"
        "- Do NOT claim any tool is missing or unavailable\n"
        "- Do NOT describe what you plan to do — just DO it by calling the tool\n\n"
        f"Your previous invalid response (excerpt):\n{_preview(previous_text, 800)}"
    )
    return _messages_with_tool_instruction(messages, tools, request_options) + [
        {"role": "user", "content": correction},
    ]


def _first_chat_choice(payload: object) -> Optional[dict[str, Any]]:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    return choices[0] if isinstance(choices[0], dict) else None


def _first_chat_message(payload: object) -> Optional[dict[str, Any]]:
    choice = _first_chat_choice(payload)
    if not choice:
        return None
    message = choice.get("message")
    return message if isinstance(message, dict) else None


def _finalize_chat_tool_calls(
    payload: dict[str, Any],
    tools: object,
    request_options: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    choice = _first_chat_choice(payload)
    message = _first_chat_message(payload)
    if not choice or not message:
        return payload
    active_tools = _tools_for_request_options(tools, request_options)
    if not active_tools and not message.get("tool_calls") and not message.get("function_call"):
        return payload

    active_names = _tool_names_from_tools(active_tools or tools)
    calls: list[dict[str, Any]] = []
    if isinstance(message.get("tool_calls"), list):
        for raw_call in message.get("tool_calls") or []:
            normalized_calls = _coerce_tool_calls(raw_call, active_names if active_tools else None)
            if normalized_calls:
                calls.extend(normalized_calls)
            elif not active_tools and isinstance(raw_call, dict):
                calls.append(raw_call)
    legacy_call = message.get("function_call")
    if isinstance(legacy_call, dict):
        calls.extend(_coerce_tool_calls(legacy_call, active_names))

    content = message.get("content")
    content_text = _chat_content_text(content)
    if content_text and active_tools:
        clean, parsed_calls = _parse_tool_calls_from_text(content_text, active_tools)
        if parsed_calls:
            message["content"] = clean
            calls.extend(parsed_calls)

    calls = _apply_tool_policy(calls, request_options)
    if calls:
        message["tool_calls"] = calls
        choice["finish_reason"] = "tool_calls"
    else:
        message.pop("tool_calls", None)
    return payload


def _chat_payload_has_tool_calls(payload: object) -> bool:
    message = _first_chat_message(payload)
    return bool(isinstance(message, dict) and message.get("tool_calls"))


def _chat_payload_content_text(payload: object) -> str:
    message = _first_chat_message(payload)
    if not isinstance(message, dict):
        return ""
    return _chat_content_text(message.get("content"))


def _chat_completion_response_with_tool_support(
    messages: list[dict[str, Any]],
    *,
    model_name: str,
    source_lang: Optional[str],
    target_lang: Optional[str],
    force_translate: bool,
    request_options: Optional[dict[str, Any]],
    cancel_event: Optional[threading.Event] = None,
) -> dict[str, Any]:
    options = dict(request_options or {})
    declared_tools = options.get("tools") if isinstance(options.get("tools"), list) else []
    active_tools = _tools_for_request_options(declared_tools, options)
    # 方案A：优先使用原生 function calling，tools 通过 options 直接传递给上游
    # 不再通过 _messages_with_tool_instruction 注入 system prompt，避免与原生 tools 参数冲突
    payload = chat_completion_response(
        messages,
        model_name=model_name,
        source_lang=source_lang,
        target_lang=target_lang,
        force_translate=force_translate,
        request_options=options,
        cancel_event=cancel_event,
    )
    payload = _finalize_chat_tool_calls(payload, declared_tools, options)

    content = _chat_payload_content_text(payload)
    if active_tools and not _chat_payload_has_tool_calls(payload) and _text_looks_like_tool_intent(content, active_tools, options):
        logger.info("local API tool intent prose detected; retrying with prompt injection fallback")
        # 回退策略：通过 prompt 注入工具指令，同时从 options 中移除原生 tools 参数避免冲突
        retry_options = dict(options)
        retry_options.pop("tools", None)
        retry_options.pop("tool_choice", None)
        retry_options.pop("parallel_tool_calls", None)
        retry_payload = chat_completion_response(
            _tool_retry_messages(messages, content, active_tools, options),
            model_name=model_name,
            source_lang=source_lang,
            target_lang=target_lang,
            force_translate=force_translate,
            request_options=retry_options,
            cancel_event=cancel_event,
        )
        retry_payload = _finalize_chat_tool_calls(retry_payload, declared_tools, options)
        if _chat_payload_has_tool_calls(retry_payload):
            payload = retry_payload
    return _enforce_apply_patch_file_writes(
        payload,
        messages,
        model_name=model_name,
        source_lang=source_lang,
        target_lang=target_lang,
        force_translate=force_translate,
        options=options,
        declared_tools=declared_tools,
        cancel_event=cancel_event,
    )


_SHELL_TOOL_NAME_TOKENS = ("shell", "bash", "terminal", "exec")

_SHELL_REDIRECT_WRITE_RE = re.compile(r"(?:echo|printf|cat)\b[^|;&]{0,6000}?>{1,2}\s*\S+", re.IGNORECASE)
_SHELL_PATH_TOKEN_RE = r'(?:"[^"]+"|\'[^\']+\'|[^\s;&|]+)'
_SHELL_HEREDOC_RE = re.compile(
    r"(?is)(?P<head>[^\r\n]*?)<<-?\s*['\"]?(?P<tag>[A-Za-z0-9_.-]+)['\"]?(?P<after>[^\r\n]*)"
    r"\r?\n(?P<content>.*?)\r?\n(?P=tag)(?=\s|$)"
)
_SHELL_SIMPLE_WRITE_RE = re.compile(
    rf"(?im)\b(?P<cmd>echo|printf)\s+(?P<body>[^\r\n;&|]*?)\s+(?<![>\d])>(?!>)\s*(?P<path>{_SHELL_PATH_TOKEN_RE})"
)
_SHELL_REDIRECT_PATH_RE = re.compile(rf"(?<![>\d])>(?!>)\s*(?P<path>{_SHELL_PATH_TOKEN_RE})", re.IGNORECASE)
_SHELL_TEE_PATH_RE = re.compile(rf"\btee\s+(?!-[aA]\b)(?:-[A-Za-z]+\s+)*(?P<path>{_SHELL_PATH_TOKEN_RE})", re.IGNORECASE)
_PS_HERE_STRING_ASSIGN_RE = re.compile(
    r"(?is)\$(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*@(?P<quote>['\"])\r?\n(?P<content>.*?)\r?\n(?P=quote)@"
)
_PS_HERE_STRING_PIPE_RE = re.compile(
    r"(?is)@(?P<quote>['\"])\r?\n(?P<content>.*?)\r?\n(?P=quote)@\s*\|\s*"
    r"(?P<cmd>Set-Content|Out-File)\b(?P<tail>[^\r\n;&|]*)"
)
_PS_WRITE_CMD_RE = re.compile(r"(?is)\b(?P<cmd>Set-Content|Out-File|New-Item)\b(?P<tail>[^\r\n;&|]*)")
_PS_PATH_ARG_RE = re.compile(
    rf"(?is)-(?:LiteralPath|Path|FilePath)\s+(?P<path>{_SHELL_PATH_TOKEN_RE})"
)
_PS_VALUE_ARG_RE = re.compile(
    r"(?is)-Value\s+(?P<value>\"(?:`.|[^\"])*\"|'(?:''|[^'])*'|[^\s;&|]+)"
)


def _tool_name_looks_like_shell(name: object) -> bool:
    lowered = str(name or "").lower()
    return any(token in lowered for token in _SHELL_TOOL_NAME_TOKENS)


_SHELL_WRAPPER_BINARIES = {
    "bash", "sh", "zsh", "pwsh", "powershell", "powershell.exe", "pwsh.exe", "cmd", "cmd.exe",
}


def _shell_argv_command_text(argv: object) -> str:
    parts = [str(part or "") for part in argv] if isinstance(argv, (list, tuple)) else []
    if not parts:
        return ""
    if len(parts) >= 3 and parts[0].lower() in _SHELL_WRAPPER_BINARIES:
        return parts[-1]
    return " ".join(part for part in parts if part)


def _shell_command_text(arguments: object) -> str:
    value: object = arguments
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            value = parsed
        except Exception:
            return value
    if isinstance(value, dict):
        for key in ("command", "cmd", "script", "input"):
            command = value.get(key)
            if isinstance(command, str) and command.strip():
                return command
            if isinstance(command, (list, tuple)) and command:
                text = _shell_argv_command_text(command)
                if text.strip():
                    return text
        return json.dumps(value, ensure_ascii=False)
    return str(value or "")


def _clean_shell_path(path: object) -> str:
    text = str(path or "").strip().strip(",")
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    return text.replace("\\`", "`").strip()


def _shell_write_operation(path: str, content: str) -> Optional[dict[str, Any]]:
    clean_path = _clean_shell_path(path)
    if not clean_path or clean_path.lower() in {"/dev/null", "nul"}:
        return None
    operation_type = "replace_file" if os.path.exists(clean_path) else "add_file"
    return {"type": operation_type, "path": clean_path, "content": content}


def _shell_path_from_heredoc_head(head: str, after: str) -> str:
    combined = f"{head or ''} {after or ''}"
    redirect = _SHELL_REDIRECT_PATH_RE.search(combined)
    if redirect:
        return _clean_shell_path(redirect.group("path"))
    tee = _SHELL_TEE_PATH_RE.search(combined)
    if tee:
        return _clean_shell_path(tee.group("path"))
    return ""


def _shell_simple_write_content(command_name: str, body: str) -> str:
    try:
        tokens = shlex.split(str(body or ""), posix=True)
    except Exception:
        tokens = []
    if not tokens:
        return ""

    command = str(command_name or "").lower()
    if command == "echo":
        while tokens and tokens[0].startswith("-"):
            tokens.pop(0)
        return " ".join(tokens)

    if command == "printf":
        if len(tokens) >= 2 and "%" in tokens[0]:
            suffix = "\n" if "\\n" in tokens[0] else ""
            return str(tokens[1]) + suffix
        return str(tokens[0]).encode("utf-8").decode("unicode_escape")
    return ""


def _powershell_tokens(text: str) -> list[str]:
    return re.findall(_SHELL_PATH_TOKEN_RE, str(text or ""))


_PS_VALUE_FLAGS = {
    "-encoding", "-delimiter", "-stream", "-credential", "-filter", "-include",
    "-exclude", "-itemtype", "-name", "-value", "-width", "-path", "-literalpath", "-filepath",
}


def _powershell_path_from_tail(tail: str) -> str:
    flagged = _PS_PATH_ARG_RE.search(tail or "")
    if flagged:
        return _clean_shell_path(flagged.group("path"))
    skip_value = False
    for token in _powershell_tokens(tail):
        clean = _clean_shell_path(token)
        if not clean:
            continue
        if skip_value:
            skip_value = False
            continue
        if clean.startswith("-"):
            # 取值型参数（如 -Encoding UTF8）的值不是文件路径，必须跳过
            skip_value = clean.lower() in _PS_VALUE_FLAGS
            continue
        if clean.startswith("$"):
            continue
        return clean
    return ""


def _powershell_unquote(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('`"', '"').replace("`n", "\n").replace("`r", "\r").replace("``", "`")
    return text


def _powershell_literal_value_from_tail(tail: str) -> str:
    value = _PS_VALUE_ARG_RE.search(tail or "")
    if not value:
        return ""
    raw = str(value.group("value") or "")
    if raw.startswith("$"):
        return ""
    return _powershell_unquote(raw)


def _shell_file_write_operations(arguments: object) -> list[dict[str, Any]]:
    command = _shell_command_text(arguments)
    if not command.strip():
        return []

    operations: list[dict[str, Any]] = []
    for match in _SHELL_HEREDOC_RE.finditer(command):
        path = _shell_path_from_heredoc_head(match.group("head"), match.group("after"))
        operation = _shell_write_operation(path, match.group("content"))
        if operation:
            operations.append(operation)

    for match in _SHELL_SIMPLE_WRITE_RE.finditer(command):
        content = _shell_simple_write_content(match.group("cmd"), match.group("body"))
        operation = _shell_write_operation(match.group("path"), content)
        if operation:
            operations.append(operation)

    variable_content: dict[str, str] = {}
    for match in _PS_HERE_STRING_ASSIGN_RE.finditer(command):
        variable_content[str(match.group("var") or "").lower()] = str(match.group("content") or "")

    for match in _PS_HERE_STRING_PIPE_RE.finditer(command):
        path = _powershell_path_from_tail(match.group("tail"))
        operation = _shell_write_operation(path, str(match.group("content") or ""))
        if operation:
            operations.append(operation)

    for match in _PS_WRITE_CMD_RE.finditer(command):
        tail = str(match.group("tail") or "")
        path = _powershell_path_from_tail(tail)
        content = ""
        lowered_tail = tail.lower()
        for variable, variable_value in variable_content.items():
            if f"${variable}" in lowered_tail:
                content = variable_value
                break
        if not content:
            content = _powershell_literal_value_from_tail(tail)
        if not content:
            continue
        operation = _shell_write_operation(path, content)
        if operation:
            operations.append(operation)

    return operations


def _shell_file_write_patch_input(arguments: object) -> str:
    operations = _shell_file_write_operations(arguments)
    if not operations:
        return ""
    return _build_apply_patch_text(operations)


def _shell_write_as_apply_patch_tool_call(tool_call: dict[str, Any]) -> Optional[dict[str, Any]]:
    fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    name = str(fn.get("name") or tool_call.get("name") or "")
    if not _tool_name_looks_like_shell(name):
        return None
    patch_input = _shell_file_write_patch_input(fn.get("arguments") or tool_call.get("arguments"))
    if not patch_input:
        return None
    converted = dict(tool_call)
    converted["type"] = "function"
    converted["function"] = {
        "name": "apply_patch",
        "arguments": json.dumps({"input": patch_input}, ensure_ascii=False),
    }
    return converted


def _rewrite_shell_file_writes_to_apply_patch(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    choice = _first_chat_choice(payload)
    message = _first_chat_message(payload)
    if not choice or not isinstance(message, dict):
        return None

    changed = False
    rewritten_calls: list[dict[str, Any]] = []
    for call in message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []:
        if not isinstance(call, dict):
            continue
        converted = _shell_write_as_apply_patch_tool_call(call)
        if converted is not None:
            rewritten_calls.append(converted)
            changed = True
        else:
            rewritten_calls.append(call)

    legacy_call = message.get("function_call")
    if isinstance(legacy_call, dict):
        converted = _shell_write_as_apply_patch_tool_call({"id": legacy_call.get("id") or legacy_call.get("call_id"), "function": legacy_call})
        if converted is not None:
            rewritten_calls.append(converted)
            changed = True

    if not changed:
        return None

    new_payload = dict(payload)
    choices = list(new_payload.get("choices") or [])
    if not choices:
        return None
    new_choice = dict(choice)
    new_message = dict(message)
    new_message["tool_calls"] = rewritten_calls
    new_message["content"] = _chat_content_text(new_message.get("content"))
    new_message.pop("function_call", None)
    new_choice["message"] = new_message
    new_choice["finish_reason"] = "tool_calls"
    choices[0] = new_choice
    new_payload["choices"] = choices
    return new_payload


def _looks_like_shell_file_write(arguments: object) -> bool:
    command = _shell_command_text(arguments)
    if command.lstrip().lower().startswith(("apply_patch", "applypatch")):
        # 模型已经在用 shell 形式的 apply_patch，codex 会拦截并渲染编辑卡片
        return False
    text = str(arguments or "")
    lowered = text.lower()
    if any(token in lowered for token in ("set-content", "out-file", "add-content", "<<")):
        return True
    if "new-item" in lowered and "-value" in lowered:
        return True
    if "/dev/null" in lowered or " nul" in lowered:
        return False
    return len(text) > 120 and bool(_SHELL_REDIRECT_WRITE_RE.search(lowered))


def _tool_calls_violate_file_write_policy(tool_calls: object) -> bool:
    for call in tool_calls if isinstance(tool_calls, list) else []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = str(fn.get("name") or call.get("name") or "").lower()
        if not any(token in name for token in _SHELL_TOOL_NAME_TOKENS):
            continue
        if _looks_like_shell_file_write(fn.get("arguments") or call.get("arguments")):
            return True
    return False


_FILE_WRITE_VIOLATION_CORRECTION = (
    "[FILE-EDIT POLICY VIOLATION] Your previous response tried to create or modify a file through a shell "
    "command. That is forbidden and the call was rejected, the file was NOT written. Re-issue the SAME file "
    "change now by calling apply_patch_add_file, apply_patch_replace_file, apply_patch_update_file or "
    "apply_patch_delete_file with explicit path/content arguments. Do not use shell commands to write files."
)


def _enforce_apply_patch_file_writes(
    payload: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    model_name: str,
    source_lang: Optional[str],
    target_lang: Optional[str],
    force_translate: bool,
    options: dict[str, Any],
    declared_tools: object,
    cancel_event: Optional[threading.Event] = None,
) -> dict[str, Any]:
    """拦截借 shell 写文件的工具调用，必要时重写为 apply_patch。

    codex 仅在 apply_patch 调用上渲染可审阅的文件编辑卡片；部分 chat 上游
    模型无视 system 策略仍用 shell 重定向写文件，这里做最后一道确定性防线。
    先给模型一次自我纠正机会，仍违规则把可解析的 shell 写文件命令改写成
    apply_patch 调用，避免普通 shell 调用绕过编辑卡片。
    """
    if not _has_apply_patch_proxy_tools(declared_tools):
        return payload
    message = _first_chat_message(payload)
    if not isinstance(message, dict) or not _tool_calls_violate_file_write_policy(message.get("tool_calls")):
        return payload
    logger.info("local API shell file-write detected; retrying with apply_patch enforcement")
    try:
        retry_payload = chat_completion_response(
            list(messages or []) + [{"role": "user", "content": _FILE_WRITE_VIOLATION_CORRECTION}],
            model_name=model_name,
            source_lang=source_lang,
            target_lang=target_lang,
            force_translate=force_translate,
            request_options=options,
            cancel_event=cancel_event,
        )
        retry_payload = _finalize_chat_tool_calls(retry_payload, declared_tools, options)
    except Exception as exc:
        logger.error("local API apply_patch enforcement retry failed: %s", repr(exc))
        converted_payload = _rewrite_shell_file_writes_to_apply_patch(payload)
        return converted_payload or payload
    retry_message = _first_chat_message(retry_payload)
    if (
        isinstance(retry_message, dict)
        and _chat_payload_has_tool_calls(retry_payload)
        and not _tool_calls_violate_file_write_policy(retry_message.get("tool_calls"))
    ):
        return retry_payload
    converted_retry = _rewrite_shell_file_writes_to_apply_patch(retry_payload)
    if converted_retry is not None:
        logger.info("local API shell file-write converted to apply_patch after retry")
        return converted_retry
    converted_payload = _rewrite_shell_file_writes_to_apply_patch(payload)
    if converted_payload is not None:
        logger.info("local API shell file-write converted to apply_patch from original response")
        return converted_payload
    return payload


def _stream_error_text(exc: Exception) -> str:
    return f"Local API upstream stream error: {str(exc) or repr(exc)}"


def _chat_content_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "input_text", "output_text", "content"):
            if key in value:
                text = _chat_content_text(value.get(key))
                if text:
                    return text
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_chat_content_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    return str(value)


def _split_leading_think_block(text: str) -> Optional[tuple[str, str]]:
    text = str(text or "")
    leading_ws_len = len(text) - len(text.lstrip())
    after_ws = text[leading_ws_len:]
    if not after_ws.startswith("<think>"):
        return None
    body_start = leading_ws_len + len("<think>")
    close_start = text.find("</think>", body_start)
    if close_start < 0:
        return None
    answer_start = close_start + len("</think>")
    return text[body_start:close_start].strip(), text[answer_start:].lstrip()


def _reasoning_output_item(text: str) -> dict[str, Any]:
    return {
        "type": "reasoning",
        "id": f"rs_{uuid.uuid4().hex[:12]}",
        "summary": [{"type": "summary_text", "text": str(text or "")}],
    }


def _message_output_item(text: str) -> dict[str, Any]:
    return {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": str(text or ""), "annotations": []}],
    }


def _codex_has_apply_patch_custom_tool(tool_context: Optional[dict[str, Any]]) -> bool:
    if not isinstance(tool_context, dict):
        return False
    custom_tools = tool_context.get("custom_tools")
    if not isinstance(custom_tools, dict):
        return False
    for name, spec in custom_tools.items():
        if str(name or "") == "apply_patch":
            return True
        if isinstance(spec, dict) and str(spec.get("openai_name") or "") == "apply_patch":
            return True
    return False


def _codex_apply_patch_emit_mode(tool_context: Optional[dict[str, Any]]) -> Optional[str]:
    """决定 apply_patch 的回程形态。

    codex 只为部分模型族注册 apply_patch 工具 handler；未注册时回传
    custom_tool_call(apply_patch) 会被当作 unsupported tool 拒绝。但 codex 的
    shell handler 对 `apply_patch <<'EOF'` 形式的命令做无条件拦截并渲染编辑
    卡片，所以未声明 apply_patch 时改走 shell 工具形态。
    """
    if not isinstance(tool_context, dict):
        return None
    if isinstance(tool_context.get("apply_patch_shell_tool"), dict):
        return "shell"
    if _codex_has_apply_patch_custom_tool(tool_context):
        return "custom"
    return None


def _codex_custom_tool_kind(tool_context: Optional[dict[str, Any]], name: str) -> str:
    if not isinstance(tool_context, dict):
        return ""
    custom_tools = tool_context.get("custom_tools")
    spec = custom_tools.get(str(name or "")) if isinstance(custom_tools, dict) else None
    if isinstance(spec, dict):
        return str(spec.get("kind") or "")
    return ""


def _shell_function_tool_info(tools: object) -> Optional[dict[str, Any]]:
    if not isinstance(tools, list):
        return None
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type") or "")
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        if tool_type not in {"function", ""} and not isinstance(tool.get("function"), dict):
            continue
        name = str(fn.get("name") or "").strip()
        if not name or not _tool_name_looks_like_shell(name):
            continue
        params = fn.get("parameters")
        if not isinstance(params, dict):
            params = fn.get("input_schema") if isinstance(fn.get("input_schema"), dict) else {}
        properties = params.get("properties") if isinstance(params.get("properties"), dict) else {}
        command_schema = properties.get("command") if isinstance(properties.get("command"), dict) else {}
        return {"name": name, "command_is_array": str(command_schema.get("type") or "") == "array"}
    return None


_APPLY_PATCH_HEREDOC_TAG = "CODEX_APPLY_PATCH_EOF"


def _shell_apply_patch_command(patch_text: str) -> str:
    body = str(patch_text or "")
    tag = _APPLY_PATCH_HEREDOC_TAG
    lines = set(body.splitlines())
    while tag in lines:
        tag = f"{_APPLY_PATCH_HEREDOC_TAG}_{uuid.uuid4().hex[:6]}"
    return f"apply_patch <<'{tag}'\n{body}\n{tag}"


def _codex_shell_apply_patch_tool_name(tool_context: Optional[dict[str, Any]]) -> str:
    spec = tool_context.get("apply_patch_shell_tool") if isinstance(tool_context, dict) else None
    if isinstance(spec, dict) and spec.get("name"):
        return str(spec.get("name"))
    return "shell"


def _shell_apply_patch_arguments(
    tool_context: Optional[dict[str, Any]],
    patch_text: str,
    original_arguments: object = None,
) -> str:
    spec = tool_context.get("apply_patch_shell_tool") if isinstance(tool_context, dict) else None
    spec = spec if isinstance(spec, dict) else {}
    original = original_arguments
    if isinstance(original, str):
        try:
            original = json.loads(original)
        except Exception:
            original = None
    rebuilt: dict[str, Any] = {}
    command_was_array = False
    if isinstance(original, dict):
        command_was_array = isinstance(original.get("command"), (list, tuple))
        for key in ("workdir", "cwd", "timeout_ms", "timeout"):
            if key in original:
                rebuilt[key] = original[key]
    if bool(spec.get("command_is_array")) or command_was_array:
        rebuilt["command"] = ["apply_patch", str(patch_text or "")]
    else:
        rebuilt["command"] = _shell_apply_patch_command(patch_text)
    return json.dumps(rebuilt, ensure_ascii=False)


def _shell_apply_patch_function_item(
    tool_context: Optional[dict[str, Any]],
    tc: dict[str, Any],
    call_id: str,
    patch_text: str,
    original_arguments: object = None,
) -> dict[str, Any]:
    name = _codex_shell_apply_patch_tool_name(tool_context)
    display_name, namespace = _codex_function_tool_name(tool_context, name)
    item = {
        "type": "function_call",
        "id": str(tc.get("item_id") or f"fc_{call_id}"),
        "call_id": call_id,
        "name": display_name,
        "arguments": _shell_apply_patch_arguments(tool_context, patch_text, original_arguments),
        "status": "completed",
    }
    if namespace:
        item["namespace"] = namespace
    return item


def _function_call_output_item(tool_call: object) -> dict[str, Any]:
    tc = tool_call if isinstance(tool_call, dict) else {}
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    call_id = str(tc.get("id") or tc.get("call_id") or f"call_{uuid.uuid4().hex[:8]}")
    name = str(fn.get("name") or tc.get("name") or "")
    arguments = _json_string(fn.get("arguments", tc.get("arguments", "{}")))
    tool_context = tc.get("_tool_context") if isinstance(tc.get("_tool_context"), dict) else None
    emit_mode = _codex_apply_patch_emit_mode(tool_context)
    if emit_mode and _tool_name_looks_like_shell(name):
        patch_input = _shell_file_write_patch_input(arguments)
        if patch_input:
            if emit_mode == "shell":
                return _shell_apply_patch_function_item(tool_context, tc, call_id, patch_input, arguments)
            return {
                "type": "custom_tool_call",
                "id": str(tc.get("item_id") or f"ctc_{call_id}"),
                "call_id": call_id,
                "name": "apply_patch",
                "input": patch_input,
                "status": "completed",
            }
    if _codex_is_custom_tool_proxy(tool_context, name):
        if emit_mode == "shell" and _codex_custom_tool_kind(tool_context, name) == "apply_patch":
            patch_input = _reconstruct_custom_tool_call_input(tool_context, name, arguments)
            return _shell_apply_patch_function_item(tool_context, tc, call_id, patch_input)
        return {
            "type": "custom_tool_call",
            "id": str(tc.get("item_id") or f"ctc_{call_id}"),
            "call_id": call_id,
            "name": _codex_original_custom_tool_name(tool_context, name),
            "input": _reconstruct_custom_tool_call_input(tool_context, name, arguments),
            "status": "completed",
        }
    display_name, namespace = _codex_function_tool_name(tool_context, name)
    item = {
        "type": "function_call",
        "id": str(tc.get("item_id") or f"fc_{call_id}"),
        "call_id": call_id,
        "name": display_name,
        "arguments": arguments,
        "status": "completed",
    }
    if namespace:
        item["namespace"] = namespace
    return item


_APPLY_PATCH_ACTION_SUFFIXES = {
    "add_file": "add_file",
    "delete_file": "delete_file",
    "update_file": "update_file",
    "replace_file": "replace_file",
    "batch": "batch",
}


def _codex_tool_context(tools: object) -> dict[str, Any]:
    context: dict[str, Any] = {"custom_tools": {}, "function_tools": {}}
    custom_tools = context["custom_tools"]
    function_tools = context["function_tools"]
    if not isinstance(tools, list):
        return context

    def add_custom(upstream_name: str, openai_name: Optional[str] = None, *, kind: str = "raw", action: Optional[str] = None) -> None:
        if not upstream_name:
            return
        custom_tools[str(upstream_name)] = {
            "openai_name": str(openai_name or upstream_name),
            "kind": kind,
            "action": action,
        }

    def add_function(upstream_name: str, name: Optional[str] = None, namespace: str = "") -> None:
        if not upstream_name:
            return
        function_tools[str(upstream_name)] = {
            "name": str(name or upstream_name),
            "namespace": str(namespace or ""),
        }

    def add_apply_patch_proxies(name: str) -> None:
        add_custom(name, name, kind="apply_patch")
        for suffix, action in _APPLY_PATCH_ACTION_SUFFIXES.items():
            add_custom(f"{name}_{suffix}", name, kind="apply_patch", action=action)

    for tool in tools:
        if isinstance(tool, str):
            if tool == "apply_patch":
                add_apply_patch_proxies(tool)
            elif tool.startswith("apply_patch_"):
                suffix = tool.removeprefix("apply_patch_")
                add_custom(tool, "apply_patch", kind="apply_patch", action=_APPLY_PATCH_ACTION_SUFFIXES.get(suffix))
            else:
                add_custom(tool)
            continue
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type") or "")
        name = str(tool.get("name") or "").strip()
        if tool_type in {"custom", "freeform"} and name:
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
                add_function(_flatten_namespace_tool_name(namespace, child_name), child_name, namespace)
        elif tool_type in {"web_search", "local_shell", "computer_use"}:
            add_custom(name or tool_type, name or tool_type, kind="built_in")

    # codex 对未知模型族不声明 apply_patch 工具；此时仍要把 shell 写文件
    # 转换成 codex shell handler 能无条件拦截的 apply_patch 调用，否则
    # 永远不会出现"已编辑文件"卡片。合成代理映射并记录目标 shell 工具。
    if not _codex_has_apply_patch_custom_tool(context):
        shell_tool = _shell_function_tool_info(tools)
        if shell_tool:
            context["apply_patch_shell_tool"] = shell_tool
            add_custom("apply_patch", "apply_patch", kind="apply_patch")
            for suffix, action in _APPLY_PATCH_ACTION_SUFFIXES.items():
                add_custom(f"apply_patch_{suffix}", "apply_patch", kind="apply_patch", action=action)
    return context


def _codex_is_custom_tool_proxy(tool_context: Optional[dict[str, Any]], name: str) -> bool:
    if not isinstance(tool_context, dict):
        return False
    custom_tools = tool_context.get("custom_tools")
    return isinstance(custom_tools, dict) and str(name or "") in custom_tools


def _codex_original_custom_tool_name(tool_context: Optional[dict[str, Any]], name: str) -> str:
    if not isinstance(tool_context, dict):
        return str(name or "")
    custom_tools = tool_context.get("custom_tools")
    spec = custom_tools.get(str(name or "")) if isinstance(custom_tools, dict) else None
    if isinstance(spec, dict):
        return str(spec.get("openai_name") or name or "")
    return str(name or "")


def _codex_function_tool_name(tool_context: Optional[dict[str, Any]], name: str) -> tuple[str, str]:
    if not isinstance(tool_context, dict):
        return str(name or ""), ""
    function_tools = tool_context.get("function_tools")
    spec = function_tools.get(str(name or "")) if isinstance(function_tools, dict) else None
    if isinstance(spec, dict):
        return str(spec.get("name") or name or ""), str(spec.get("namespace") or "")
    return str(name or ""), ""


def _reconstruct_custom_tool_call_input(tool_context: Optional[dict[str, Any]], name: str, arguments: str) -> str:
    spec = None
    if isinstance(tool_context, dict) and isinstance(tool_context.get("custom_tools"), dict):
        spec = tool_context["custom_tools"].get(str(name or ""))
    if isinstance(spec, dict) and spec.get("kind") == "apply_patch":
        return _reconstruct_apply_patch_input(str(spec.get("action") or ""), arguments)
    try:
        value = json.loads(str(arguments or "{}"))
    except Exception:
        return str(arguments or "")
    if isinstance(value, dict) and "input" in value:
        return _response_content_text(value.get("input")) or str(value.get("input") or "")
    return str(arguments or "")


def _reconstruct_apply_patch_input(action: str, arguments: str) -> str:
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
        operations = [
            {
                "type": "update_file",
                "path": str(value.get("path") or ""),
                "move_to": str(value.get("move_to") or ""),
                "hunks": value.get("hunks") if isinstance(value.get("hunks"), list) else [],
            }
        ]
    elif action == "replace_file":
        operations = [{"type": "replace_file", "path": str(value.get("path") or ""), "content": str(value.get("content") or "")}]
    else:
        operations = value.get("operations") if isinstance(value.get("operations"), list) else []
    return _build_apply_patch_text(operations)


def _build_apply_patch_text(operations: list[object]) -> str:
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


def _response_output_text(output: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in output or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                parts.append(str(content.get("text") or ""))
    return "".join(parts)


def _response_output_summary(output: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for item in output or []:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        entry: dict[str, Any] = {"type": item_type}
        if item_type in {"function_call", "custom_tool_call"}:
            entry["name"] = str(item.get("name") or "")
            if item.get("namespace"):
                entry["namespace"] = str(item.get("namespace") or "")
            if item_type == "custom_tool_call":
                entry["input_chars"] = len(str(item.get("input") or ""))
            else:
                entry["argument_chars"] = len(str(item.get("arguments") or ""))
        elif item_type == "message":
            entry["text_chars"] = len(_response_output_text([item]))
        elif item_type == "reasoning":
            entry["summary_chars"] = len(_response_reasoning_text([item]))
        summary.append(entry)
    return summary


def _response_reasoning_text(output: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in output or []:
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        for summary in item.get("summary", []) or []:
            if isinstance(summary, dict):
                parts.append(str(summary.get("text") or ""))
            elif isinstance(summary, str):
                parts.append(summary)
    return "".join(parts)


def _chat_usage_to_response_usage(usage: object) -> dict[str, Any]:
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
    result: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    cached = None
    prompt_details = usage.get("prompt_tokens_details")
    if isinstance(prompt_details, dict):
        cached = prompt_details.get("cached_tokens")
    input_details = usage.get("input_tokens_details")
    if cached is None and isinstance(input_details, dict):
        cached = input_details.get("cached_tokens")
    if cached is not None:
        result["input_tokens_details"] = {"cached_tokens": cached}
    completion_details = usage.get("completion_tokens_details")
    if isinstance(completion_details, dict):
        details = dict(completion_details)
        details.setdefault("reasoning_tokens", 0)
        result["output_tokens_details"] = details
    return result


def _chat_message_reasoning_text(message: object) -> str:
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
        parts: list[str] = []
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


def _response_id_from_chat_id(chat_id: object) -> str:
    text = str(chat_id or f"chatcmpl_{uuid.uuid4().hex[:12]}")
    return text if text.startswith("resp") else f"resp_{text}"


def _chat_completion_to_response_payload(
    body: dict[str, Any],
    *,
    response_id: Optional[str] = None,
    created: Optional[int] = None,
    original_request: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    choices = body.get("choices") if isinstance(body, dict) else None
    if not choices:
        raise ValueError("No choices in chat response")
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    finish_reason = choice.get("finish_reason")
    model = str(body.get("model") or SERVICE_MODEL_ID)
    created_ts = int(created if created is not None else body.get("created") or time.time())
    output: list[dict[str, Any]] = []
    tool_context = _codex_tool_context(original_request.get("tools") if isinstance(original_request, dict) else None)

    reasoning = _chat_message_reasoning_text(message)
    content = _chat_content_text(message.get("content"))
    think = _split_leading_think_block(content)
    if think and not reasoning:
        reasoning, content = think
    if reasoning:
        output.append(_reasoning_output_item(reasoning))
    if content:
        output.append(_message_output_item(content))
    for tool_call in message.get("tool_calls") or []:
        if isinstance(tool_call, dict):
            tool_call = dict(tool_call)
            tool_call["_tool_context"] = tool_context
        output.append(_function_call_output_item(tool_call))
    if isinstance(message.get("function_call"), dict):
        for tool_call in _coerce_tool_calls(message.get("function_call")):
            tool_call["_tool_context"] = tool_context
            output.append(_function_call_output_item(tool_call))
    if not output:
        output.append(_message_output_item(""))

    status = "incomplete" if finish_reason == "length" else "completed"
    payload = _openai_response_payload(
        model,
        "",
        created=created_ts,
        response_id=response_id or _response_id_from_chat_id(body.get("id")),
        status=status,
        output=output,
        usage=_chat_usage_to_response_usage(body.get("usage")),
    )
    if status == "incomplete":
        payload["incomplete_details"] = {"reason": "max_output_tokens"}
    _copy_response_request_fields(payload, original_request)
    return payload


def _openai_response_payload(
    model: str,
    content: str,
    *,
    created: Optional[int] = None,
    response_id: Optional[str] = None,
    status: str = "completed",
    include_output: bool = True,
    output: Optional[list[dict[str, Any]]] = None,
    usage: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    created_ts = int(created if created is not None else time.time())
    response_id = response_id or f"resp-deepcat-{created_ts}"
    message_id = f"msg-deepcat-{created_ts}"
    text = str(content or "")
    if output is None:
        output = [
            {
                "id": message_id,
                "type": "message",
                "status": str(status or "completed"),
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
        ] if include_output else []
    else:
        text = _response_output_text(output)
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_ts,
        "status": str(status or "completed"),
        "model": str(model or SERVICE_MODEL_ID),
        "output": output,
        "output_text": text,
        "usage": usage or {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
    }


def _copy_response_request_fields(response: dict[str, Any], original_request: Optional[dict[str, Any]]) -> None:
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


def _service_info_payload(handler: TranslationRequestHandler) -> dict[str, Any]:
    # IPv6 监听时 server_address 是四元组，这里只取主机与端口
    host, port = handler.server.server_address[:2]
    display_host = f"[{host}]" if ":" in str(host) else str(host)
    base_url = f"http://{display_host}:{port}"
    return {
        "ok": True,
        "service": "deepcat-translate",
        "base_url": base_url,
        "endpoints": {
            "models": f"{base_url}/v1/models",
            "chat_completions": f"{base_url}/v1/chat/completions",
            "responses": f"{base_url}/v1/responses",
            "translate": f"{base_url}/translate",
            "health": f"{base_url}/health",
        },
        "model": SERVICE_MODEL_ID,
    }


def _client_host_port(client_address: object) -> tuple[str, str]:
    if isinstance(client_address, tuple) and len(client_address) >= 2:
        return str(client_address[0] or ""), str(client_address[1] or "")
    return "", ""


def _normalized_path(path: object) -> str:
    text = str(path or "").strip() or "/"
    if not text.startswith("/"):
        text = f"/{text}"
    return text.rstrip("/") or "/"


def _is_chat_completion_path(path: str) -> bool:
    normalized = _normalized_path(path)
    return normalized in {"/v1/chat/completions", "/chat/completions"} or normalized.endswith(
        ("/v1/chat/completions", "/chat/completions")
    )


def _is_responses_path(path: str) -> bool:
    normalized = _normalized_path(path)
    return normalized in {"/v1/responses", "/responses"} or normalized.endswith(("/v1/responses", "/responses"))


def _is_translate_responses_path(path: str) -> bool:
    normalized = _normalized_path(path)
    return normalized == "/translate/responses" or normalized.endswith("/translate/responses")


def _is_codex_responses_client(headers: Any, payload: dict[str, Any]) -> bool:
    try:
        user_agent = str(headers.get("User-Agent") or headers.get("user-agent") or "")
    except Exception:
        user_agent = ""
    if "codex" in user_agent.lower():
        return True

    if not isinstance(payload, dict):
        return False
    keys = {str(key) for key in payload.keys()}
    codex_key_sets = (
        {"instructions", "tools", "parallel_tool_calls"},
        {"client_metadata", "prompt_cache_key"},
        {"reasoning", "store", "include"},
    )
    if any(required.issubset(keys) for required in codex_key_sets):
        return True

    tools = payload.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tool_type = str(tool.get("type") or "").lower()
            tool_name = str(tool.get("name") or "").lower()
            if tool_type in {"namespace", "custom"}:
                return True
            if tool_name in {"apply_patch", "shell_command"}:
                return True
    return False


def _is_models_path(path: str) -> bool:
    normalized = _normalized_path(path)
    return normalized in {"/v1/models", "/models"} or normalized.endswith(("/v1/models", "/models"))


def _is_translate_path(path: str) -> bool:
    normalized = _normalized_path(path)
    return normalized == "/translate" or normalized.endswith("/translate") or normalized.startswith("/translate/")


def _payload_from_query(query: str) -> dict[str, Any]:
    parsed = parse_qs(str(query or ""), keep_blank_values=True)
    payload: dict[str, Any] = {}
    for key, values in parsed.items():
        payload[str(key)] = values[0] if len(values) == 1 else values
    return payload


def _payload_from_value(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"text": _text_from_any(data)}
    if isinstance(data, str):
        return {"text": data}
    if data is None:
        return {}
    return {"text": str(data)}


def _preview(value: object, limit: int = 240) -> str:
    text = str(value or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= int(limit):
        return text
    return text[: max(0, int(limit) - 3)] + "..."


def _optional_str(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_optional_str(payload: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        if key not in payload:
            continue
        text = _optional_str(payload.get(key))
        if text:
            return text
    return None


def _language_option(payload: dict[str, Any], keys: Iterable[str]) -> Optional[str]:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, (dict, list, tuple)):
            continue
        text = _optional_str(value)
        if text:
            return text
    return None


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on", "stream"}


def _text_from_any(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    if isinstance(value, dict):
        text, _ = _extract_translate_text(value)
        return text.strip()
    if isinstance(value, (list, tuple)):
        parts: list[str] = []
        for item in value:
            text = _text_from_any(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return str(value).strip()


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        for key in ("text", "input_text", "output_text", "content"):
            text = _text_from_any(content.get(key))
            if text:
                return text
        return ""
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for item in content:
            text = _content_to_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return _text_from_any(content)


def _response_role(role: object) -> str:
    normalized = str(role or "").strip().lower()
    if normalized in {"system", "developer"}:
        return "system"
    if normalized == "assistant":
        return "assistant"
    if normalized in {"tool", "function"}:
        return "tool"
    return "user"


def _response_reasoning_summary(item: dict[str, Any]) -> str:
    summary = item.get("summary")
    if summary:
        return _response_content_text(summary)
    for key in ("text", "content"):
        text = _response_content_text(item.get(key))
        if text:
            return text
    return ""


def _response_content_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = [_response_content_text(item).strip() for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        content_type = str(value.get("type") or "")
        if content_type in {"function_call", "custom_tool_call"}:
            return ""
        if content_type == "function_call_output":
            return _response_content_text(value.get("output"))
        if content_type == "reasoning":
            return _response_reasoning_summary(value)
        for key in ("text", "input_text", "output_text", "summary_text", "refusal", "output", "content"):
            if key in value:
                text = _response_content_text(value.get(key))
                if text:
                    return text
        image_url = value.get("image_url") or value.get("url")
        if image_url:
            if isinstance(image_url, dict):
                image_url = image_url.get("url", "")
            return f"[Image attachment: {image_url}]"
        file_name = value.get("filename") or value.get("name") or value.get("file_id") or value.get("file_url")
        if file_name:
            return f"[File attachment: {file_name}]"
    return ""


def _response_message_content(content: object) -> object:
    if isinstance(content, dict):
        content = [content]
    if isinstance(content, list):
        new_content = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "input_image":
                img_url = part.get("image_url") or part.get("url")
                if isinstance(img_url, dict):
                    img_url = img_url.get("url", "")
                if img_url:
                    new_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": img_url
                        }
                    })
            elif isinstance(part, dict) and part.get("type") == "input_file":
                file_name = part.get("filename") or part.get("name") or "file"
                new_content.append({
                    "type": "text",
                    "text": f"[File attachment: {file_name}]"
                })
            else:
                new_content.append(part)
        return new_content
    return content


def _response_tool_call_entry(item: dict[str, Any]) -> dict[str, Any]:
    call_id = str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}")
    name = str(item.get("name") or item.get("tool_name") or "")
    namespace = str(item.get("namespace") or "")
    upstream_name = _flatten_namespace_tool_name(namespace, name) if namespace else name
    if str(item.get("type") or "") == "custom_tool_call":
        arguments = _json_string({"input": item.get("input", "")})
    else:
        arguments = _json_string(item.get("arguments", "{}"))
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": upstream_name,
            "arguments": arguments,
        },
    }


def _response_tool_call_message(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [_response_tool_call_entry(item)],
    }


def _response_tool_calls_from_content(content: object) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, (list, tuple)):
        return calls
    for index, part in enumerate(content):
        if not isinstance(part, dict) or part.get("type") not in {"function_call", "custom_tool_call"}:
            continue
        call_id = str(part.get("call_id") or part.get("id") or f"call_{index}")
        name = str(part.get("name") or part.get("tool_name") or "")
        namespace = str(part.get("namespace") or "")
        upstream_name = _flatten_namespace_tool_name(namespace, name) if namespace else name
        calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": upstream_name,
                    "arguments": _json_string({"input": part.get("input", "")})
                    if part.get("type") == "custom_tool_call"
                    else _json_string(part.get("arguments", "{}")),
                },
            }
        )
    return calls


_APPLY_PATCH_HUNK_LINES_SCHEMA: dict[str, Any] = {
    "type": "array",
    "description": "Lines of this hunk in order. Include 2-3 unchanged context lines around the change.",
    "items": {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": ["context", "add", "remove"],
                "description": "context = unchanged line kept as-is, add = new line, remove = deleted line.",
            },
            "text": {"type": "string", "description": "The line text WITHOUT any +/-/space diff prefix."},
        },
        "required": ["op", "text"],
    },
}


def _apply_patch_proxy_function_tools(base_name: str) -> list[dict[str, Any]]:
    """为 codex 的 apply_patch 自定义工具生成结构化代理 function 工具。

    上游 chat 模型大多写不好 apply_patch 的 freeform patch 语法，会退回用
    shell 命令写文件，导致 codex 无法展示“已编辑文件”卡片。这里把文件编辑
    暴露为结构化参数的代理工具；模型调用后由 _reconstruct_apply_patch_input
    重构回 patch 文本，再以 custom_tool_call(apply_patch) 返还 codex 执行。
    """
    base = str(base_name or "apply_patch")
    common_note = (
        "ALWAYS use this tool (or the other "
        f"{base}_* tools) to create, modify or delete files. NEVER write files via shell commands "
        "(no echo/cat/printf/heredoc redirection): file changes made through this tool are shown to "
        "the user as reviewable file-edit cards, shell writes are not."
    )

    def proxy(suffix: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": f"{base}_{suffix}",
                "description": f"{description} {common_note}",
                "parameters": _normalize_tool_schema(
                    {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    }
                ),
            },
        }

    path_property = {"type": "string", "description": "Relative path of the target file."}
    content_property = {"type": "string", "description": "Full file content (plain text, no diff markers)."}
    return [
        proxy(
            "add_file",
            "Create a new file with the given content.",
            {"path": path_property, "content": content_property},
            ["path", "content"],
        ),
        proxy(
            "replace_file",
            "Overwrite an existing file with entirely new content.",
            {"path": path_property, "content": content_property},
            ["path", "content"],
        ),
        proxy(
            "update_file",
            "Apply a partial edit to an existing file using one or more hunks.",
            {
                "path": path_property,
                "move_to": {"type": "string", "description": "Optional new path if the file should be renamed."},
                "hunks": {
                    "type": "array",
                    "description": "Edit hunks applied top-down; each hunk locates its position by its context lines.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "context": {
                                "type": "string",
                                "description": "Optional @@ header to disambiguate the location (e.g. enclosing function name).",
                            },
                            "lines": _APPLY_PATCH_HUNK_LINES_SCHEMA,
                        },
                        "required": ["lines"],
                    },
                },
            },
            ["path", "hunks"],
        ),
        proxy(
            "delete_file",
            "Delete the file at the given path.",
            {"path": path_property},
            ["path"],
        ),
    ]


def _dedupe_function_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        name = str(fn.get("name") or "")
        if name and name in seen:
            continue
        if name:
            seen.add(name)
        deduped.append(tool)
    return deduped


def _normalize_responses_tools(tools: object, *, inject_shell_apply_patch_proxies: bool = False) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(tools, list):
        return normalized
    has_apply_patch = False
    for tool in tools:
        if isinstance(tool, str):
            normalized.append(
                {
                    "type": "function",
                    "function": {
                        "name": str(tool),
                        "description": "",
                        "parameters": _normalize_tool_schema(
                            {
                                "type": "object",
                                "properties": {
                                    "input": {
                                        "type": "string",
                                        "description": "Raw input text for the custom tool.",
                                    }
                                },
                                "required": ["input"],
                            }
                        ),
                    },
                }
            )
            if str(tool) == "apply_patch":
                has_apply_patch = True
                normalized.extend(_apply_patch_proxy_function_tools(str(tool)))
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
                function: dict[str, Any] = {
                    "name": _flatten_namespace_tool_name(namespace, child_name),
                    "description": _combine_tool_description(namespace_description, child.get("description") or ""),
                    "parameters": _normalize_tool_schema(child_parameters or {}),
                }
                if "strict" in child:
                    function["strict"] = bool(child.get("strict"))
                normalized.append({"type": "function", "function": function})
            continue
        if tool_type == "function":
            fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        elif tool_type in {"custom", "freeform"}:
            fn = dict(tool)
            fn.setdefault(
                "parameters",
                {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": "Raw input text for the custom tool.",
                        }
                    },
                    "required": ["input"],
                },
            )
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
        function: dict[str, Any] = {
            "name": str(name),
            "description": str(fn.get("description") or tool.get("description") or ""),
            "parameters": _normalize_tool_schema(parameters or {}),
        }
        if "strict" in fn or "strict" in tool:
            function["strict"] = bool(fn.get("strict", tool.get("strict")))
        normalized.append({"type": "function", "function": function})
        if tool_type in {"custom", "freeform"} and str(name) == "apply_patch":
            has_apply_patch = True
            normalized.extend(_apply_patch_proxy_function_tools(str(name)))
    # codex 对未知模型族不会声明 apply_patch；只要客户端带 shell 工具仍注入
    # 结构化代理工具，回程由 _codex_tool_context 的 apply_patch_shell_tool
    # 映射改写成 shell apply_patch 调用，编辑卡片才能出现。
    if not has_apply_patch and inject_shell_apply_patch_proxies and _shell_function_tool_info(tools):
        normalized.extend(_apply_patch_proxy_function_tools("apply_patch"))
    return _dedupe_function_tools(normalized)


def _normalize_tool_choice(tool_choice: object) -> object:
    if not isinstance(tool_choice, dict):
        return tool_choice
    if tool_choice.get("type") == "function" and isinstance(tool_choice.get("function"), dict):
        fn = tool_choice.get("function") or {}
        namespace = fn.get("namespace") or tool_choice.get("namespace")
        name = fn.get("name") or tool_choice.get("name")
        if name and namespace:
            return {"type": "function", "function": {"name": _flatten_namespace_tool_name(namespace, name)}}
        return tool_choice
    name = tool_choice.get("name")
    if tool_choice.get("type") == "function" and name:
        namespace = tool_choice.get("namespace")
        return {"type": "function", "function": {"name": _flatten_namespace_tool_name(namespace, name) if namespace else str(name)}}
    if name:
        return {"type": "function", "function": {"name": str(name)}}
    return tool_choice


def _responses_request_options(payload: dict[str, Any]) -> dict[str, Any]:
    request_options = {
        key: payload[key]
        for key in (
            "temperature",
            "max_tokens",
            "max_output_tokens",
            "top_p",
            "top_k",
            "frequency_penalty",
            "presence_penalty",
            "response_format",
            "stop",
            "seed",
            "user",
        )
        if key in payload
    }
    tools = _normalize_responses_tools(payload.get("tools"), inject_shell_apply_patch_proxies=True)
    if tools:
        request_options["tools"] = tools
    if "tool_choice" in payload:
        request_options["tool_choice"] = _normalize_tool_choice(payload.get("tool_choice"))
    if "parallel_tool_calls" in payload:
        request_options["parallel_tool_calls"] = payload["parallel_tool_calls"]
    return request_options


def _responses_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    instructions = _first_optional_str(payload, "instructions", "system", "system_prompt", "systemPrompt")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    input_value = payload.get("input")
    if input_value is None:
        input_value = payload.get("messages", [])

    # 思考模型（如 DeepSeek thinking）要求 assistant 历史携带 reasoning_content
    # 字段回传，且 message+tool_calls 必须合并为单条 assistant 消息；否则上游
    # 400 或模型把“思考”当作完整回合输出导致 codex 会话中断。
    pending_tool_calls: list[dict[str, Any]] = []
    pending_reasoning: list[str] = []

    def merge_tool_calls_into(message: dict[str, Any]) -> None:
        existing = message.get("tool_calls")
        if not isinstance(existing, list):
            existing = []
            message["tool_calls"] = existing
        seen_ids = {str(call.get("id") or "") for call in existing if isinstance(call, dict)}
        for call in pending_tool_calls:
            call_id = str(call.get("id") or "")
            if call_id and call_id in seen_ids:
                continue
            existing.append(call)
        pending_tool_calls.clear()
        if message.get("content") is None:
            message["content"] = ""

    def attach_reasoning(message: dict[str, Any]) -> None:
        if not pending_reasoning:
            return
        reasoning = "\n".join(pending_reasoning)
        pending_reasoning.clear()
        existing = str(message.get("reasoning_content") or "")
        message["reasoning_content"] = f"{existing}\n{reasoning}" if existing else reasoning
        if message.get("content") is None:
            message["content"] = ""

    def flush_tool_calls() -> None:
        if not pending_tool_calls:
            return
        last = messages[-1] if messages else None
        if isinstance(last, dict) and last.get("role") == "assistant":
            merge_tool_calls_into(last)
            attach_reasoning(last)
            return
        message: dict[str, Any] = {"role": "assistant", "content": "", "tool_calls": list(pending_tool_calls)}
        pending_tool_calls.clear()
        attach_reasoning(message)
        messages.append(message)

    def flush_reasoning() -> None:
        if not pending_reasoning:
            return
        last = messages[-1] if messages else None
        if isinstance(last, dict) and last.get("role") == "assistant":
            attach_reasoning(last)
            return
        message = {"role": "assistant", "content": ""}
        attach_reasoning(message)
        messages.append(message)

    def add_message(role: object, content: object, **extra: Any) -> Optional[dict[str, Any]]:
        normalized_role = _response_role(role)
        normalized = _response_message_content(content)
        text = _response_content_text(normalized)
        if not text and not (normalized_role == "assistant" and extra.get("tool_calls")):
            return None
        message: dict[str, Any] = {"role": normalized_role, "content": normalized if isinstance(normalized, (str, list)) else text}
        message.update(extra)
        messages.append(message)
        return message

    def handle_item(item: object) -> None:
        if isinstance(item, str):
            flush_tool_calls()
            flush_reasoning()
            add_message("user", item)
            return
        if not isinstance(item, dict):
            flush_tool_calls()
            flush_reasoning()
            add_message("user", _response_content_text(item))
            return

        item_type = str(item.get("type") or "")
        if item_type == "reasoning":
            summary = _response_reasoning_summary(item)
            if summary:
                pending_reasoning.append(summary)
            return
        if item_type in {"function_call", "custom_tool_call"}:
            pending_tool_calls.append(_response_tool_call_entry(item))
            return
        if item_type == "function_call_output":
            flush_tool_calls()
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(item.get("call_id") or item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "content": _response_content_text(item.get("output")),
                }
            )
            return
        if item_type in {"custom_tool_call_output", "local_shell_call_output"}:
            # Codex 执行 apply_patch 等自定义工具后回传的结果项，
            # 必须映射为 role=tool 消息与前面的 tool_calls 配对，
            # 否则严格的 OpenAI 兼容上游（如 DeepSeek）会直接 400 中断会话。
            flush_tool_calls()
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(item.get("call_id") or item.get("id") or ""),
                    "content": _response_content_text(item.get("output")),
                }
            )
            return

        flush_tool_calls()
        if item_type == "message" or "role" in item:
            role = item.get("role", "user")
            content = item.get("content", item.get("text", item.get("input_text", "")))
            tool_calls = item.get("tool_calls") or _response_tool_calls_from_content(content)
            if _response_role(role) == "assistant":
                if tool_calls:
                    appended = add_message("assistant", content, tool_calls=tool_calls)
                else:
                    appended = add_message(role, content)
                if appended is not None:
                    attach_reasoning(appended)
            else:
                flush_reasoning()
                add_message(role, content)
            return
        if item_type in {"input_text", "text"}:
            flush_reasoning()
            add_message("user", item.get("text", item.get("input_text", "")))
            return
        if item_type in {"output_text", "summary_text"}:
            appended = add_message("assistant", item.get("text", item.get("output_text", "")))
            if appended is not None:
                attach_reasoning(appended)
            return
        if item_type in {"input_image", "input_file"}:
            flush_reasoning()
            add_message("user", [item])
            return

        text = _response_content_text(item)
        if text:
            flush_reasoning()
            add_message(item.get("role", "user"), text)

    if isinstance(input_value, list):
        for entry in input_value:
            handle_item(entry)
    else:
        handle_item(input_value)
    flush_tool_calls()
    flush_reasoning()

    has_user_text = any(msg.get("role") == "user" and _response_content_text(msg.get("content")).strip() for msg in messages)
    has_tool_context = any(msg.get("role") in {"assistant", "tool"} for msg in messages)
    if not has_user_text and not has_tool_context:
        text, _ = _extract_translate_text(payload)
        if text:
            messages.append({"role": "user", "content": text})
    messages = _repair_tool_call_pairing(messages)
    return messages or [{"role": "user", "content": ""}]


def _repair_tool_call_pairing(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """确保 assistant.tool_calls 与 role=tool 结果严格配对。

    严格的 OpenAI 兼容上游（如 DeepSeek）要求每个 tool_call 后必须跟随
    匹配 tool_call_id 的 tool 消息，且 tool 消息不能凭空出现。客户端
    （如 Codex）的输入项偶发缺失/乱序时，在这里补占位结果或降级为
    user 文本，避免上游 400 导致整轮会话中断。
    """
    repaired: list[dict[str, Any]] = []
    pending: list[str] = []

    def flush_pending() -> None:
        for call_id in pending:
            repaired.append({"role": "tool", "tool_call_id": call_id, "content": "(no tool output provided)"})
        pending.clear()

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            if call_id and call_id in pending:
                pending.remove(call_id)
                repaired.append(msg)
            elif pending:
                fixed = dict(msg)
                fixed["tool_call_id"] = pending.pop(0)
                repaired.append(fixed)
            else:
                text = _chat_content_text(msg.get("content"))
                if text:
                    repaired.append({"role": "user", "content": f"[tool result] {text}"})
            continue
        if pending:
            flush_pending()
        repaired.append(msg)
        if role == "assistant" and isinstance(msg.get("tool_calls"), list):
            pending.extend(
                str(call.get("id") or "")
                for call in msg["tool_calls"]
                if isinstance(call, dict) and str(call.get("id") or "")
            )
    flush_pending()
    return repaired


def _extract_translate_text(payload: dict[str, Any]) -> tuple[str, str]:
    direct_keys = (
        "text",
        "q",
        "query",
        "input",
        "input_text",
        "output_text",
        "sourceText",
        "source_text",
        "source_texts",
        "sourceTexts",
        "text_list",
        "texts",
        "sentence",
        "sentences",
        "content",
        "body",
        "prompt",
    )
    for key in direct_keys:
        if key not in payload:
            continue
        text = _text_from_any(payload.get(key))
        if text:
            return text, key

    messages = payload.get("messages")
    if isinstance(messages, list):
        try:
            text = extract_text_from_messages(messages)
        except Exception:
            text = ""
        if text:
            return text.strip(), "messages"

    for key in ("data", "payload", "request", "params"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            text, source = _extract_translate_text(nested)
            if text:
                return text, f"{key}.{source}"
        elif isinstance(nested, list):
            text = _text_from_any(nested)
            if text:
                return text, key

    return "", ""


def _chat_completion_from_stream_chunks(
    chunks: Iterable[dict[str, Any]],
    *,
    default_model: str,
    created: int,
) -> dict[str, Any]:
    chat_id = ""
    model = str(default_model or SERVICE_MODEL_ID)
    created_ts = int(created or time.time())
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason = "stop"
    usage: Optional[dict[str, Any]] = None

    def append_tool_call(part: dict[str, Any], fallback_index: int) -> None:
        try:
            index = int(part.get("index", fallback_index))
        except Exception:
            index = fallback_index
        state = tool_calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
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

    def append_legacy_function_call(function_call: object) -> None:
        if not isinstance(function_call, dict):
            return
        append_tool_call(
            {
                "index": 0,
                "id": function_call.get("id") or function_call.get("call_id") or "call_0",
                "type": "function",
                "function": {
                    "name": function_call.get("name") or "",
                    "arguments": function_call.get("arguments") or "",
                },
            },
            0,
        )

    def apply_message(message: dict[str, Any]) -> None:
        content = _chat_content_text(message.get("content"))
        if content:
            content_parts.append(content)
        reasoning = _chat_message_reasoning_text(message)
        if reasoning:
            reasoning_parts.append(reasoning)
        for index, tool_call in enumerate(message.get("tool_calls") or []):
            if isinstance(tool_call, dict):
                append_tool_call(tool_call, index)
        append_legacy_function_call(message.get("function_call"))

    for chunk in chunks:
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
                apply_message(message)
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            content = _chat_content_text(delta.get("content"))
            if content:
                content_parts.append(content)
            for key in ("reasoning_content", "reasoning"):
                value = delta.get(key)
                if isinstance(value, str) and value:
                    reasoning_parts.append(value)
            for index, tool_call in enumerate(delta.get("tool_calls") or []):
                if isinstance(tool_call, dict):
                    append_tool_call(tool_call, index)
            append_legacy_function_call(delta.get("function_call"))

    normalized_calls: list[dict[str, Any]] = []
    for index in sorted(tool_calls):
        call = tool_calls[index]
        if not call.get("id"):
            call["id"] = f"call_{uuid.uuid4().hex[:8]}"
        call.setdefault("type", "function")
        call.setdefault("function", {"name": "", "arguments": ""})
        normalized_calls.append(call)

    message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if normalized_calls:
        message["tool_calls"] = normalized_calls
    return {
        "id": chat_id or f"chatcmpl-deepcat-{created_ts}",
        "object": "chat.completion",
        "created": created_ts,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage
        or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


class _ChatToResponsesStreamState:
    def __init__(
        self,
        *,
        model: str,
        created: int,
        response_id: str,
        tool_context: Optional[dict[str, Any]] = None,
        original_request: Optional[dict[str, Any]] = None,
    ) -> None:
        self.model = str(model or SERVICE_MODEL_ID)
        self.created = int(created or time.time())
        self.response_id = str(response_id or f"resp-deepcat-{self.created}-{uuid.uuid4().hex[:8]}")
        self.tool_context = tool_context or {}
        self.original_request = original_request
        self.finish_reason = "stop"
        self.usage: Optional[dict[str, Any]] = None
        self.output_items: list[tuple[int, dict[str, Any]]] = []
        self.next_output_index = 0

        self.text_index: Optional[int] = None
        self.text_item_id = f"msg_{uuid.uuid4().hex[:12]}"
        self.text_parts: list[str] = []
        self.text_added = False
        self.text_done = False

        self.reasoning_index: Optional[int] = None
        self.reasoning_item_id = f"rs_{uuid.uuid4().hex[:12]}"
        self.reasoning_parts: list[str] = []
        self.reasoning_added = False
        self.reasoning_done = False

        self.tool_calls: dict[int, dict[str, Any]] = {}

    def _next_index(self) -> int:
        value = self.next_output_index
        self.next_output_index += 1
        return value

    def apply_chunk(self, chunk: dict[str, Any], event) -> None:
        if not isinstance(chunk, dict):
            return
        if chunk.get("model"):
            self.model = str(chunk.get("model"))
        if isinstance(chunk.get("usage"), dict):
            self.usage = chunk.get("usage")

        choices = chunk.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                self.finish_reason = str(choice.get("finish_reason"))

            message = choice.get("message")
            if isinstance(message, dict):
                self._apply_message(message, event)

            delta = choice.get("delta")
            if isinstance(delta, dict):
                self._apply_delta(delta, event)

    def push_error_text(self, text: str, event) -> None:
        prefix = "\n" if self.text_parts else ""
        self._push_text_delta(f"{prefix}{text}", event)

    def finish(self, event) -> dict[str, Any]:
        self._finish_reasoning(event)
        self._finish_text(event, force_empty=not self.output_items and not self.tool_calls)
        self._finish_tools(event)

        output = [item for _, item in sorted(self.output_items, key=lambda pair: pair[0])]
        status = "incomplete" if self.finish_reason == "length" else "completed"
        payload = _openai_response_payload(
            self.model,
            "",
            created=self.created,
            response_id=self.response_id,
            status=status,
            output=output,
            usage=_chat_usage_to_response_usage(self.usage),
        )
        if status == "incomplete":
            payload["incomplete_details"] = {"reason": "max_output_tokens"}
        _copy_response_request_fields(payload, self.original_request)
        return payload

    def _apply_message(self, message: dict[str, Any], event) -> None:
        reasoning = _chat_message_reasoning_text(message)
        if reasoning:
            self._push_reasoning_delta(reasoning, event)
        content = _chat_content_text(message.get("content"))
        if content:
            self._push_text_delta(content, event)
        for index, tool_call in enumerate(message.get("tool_calls") or []):
            if isinstance(tool_call, dict):
                self._push_tool_call_delta(tool_call, index, event)
        function_call = message.get("function_call")
        if isinstance(function_call, dict):
            self._push_tool_call_delta(
                {
                    "index": 0,
                    "id": function_call.get("id") or function_call.get("call_id") or "call_0",
                    "type": "function",
                    "function": {
                        "name": function_call.get("name") or "",
                        "arguments": function_call.get("arguments") or "",
                    },
                },
                0,
                event,
            )

    def _apply_delta(self, delta: dict[str, Any], event) -> None:
        for key in ("reasoning_content", "reasoning"):
            value = delta.get(key)
            if isinstance(value, str) and value:
                self._push_reasoning_delta(value, event)

        content = _chat_content_text(delta.get("content"))
        if content:
            self._push_text_delta(content, event)

        for index, tool_call in enumerate(delta.get("tool_calls") or []):
            if isinstance(tool_call, dict):
                self._push_tool_call_delta(tool_call, index, event)

        function_call = delta.get("function_call")
        if isinstance(function_call, dict):
            self._push_tool_call_delta(
                {
                    "index": 0,
                    "id": function_call.get("id") or function_call.get("call_id") or "call_0",
                    "type": "function",
                    "function": {
                        "name": function_call.get("name") or "",
                        "arguments": function_call.get("arguments") or "",
                    },
                },
                0,
                event,
            )

    def _push_text_delta(self, delta: str, event) -> None:
        if not delta:
            return
        if not self.text_added:
            self._start_text(event)
        self.text_parts.append(str(delta))
        event(
            {
                "type": "response.output_text.delta",
                "response_id": self.response_id,
                "item_id": self.text_item_id,
                "output_index": self.text_index or 0,
                "content_index": 0,
                "delta": str(delta),
            }
        )

    def _start_text(self, event) -> None:
        if self.text_added:
            return
        self.text_index = self._next_index()
        self.text_added = True
        event(
            {
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
            }
        )
        event(
            {
                "type": "response.content_part.added",
                "response_id": self.response_id,
                "item_id": self.text_item_id,
                "output_index": self.text_index,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            }
        )

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
        event(
            {
                "type": "response.output_text.done",
                "response_id": self.response_id,
                "item_id": self.text_item_id,
                "output_index": self.text_index or 0,
                "content_index": 0,
                "text": full_text,
            }
        )
        event(
            {
                "type": "response.content_part.done",
                "response_id": self.response_id,
                "item_id": self.text_item_id,
                "output_index": self.text_index or 0,
                "content_index": 0,
                "part": item["content"][0],
            }
        )
        event(
            {
                "type": "response.output_item.done",
                "response_id": self.response_id,
                "output_index": self.text_index or 0,
                "item": item,
            }
        )
        self.output_items.append((self.text_index or 0, item))
        self.text_done = True

    def _push_reasoning_delta(self, delta: str, event) -> None:
        if not delta:
            return
        if not self.reasoning_added:
            self.reasoning_index = self._next_index()
            self.reasoning_added = True
            event(
                {
                    "type": "response.output_item.added",
                    "response_id": self.response_id,
                    "output_index": self.reasoning_index,
                    "item": {
                        "id": self.reasoning_item_id,
                        "type": "reasoning",
                        "status": "in_progress",
                        "summary": [],
                    },
                }
            )
            event(
                {
                    "type": "response.reasoning_summary_part.added",
                    "response_id": self.response_id,
                    "item_id": self.reasoning_item_id,
                    "output_index": self.reasoning_index,
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": ""},
                }
            )
        self.reasoning_parts.append(str(delta))
        event(
            {
                "type": "response.reasoning_summary_text.delta",
                "response_id": self.response_id,
                "item_id": self.reasoning_item_id,
                "output_index": self.reasoning_index or 0,
                "summary_index": 0,
                "delta": str(delta),
            }
        )

    def _finish_reasoning(self, event) -> None:
        if not self.reasoning_added or self.reasoning_done:
            return
        text = "".join(self.reasoning_parts)
        item = {
            "type": "reasoning",
            "id": self.reasoning_item_id,
            "summary": [{"type": "summary_text", "text": text}],
        }
        event(
            {
                "type": "response.reasoning_summary_text.done",
                "response_id": self.response_id,
                "item_id": self.reasoning_item_id,
                "output_index": self.reasoning_index or 0,
                "summary_index": 0,
                "text": text,
            }
        )
        event(
            {
                "type": "response.reasoning_summary_part.done",
                "response_id": self.response_id,
                "item_id": self.reasoning_item_id,
                "output_index": self.reasoning_index or 0,
                "summary_index": 0,
                "part": {"type": "summary_text", "text": text},
            }
        )
        event(
            {
                "type": "response.output_item.done",
                "response_id": self.response_id,
                "output_index": self.reasoning_index or 0,
                "item": item,
            }
        )
        self.output_items.append((self.reasoning_index or 0, item))
        self.reasoning_done = True

    def _should_defer_tool_call_start(self, state: dict[str, Any]) -> bool:
        mode = _codex_apply_patch_emit_mode(self.tool_context)
        if not mode:
            return False
        name = str(state.get("name") or "")
        if _tool_name_looks_like_shell(name):
            return True
        # shell 形态下代理工具最终要以 function_call(shell) 落地，
        # 不能先以 custom_tool_call added 开头，否则事件类型前后不一致
        return mode == "shell" and _codex_custom_tool_kind(self.tool_context, name) == "apply_patch"

    def _convert_deferred_shell_write(self, state: dict[str, Any]) -> None:
        mode = _codex_apply_patch_emit_mode(self.tool_context)
        if not mode:
            return
        name = str(state.get("name") or "")
        if _tool_name_looks_like_shell(name):
            patch_input = _shell_file_write_patch_input(state.get("arguments"))
            if not patch_input:
                return
            if mode == "custom":
                state["name"] = "apply_patch"
                state["arguments"] = json.dumps({"input": patch_input}, ensure_ascii=False)
            else:
                state["arguments"] = _shell_apply_patch_arguments(self.tool_context, patch_input, state.get("arguments"))
            return
        if mode == "shell" and _codex_custom_tool_kind(self.tool_context, name) == "apply_patch":
            patch_input = _reconstruct_custom_tool_call_input(self.tool_context, name, str(state.get("arguments") or ""))
            if not patch_input:
                return
            state["name"] = _codex_shell_apply_patch_tool_name(self.tool_context)
            state["arguments"] = _shell_apply_patch_arguments(self.tool_context, patch_input)

    def _push_tool_call_delta(self, tool_call: dict[str, Any], fallback_index: int, event) -> None:
        try:
            index = int(tool_call.get("index", fallback_index))
        except Exception:
            index = fallback_index
        state = self.tool_calls.setdefault(
            index,
            {
                "call_id": "",
                "item_id": "",
                "name": "",
                "arguments": "",
                "output_index": None,
                "added": False,
                "done": False,
            },
        )
        if tool_call.get("id"):
            state["call_id"] = str(tool_call.get("id"))
        fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        if fn.get("name"):
            state["name"] = str(fn.get("name"))
        args_delta = str(fn.get("arguments") or "") if fn.get("arguments") is not None else ""
        if args_delta:
            state["arguments"] = str(state.get("arguments") or "") + args_delta

        should_start = not state["added"] and (state["call_id"] or state["name"])
        if should_start:
            if self._should_defer_tool_call_start(state):
                return
            self._start_tool_call(index, event)
            if state["arguments"]:
                self._write_tool_call_arguments_delta(state, state["arguments"], event)
            return
        if state["added"] and args_delta:
            self._write_tool_call_arguments_delta(state, args_delta, event)

    def _start_tool_call(self, index: int, event) -> None:
        state = self.tool_calls[index]
        if state["added"]:
            return
        if not state["call_id"]:
            state["call_id"] = f"call_{uuid.uuid4().hex[:8]}"
        if not state["name"]:
            state["name"] = "unknown_tool"
        state["output_index"] = self._next_index()
        is_custom = _codex_is_custom_tool_proxy(self.tool_context, str(state["name"] or ""))
        state["item_id"] = f"{'ctc' if is_custom else 'fc'}_{state['call_id']}"
        state["added"] = True
        if is_custom:
            event(
                {
                    "type": "response.output_item.added",
                    "response_id": self.response_id,
                    "output_index": state["output_index"],
                    "item": {
                        "id": state["item_id"],
                        "type": "custom_tool_call",
                        "status": "in_progress",
                        "call_id": state["call_id"],
                        "name": _codex_original_custom_tool_name(self.tool_context, str(state["name"] or "")),
                        "input": "",
                    },
                }
            )
            return
        display_name, namespace = _codex_function_tool_name(self.tool_context, str(state["name"] or ""))
        item = {
            "id": state["item_id"],
            "type": "function_call",
            "status": "in_progress",
            "call_id": state["call_id"],
            "name": display_name,
            "arguments": "",
        }
        if namespace:
            item["namespace"] = namespace
        event(
            {
                "type": "response.output_item.added",
                "response_id": self.response_id,
                "output_index": state["output_index"],
                "item": item,
            }
        )

    def _write_tool_call_arguments_delta(self, state: dict[str, Any], delta: str, event) -> None:
        if _codex_is_custom_tool_proxy(self.tool_context, str(state.get("name") or "")):
            return
        event(
            {
                "type": "response.function_call_arguments.delta",
                "response_id": self.response_id,
                "item_id": state["item_id"],
                "output_index": state["output_index"] or 0,
                "delta": str(delta),
            }
        )

    def _finish_tools(self, event) -> None:
        for index in sorted(self.tool_calls):
            state = self.tool_calls[index]
            if not state["added"]:
                self._convert_deferred_shell_write(state)
                self._start_tool_call(index, event)
                if state["arguments"]:
                    self._write_tool_call_arguments_delta(state, state["arguments"], event)
            if state["done"]:
                continue
            arguments = str(state.get("arguments") or "")
            if _codex_is_custom_tool_proxy(self.tool_context, str(state.get("name") or "")):
                input_text = _reconstruct_custom_tool_call_input(self.tool_context, str(state.get("name") or ""), arguments)
                if input_text:
                    event(
                        {
                            "type": "response.custom_tool_call_input.delta",
                            "response_id": self.response_id,
                            "item_id": state["item_id"],
                            "call_id": state["call_id"],
                            "output_index": state["output_index"] or 0,
                            "delta": input_text,
                        }
                    )
                item = {
                    "id": state["item_id"],
                    "type": "custom_tool_call",
                    "call_id": state["call_id"],
                    "name": _codex_original_custom_tool_name(self.tool_context, str(state.get("name") or "")),
                    "input": input_text,
                    "status": "completed",
                }
                event(
                    {
                        "type": "response.output_item.done",
                        "response_id": self.response_id,
                        "output_index": state["output_index"] or 0,
                        "item": item,
                    }
                )
                self.output_items.append((state["output_index"] or 0, item))
                state["done"] = True
                continue
            display_name, namespace = _codex_function_tool_name(self.tool_context, str(state.get("name") or ""))
            done_event = {
                "type": "response.function_call_arguments.done",
                "response_id": self.response_id,
                "item_id": state["item_id"],
                "output_index": state["output_index"] or 0,
                "call_id": state["call_id"],
                "name": display_name,
                "arguments": arguments,
            }
            if namespace:
                done_event["namespace"] = namespace
            event(
                done_event
            )
            item = {
                "id": state["item_id"],
                "type": "function_call",
                "call_id": state["call_id"],
                "name": display_name,
                "arguments": arguments,
                "status": "completed",
            }
            if namespace:
                item["namespace"] = namespace
            event(
                {
                    "type": "response.output_item.done",
                    "response_id": self.response_id,
                    "output_index": state["output_index"] or 0,
                    "item": item,
                }
            )
            self.output_items.append((state["output_index"] or 0, item))
            state["done"] = True


def _stream_text_chunks(text: str, chunk_size: int = 120) -> list[str]:
    if not text:
        return [""]
    chunks: list[str] = []
    current = ""
    for part in str(text).splitlines(keepends=True):
        while len(current) + len(part) > chunk_size and part:
            take = max(1, int(chunk_size) - len(current))
            current += part[:take]
            part = part[take:]
            chunks.append(current)
            current = ""
        current += part
        if len(current) >= chunk_size:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks or [""]


def _iter_outbound_text_chunks(chunks: Iterable[str] | str) -> Iterable[str]:
    if isinstance(chunks, str):
        yield from _stream_text_chunks(chunks)
        return
    for chunk in chunks:
        text = str(chunk or "")
        if text:
            yield text


def start_background_translation_server(
    host: str = "127.0.0.1",
    port: int = 11888,
    api_key: str = DEFAULT_SERVICE_API_KEY,
) -> tuple[str, int]:
    global _background_server, _background_thread, _background_server_v6, _background_thread_v6
    address = (str(host or "127.0.0.1"), int(port))
    with _background_lock:
        if _background_server is not None:
            running_host, running_port = _background_server.server_address
            if (str(running_host), int(running_port), str(_background_server.api_key or "")) == (
                str(address[0]),
                int(address[1]),
                str(api_key or ""),
            ):
                return str(running_host), int(running_port)
            stop_background_translation_server()
        httpd = TranslationHTTPServer(address, TranslationRequestHandler, api_key=str(api_key or ""))
        thread = threading.Thread(target=httpd.serve_forever, name="DeepCatTranslationService", daemon=True)
        thread.start()
        _background_server = httpd
        _background_thread = thread
        actual_host, actual_port = httpd.server_address
        _background_server_v6, _background_thread_v6 = _start_ipv6_loopback_server(int(actual_port), api_key)
        return str(actual_host), int(actual_port)


def _start_ipv6_loopback_server(port: int, api_key: str) -> tuple[Optional["TranslationHTTPServer"], Optional[threading.Thread]]:
    """在 ::1 上再监听一份；端口无效或系统未启用 IPv6 时返回 (None, None)。

    失败只影响 IPv6 客户端，IPv4 主服务照常提供，因此这里不向上抛错。
    """
    if int(port) <= 0:
        return None, None
    try:
        httpd = TranslationHTTPServerV6(("::1", int(port)), TranslationRequestHandler, api_key=str(api_key or ""))
    except OSError:
        return None, None
    thread = threading.Thread(target=httpd.serve_forever, name="DeepCatTranslationServiceV6", daemon=True)
    thread.start()
    return httpd, thread


def stop_background_translation_server() -> None:
    global _background_server, _background_thread, _background_server_v6, _background_thread_v6
    with _background_lock:
        servers = [item for item in (_background_server, _background_server_v6) if item is not None]
        threads = [item for item in (_background_thread, _background_thread_v6) if item is not None]
        _background_server = None
        _background_thread = None
        _background_server_v6 = None
        _background_thread_v6 = None
    for httpd in servers:
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
    for thread in threads:
        if thread.is_alive():
            try:
                thread.join(timeout=2)
            except Exception:
                pass


def background_translation_server_address() -> Optional[tuple[str, int]]:
    with _background_lock:
        if _background_server is None:
            return None
        host, port = _background_server.server_address[:2]
        return str(host), int(port)


# 回环自检：bind 成功只说明系统分配了端口，不代表客户端真的连得上。
# Windows 防火墙的 WFP 会在 ALE_AUTH_RECV_ACCEPT 层静默丢弃入站 SYN，此时端口仍是
# LISTENING，客户端却会一直停在 SYN_SENT 直到超时，看起来像「服务没有启动」。
LOOPBACK_PROBE_ADDRESSES = ("127.0.0.1", "::1")
LOOPBACK_PROBE_TIMEOUT_SECONDS = 1.5

PROBE_STATUS_REACHABLE = "reachable"
PROBE_STATUS_TIMEOUT = "timeout"
PROBE_STATUS_REFUSED = "refused"
PROBE_STATUS_UNAVAILABLE = "unavailable"
PROBE_STATUS_ERROR = "error"

# IPv6 栈未启用时，创建或连接 AF_INET6 回环会直接返回这些错误码，属于「本机没有 IPv6」而非故障。
_IPV6_UNAVAILABLE_ERRNOS = frozenset(
    code
    for code in (
        getattr(errno, "EAFNOSUPPORT", None),
        getattr(errno, "EPROTONOSUPPORT", None),
        getattr(errno, "EADDRNOTAVAIL", None),
        getattr(errno, "WSAEAFNOSUPPORT", None),
        getattr(errno, "WSAEPROTONOSUPPORT", None),
        getattr(errno, "WSAEADDRNOTAVAIL", None),
    )
    if code is not None
)

_PROBE_STATUS_SEVERITY = {
    PROBE_STATUS_REACHABLE: 0,
    PROBE_STATUS_UNAVAILABLE: 1,
    PROBE_STATUS_REFUSED: 2,
    PROBE_STATUS_ERROR: 3,
    PROBE_STATUS_TIMEOUT: 4,
}

_FAILED_PROBE_SUMMARY = {
    PROBE_STATUS_TIMEOUT: "{address} 连接超时（SYN 无响应），通常是被 Windows 防火墙拦截",
    PROBE_STATUS_REFUSED: "{address} 拒绝连接，本地 API 服务没有在该端口监听",
    PROBE_STATUS_ERROR: "{address} 探测失败：{detail}",
    PROBE_STATUS_UNAVAILABLE: "{address} 不可用（本机未启用 IPv6）",
}


@dataclasses.dataclass(frozen=True)
class LoopbackProbeResult:
    """单个回环地址的连接自检结果。"""

    address: str
    port: int
    status: str
    elapsed_ms: int
    detail: str = ""

    @property
    def reachable(self) -> bool:
        return self.status == PROBE_STATUS_REACHABLE

    @property
    def timed_out(self) -> bool:
        return self.status == PROBE_STATUS_TIMEOUT


@dataclasses.dataclass(frozen=True)
class TranslationServiceProbeReport:
    """127.0.0.1 与 ::1 两条回环路径的自检汇总。"""

    port: int
    probes: tuple[LoopbackProbeResult, ...]

    def probe_for(self, address: str) -> Optional[LoopbackProbeResult]:
        for probe in self.probes:
            if probe.address == address:
                return probe
        return None

    @property
    def ipv4(self) -> Optional[LoopbackProbeResult]:
        return self.probe_for("127.0.0.1")

    @property
    def ipv6(self) -> Optional[LoopbackProbeResult]:
        return self.probe_for("::1")

    @property
    def failed_probes(self) -> tuple[LoopbackProbeResult, ...]:
        return tuple(probe for probe in self.probes if probe.status != PROBE_STATUS_REACHABLE)

    @property
    def is_healthy(self) -> bool:
        # 本机没有 IPv6 栈不算故障，其余任一地址连不上都说明客户端可能连不通。
        if not self.probes:
            return False
        return all(
            probe.status in {PROBE_STATUS_REACHABLE, PROBE_STATUS_UNAVAILABLE} for probe in self.probes
        )

    @property
    def is_blocked(self) -> bool:
        """IPv4 主服务已绑定监听却连不上，才是防火墙丢包的典型特征。

        注意：部分 Windows 环境对「未监听的环回端口」同样会静默超时而不是拒绝连接，
        所以不能只看超时，必须结合服务是否已经 bind（由调用方保证）。
        """
        ipv4 = self.ipv4
        return ipv4 is not None and ipv4.timed_out

    def _leading_failure(self) -> LoopbackProbeResult:
        """挑一条最能解释失败原因的探测结果：优先 IPv4 主服务。"""
        ipv4 = self.ipv4
        if ipv4 is not None and not ipv4.reachable and ipv4.status != PROBE_STATUS_UNAVAILABLE:
            return ipv4
        return max(self.probes, key=lambda probe: _PROBE_STATUS_SEVERITY.get(probe.status, 0))

    @property
    def reason(self) -> str:
        if not self.probes:
            return "本地 API 服务尚未启动。"
        if self.is_healthy:
            return "回环自检通过。"
        failure = self._leading_failure()
        template = _FAILED_PROBE_SUMMARY.get(failure.status, "{address} 连接异常")
        return template.format(address=failure.address, detail=failure.detail or "")


def _probe_loopback_address(
    address: str,
    port: int,
    timeout: float = LOOPBACK_PROBE_TIMEOUT_SECONDS,
) -> LoopbackProbeResult:
    """对单个回环地址发起一次 TCP 连接，把失败原因编码进返回值而不是抛异常。"""
    family = socket.AF_INET6 if ":" in str(address) else socket.AF_INET
    started = time.monotonic()
    sock: Optional[socket.socket] = None
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(max(0.1, float(timeout)))
        sock.connect((str(address), int(port)))
    except (socket.timeout, TimeoutError):
        status, detail = PROBE_STATUS_TIMEOUT, "connect 超时"
    except ConnectionRefusedError:
        status, detail = PROBE_STATUS_REFUSED, "连接被拒绝"
    except OSError as exc:
        if exc.errno in _IPV6_UNAVAILABLE_ERRNOS:
            status, detail = PROBE_STATUS_UNAVAILABLE, "本机未启用 IPv6 回环"
        else:
            status, detail = PROBE_STATUS_ERROR, str(exc) or exc.__class__.__name__
    else:
        status, detail = PROBE_STATUS_REACHABLE, ""
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    elapsed_ms = int(round((time.monotonic() - started) * 1000))
    return LoopbackProbeResult(
        address=str(address),
        port=int(port),
        status=status,
        elapsed_ms=elapsed_ms,
        detail=detail,
    )


def probe_translation_service_loopback(
    port: int,
    timeout: float = LOOPBACK_PROBE_TIMEOUT_SECONDS,
) -> TranslationServiceProbeReport:
    """分别连接 127.0.0.1 与 ::1，验证本地 API 服务对客户端真实可达。

    bind 成功只代表端口被占用，Windows 防火墙仍可能丢包，所以这里必须真连一次。
    """
    target_port = int(port)
    if target_port <= 0:
        return TranslationServiceProbeReport(port=target_port, probes=())
    probes = tuple(
        _probe_loopback_address(item, target_port, timeout) for item in LOOPBACK_PROBE_ADDRESSES
    )
    return TranslationServiceProbeReport(port=target_port, probes=probes)


def probe_background_translation_server(
    timeout: float = LOOPBACK_PROBE_TIMEOUT_SECONDS,
) -> TranslationServiceProbeReport:
    """对当前进程已启动的后台本地 API 服务做回环自检。"""
    address = background_translation_server_address()
    if address is None:
        return TranslationServiceProbeReport(port=0, probes=())
    return probe_translation_service_loopback(int(address[1]), timeout)


def run_translation_server(host: str = "127.0.0.1", port: int = 11888, api_key: str = DEFAULT_SERVICE_API_KEY) -> int:
    address = (str(host or "127.0.0.1"), int(port))
    httpd = TranslationHTTPServer(address, TranslationRequestHandler, api_key=str(api_key or ""))
    actual_host, actual_port = httpd.server_address[:2]
    print(f"DeepCat local API service listening on http://{actual_host}:{actual_port}")
    print(f"OpenAI-compatible endpoint: http://{actual_host}:{actual_port}/v1/chat/completions")
    if api_key:
        print("API key is required.")
    httpd_v6, _ = _start_ipv6_loopback_server(int(actual_port), api_key)
    probe_report = probe_translation_service_loopback(int(actual_port))
    if not probe_report.is_healthy:
        print(f"Warning: {probe_report.reason}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDeepCat local API service stopped.")
    finally:
        if httpd_v6 is not None:
            try:
                httpd_v6.shutdown()
            except Exception:
                pass
            try:
                httpd_v6.server_close()
            except Exception:
                pass
        httpd.server_close()
    return 0


atexit.register(stop_background_translation_server)
