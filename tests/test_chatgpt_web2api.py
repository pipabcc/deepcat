from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zlib
from http.server import HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

from deepcat import chatgpt_web2api as server_mod
from deepcat.local_chatgpt_web2api_server import identify_spec
from deepcat.settings_store import infer_translator_model_provider, infer_translator_model_type


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class TestChatGPTWeb2API(unittest.TestCase):
    SAMPLE_PNG_DATA_URL = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN4QAAAAASUVORK5CYII="
    )

    @staticmethod
    def _png_bytes(width: int, height: int) -> bytes:
        import struct

        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        row = b"\x00" + (b"\xff\xff\xff\xff" * int(width))
        raw = row * int(height)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )

    def _start_server(self):
        httpd = _ThreadedHTTPServer(("127.0.0.1", 0), server_mod.ChatGPTWeb2APIHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread

    def test_contains_inline_image_rejects_unresolved_chatgpt_placeholders(self) -> None:
        self.assertFalse(
            server_mod._contains_inline_image(
                "![generated image](sediment://file_00000000d67071fdb9effff21f9902ab)"
            )
        )
        self.assertFalse(
            server_mod._contains_inline_image(
                "![generated image](https://chatgpt.com/backend-api/estuary/content?id=file_abc&ts=1&p=fs&sig=x)"
            )
        )
        self.assertFalse(
            server_mod._contains_inline_image(
                "![generated image](https://chatgpt.com/backend-api/files/file-abc/download)"
            )
        )
        self.assertTrue(server_mod._contains_inline_image(f"![generated image]({self.SAMPLE_PNG_DATA_URL})"))
        self.assertTrue(server_mod._contains_inline_image("![generated image](file:///C:/tmp/generated.png)"))

    def test_parse_auth_accepts_cookie_token_and_json(self) -> None:
        cookie_auth = server_mod.parse_auth_value("oai-did=device; __Secure-next-auth.session-token=session-token")
        self.assertIn("__Secure-next-auth.session-token=session-token", cookie_auth.cookie)
        self.assertIsNone(cookie_auth.access_token)

        token_auth = server_mod.parse_auth_value("plain-session-token")
        self.assertEqual(token_auth.access_token, "plain-session-token")
        self.assertEqual(token_auth.cookie, "__Secure-next-auth.session-token=plain-session-token")

        json_auth = server_mod.parse_auth_value(
            json.dumps(
                {
                    "cookie": "a=b",
                    "access_token": "access",
                    "user_agent": "UA",
                    "protocol_headers": {"x-conduit-token": "proof"},
                }
            )
        )
        self.assertEqual(json_auth.cookie, "a=b")
        self.assertEqual(json_auth.access_token, "access")
        self.assertEqual(json_auth.user_agent, "UA")
        self.assertEqual(json_auth.protocol_headers["x-conduit-token"], "proof")

        session_auth = server_mod.parse_auth_value(
            json.dumps(
                {
                    "accessToken": "access-token-from-session",
                    "sessionToken": "session-token-from-session",
                    "oaiDeviceId": "device-from-session",
                }
            )
        )
        self.assertEqual(session_auth.access_token, "access-token-from-session")
        self.assertEqual(session_auth.session_token, "session-token-from-session")
        self.assertEqual(
            session_auth.cookie,
            "__Secure-next-auth.session-token=session-token-from-session",
        )
        self.assertEqual(session_auth.device_id, "device-from-session")

    def test_parse_auth_accepts_browser_export_headers_and_cookies(self) -> None:
        auth = server_mod.parse_auth_value(
            json.dumps(
                {
                    "request": {
                        "headers": [
                            {
                                "name": "Cookie",
                                "value": "cf_clearance=cf-token; oai-did=device-id; __Secure-next-auth.session-token=session-token",
                            },
                            {"name": "User-Agent", "value": "Browser UA"},
                            {"name": "Authorization", "value": "Bearer access-from-header"},
                            {"name": "openai-sentinel-chat-requirements-token", "value": "requirements-token"},
                            {"name": "Content-Length", "value": "999"},
                        ]
                    }
                }
            )
        )

        self.assertIn("cf_clearance=cf-token", auth.cookie)
        self.assertEqual(auth.access_token, "access-from-header")
        self.assertEqual(auth.device_id, "device-id")
        self.assertEqual(auth.user_agent, "Browser UA")
        self.assertEqual(auth.protocol_headers["openai-sentinel-chat-requirements-token"], "requirements-token")
        self.assertNotIn("Content-Length", auth.protocol_headers)

    def test_parse_auth_accepts_top_level_cookie_array_export(self) -> None:
        auth = server_mod.parse_auth_value(
            json.dumps(
                [
                    {"domain": ".chatgpt.com", "name": "oai-did", "value": "device-id"},
                    {
                        "domain": ".chatgpt.com",
                        "name": "__Secure-next-auth.session-token.0",
                        "value": "part0",
                    },
                    {
                        "domain": ".chatgpt.com",
                        "name": "__Secure-next-auth.session-token.1",
                        "value": "part1",
                    },
                    {"domain": ".chatgpt.com", "name": "__cf_bm", "value": "cf-cookie"},
                ]
            )
        )

        self.assertIn("oai-did=device-id", auth.cookie)
        self.assertIn("__Secure-next-auth.session-token.0=part0", auth.cookie)
        self.assertIn("__Secure-next-auth.session-token.1=part1", auth.cookie)
        self.assertIn("__cf_bm=cf-cookie", auth.cookie)
        self.assertEqual(auth.device_id, "device-id")
        self.assertIsNone(auth.access_token)

    def test_get_session_info_prefers_refreshed_access_token(self) -> None:
        class _Session:
            def get(self, url, timeout=30):
                class _Response:
                    ok = True

                    def json(self):
                        return {"accessToken": "fresh-access", "user": {"id": "user"}}

                return _Response()

        _info, token, error = server_mod.get_session_info(_Session(), {}, "stale-access")

        self.assertEqual(error, "")
        self.assertEqual(token, "fresh-access")

    def test_device_id_is_stable_when_cookie_has_no_oai_did(self) -> None:
        first = server_mod.get_device_id(None, "__Secure-next-auth.session-token=session-token")
        second = server_mod.get_device_id(None, "__Secure-next-auth.session-token=session-token")

        self.assertEqual(first, second)

    def test_merge_dynamic_headers_from_warmup_response(self) -> None:
        class _Response:
            headers = {"openai-sentinel-proof-token": "proof-from-header"}

            def json(self):
                return {"chat_requirements_token": "requirements-from-body"}

        headers = {}

        server_mod._merge_dynamic_headers_from_response(headers, _Response())

        self.assertEqual(headers["openai-sentinel-proof-token"], "proof-from-header")
        self.assertEqual(headers["openai-sentinel-chat-requirements-token"], "requirements-from-body")

    def test_prompt_network_error_retry_delay_defaults_to_two_seconds(self) -> None:
        self.assertEqual(server_mod._prompt_network_error_retry_delay_sec({}), 2.0)

    def test_warmup_uses_default_pow_script_when_bootstrap_times_out(self) -> None:
        class _Session:
            headers = {}
            post_called = False

            def get(self, url, headers=None, timeout=None):
                raise RuntimeError(
                    "Failed to perform, curl: (28) Connection timed out after 8001 milliseconds"
                )

            def post(self, url, headers=None, data=None, timeout=None):
                self.post_called = True
                if url.endswith("/backend-api/sentinel/chat-requirements/prepare"):
                    return _Response(
                        {
                            "prepare_token": "prepare-token",
                            "proofofwork": {"required": False},
                            "turnstile": {"required": False},
                        }
                    )
                if url.endswith("/backend-api/sentinel/chat-requirements/finalize"):
                    return _Response({"token": "sentinel-token"})
                raise AssertionError(f"unexpected POST {url}")

        class _Response:
            ok = True
            status_code = 200
            text = "{}"

            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        session = _Session()
        with server_mod._bootstrap_cache_lock:
            server_mod._bootstrap_script_sources = []
            server_mod._bootstrap_data_build = ""
            server_mod._bootstrap_timestamp = 0

        dynamic_headers = {}
        server_mod.warmup_chat_requirements(
            session,
            {"base_url": "https://chatgpt.com", "warmup_timeout_sec": 0.5, "bootstrap_timeout_sec": 0.5},
            "access-token",
            "device-id",
            dynamic_headers,
        )

        self.assertTrue(session.post_called)
        self.assertEqual(dynamic_headers["openai-sentinel-chat-requirements-token"], "sentinel-token")
        with server_mod._bootstrap_cache_lock:
            server_mod._bootstrap_script_sources = []
            server_mod._bootstrap_data_build = ""
            server_mod._bootstrap_timestamp = 0

    def test_warmup_does_not_fall_back_to_legacy_when_sentinel_returns_false(self) -> None:
        calls = []
        original_fetch_sentinel_token = server_mod._fetch_sentinel_token
        original_legacy_warmup = server_mod._legacy_warmup_chat_requirements
        try:
            server_mod._fetch_sentinel_token = lambda *args, **kwargs: False

            def legacy_warmup(*args, **kwargs):
                calls.append("legacy")

            server_mod._legacy_warmup_chat_requirements = legacy_warmup
            with self.assertRaises(server_mod.ChatGPTWebError) as caught:
                server_mod.warmup_chat_requirements(
                    object(),
                    {"base_url": "https://chatgpt.com"},
                    "access-token",
                    "device-id",
                    {},
                )
        finally:
            server_mod._fetch_sentinel_token = original_fetch_sentinel_token
            server_mod._legacy_warmup_chat_requirements = original_legacy_warmup

        self.assertEqual(calls, [])
        self.assertEqual(caught.exception.error_code, "challenge_required")
        self.assertIn("Sentinel 验证失败", str(caught.exception))

    def test_warmup_reclassifies_sentinel_curl_error_as_network_error(self) -> None:
        original_fetch_sentinel_token = server_mod._fetch_sentinel_token
        try:
            def fail_sentinel(*args, **kwargs):
                raise server_mod.ChatGPTWebError(
                    "ChatGPT Web Sentinel 验证失败：Failed to perform, curl: (28) Connection timed out after 2015 milliseconds.",
                    status_code=502,
                    error_code="challenge_required",
                )

            server_mod._fetch_sentinel_token = fail_sentinel
            with self.assertRaises(server_mod.ChatGPTWebError) as caught:
                server_mod.warmup_chat_requirements(
                    object(),
                    {"base_url": "https://chatgpt.com"},
                    "access-token",
                    "device-id",
                    {},
                )
        finally:
            server_mod._fetch_sentinel_token = original_fetch_sentinel_token

        self.assertEqual(caught.exception.error_code, "upstream_network_error")
        self.assertIn("上游网络连接失败，请检查代理/网络", str(caught.exception))

    def test_plain_curl_timeout_text_is_connectivity_error(self) -> None:
        exc = RuntimeError("Failed to perform, curl: (28) Connection timed out after 8001 milliseconds")

        self.assertTrue(server_mod.is_upstream_connectivity_error(exc))

    def test_upstream_request_timeout_splits_connect_and_read_timeout(self) -> None:
        self.assertEqual(
            server_mod._upstream_request_timeout(
                {"request_connect_timeout_sec": 6, "request_timeout_sec": 12}
            ),
            (6.0, 12.0),
        )

    def test_prepare_chatgpt_request_accepts_long_image_friendly_timeout(self) -> None:
        old_api_key = server_mod.CONFIG.get("api_key")
        server_mod.CONFIG["api_key"] = "session-token"
        prepared = None
        try:
            prepared = server_mod._prepare_chatgpt_request(
                [{"role": "user", "content": "生成一张图片"}],
                "gpt-5-5-thinking",
                {"upstream_timeout_sec": 420},
            )
            self.assertEqual(prepared.config["request_timeout_sec"], 420.0)
        finally:
            server_mod.CONFIG["api_key"] = old_api_key
            if prepared is not None:
                prepared.session.close()

    def test_conversation_append_enabled_defaults_to_false(self) -> None:
        self.assertFalse(server_mod._conversation_append_enabled(None))
        self.assertFalse(server_mod._conversation_append_enabled({}))
        self.assertFalse(server_mod._conversation_append_enabled({"enable_conversation_append": False}))
        self.assertTrue(server_mod._conversation_append_enabled({"enable_conversation_append": True}))

    def test_messages_to_prompt_preserves_roles_for_multi_message_request(self) -> None:
        prompt = server_mod.messages_to_prompt(
            [
                {"role": "system", "content": "你是助手"},
                {"role": "user", "content": [{"type": "text", "text": "你好"}]},
                {"role": "assistant", "content": "您好"},
            ]
        )

        self.assertIn("System: 你是助手", prompt)
        self.assertIn("User: 你好", prompt)
        self.assertIn("Assistant: 您好", prompt)

    def test_parse_data_image_url_and_extract_message_images(self) -> None:
        mime_type, data = server_mod._parse_data_image_url(self.SAMPLE_PNG_DATA_URL)  # type: ignore[misc]

        self.assertEqual(mime_type, "image/png")
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))

        images = server_mod._extract_message_images(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看图"},
                        {"type": "image_url", "image_url": {"url": self.SAMPLE_PNG_DATA_URL}},
                    ],
                }
            ]
        )

        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].mime_type, "image/png")
        self.assertEqual(images[0].width, 1)
        self.assertEqual(images[0].height, 1)
        self.assertEqual(images[0].data, data)

    def test_messages_to_prompt_ignores_base64_image_payload_text(self) -> None:
        prompt = server_mod.messages_to_prompt(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请描述图片"},
                        {"type": "image_url", "image_url": {"url": self.SAMPLE_PNG_DATA_URL}},
                    ],
                }
            ]
        )

        self.assertEqual(prompt, "请描述图片")
        self.assertNotIn("data:image/png;base64", prompt)

    def test_build_body_supports_multimodal_uploaded_images(self) -> None:
        uploaded = [
            server_mod.ChatGPTWebUploadedImage(
                file_id="file-123",
                filename="image-1.png",
                mime_type="image/png",
                size_bytes=123,
                width=1,
                height=1,
            )
        ]

        body = server_mod.build_body(
            "请描述图片",
            {"timezone": "Asia/Shanghai", "timezone_offset_min": -480},
            model="gpt-5-3",
            uploaded_images=uploaded,
        )

        message = body["messages"][0]
        self.assertEqual(message["content"]["content_type"], "multimodal_text")
        self.assertEqual(message["content"]["parts"][0]["asset_pointer"], "file-service://file-123")
        self.assertEqual(message["content"]["parts"][-1], "请描述图片")
        self.assertEqual(message["metadata"]["attachments"][0]["id"], "file-123")
        self.assertEqual(message["metadata"]["attachments"][0]["mimeType"], "image/png")

    def test_build_body_omits_empty_text_part_for_image_only_message(self) -> None:
        uploaded = [
            server_mod.ChatGPTWebUploadedImage(
                file_id="file-123",
                filename="image-1.png",
                mime_type="image/png",
                size_bytes=123,
                width=1,
                height=1,
            )
        ]

        body = server_mod.build_body(
            "",
            {"timezone": "Asia/Shanghai", "timezone_offset_min": -480},
            model="gpt-5-3",
            uploaded_images=uploaded,
        )

        parts = body["messages"][0]["content"]["parts"]
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["asset_pointer"], "file-service://file-123")

    def test_image_generation_body_uses_chat_style_uploaded_images(self) -> None:
        uploaded = [
            server_mod.ChatGPTWebUploadedImage(
                file_id="file-123",
                filename="image-1.png",
                mime_type="image/png",
                size_bytes=123,
                width=1,
                height=1,
            )
        ]

        body = server_mod._build_image_generation_body(
            "参考附件生成图片",
            {"timezone": "Asia/Shanghai", "timezone_offset_min": -480},
            "gpt-image-2",
            uploaded,
        )

        message = body["messages"][0]
        image_part = message["content"]["parts"][0]
        self.assertEqual(message["content"]["content_type"], "multimodal_text")
        self.assertEqual(image_part["asset_pointer"], "file-service://file-123")
        self.assertEqual(image_part["size_bytes"], 123)
        self.assertNotIn("content_type", image_part)
        self.assertEqual(message["content"]["parts"][-1], "参考附件生成图片")
        self.assertEqual(message["metadata"]["attachments"][0]["id"], "file-123")
        self.assertEqual(message["metadata"]["attachments"][0]["mimeType"], "image/png")

    def test_prepare_image_conversation_includes_uploaded_images_in_partial_query(self) -> None:
        uploaded = [
            server_mod.ChatGPTWebUploadedImage(
                file_id="file-123",
                filename="image-1.png",
                mime_type="image/png",
                size_bytes=123,
                width=1,
                height=1,
            )
        ]
        captured = {}

        class _Response:
            ok = True
            status_code = 200
            headers = {}
            text = "{}"

            def json(self):
                return {"conduit_token": "conduit-token"}

        class _Session:
            def post(self, url, headers=None, data=None, timeout=None):
                captured["url"] = url
                captured["headers"] = dict(headers or {})
                captured["payload"] = json.loads(data)
                return _Response()

        token = server_mod._prepare_image_conversation(
            _Session(),
            {"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
            "参考附件生成图片",
            "access-token",
            "device-id",
            {},
            "gpt-image-2",
            uploaded,
        )

        self.assertEqual(token, "conduit-token")
        partial_query = captured["payload"]["partial_query"]
        image_part = partial_query["content"]["parts"][0]
        self.assertEqual(image_part["asset_pointer"], "file-service://file-123")
        self.assertNotIn("content_type", image_part)
        self.assertEqual(partial_query["metadata"]["attachments"][0]["id"], "file-123")

    def test_build_protocol_headers_preserves_explicit_target_path(self) -> None:
        headers = server_mod.build_protocol_headers(
            {"base_url": "https://chatgpt.com"},
            "access-token",
            "device-id",
            {
                "x-openai-target-path": "/backend-api/f/conversation",
                "x-openai-target-route": "/backend-api/f/conversation",
            },
            accept="application/json",
            target_path="/backend-api/files",
        )

        self.assertEqual(headers["x-openai-target-path"], "/backend-api/files")
        self.assertEqual(headers["x-openai-target-route"], "/backend-api/files")

    def test_request_conversation_removes_client_version_headers_instead_of_sending_none(self) -> None:
        class _Session:
            def __init__(self):
                self.captured = None

            def post(self, url, headers=None, data=None, timeout=None, stream=None):
                self.captured = {
                    "url": url,
                    "headers": headers,
                    "data": data,
                    "timeout": timeout,
                    "stream": stream,
                }
                return object()

        session = _Session()
        response = server_mod.request_conversation(
            session,
            {
                "base_url": "https://chatgpt.com",
                "request_timeout_sec": 10,
                "request_connect_timeout_sec": 3,
            },
            {"action": "next", "messages": []},
            "access-token",
            "device-id",
            {},
        )

        self.assertIs(response.__class__, object)
        self.assertNotIn("OAI-Client-Version", session.captured["headers"])
        self.assertNotIn("OAI-Client-Build-Number", session.captured["headers"])
        self.assertNotIn("oai-client-version", session.captured["headers"])
        self.assertNotIn("oai-client-build-number", session.captured["headers"])

    def test_upload_chatgpt_images_uses_three_step_flow(self) -> None:
        class _Response:
            def __init__(self, payload, ok=True, status_code=200, text=""):
                self._payload = payload
                self.ok = ok
                self.status_code = status_code
                self.text = text or json.dumps(payload, ensure_ascii=False)
                self.headers = {}

            def json(self):
                return self._payload

        class _Session:
            def __init__(self):
                self.headers = {"User-Agent": "UA"}
                self.calls = []

            def post(self, url, headers=None, data=None, timeout=None):
                self.calls.append(("post", url, headers, data, timeout))
                if url.endswith("/backend-api/files"):
                    return _Response({"file_id": "file-abc", "upload_url": "https://upload.example/blob"})
                if url.endswith("/backend-api/files/file-abc/uploaded"):
                    return _Response({"download_url": "https://cdn.example/file-abc"})
                raise AssertionError(f"unexpected POST {url}")

            def put(self, url, headers=None, data=None, timeout=None):
                self.calls.append(("put", url, headers, data, timeout))
                if url == "https://upload.example/blob":
                    return _Response({}, ok=True, text="")
                raise AssertionError(f"unexpected PUT {url}")

        session = _Session()
        image_bytes = base64.b64decode(self.SAMPLE_PNG_DATA_URL.split(",", 1)[1])
        uploaded = server_mod._upload_chatgpt_images(
            session,
            {"base_url": "https://chatgpt.com", "request_timeout_sec": 10, "request_connect_timeout_sec": 3},
            "access-token",
            "device-id",
            {},
            [
                server_mod.ChatGPTWebImageUpload(
                    filename="image-1.png",
                    mime_type="image/png",
                    data=image_bytes,
                    width=1,
                    height=1,
                )
            ],
        )

        self.assertEqual(len(uploaded), 1)
        self.assertEqual(uploaded[0].file_id, "file-abc")
        self.assertEqual([call[0] for call in session.calls], ["post", "put", "post"])
        self.assertTrue(session.calls[0][1].endswith("/backend-api/files"))
        self.assertEqual(session.calls[1][1], "https://upload.example/blob")
        self.assertTrue(session.calls[2][1].endswith("/backend-api/files/file-abc/uploaded"))

    def test_complete_chatgpt_web_builds_multimodal_request_when_images_present(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_parse = server_mod.parse_sse_events

        class _Session:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class _Response:
            ok = True

            def close(self):
                pass

        captured = {}

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-3",
            )

        def fake_warmup(session, config, access_token, device_id, dynamic_headers, **_kwargs):
            captured["warmed"] = (access_token, device_id)
            return False

        def fake_upload(session, config, access_token, device_id, dynamic_headers, images):
            captured["upload_count"] = len(images)
            return [
                server_mod.ChatGPTWebUploadedImage(
                    file_id="file-xyz",
                    filename="image-1.png",
                    mime_type="image/png",
                    size_bytes=68,
                    width=1,
                    height=1,
                )
            ]

        def fake_request(session, config, body, access_token, device_id, dynamic_headers):
            captured["body"] = body
            return _Response()

        def fake_parse(_events, _context=None):
            return "图片已收到", "conv-1", "msg-1", False

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = fake_warmup
            server_mod._upload_chatgpt_images = fake_upload
            server_mod.request_conversation_with_requirements = fake_request
            server_mod.parse_sse_events = fake_parse

            result = server_mod.complete_chatgpt_web(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "请描述图片"},
                            {"type": "image_url", "image_url": {"url": self.SAMPLE_PNG_DATA_URL}},
                        ],
                    }
                ],
                "gpt-5-3",
                {},
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod.parse_sse_events = original_parse

        self.assertEqual(result.text, "图片已收到")
        self.assertEqual(captured["upload_count"], 1)
        self.assertEqual(captured["body"]["messages"][0]["content"]["content_type"], "multimodal_text")
        self.assertEqual(captured["body"]["messages"][0]["content"]["parts"][0]["asset_pointer"], "file-service://file-xyz")
        self.assertEqual(captured["body"]["messages"][0]["content"]["parts"][-1], "请描述图片")

    def test_complete_chatgpt_web_falls_back_when_sse_text_is_whitespace(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_parse = server_mod.parse_sse_events
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        class _Session:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class _Response:
            ok = True

            def close(self):
                pass

        captured = {}

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        def fake_request(session, config, body, access_token, device_id, dynamic_headers):
            captured["user_message_id"] = body["messages"][0]["id"]
            return _Response()

        def fake_fetch_retry(*args, **kwargs):
            captured["fetch_kwargs"] = kwargs
            return "OK", "assistant-msg"

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = fake_request
            server_mod.parse_sse_events = lambda _events, _context=None: (" \n\t", "conv-1", None, False)
            server_mod._fetch_conversation_with_retry = fake_fetch_retry

            result = server_mod.complete_chatgpt_web(
                [{"role": "user", "content": "请只回复 OK"}],
                "gpt-5-5-thinking",
                {},
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod.parse_sse_events = original_parse
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(result.text, "OK")
        self.assertEqual(result.conversation_id, "conv-1")
        self.assertEqual(result.message_id, "assistant-msg")
        self.assertEqual(captured["fetch_kwargs"]["user_message_id"], captured["user_message_id"])

    def test_iter_sse_lines_decodes_byte_stream_without_unicode_mode(self) -> None:
        class _Response:
            requested_decode_unicode = None
            requested_chunk_size = None

            def iter_lines(self, chunk_size=None, decode_unicode=False):
                self.requested_chunk_size = chunk_size
                self.requested_decode_unicode = decode_unicode
                if decode_unicode:
                    raise NotImplementedError()
                return iter(
                    [
                        b": keep-alive",
                        "data: {\"type\":\"ping\"}".encode("utf-8"),
                        "data: [DONE]".encode("utf-8"),
                    ]
                )

        response = _Response()

        self.assertEqual(list(server_mod.iter_sse_lines(response)), ['{"type":"ping"}', "[DONE]"])
        self.assertFalse(response.requested_decode_unicode)
        self.assertEqual(response.requested_chunk_size, 1)

    def test_iter_sse_lines_falls_back_when_response_iter_lines_does_not_accept_chunk_size(self) -> None:
        class _Response:
            calls = []

            def iter_lines(self, decode_unicode=False):
                self.calls.append(decode_unicode)
                return iter(
                    [
                        "data: {\"type\":\"ping\"}".encode("utf-8"),
                    ]
                )

        response = _Response()

        self.assertEqual(list(server_mod.iter_sse_lines(response)), ['{"type":"ping"}'])
        self.assertEqual(response.calls, [False])

    def test_fetch_conversation_retries_with_fresh_session_after_request_failure(self) -> None:
        class _FailingSession:
            headers = {"Cookie": "session=token"}
            proxies = {}

            def get(self, url, headers=None, timeout=None):
                raise RuntimeError("TLS connect error")

        class _RetrySession:
            headers = {}
            proxies = {}

            def get(self, url, headers=None, timeout=None):
                class _Response:
                    ok = True

                    def json(self):
                        return {
                            "mapping": {
                                "user-msg": {"message": {"id": "user-msg"}, "parent": None},
                                "assistant-node": {
                                    "parent": "user-msg",
                                    "message": {
                                        "id": "assistant-msg",
                                        "author": {"role": "assistant"},
                                        "content": {"content_type": "text", "parts": ["兜底成功"]},
                                        "recipient": "all",
                                        "create_time": 1,
                                    },
                                },
                            }
                        }

                return _Response()

            def close(self):
                pass

        original_make_curl_retry_session = server_mod._make_curl_retry_session
        try:
            server_mod._make_curl_retry_session = lambda source: _RetrySession()
            text, message_id = server_mod.fetch_conversation(
                _FailingSession(),
                {"base_url": "https://chatgpt.com"},
                "conversation-id",
                "access-token",
                user_message_id="user-msg",
            )
        finally:
            server_mod._make_curl_retry_session = original_make_curl_retry_session

        self.assertEqual(text, "兜底成功")
        self.assertEqual(message_id, "assistant-msg")

    def test_fetch_conversation_raises_network_error_after_retry_failures(self) -> None:
        class _FailingSession:
            headers = {}
            proxies = {}

            def get(self, url, headers=None, timeout=None):
                raise server_mod.requests.ConnectTimeout("Failed to connect")

        original_make_curl_retry_session = server_mod._make_curl_retry_session
        original_make_requests_retry_session = server_mod._make_requests_retry_session
        try:
            server_mod._make_curl_retry_session = lambda source: None
            server_mod._make_requests_retry_session = lambda source: _FailingSession()
            with self.assertRaises(server_mod.ChatGPTWebError) as caught:
                server_mod.fetch_conversation(
                    _FailingSession(),
                    {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
                    "conversation-id",
                    "access-token",
                )
        finally:
            server_mod._make_curl_retry_session = original_make_curl_retry_session
            server_mod._make_requests_retry_session = original_make_requests_retry_session

        self.assertEqual(caught.exception.error_code, "upstream_network_error")

    def test_fetch_conversation_tries_f_endpoint_after_inaccessible_primary_endpoint(self) -> None:
        class _Response:
            def __init__(self, ok, status_code, payload):
                self.ok = ok
                self.status_code = status_code
                self.headers = {}
                self.text = json.dumps(payload, ensure_ascii=False)
                self._payload = payload

            def json(self):
                return self._payload

        class _Session:
            headers = {}
            proxies = {}

            def __init__(self):
                self.urls = []

            def get(self, url, headers=None, timeout=None):
                self.urls.append((url, headers))
                if "/backend-api/conversation/" in url and "/backend-api/f/conversation/" not in url:
                    return _Response(
                        False,
                        404,
                        {
                            "detail": {
                                "message": "你无权访问此对话。确保已登录正确的账户，或请对话所有者向你发送共享链接。",
                                "code": "conversation_inaccessible",
                                "can_retry": False,
                            }
                        },
                    )
                return _Response(
                    True,
                    200,
                    {
                        "mapping": {
                            "user-msg": {"message": {"id": "user-msg"}, "parent": None},
                            "assistant-node": {
                                "parent": "user-msg",
                                "message": {
                                    "id": "assistant-msg",
                                    "author": {"role": "assistant"},
                                    "content": {"content_type": "text", "parts": ["兜底成功"]},
                                    "recipient": "all",
                                    "create_time": 1,
                                },
                            },
                        }
                    },
                )

        session = _Session()
        text, message_id = server_mod.fetch_conversation(
            session,
            {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
            "conversation-id",
            "access-token",
            user_message_id="user-msg",
        )

        self.assertEqual(text, "兜底成功")
        self.assertEqual(message_id, "assistant-msg")
        self.assertEqual(len(session.urls), 2)
        self.assertIn("/backend-api/f/conversation/conversation-id", session.urls[1][0])
        self.assertEqual(
            session.urls[1][1]["x-openai-target-path"],
            "/backend-api/f/conversation/conversation-id",
        )

    def test_fetch_conversation_inlines_generated_file_asset_image(self) -> None:
        image_bytes = base64.b64decode(self.SAMPLE_PNG_DATA_URL.split(",", 1)[1])

        class _Response:
            def __init__(self, payload=None, content=b"", content_type="application/json"):
                self.ok = True
                self.status_code = 200
                self.headers = {"content-type": content_type}
                self._payload = payload or {}
                self.content = content
                self.text = json.dumps(self._payload, ensure_ascii=False) if payload is not None else ""

            def json(self):
                return self._payload

        class _Session:
            headers = {}
            proxies = {}

            def __init__(self):
                self.urls = []

            def get(self, url, headers=None, timeout=None):
                self.urls.append(url)
                if "/backend-api/files/file-generated-image/download" in url:
                    return _Response(content=image_bytes, content_type="image/png")
                return _Response(
                    {
                        "mapping": {
                            "user-msg": {"message": {"id": "user-msg"}, "parent": None},
                            "assistant-node": {
                                "parent": "user-msg",
                                "message": {
                                    "id": "assistant-msg",
                                    "author": {"role": "assistant"},
                                    "content": {
                                        "content_type": "multimodal_text",
                                        "parts": [
                                            {
                                                "asset_pointer": "file-service://file-generated-image",
                                                "mime_type": "image/png",
                                            }
                                        ],
                                    },
                                    "recipient": "all",
                                    "create_time": 1,
                                },
                            },
                        }
                    }
                )

        session = _Session()
        text, message_id = server_mod.fetch_conversation(
            session,
            {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
            "conversation-id",
            "access-token",
            user_message_id="user-msg",
        )

        self.assertEqual(message_id, "assistant-msg")
        self.assertIn("![generated image](data:image/png;base64,", text)
        self.assertNotIn("/backend-api/files/file-generated-image/download", text)

    def test_fetch_conversation_retry_waits_for_generated_image_instead_of_input_attachment(self) -> None:
        input_bytes = self._png_bytes(32, 32)
        generated_bytes = self._png_bytes(640, 640)

        class _Response:
            def __init__(self, payload=None, content=b"", content_type="application/json"):
                self.ok = True
                self.status_code = 200
                self.headers = {"content-type": content_type}
                self._payload = payload or {}
                self.content = content
                self.text = json.dumps(self._payload, ensure_ascii=False) if payload is not None else ""

            def json(self):
                return self._payload

        class _Session:
            headers = {}
            proxies = {}

            def __init__(self):
                self.conversation_calls = 0
                self.urls = []

            def get(self, url, headers=None, timeout=None):
                self.urls.append(url)
                if "/backend-api/files/file-input/download" in url:
                    raise AssertionError("不应下载并返回用户上传的参考图")
                if "/backend-api/files/file-generated/download" in url:
                    return _Response(content=generated_bytes, content_type="image/png")
                if "/backend-api/conversation/" in url or "/backend-api/f/conversation/" in url:
                    self.conversation_calls += 1
                    mapping = {
                        "user-msg": {
                            "message": {
                                "id": "user-msg",
                                "author": {"role": "user"},
                                "content": {
                                    "content_type": "multimodal_text",
                                    "parts": [
                                        {
                                            "asset_pointer": "file-service://file-input",
                                            "mime_type": "image/png",
                                            "size_bytes": len(input_bytes),
                                        },
                                        "参考附件生成图片",
                                    ],
                                },
                                "metadata": {
                                    "attachments": [
                                        {
                                            "id": "file-input",
                                            "mimeType": "image/png",
                                            "size": len(input_bytes),
                                        }
                                    ]
                                },
                                "recipient": "all",
                                "create_time": 10,
                            },
                            "parent": None,
                        }
                    }
                    if self.conversation_calls >= 2:
                        mapping["image-tool-node"] = {
                            "parent": "user-msg",
                            "message": {
                                "id": "generated-msg",
                                "author": {"role": "tool"},
                                "status": "finished_successfully",
                                "content": {
                                    "content_type": "multimodal_text",
                                    "parts": [
                                        {
                                            "content_type": "image_asset_pointer",
                                            "asset_pointer": "file-service://file-generated",
                                            "mime_type": "image/png",
                                        }
                                    ],
                                },
                                "metadata": {"image_gen_title": "生成图"},
                                "recipient": "all",
                                "create_time": 20,
                            },
                        }
                    return _Response({"mapping": mapping})
                return _Response()

        session = _Session()
        text, message_id = server_mod._fetch_conversation_with_retry(
            session,
            {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
            "conversation-id",
            "access-token",
            user_message_id="user-msg",
            attempts=3,
            interval_sec=0,
        )

        self.assertEqual(message_id, "generated-msg")
        self.assertIn(base64.b64encode(generated_bytes).decode("ascii"), text)
        self.assertNotIn(base64.b64encode(input_bytes).decode("ascii"), text)
        self.assertEqual(session.conversation_calls, 2)
        self.assertFalse(any("file-input/download" in url for url in session.urls))

    def test_chatgpt_image_download_accept_header_prefers_qt_supported_formats(self) -> None:
        image_bytes = base64.b64decode(self.SAMPLE_PNG_DATA_URL.split(",", 1)[1])

        class _Response:
            ok = True
            status_code = 200
            headers = {"content-type": "image/png"}
            content = image_bytes
            text = ""

        class _Session:
            def __init__(self):
                self.headers_seen = []

            def get(self, url, headers=None, timeout=None):
                self.headers_seen.append(headers or {})
                return _Response()

        session = _Session()
        downloaded = server_mod._download_chatgpt_file_image(
            session,
            {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
            "file-service://file-generated-image",
            "access-token",
            None,
        )

        self.assertIsNotNone(downloaded)
        accept = session.headers_seen[0]["Accept"]
        self.assertTrue(accept.startswith("image/png,image/jpeg,image/webp"))
        self.assertNotIn("image/avif", accept)

    def test_inline_chatgpt_file_images_can_localize_downloaded_image(self) -> None:
        image_bytes = base64.b64decode(self.SAMPLE_PNG_DATA_URL.split(",", 1)[1])

        class _Response:
            ok = True
            status_code = 200
            headers = {"content-type": "image/png"}
            content = image_bytes
            text = ""

        class _Session:
            def get(self, url, headers=None, timeout=None):
                return _Response()

        with tempfile.TemporaryDirectory() as tmpdir:
            rendered = server_mod._inline_chatgpt_file_images(
                "![generated image](https://chatgpt.com/backend-api/files/file-generated-image/download)",
                _Session(),
                {
                    "base_url": "https://chatgpt.com",
                    "fallback_fetch_timeout_sec": 0.5,
                    "localize_generated_images": True,
                    "generated_images_dir": tmpdir,
                },
                "access-token",
                None,
            )
            saved_files = list(Path(tmpdir).glob("*.png"))
            saved_name = saved_files[0].name if saved_files else ""
            saved_bytes = saved_files[0].read_bytes() if saved_files else b""

        self.assertEqual(len(saved_files), 1)
        self.assertEqual(saved_bytes, image_bytes)
        self.assertIn("![generated image](file:///", rendered)
        self.assertIn(saved_name, rendered)
        self.assertNotIn("data:image/png;base64", rendered)

    def test_fetch_conversation_inlines_generated_sediment_tool_image(self) -> None:
        image_bytes = self._png_bytes(640, 640)

        class _Response:
            def __init__(self, payload=None, content=b"", content_type="application/json"):
                self.ok = True
                self.status_code = 200
                self.headers = {"content-type": content_type}
                self._payload = payload or {}
                self.content = content
                self.text = json.dumps(self._payload, ensure_ascii=False) if payload is not None else ""

            def json(self):
                return self._payload

        class _Session:
            headers = {}
            proxies = {}

            def __init__(self):
                self.urls = []

            def get(self, url, headers=None, timeout=None):
                self.urls.append(url)
                if "/backend-api/images/bootstrap" in url:
                    return _Response(
                        {
                            "thumbnail_url": (
                                "https://chatgpt.com/backend-api/estuary/content"
                                "?id=prefix%23file_00000000199c7206a3da12a5dc3a62df%23icon"
                                "&ts=20628&p=igh&cid=1&sig=signed&v=0"
                            ),
                            "content_url": (
                                "https://chatgpt.com/backend-api/estuary/content"
                                "?id=file_00000000199c7206a3da12a5dc3a62df"
                                "&ts=20628&p=fs&cid=1&sig=signed-original&v=0"
                            ),
                        }
                    )
                if "/backend-api/estuary/content" in url and "file_00000000199c7206a3da12a5dc3a62df" in url:
                    return _Response(content=image_bytes, content_type="image/png")
                return _Response(
                    {
                        "mapping": {
                            "user-msg": {"message": {"id": "user-msg"}, "parent": None},
                            "assistant-parent": {
                                "parent": "user-msg",
                                "message": {
                                    "id": "assistant-parent",
                                    "author": {"role": "assistant"},
                                    "content": {"content_type": "text", "parts": [""]},
                                    "recipient": "all",
                                    "create_time": 1,
                                },
                            },
                            "image-tool-node": {
                                "parent": "assistant-parent",
                                "message": {
                                    "id": "4ae9d116-8c86-4a2f-9339-ada9d42bc3a7",
                                    "author": {"role": "tool"},
                                    "status": "finished_successfully",
                                    "content": {
                                        "content_type": "multimodal_text",
                                        "parts": [
                                            {
                                                "content_type": "image_asset_pointer",
                                                "asset_pointer": "sediment://file_00000000199c7206a3da12a5dc3a62df",
                                                "width": 1086,
                                                "height": 1448,
                                            }
                                        ],
                                    },
                                    "metadata": {"image_gen_title": "温暖阳光下的温柔微笑"},
                                    "recipient": "all",
                                    "create_time": 2,
                                },
                            },
                        }
                    }
                )

        session = _Session()
        text, message_id = server_mod.fetch_conversation(
            session,
            {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
            "conversation-id",
            "access-token",
            user_message_id="user-msg",
        )

        self.assertEqual(message_id, "4ae9d116-8c86-4a2f-9339-ada9d42bc3a7")
        self.assertIn("![generated image](data:image/png;base64,", text)
        self.assertNotIn("/backend-api/estuary/content", text)
        downloaded_urls = [url for url in session.urls if "/backend-api/estuary/content" in url]
        self.assertEqual(len(downloaded_urls), 1)
        self.assertIn("id=file_00000000199c7206a3da12a5dc3a62df", downloaded_urls[0])
        self.assertIn("p=fs", downloaded_urls[0])

    def test_fetch_conversation_prefers_latest_assistant_choice_message(self) -> None:
        original_download = server_mod._download_chatgpt_estuary_image
        image_a = self._png_bytes(640, 640)
        image_b = self._png_bytes(800, 600)

        class _Response:
            ok = True
            status_code = 200
            text = ""

            def json(self):
                return {
                    "mapping": {
                        "user-msg": {"message": {"id": "user-msg"}, "parent": None},
                        "image-a": {
                            "parent": "user-msg",
                            "message": {
                                "id": "image-a",
                                "author": {"role": "tool"},
                                "status": "finished_successfully",
                                "content": {
                                    "content_type": "multimodal_text",
                                    "parts": [
                                        {
                                            "content_type": "image_asset_pointer",
                                            "asset_pointer": "sediment://file_choice_a",
                                            "width": 640,
                                            "height": 640,
                                        }
                                    ],
                                },
                                "metadata": {"async_task_type": "image_gen"},
                                "recipient": "all",
                                "create_time": 1,
                            },
                        },
                        "image-b": {
                            "parent": "user-msg",
                            "message": {
                                "id": "image-b",
                                "author": {"role": "tool"},
                                "status": "finished_successfully",
                                "content": {
                                    "content_type": "multimodal_text",
                                    "parts": [
                                        {
                                            "content_type": "image_asset_pointer",
                                            "asset_pointer": "sediment://file_choice_b",
                                            "width": 800,
                                            "height": 600,
                                        }
                                    ],
                                },
                                "metadata": {"async_task_type": "image_gen"},
                                "recipient": "all",
                                "create_time": 2,
                            },
                        },
                        "choice": {
                            "parent": "image-b",
                            "message": {
                                "id": "choice-msg",
                                "author": {"role": "assistant"},
                                "status": "finished_successfully",
                                "content": {
                                    "content_type": "text",
                                    "parts": ["请选择 Image 1 还是 Image 2。"],
                                },
                                "metadata": {},
                                "recipient": "all",
                                "create_time": 3,
                            },
                        },
                    }
                }

        class _Session:
            headers = {}
            proxies = {}

            def get(self, url, headers=None, timeout=None):
                return _Response()

        def fake_download(session, config, file_id, access_token, dynamic_headers, *, conversation_id=None, signed_url=None):
            data = image_a if file_id == "file_choice_a" else image_b
            return "image/png", data

        try:
            server_mod._download_chatgpt_estuary_image = fake_download
            text, message_id = server_mod.fetch_conversation(
                _Session(),
                {"base_url": "https://chatgpt.com"},
                "conversation-id",
                "access-token",
                user_message_id="user-msg",
            )
        finally:
            server_mod._download_chatgpt_estuary_image = original_download

        self.assertEqual(message_id, "choice-msg")
        self.assertIn("请选择 Image 1 还是 Image 2。", text)
        self.assertNotIn("![generated image](data:image/png;base64,", text)

    def test_fetch_conversation_prefers_sediment_attachment_original_over_icon(self) -> None:
        thumbnail_bytes = self._png_bytes(48, 48)
        original_bytes = self._png_bytes(640, 640)

        class _Response:
            def __init__(self, payload=None, content=b"", content_type="application/json"):
                self.ok = True
                self.status_code = 200
                self.headers = {"content-type": content_type}
                self._payload = payload or {}
                self.content = content
                self.text = json.dumps(self._payload, ensure_ascii=False) if payload is not None else ""

            def json(self):
                return self._payload

        class _Session:
            headers = {}
            proxies = {}

            def __init__(self):
                self.urls = []
                self.requests = []

            def get(self, url, headers=None, timeout=None):
                self.urls.append(url)
                self.requests.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
                if "/backend-api/conversation/conversation-id/attachment/file_00000000original/download" in url:
                    return _Response({"download_url": "https://cdn.example/original.png"})
                if url == "https://cdn.example/original.png":
                    return _Response(content=original_bytes, content_type="image/png")
                if "/backend-api/images/bootstrap" in url:
                    return _Response(
                        {
                            "thumbnail_url": (
                                "https://chatgpt.com/backend-api/estuary/content"
                                "?id=prefix%23file_00000000original%23icon&ts=20628&p=igh&sig=thumb"
                            )
                        }
                    )
                if "/backend-api/estuary/content" in url:
                    return _Response(content=thumbnail_bytes, content_type="image/png")
                return _Response(
                    {
                        "mapping": {
                            "user-msg": {"message": {"id": "user-msg"}, "parent": None},
                            "image-tool-node": {
                                "parent": "user-msg",
                                "message": {
                                    "id": "assistant-msg",
                                    "author": {"role": "tool"},
                                    "status": "finished_successfully",
                                    "content": {
                                        "content_type": "multimodal_text",
                                        "parts": [
                                            {
                                                "content_type": "image_asset_pointer",
                                                "asset_pointer": "sediment://file_00000000original",
                                                "width": 1024,
                                                "height": 1024,
                                            }
                                        ],
                                    },
                                    "metadata": {"image_gen_title": "原图"},
                                    "recipient": "all",
                                    "create_time": 2,
                                },
                            },
                        }
                    }
                )

        session = _Session()
        text, message_id = server_mod.fetch_conversation(
            session,
            {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
            "conversation-id",
            "access-token",
            user_message_id="user-msg",
        )

        self.assertEqual(message_id, "assistant-msg")
        self.assertIn(base64.b64encode(original_bytes).decode("ascii"), text)
        self.assertNotIn(base64.b64encode(thumbnail_bytes).decode("ascii"), text)
        self.assertTrue(any("/attachment/file_00000000original/download" in url for url in session.urls))
        self.assertIn("https://cdn.example/original.png", session.urls)
        self.assertFalse(any("/backend-api/images/bootstrap" in url for url in session.urls))
        attachment_request = next(
            item for item in session.requests
            if "/backend-api/conversation/conversation-id/attachment/file_00000000original/download" in item["url"]
        )
        self.assertEqual(attachment_request["headers"]["Accept"], "application/json")
        self.assertEqual(
            attachment_request["headers"]["x-openai-target-path"],
            "/backend-api/conversation/conversation-id/attachment/file_00000000original/download",
        )
        self.assertEqual(
            attachment_request["headers"]["x-openai-target-route"],
            "/backend-api/conversation/conversation-id/attachment/file_00000000original/download",
        )
        self.assertGreaterEqual(float(attachment_request["timeout"]), 60.0)

    def test_fetch_conversation_uses_tool_original_when_latest_assistant_is_icon(self) -> None:
        thumbnail_bytes = self._png_bytes(48, 48)
        original_bytes = self._png_bytes(640, 640)

        class _Response:
            def __init__(
                self,
                payload=None,
                content=b"",
                content_type="application/json",
                *,
                ok=True,
                status_code=200,
            ):
                self.ok = ok
                self.status_code = status_code
                self.headers = {"content-type": content_type}
                self._payload = payload or {}
                self.content = content
                self.text = json.dumps(self._payload, ensure_ascii=False) if payload is not None else ""

            def json(self):
                return self._payload

        class _Session:
            headers = {}
            proxies = {}

            def __init__(self):
                self.urls = []

            def get(self, url, headers=None, timeout=None):
                self.urls.append(url)
                if "/backend-api/conversation/conversation-id/attachment/file_00000000icon/download" in url:
                    return _Response(
                        {"detail": "File stream access denied."},
                        ok=False,
                        status_code=403,
                    )
                if "/backend-api/conversation/conversation-id/attachment/file_00000000original/download" in url:
                    return _Response({"download_url": "https://cdn.example/original.png"})
                if url == "https://cdn.example/original.png":
                    return _Response(content=original_bytes, content_type="image/png")
                if "/backend-api/images/bootstrap" in url:
                    return _Response(
                        {
                            "thumbnail_url": (
                                "https://chatgpt.com/backend-api/estuary/content"
                                "?id=prefix%23file_00000000icon%23icon&ts=20628&p=igh&sig=thumb"
                            )
                        }
                    )
                if "/backend-api/estuary/content" in url:
                    return _Response(content=thumbnail_bytes, content_type="image/png")
                return _Response(
                    {
                        "mapping": {
                            "user-msg": {"message": {"id": "user-msg"}, "parent": None},
                            "image-tool-node": {
                                "parent": "user-msg",
                                "message": {
                                    "id": "tool-image-msg",
                                    "author": {"role": "tool"},
                                    "status": "finished_successfully",
                                    "content": {
                                        "content_type": "multimodal_text",
                                        "parts": [
                                            {
                                                "content_type": "image_asset_pointer",
                                                "asset_pointer": "sediment://file_00000000original",
                                                "width": 1024,
                                                "height": 1024,
                                            }
                                        ],
                                    },
                                    "metadata": {"image_gen_title": "原图"},
                                    "recipient": "all",
                                    "create_time": 2,
                                },
                            },
                            "assistant-placeholder": {
                                "parent": "image-tool-node",
                                "message": {
                                    "id": "assistant-placeholder",
                                    "author": {"role": "assistant"},
                                    "status": "finished_successfully",
                                    "content": {
                                        "content_type": "multimodal_text",
                                        "parts": [
                                            {
                                                "content_type": "image_asset_pointer",
                                                "asset_pointer": "sediment://file_00000000icon",
                                                "width": 48,
                                                "height": 48,
                                            }
                                        ],
                                    },
                                    "recipient": "all",
                                    "create_time": 3,
                                },
                            },
                        }
                    }
                )

        session = _Session()
        text, message_id = server_mod.fetch_conversation(
            session,
            {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
            "conversation-id",
            "access-token",
            user_message_id="user-msg",
            assistant_message_id="assistant-placeholder",
        )

        self.assertEqual(message_id, "tool-image-msg")
        self.assertIn(base64.b64encode(original_bytes).decode("ascii"), text)
        self.assertNotIn(base64.b64encode(thumbnail_bytes).decode("ascii"), text)
        self.assertTrue(any("/attachment/file_00000000icon/download" in url for url in session.urls))
        self.assertTrue(any("/attachment/file_00000000original/download" in url for url in session.urls))
        self.assertIn("https://cdn.example/original.png", session.urls)

    def test_download_chatgpt_estuary_image_rejects_thumbnail_only_result(self) -> None:
        thumbnail_bytes = self._png_bytes(48, 48)

        class _Response:
            def __init__(self, payload=None, content=b"", content_type="application/json"):
                self.ok = True
                self.status_code = 200
                self.headers = {"content-type": content_type}
                self._payload = payload or {}
                self.content = content
                self.text = json.dumps(self._payload, ensure_ascii=False) if payload is not None else ""

            def json(self):
                return self._payload

        class _Session:
            headers = {}
            proxies = {}

            def __init__(self):
                self.requests = []

            def get(self, url, headers=None, timeout=None):
                self.requests.append({"url": url, "headers": dict(headers or {})})
                if "/backend-api/conversation/conversation-id/attachment/file_00000000thumb/download" in url:
                    return _Response({})
                if "/backend-api/images/bootstrap" in url:
                    return _Response(
                        {
                            "thumbnail_url": (
                                "https://chatgpt.com/backend-api/estuary/content"
                                "?id=prefix%23file_00000000thumb%23icon&ts=20628&p=igh&sig=thumb"
                            )
                        }
                    )
                if "/backend-api/estuary/content" in url:
                    return _Response(content=thumbnail_bytes, content_type="image/png")
                raise AssertionError(f"unexpected GET {url}")

        session = _Session()
        downloaded = server_mod._download_chatgpt_estuary_image(
            session,
            {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
            "sediment://file_00000000thumb",
            "access-token",
            None,
            conversation_id="conversation-id",
        )

        self.assertIsNone(downloaded)
        for request in session.requests:
            if "/backend-api/images/bootstrap" in request["url"] or "/backend-api/estuary/content" in request["url"]:
                self.assertEqual(request["headers"]["Referer"], "https://chatgpt.com/c/conversation-id")

    def test_download_chatgpt_estuary_image_uses_bootstrap_original_with_different_file_id(self) -> None:
        thumbnail_bytes = self._png_bytes(48, 48)
        original_bytes = self._png_bytes(918, 1713)

        class _Response:
            def __init__(self, payload=None, content=b"", content_type="application/json"):
                self.ok = True
                self.status_code = 200
                self.headers = {"content-type": content_type}
                self._payload = payload or {}
                self.content = content
                self.text = json.dumps(self._payload, ensure_ascii=False) if payload is not None else ""

            def json(self):
                return self._payload

        class _Session:
            headers = {}
            proxies = {}

            def __init__(self):
                self.urls = []

            def get(self, url, headers=None, timeout=None):
                self.urls.append(url)
                if "/backend-api/conversation/conversation-id/attachment/file_00000000icon/download" in url:
                    return _Response({})
                if "/backend-api/images/bootstrap" in url:
                    return _Response(
                        {
                            "thumbnail_url": (
                                "https://chatgpt.com/backend-api/estuary/content"
                                "?id=prefix%23file_00000000icon%23icon&ts=20628&p=igh&sig=thumb"
                            ),
                            "content_url": (
                                "https://chatgpt.com/backend-api/estuary/content"
                                "?id=file_00000000original&ts=20628&p=fs&sig=original"
                            ),
                        }
                    )
                if "file_00000000icon" in url:
                    return _Response(content=thumbnail_bytes, content_type="image/png")
                if "file_00000000original" in url:
                    return _Response(content=original_bytes, content_type="image/png")
                raise AssertionError(f"unexpected GET {url}")

        session = _Session()
        downloaded = server_mod._download_chatgpt_estuary_image(
            session,
            {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
            "sediment://file_00000000icon",
            "access-token",
            None,
            conversation_id="conversation-id",
        )

        self.assertIsNotNone(downloaded)
        assert downloaded is not None
        self.assertEqual(downloaded[1], original_bytes)
        self.assertTrue(any("file_00000000original" in url for url in session.urls))

    def test_download_chatgpt_estuary_image_uses_browser_like_headers_for_signed_content(self) -> None:
        original_bytes = self._png_bytes(1024, 1024)

        class _Response:
            def __init__(self, payload=None, content=b"", content_type="application/json", *, ok=True, status_code=200):
                self.ok = ok
                self.status_code = status_code
                self.headers = {"content-type": content_type}
                self._payload = payload or {}
                self.content = content
                self.text = json.dumps(self._payload, ensure_ascii=False) if payload is not None else ""

            def json(self):
                return self._payload

        class _Session:
            headers = {"Cookie": "session=ok"}
            proxies = {}

            def __init__(self):
                self.estuary_headers = None

            def get(self, url, headers=None, timeout=None):
                clean_headers = dict(headers or {})
                if "/backend-api/conversation/conversation-id/attachment/file_00000000original/download" in url:
                    return _Response({})
                if "/backend-api/images/bootstrap" in url:
                    return _Response(
                        {
                            "content_url": (
                                "https://chatgpt.com/backend-api/estuary/content"
                                "?id=file_00000000original&ts=20628&p=fs&sig=original"
                            )
                        }
                    )
                if "/backend-api/estuary/content" in url:
                    self.estuary_headers = clean_headers
                    if "Authorization" in clean_headers or "x-openai-target-path" in clean_headers:
                        return _Response(
                            {"detail": "File stream access denied."},
                            ok=False,
                            status_code=403,
                        )
                    return _Response(content=original_bytes, content_type="image/png")
                raise AssertionError(f"unexpected GET {url}")

        session = _Session()
        downloaded = server_mod._download_chatgpt_estuary_image(
            session,
            {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
            "sediment://file_00000000original",
            "access-token",
            {"x-openai-target-path": "/should-not-leak"},
            conversation_id="conversation-id",
        )

        self.assertIsNotNone(downloaded)
        assert downloaded is not None
        self.assertEqual(downloaded[1], original_bytes)
        assert session.estuary_headers is not None
        self.assertEqual(session.estuary_headers["Referer"], "https://chatgpt.com/c/conversation-id")
        self.assertNotIn("Authorization", session.estuary_headers)
        self.assertNotIn("x-openai-target-path", session.estuary_headers)

    def test_fetch_conversation_with_retry_waits_through_inaccessible_conversation(self) -> None:
        calls = []
        sleeps = []
        original_fetch_conversation = server_mod.fetch_conversation
        original_sleep = server_mod.time.sleep
        try:
            def fake_fetch_conversation(
                session,
                config,
                conversation_id,
                access_token,
                dynamic_headers=None,
                user_message_id=None,
                assistant_message_id=None,
            ):
                calls.append(
                    {
                        "conversation_id": conversation_id,
                        "access_token": access_token,
                        "user_message_id": user_message_id,
                    }
                )
                if len(calls) == 1:
                    raise server_mod.ChatGPTWebUpstreamError(
                        "当前账号无法继续访问该 ChatGPT Web 会话。详情：conversation_inaccessible",
                        429,
                        "model_limited",
                    )
                return "兜底成功", "assistant-msg"

            server_mod.fetch_conversation = fake_fetch_conversation
            server_mod.time.sleep = lambda seconds: sleeps.append(seconds)

            text, message_id = server_mod._fetch_conversation_with_retry(
                object(),
                {"base_url": "https://chatgpt.com"},
                "conversation-id",
                "access-token",
                user_message_id="user-msg",
                attempts=3,
                interval_sec=0.01,
            )
        finally:
            server_mod.fetch_conversation = original_fetch_conversation
            server_mod.time.sleep = original_sleep

        self.assertEqual(text, "兜底成功")
        self.assertEqual(message_id, "assistant-msg")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["user_message_id"], "user-msg")
        self.assertEqual(sleeps, [0.01])

    def test_fetch_conversation_with_retry_can_suppress_transient_poll_logs(self) -> None:
        calls = []
        logs = []
        original_fetch_conversation = server_mod.fetch_conversation
        original_sleep = server_mod.time.sleep
        original_log = server_mod.log
        try:
            def fake_fetch_conversation(
                session,
                config,
                conversation_id,
                access_token,
                dynamic_headers=None,
                user_message_id=None,
                assistant_message_id=None,
                suppress_transient_logs=False,
            ):
                calls.append({"suppress_transient_logs": suppress_transient_logs})
                if len(calls) == 1:
                    raise server_mod.ChatGPTWebUpstreamError(
                        "当前账号无法继续访问该 ChatGPT Web 会话。详情：conversation_inaccessible",
                        404,
                        "conversation_inaccessible",
                    )
                return "兜底成功", "assistant-msg"

            server_mod.fetch_conversation = fake_fetch_conversation
            server_mod.time.sleep = lambda seconds: None
            server_mod.log = lambda message: logs.append(str(message))

            text, message_id = server_mod._fetch_conversation_with_retry(
                object(),
                {"base_url": "https://chatgpt.com"},
                "conversation-id",
                "access-token",
                attempts=2,
                interval_sec=0.01,
                suppress_transient_logs=True,
            )
        finally:
            server_mod.fetch_conversation = original_fetch_conversation
            server_mod.time.sleep = original_sleep
            server_mod.log = original_log

        self.assertEqual(text, "兜底成功")
        self.assertEqual(message_id, "assistant-msg")
        self.assertEqual([call["suppress_transient_logs"] for call in calls], [True, True])
        self.assertFalse(any("暂时无法访问会话" in entry or "遇到异常" in entry for entry in logs))

    def test_fetch_conversation_with_retry_can_wait_for_stable_text(self) -> None:
        responses = [
            ("半截", "assistant-msg"),
            ("完整回答", "assistant-msg"),
            ("完整回答", "assistant-msg"),
            ("完整回答", "assistant-msg"),
        ]
        original_fetch_conversation = server_mod.fetch_conversation
        original_sleep = server_mod.time.sleep
        try:
            def fake_fetch_conversation(*args, **kwargs):
                return responses.pop(0)

            server_mod.fetch_conversation = fake_fetch_conversation
            server_mod.time.sleep = lambda seconds: None

            text, message_id = server_mod._fetch_conversation_with_retry(
                object(),
                {"base_url": "https://chatgpt.com"},
                "conversation-id",
                "access-token",
                attempts=4,
                interval_sec=0.01,
                stable_after_text_attempts=2,
            )
        finally:
            server_mod.fetch_conversation = original_fetch_conversation
            server_mod.time.sleep = original_sleep

        self.assertEqual(text, "完整回答")
        self.assertEqual(message_id, "assistant-msg")
        self.assertEqual(responses, [])

    def test_fetch_conversation_with_retry_prefers_more_complete_snapshot_before_stable(self) -> None:
        responses = [
            ("今日行业三大趋势\n① 中国 AI 已进入“十强争霸”阶段\n-包\n小米 AI\n等头部玩家。", "assistant-msg"),
            ("今日行业三大趋势\n① 中国 AI 已进入“十强争霸”阶段\n\nDeepSeek\n阿里 Qwen\n智谱 Z.ai\n小米 AI\n\n等头部玩家。", "assistant-msg"),
            ("今日行业三大趋势\n① 中国 AI 已进入“十强争霸”阶段\n\nDeepSeek\n阿里 Qwen\n智谱 Z.ai\n小米 AI\n\n等头部玩家。", "assistant-msg"),
            ("今日行业三大趋势\n① 中国 AI 已进入“十强争霸”阶段\n\nDeepSeek\n阿里 Qwen\n智谱 Z.ai\n小米 AI\n\n等头部玩家。", "assistant-msg"),
        ]
        original_fetch_conversation = server_mod.fetch_conversation
        original_sleep = server_mod.time.sleep
        try:
            def fake_fetch_conversation(*args, **kwargs):
                return responses.pop(0)

            server_mod.fetch_conversation = fake_fetch_conversation
            server_mod.time.sleep = lambda seconds: None

            text, message_id = server_mod._fetch_conversation_with_retry(
                object(),
                {"base_url": "https://chatgpt.com"},
                "conversation-id",
                "access-token",
                attempts=4,
                interval_sec=0.01,
                stable_after_text_attempts=2,
            )
        finally:
            server_mod.fetch_conversation = original_fetch_conversation
            server_mod.time.sleep = original_sleep

        self.assertIn("DeepSeek", text)
        self.assertIn("智谱 Z.ai", text)
        self.assertNotIn("-包", text)
        self.assertEqual(message_id, "assistant-msg")
        self.assertEqual(responses, [])

    def test_prefer_more_complete_text_keeps_current_when_new_snapshot_is_not_more_complete(self) -> None:
        current = (
            "今日 AI 新闻汇总（2026年6月24日）\n"
            "1. 美国政府加强对前沿 AI 模型的安全审查\n"
            "美国政府正推动更多头部 AI 公司自愿接受联邦安全评估。\n"
            "影响：\nAI监管开始从“原则讨论”进入“实际审查”阶段。"
        )
        fetched = (
            "今日 AI 新闻汇总（2026年6月24日）\n"
            "1. 美国政府加强对前沿 AI 模型的安全审查\n"
            "美国政府正推动更多头部 AI 公司自愿接受联邦安全评估。\n"
            "影响：\nAI监管开始从“原则讨论”进入“实际审查”阶段。"
        )
        self.assertEqual(server_mod._prefer_more_complete_text(current, fetched), current)

    def test_prefer_more_complete_text_accepts_mid_section_completion(self) -> None:
        current = (
            "今日 AI 新闻汇总（2026年6月24日）\n"
            "2. 韩国出台金融行业 AI 新规\n\n"
            "韩国金融监管、风险控制、投资建议等场景中使用AI时，需要保留人工监督机制。\n\n"
            "影响：\n全球金融AI监管正在趋严。"
        )
        fetched = (
            "今日 AI 新闻汇总（2026年6月24日）\n"
            "2. 韩国出台金融行业 AI 新规\n\n"
            "韩国金融监管机构发布新原则，明确要求：\n\n"
            "AI可以辅助决策，但最终责任必须由人承担。\n\n"
            "金融机构在贷款审批、风险控制、投资建议等场景中使用AI时，需要保留人工监督机制。\n\n"
            "影响：\n全球金融AI监管正在趋严。"
        )
        self.assertEqual(server_mod._prefer_more_complete_text(current, fetched), fetched)

    def test_fetch_conversation_with_retry_returns_inline_image_without_stability_wait(self) -> None:
        calls = []
        original_fetch_conversation = server_mod.fetch_conversation
        original_sleep = server_mod.time.sleep
        try:
            def fake_fetch_conversation(*args, **kwargs):
                calls.append(1)
                return "![generated image](data:image/png;base64,AAA)", "assistant-msg"

            server_mod.fetch_conversation = fake_fetch_conversation
            server_mod.time.sleep = lambda seconds: self.fail("图片已内联时不应继续等待稳定轮询")

            text, message_id = server_mod._fetch_conversation_with_retry(
                object(),
                {"base_url": "https://chatgpt.com"},
                "conversation-id",
                "access-token",
                attempts=4,
                interval_sec=0.01,
                stable_after_text_attempts=2,
            )
        finally:
            server_mod.fetch_conversation = original_fetch_conversation
            server_mod.time.sleep = original_sleep

        self.assertEqual(text, "![generated image](data:image/png;base64,AAA)")
        self.assertEqual(message_id, "assistant-msg")
        self.assertEqual(len(calls), 1)

    def test_iter_delta_events_records_stream_handoff_context(self) -> None:
        context = {}
        events = [
            json.dumps(
                {
                    "type": "resume_conversation_token",
                    "kind": "topic",
                    "token": "resume-token",
                    "conversation_id": "conv-1",
                }
            ),
            json.dumps(
                {
                    "type": "stream_handoff",
                    "conversation_id": "conv-1",
                    "turn_exchange_id": "turn-1",
                    "options": [
                        {"type": "resume_sse_endpoint", "topic_id": "conversation-turn-sse"},
                        {"type": "subscribe_ws_topic", "topic_id": "conversation-turn-ws"},
                    ],
                }
            )
        ]

        self.assertEqual(list(server_mod.iter_delta_events(events, context)), [])
        self.assertTrue(context["handoff"])
        self.assertEqual(context["conversation_id"], "conv-1")
        self.assertEqual(context["handoff_token"], "resume-token")
        self.assertEqual(context["turn_exchange_id"], "turn-1")
        self.assertEqual(server_mod._handoff_topic_id(context), "conversation-turn-ws")

    def test_handoff_ws_encoded_item_can_render_generated_image(self) -> None:
        encoded_item = "\n".join(
            [
                "event: delta_encoding",
                'data: "v1"',
                "",
                "event: delta",
                "data: "
                + json.dumps(
                    {
                        "o": "add",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "finished_successfully",
                                "content": {
                                    "content_type": "multimodal_text",
                                    "parts": [
                                        {
                                            "asset_pointer": "file-service://file-generated-image",
                                            "mime_type": "image/png",
                                        }
                                    ],
                                },
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                "",
            ]
        )
        ws_message = {
            "type": "message",
            "topic_id": "conversation-turn-1",
            "payload": {
                "payload": {
                    "conversation_id": "conv-1",
                    "encoded_item": encoded_item,
                }
            },
        }

        events, terminal = server_mod._handoff_event_strings_from_ws_message(ws_message, "conversation-turn-1")
        text, conversation_id, message_id, _handoff = server_mod.parse_sse_events(events, {})

        self.assertTrue(terminal)
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(message_id, "assistant-msg")
        self.assertEqual(
            text,
            "![generated image](https://chatgpt.com/backend-api/files/file-generated-image/download)",
        )

    def test_handoff_ws_encoded_item_accepts_nested_payload_variants(self) -> None:
        encoded_item = "\n".join(
            [
                "event: delta",
                "data: "
                + json.dumps(
                    {
                        "o": "add",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "finished_successfully",
                                "content": {"content_type": "text", "parts": ["OK"]},
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                "",
            ]
        )
        ws_message = {
            "type": "message",
            "topic_id": "conversation-turn-1",
            "payload": {
                "conversation_id": "conv-1",
                "encoded_item": encoded_item,
            },
        }

        events, terminal = server_mod._handoff_event_strings_from_ws_message(ws_message, "conversation-turn-1")
        text, conversation_id, message_id, _handoff = server_mod.parse_sse_events(events, {})

        self.assertTrue(terminal)
        self.assertEqual(text, "OK")
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(message_id, "assistant-msg")

    def test_handoff_ws_subscribe_catchups_are_ordered_by_offset(self) -> None:
        def encoded_event(payload: dict[str, object]) -> str:
            return "\n".join(
                [
                    "event: delta",
                    "data: " + json.dumps(payload, ensure_ascii=False),
                    "",
                ]
            )

        catchups = [
            {
                "type": "message",
                "topic_id": "conversation-turn-1",
                "offset": "2",
                "payload": {
                    "encoded_item": encoded_event(
                        {"o": "append", "p": "/content/parts/0", "v": "John Jumper 从 Google DeepMind 转投 Anthropic。"}
                    )
                },
            },
            {
                "type": "message",
                "topic_id": "conversation-turn-1",
                "offset": "0",
                "payload": {
                    "conversation_id": "conv-1",
                    "encoded_item": encoded_event(
                        {
                            "o": "add",
                            "conversation_id": "conv-1",
                            "v": {
                                "message": {
                                    "id": "assistant-msg",
                                    "author": {"role": "assistant"},
                                    "status": "in_progress",
                                    "content": {"content_type": "text", "parts": [""]},
                                }
                            },
                        }
                    ),
                },
            },
            {
                "type": "message",
                "topic_id": "conversation-turn-1",
                "offset": "1",
                "payload": {
                    "encoded_item": encoded_event(
                        {"o": "append", "p": "/content/parts/0", "v": "1. 美国政府要求 Meta 接受前沿 AI 安全审查\n"}
                    )
                },
            },
        ]
        item = {"type": "reply", "reply": {"type": "subscribe", "catchups": catchups}}

        messages, _should_subscribe, _terminal = server_mod._handoff_messages_from_ws_item(item, "conversation-turn-1")
        events: list[str] = []
        for message in messages:
            new_events, _message_terminal = server_mod._handoff_event_strings_from_ws_message(message, "conversation-turn-1")
            events.extend(new_events)
        text, conversation_id, message_id, _handoff = server_mod.parse_sse_events(events, {})

        self.assertEqual(
            text,
            "1. 美国政府要求 Meta 接受前沿 AI 安全审查\nJohn Jumper 从 Google DeepMind 转投 Anthropic。",
        )
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(message_id, "assistant-msg")

    def test_content_part_replace_overwrites_instead_of_merging_old_text(self) -> None:
        events = [
            json.dumps(
                {
                    "o": "add",
                    "conversation_id": "conv-1",
                    "v": {
                        "message": {
                            "id": "assistant-msg",
                            "author": {"role": "assistant"},
                            "status": "in_progress",
                            "content": {"content_type": "text", "parts": ["202"]},
                        }
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {"o": "replace", "p": "/content/parts/0", "v": "2024 年诺奖得主 John Jumper"},
                ensure_ascii=False,
            ),
        ]

        text, conversation_id, message_id, _handoff = server_mod.parse_sse_events(events, {})

        self.assertEqual(text, "2024 年诺奖得主 John Jumper")
        self.assertNotIn("2022024", text)
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(message_id, "assistant-msg")

    def test_parse_sse_events_keeps_visible_text_across_ignored_messages(self) -> None:
        events = [
            json.dumps(
                {
                    "o": "add",
                    "conversation_id": "conv-1",
                    "v": {
                        "message": {
                            "id": "assistant-msg",
                            "author": {"role": "assistant"},
                            "status": "in_progress",
                            "content": {"content_type": "text", "parts": ["第一段。"]},
                        }
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "o": "add",
                    "v": {
                        "message": {
                            "id": "tool-msg",
                            "author": {"role": "assistant"},
                            "recipient": "python",
                            "content": {"content_type": "code", "parts": ["print('hidden')"]},
                        }
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "o": "replace",
                    "v": {
                        "message": {
                            "id": "assistant-msg",
                            "author": {"role": "assistant"},
                            "status": "finished_successfully",
                            "content": {"content_type": "text", "parts": ["第二段。"]},
                        }
                    },
                },
                ensure_ascii=False,
            ),
        ]

        text, conversation_id, message_id, _handoff = server_mod.parse_sse_events(events, {})

        self.assertEqual(text, "第一段。第二段。")
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(message_id, "assistant-msg")

    def test_handoff_ws_waits_for_followup_frames_after_terminal_snapshot(self) -> None:
        def encoded_event(payload: dict[str, object]) -> str:
            return "\n".join(
                [
                    "event: delta",
                    "data: " + json.dumps(payload, ensure_ascii=False),
                    "",
                ]
            )

        first_text = "You've hit the Free plan limit for image generations requests. You can"
        rest_text = " create more images when the limit resets in 21 hours and 12 minutes."
        frames = [
            json.dumps([{"type": "reply", "reply": {"type": "connect"}}], ensure_ascii=False),
            json.dumps(
                [
                    {
                        "type": "reply",
                        "reply": {
                            "type": "subscribe",
                            "catchups": [
                                {
                                    "type": "message",
                                    "topic_id": "conversation-turn-1",
                                    "offset": "0",
                                    "payload": {
                                        "conversation_id": "conv-1",
                                        "encoded_item": encoded_event(
                                            {
                                                "o": "add",
                                                "conversation_id": "conv-1",
                                                "v": {
                                                    "message": {
                                                        "id": "assistant-msg",
                                                        "author": {"role": "assistant"},
                                                        "status": "finished_successfully",
                                                        "content": {"content_type": "text", "parts": [first_text]},
                                                    }
                                                },
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ],
                ensure_ascii=False,
            ),
            json.dumps(
                [
                    {
                        "type": "message",
                        "topic_id": "conversation-turn-1",
                        "offset": "1",
                        "payload": {
                            "encoded_item": encoded_event(
                                {"o": "append", "p": "/content/parts/0", "v": rest_text}
                            )
                        },
                    }
                ],
                ensure_ascii=False,
            ),
        ]

        class FakeWebSocket:
            def __init__(self, pending_frames: list[str]) -> None:
                self.pending_frames = list(pending_frames)
                self.sent: list[str] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

            async def send(self, data: str) -> None:
                self.sent.append(data)

            async def recv(self) -> str:
                if self.pending_frames:
                    return self.pending_frames.pop(0)
                await asyncio.sleep(5)
                return ""

        class FakeWebsocketsModule:
            def connect(self, _url: str, **_kwargs):
                return FakeWebSocket(frames)

        old_websockets = sys.modules.get("websockets")
        sys.modules["websockets"] = FakeWebsocketsModule()
        try:
            session = type("Session", (), {"headers": {}, "cookies": {}})()
            text, conversation_id, message_id = asyncio.run(
                server_mod._consume_handoff_ws_topic(
                    "wss://example.invalid/celsius",
                    "conversation-turn-1",
                    session,
                    {"handoff_terminal_quiet_sec": 1.0, "handoff_idle_return_sec": 8.0},
                    None,
                    30.0,
                )
            )
        finally:
            if old_websockets is None:
                sys.modules.pop("websockets", None)
            else:
                sys.modules["websockets"] = old_websockets

        self.assertEqual(text, first_text + rest_text)
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(message_id, "assistant-msg")

    def test_consume_handoff_ws_topic_returns_to_polling_when_subscribed_without_events(self) -> None:
        frames = [
            json.dumps(
                [
                    {
                        "type": "reply",
                        "reply": {"type": "connect"},
                    }
                ],
                ensure_ascii=False,
            )
        ]
        sockets: list[object] = []

        class FakeWebSocket:
            def __init__(self, pending_frames: list[str]) -> None:
                self.pending_frames = list(pending_frames)
                self.sent: list[str] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

            async def send(self, data: str) -> None:
                self.sent.append(data)

            async def recv(self) -> str:
                if self.pending_frames:
                    return self.pending_frames.pop(0)
                await asyncio.sleep(5)
                return ""

        class FakeWebsocketsModule:
            def connect(self, _url: str, **_kwargs):
                socket = FakeWebSocket(frames)
                sockets.append(socket)
                return socket

        old_websockets = sys.modules.get("websockets")
        sys.modules["websockets"] = FakeWebsocketsModule()
        try:
            session = type("Session", (), {"headers": {}, "cookies": {}})()
            text, conversation_id, message_id = asyncio.run(
                server_mod._consume_handoff_ws_topic(
                    "wss://example.invalid/celsius",
                    "conversation-turn-no-events",
                    session,
                    {"handoff_no_event_return_sec": 0.02},
                    None,
                    30.0,
                )
            )
        finally:
            if old_websockets is None:
                sys.modules.pop("websockets", None)
            else:
                sys.modules["websockets"] = old_websockets

        self.assertIsNone(text)
        self.assertIsNone(conversation_id)
        self.assertIsNone(message_id)
        self.assertEqual(len(sockets), 1)
        self.assertTrue(any('"type": "subscribe"' in item for item in sockets[0].sent))

    def test_consume_handoff_ws_topic_returns_to_polling_on_heartbeat_only_frames(self) -> None:
        connect_frame = json.dumps(
            [
                {
                    "type": "reply",
                    "reply": {"type": "connect"},
                }
            ],
            ensure_ascii=False,
        )
        heartbeat_frame = json.dumps(
            [
                {
                    "type": "message",
                    "topic_id": "conversation-turn-heartbeat",
                    "payload": {"type": "heartbeat"},
                }
            ],
            ensure_ascii=False,
        )
        sockets: list[object] = []

        class FakeWebSocket:
            def __init__(self) -> None:
                self.sent: list[str] = []
                self.connected = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

            async def send(self, data: str) -> None:
                self.sent.append(data)

            async def recv(self) -> str:
                await asyncio.sleep(0)
                if not self.connected:
                    self.connected = True
                    return connect_frame
                return heartbeat_frame

        class FakeWebsocketsModule:
            def connect(self, _url: str, **_kwargs):
                socket = FakeWebSocket()
                sockets.append(socket)
                return socket

        old_websockets = sys.modules.get("websockets")
        sys.modules["websockets"] = FakeWebsocketsModule()
        try:
            session = type("Session", (), {"headers": {}, "cookies": {}})()
            text, conversation_id, message_id = asyncio.run(
                server_mod._consume_handoff_ws_topic(
                    "wss://example.invalid/celsius",
                    "conversation-turn-heartbeat",
                    session,
                    {"handoff_no_event_return_sec": 0.02},
                    None,
                    30.0,
                )
            )
        finally:
            if old_websockets is None:
                sys.modules.pop("websockets", None)
            else:
                sys.modules["websockets"] = old_websockets

        self.assertIsNone(text)
        self.assertIsNone(conversation_id)
        self.assertIsNone(message_id)
        self.assertEqual(len(sockets), 1)
        self.assertTrue(any('"type": "subscribe"' in item for item in sockets[0].sent))

    def test_iter_handoff_topic_chunks_streams_snapshot_updates_progressively(self) -> None:
        original_fetch_handoff = server_mod._fetch_handoff_topic_text

        partial_text = "AI时代学习的利用AI放大自身能力的人。"
        full_text = (
            "AI时代学习的核心已经从“记住知识”转变为“驾驭知识”。\n\n"
            "过去：\n学习 = 获取信息 + 记忆信息\n\n"
            "现在：\n学习 = 提出问题 + 理解原理 + 利用AI解决问题\n\n"
            "真正领先的，往往是既懂专业领域、又懂如何利用AI放大自身能力的人。"
        )

        def fake_fetch_handoff(_session, _config, _access_token, _dynamic_headers, _context):
            callback = server_mod._get_handoff_stream_snapshot_callback()
            self.assertIsNotNone(callback)
            callback(partial_text, "conv-1", "assistant-msg")
            callback(full_text, "conv-1", "assistant-msg")
            return full_text, "conv-1", "assistant-msg"

        result = server_mod.HandoffStreamResult()
        try:
            server_mod._fetch_handoff_topic_text = fake_fetch_handoff
            chunks = list(
                server_mod._iter_handoff_topic_chunks(
                    session=type("Session", (), {"headers": {}, "cookies": {}})(),
                    config={"base_url": "https://chatgpt.com"},
                    access_token="access-token",
                    dynamic_headers={},
                    context={"handoff": True, "conversation_id": "conv-1", "message_id": "assistant-msg"},
                    initial_text="",
                    model_id="gpt-5-5-thinking",
                    result=result,
                )
            )
        finally:
            server_mod._fetch_handoff_topic_text = original_fetch_handoff

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, partial_text)
        self.assertEqual(result.text, full_text)
        self.assertEqual(result.conversation_id, "conv-1")
        self.assertEqual(result.message_id, "assistant-msg")

    def test_iter_handoff_topic_chunks_skips_non_append_rewrite_snapshots(self) -> None:
        original_fetch_handoff = server_mod._fetch_handoff_topic_text

        first_text = "调试标记：DBG-THINKING-20260624-2227\n\n# 今日 AI 新闻\n\n1. Google 人才"
        rewritten_text = "调试标记：DBG-THINKING-20260624-2227\n\n# 今日 AI 新闻\n\n1. Google 人才流动\n\n摘要：John Jumper 加入 Anthropic。"
        later_rewrite = "调试标记：DBG-THINKING-2026064 年诺奖得主 John Jumper 加入 Anthropic。"

        def fake_fetch_handoff(_session, _config, _access_token, _dynamic_headers, _context):
            callback = server_mod._get_handoff_stream_snapshot_callback()
            self.assertIsNotNone(callback)
            callback(first_text, "conv-1", "assistant-msg")
            callback(rewritten_text, "conv-1", "assistant-msg")
            callback(later_rewrite, "conv-1", "assistant-msg")
            return later_rewrite, "conv-1", "assistant-msg"

        result = server_mod.HandoffStreamResult()
        context = {"handoff": True, "conversation_id": "conv-1", "message_id": "assistant-msg"}
        try:
            server_mod._fetch_handoff_topic_text = fake_fetch_handoff
            chunks = list(
                server_mod._iter_handoff_topic_chunks(
                    session=type("Session", (), {"headers": {}, "cookies": {}})(),
                    config={"base_url": "https://chatgpt.com"},
                    access_token="access-token",
                    dynamic_headers={},
                    context=context,
                    initial_text="",
                    model_id="gpt-5-5-thinking",
                    result=result,
                )
            )
        finally:
            server_mod._fetch_handoff_topic_text = original_fetch_handoff

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].text, first_text)
        self.assertEqual(chunks[0].snapshot_text, first_text)
        self.assertEqual(chunks[1].text, rewritten_text[len(first_text):])
        self.assertEqual(chunks[1].snapshot_text, rewritten_text)
        self.assertTrue(context["handoff_rewrite_seen"])
        self.assertEqual(result.text, later_rewrite)
        self.assertNotIn(later_rewrite, [chunk.text for chunk in chunks])

    def test_prefer_more_complete_text_prefers_cleaner_conversation_text_over_longer_repeated_stream_text(self) -> None:
        current = (
            "今日 AI 新闻大汇总（2026年6月24日）\n"
            "🔥 1. OpenAI 与 Anthropic 进入 IPO 冲刺阶段\n"
            "近期 AI 行业最大的资本市场事件，是两大头部模型"
            "# 今日 AI 新闻大汇总（2026年6月24公司：\n"
            "OpenAI 已秘密提交上市申请（S-1）。\n"
            "Anthropic 同样已启动 IPO 流程。\n"
            "市场普遍认为，两家公司可能成为 AI 历史上规模最大的科技上市案例之一。\n"
            "未来竞争重点逐渐从：\n"
            "谁模型更强\n"
            "转变为：\n"
            "谁能让 AI 真正替用户完成工作。\n"
            "未来竞争重点逐渐从：\n"
            "谁模型更强\n"
            "转变为：\n"
            "谁能让 AI 真正替用户完成工作。"
        )
        fetched = (
            "今日 AI 新闻大汇总（2026年6月24日）\n"
            "🔥 1. OpenAI 与 Anthropic 进入 IPO 冲刺阶段\n\n"
            "近期 AI 行业最大的资本市场事件，是两大头部模型公司：\n\n"
            "OpenAI 已秘密提交上市申请（S-1）。\n"
            "Anthropic 同样已启动 IPO 流程。\n"
            "市场普遍认为，两家公司可能成为 AI 历史上规模最大的科技上市案例之一。"
        )

        self.assertEqual(server_mod._prefer_more_complete_text(current, fetched), fetched)

    def test_parse_sse_events_accepts_handoff_content_part_delta_without_message_prefix(self) -> None:
        events = [
            json.dumps(
                {
                    "o": "add",
                    "conversation_id": "conv-1",
                    "v": {
                        "message": {
                            "id": "assistant-msg",
                            "author": {"role": "assistant"},
                            "status": "in_progress",
                            "content": {"content_type": "text", "parts": [""]},
                        }
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps({"o": "append", "p": "/content/parts/0", "v": "完成了"}, ensure_ascii=False),
        ]

        text, conversation_id, message_id, _handoff = server_mod.parse_sse_events(events, {})

        self.assertEqual(text, "完成了")
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(message_id, "assistant-msg")

    def test_parse_sse_events_keeps_orphan_handoff_delta_before_message_envelope(self) -> None:
        events = [
            json.dumps({"o": "append", "p": "/content/parts/0", "v": "前半段"}, ensure_ascii=False),
            json.dumps(
                {
                    "o": "add",
                    "conversation_id": "conv-1",
                    "v": {
                        "message": {
                            "id": "assistant-msg",
                            "author": {"role": "assistant"},
                            "status": "in_progress",
                            "content": {"content_type": "text", "parts": ["后半段"]},
                        }
                    },
                },
                ensure_ascii=False,
            ),
        ]

        text, conversation_id, message_id, _handoff = server_mod.parse_sse_events(events, {})

        self.assertEqual(text, "前半段后半段")
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(message_id, "assistant-msg")

    def test_parse_sse_events_does_not_truncate_when_snapshot_contains_only_later_text(self) -> None:
        events = [
            json.dumps(
                {
                    "o": "add",
                    "conversation_id": "conv-1",
                    "v": {
                        "message": {
                            "id": "assistant-msg",
                            "author": {"role": "assistant"},
                            "status": "in_progress",
                            "content": {"content_type": "text", "parts": [""]},
                        }
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps({"o": "append", "p": "/content/parts/0", "v": "第一段。"}, ensure_ascii=False),
            json.dumps(
                {
                    "o": "replace",
                    "v": {
                        "message": {
                            "id": "assistant-msg",
                            "author": {"role": "assistant"},
                            "status": "in_progress",
                            "content": {"content_type": "text", "parts": ["第二段。"]},
                        }
                    },
                },
                ensure_ascii=False,
            ),
        ]

        text, conversation_id, message_id, _handoff = server_mod.parse_sse_events(events, {})

        self.assertEqual(text, "第一段。第二段。")
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(message_id, "assistant-msg")

    def test_parse_sse_events_reorders_out_of_order_content_parts(self) -> None:
        events = [
            json.dumps(
                {
                    "o": "add",
                    "conversation_id": "conv-1",
                    "v": {
                        "message": {
                            "id": "assistant-msg",
                            "author": {"role": "assistant"},
                            "status": "in_progress",
                            "content": {"content_type": "text", "parts": ["1. 第一条\n"]},
                        }
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps({"o": "add", "p": "/content/parts/5", "v": "6. 第六条\n"}, ensure_ascii=False),
            json.dumps({"o": "add", "p": "/content/parts/1", "v": "2. 第二条\n"}, ensure_ascii=False),
            json.dumps({"o": "add", "p": "/content/parts/2", "v": "3. 第三条\n"}, ensure_ascii=False),
            json.dumps({"o": "add", "p": "/content/parts/3", "v": "4. 第四条\n"}, ensure_ascii=False),
            json.dumps({"o": "add", "p": "/content/parts/4", "v": "5. 第五条\n"}, ensure_ascii=False),
        ]

        text, conversation_id, message_id, _handoff = server_mod.parse_sse_events(events, {})

        self.assertEqual(text, "1. 第一条\n2. 第二条\n3. 第三条\n4. 第四条\n5. 第五条\n6. 第六条\n")
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(message_id, "assistant-msg")

    def test_delta_after_yielded_text_uses_overlap_instead_of_blind_length_slice(self) -> None:
        self.assertEqual(
            server_mod._delta_after_yielded_text("今日 AI 新闻", "今日 AI 新闻汇总"),
            "汇总",
        )
        self.assertEqual(
            server_mod._delta_after_yielded_text("标题\n第一段", "第一段\n第二段"),
            "\n第二段",
        )

    def test_delta_after_yielded_text_returns_full_text_when_prefix_was_cleaned_differently(self) -> None:
        yielded = "今日 AI 新闻汇总（2026年6月24才的价值。[引用: turn0news11]\n看点："
        fetched = (
            "今日 AI 新闻汇总（2026年6月24日）\n"
            "🔥 1. AI 人才争夺战持续升级\n\n"
            "近期全球 AI 巨头之间的人才竞争愈发激烈。\n"
            "看点：\n顶级研究员成为各大实验室争夺核心资源。"
        )

        delta = server_mod._delta_after_yielded_text(yielded, fetched, allow_full_replacement=True)

        self.assertEqual(delta, fetched)
        self.assertIn("今日 AI 新闻汇总", delta)
        self.assertIn("🔥 1. AI 人才争夺战持续升级", delta)

    def test_delta_after_yielded_text_resolves_rfind_leak_on_repeated_tail_phrases(self) -> None:
        yielded = "【差异】AI时代学习的核心已经从记住知识转变为驾驭知识，利用AI解决问题"
        fetched = (
            "【不同】AI时代学习的核心已经从记住知识转变为驾驭知识，利用AI解决问题。\n"
            "过去：\n学习 = 获取信息 + 记忆信息。\n"
            "总之，AI时代学习的核心已经从记住知识转变为驾驭知识，利用AI解决问题。"
        )
        delta = server_mod._delta_after_yielded_text(yielded, fetched)
        self.assertIn("过去：\n学习 = 获取信息 + 记忆信息", delta)
        self.assertIn("总之，AI时代学习的核心已经从记住知识转变为驾驭知识", delta)

    def test_delta_after_yielded_text_rejects_large_offset_slide_matching(self) -> None:
        yielded = "【差异】AI时代学习的核心已经从记住知识转变为驾驭知识，利用AI解决问题"
        fetched = (
            "完全不相干的开头填充。" + "无关联文本填充" * 15 + "，从记住知识转变为驾驭知识，利用AI解决问题。"
        )
        delta = server_mod._delta_after_yielded_text(yielded, fetched)
        self.assertEqual(delta, "")

    def test_delta_after_yielded_text_with_citation_alignment(self) -> None:
        yielded = "这是多重引用■cite☆turn0news27☆turn0news29↩。"
        fetched = "这是多重引用[新闻27](https://news27.com) [新闻29](https://news29.com)。\n这是新追加的结尾段落。"
        references = [
            {"markers": "turn0news27", "url": "https://news27.com", "label": "新闻27"},
            {"markers": "turn0news29", "url": "https://news29.com", "label": "新闻29"},
        ]
        applied_yielded = server_mod._apply_citation_references(yielded, references)
        applied_fetched = server_mod._apply_citation_references(fetched, references)
        delta = server_mod._delta_after_yielded_text(applied_yielded, applied_fetched)
        self.assertEqual(delta, "\n这是新追加的结尾段落。")

    def test_delta_after_yielded_text_skips_full_snapshot_for_prefix_suffix_gap_without_replacement(self) -> None:
        yielded = "AI时代学习的利用AI放大自身能力的人。"
        fetched = (
            "AI时代学习的核心已经从“记住知识”转变为“驾驭知识”。\n\n"
            "过去：\n学习 = 获取信息 + 记忆信息\n\n"
            "现在：\n学习 = 提出问题 + 理解原理 + 利用AI解决问题\n\n"
            "掌握AI的人，未来大概率会替代不会使用AI的人；但真正领先的，"
            "往往是既懂专业领域、又懂如何利用AI放大自身能力的人。"
        )

        delta = server_mod._delta_after_yielded_text(yielded, fetched)

        self.assertEqual(delta, "")

    def test_delta_after_yielded_text_keeps_suffix_delta_for_marker_tail(self) -> None:
        yielded = "今日 AI 新闻\n涉及实体：\n\ue200entity\ue202[\"company\",\"OpenAI\""
        fetched = "今日 AI 新闻\n涉及实体：\nOpenAI\nBroadcom\n\n2. 谷歌 AI 人才继续流向 Anthropic"

        delta = server_mod._delta_after_yielded_text(yielded, fetched)

        self.assertEqual(delta, "\nOpenAI\nBroadcom\n\n2. 谷歌 AI 人才继续流向 Anthropic")

    def test_iter_delta_events_does_not_repeat_prefix_when_entity_finishes(self) -> None:
        partial = "今日 AI 新闻\n涉及实体：\n\ue200entity\ue202[\"company\",\"OpenAI\""
        full = "今日 AI 新闻\n涉及实体：\nOpenAI\nBroadcom\n\n2. 谷歌 AI 人才继续流向 Anthropic"
        events = [
            json.dumps(
                {
                    "o": "add",
                    "conversation_id": "conv-1",
                    "v": {
                        "message": {
                            "id": "assistant-msg",
                            "author": {"role": "assistant"},
                            "status": "in_progress",
                            "content": {"content_type": "text", "parts": [partial]},
                        }
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "o": "replace",
                    "conversation_id": "conv-1",
                    "v": {
                        "message": {
                            "id": "assistant-msg",
                            "author": {"role": "assistant"},
                            "status": "finished_successfully",
                            "content": {"content_type": "text", "parts": [full]},
                        }
                    },
                },
                ensure_ascii=False,
            ),
        ]

        chunks = list(server_mod.iter_delta_events(events, {}))

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].text, "今日 AI 新闻\n涉及实体：\n")
        self.assertEqual("".join(chunk.text for chunk in chunks), full)
        self.assertIsNone(chunks[1].snapshot_text)

    def test_fetch_conversation_filters_by_assistant_message_id(self) -> None:
        mapping = {
            "old-msg-id": {
                "message": {
                    "id": "old-msg-id",
                    "author": {"role": "assistant"},
                    "create_time": 1000,
                    "content": {"content_type": "text", "parts": ["旧的历史消息，创造时间早"]},
                }
            },
            "new-other-branch-id": {
                "message": {
                    "id": "new-other-branch-id",
                    "author": {"role": "assistant"},
                    "create_time": 3000,
                    "content": {"content_type": "text", "parts": ["其他分支消息，创造时间最新，但非目标分支"]},
                }
            },
            "target-msg-id": {
                "message": {
                    "id": "target-msg-id",
                    "author": {"role": "assistant"},
                    "create_time": 2000,
                    "content": {"content_type": "text", "parts": ["目标 assistant 消息"]},
                }
            }
        }
        fake_data = {"mapping": mapping}
        original_response_json = server_mod._conversation_response_json
        server_mod._conversation_response_json = lambda *args, **kwargs: fake_data
        try:
            text_blind, msg_id_blind = server_mod.fetch_conversation(
                None, {"base_url": "https://chatgpt.com"}, "conv-1", "access-token"
            )
            self.assertEqual(msg_id_blind, "new-other-branch-id")
            self.assertEqual(text_blind, "其他分支消息，创造时间最新，但非目标分支")

            text_target, msg_id_target = server_mod.fetch_conversation(
                None, {"base_url": "https://chatgpt.com"}, "conv-1", "access-token",
                assistant_message_id="target-msg-id"
            )
            self.assertEqual(msg_id_target, "target-msg-id")
            self.assertEqual(text_target, "目标 assistant 消息")
        finally:
            server_mod._conversation_response_json = original_response_json

    def test_parse_sse_events_reads_text_from_structured_writing_part_snapshot(self) -> None:
        events = [
            json.dumps(
                {
                    "o": "add",
                    "conversation_id": "conv-1",
                    "v": {
                        "message": {
                            "id": "assistant-msg",
                            "author": {"role": "assistant"},
                            "status": "finished_successfully",
                            "content": {
                                "content_type": "multimodal_text",
                                "parts": [
                                    ':::writing{variant="document" id="58241"}\n\n',
                                    {"text": "标题\n\n第一段。\n\n第二段。"},
                                ],
                            },
                        }
                    },
                },
                ensure_ascii=False,
            ),
        ]

        text, conversation_id, message_id, _handoff = server_mod.parse_sse_events(events, {})

        self.assertIn(':::writing{variant="document" id="58241"}', text)
        self.assertIn("第一段。", text)
        self.assertIn("第二段。", text)
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(message_id, "assistant-msg")

    def test_extract_content_reads_rich_paragraph_run_structure(self) -> None:
        event = {
            "message": {
                "id": "assistant-msg",
                "author": {"role": "assistant"},
                "content": {
                    "content_type": "text",
                    "parts": [
                        {
                            "paragraphs": [
                                {
                                    "runs": [
                                        {"text": "再为你写一篇风格偏唯美、带有淡淡哲思的散文。"},
                                        {"text": "\n\n"},
                                        {"text": "《月光落在旧时光里》"},
                                    ]
                                }
                            ]
                        }
                    ],
                },
            }
        }

        rendered = server_mod.extract_content(event)

        self.assertEqual(rendered, "再为你写一篇风格偏唯美、带有淡淡哲思的散文。\n\n《月光落在旧时光里》")

    def test_extract_content_reads_rich_block_segment_structure(self) -> None:
        event = {
            "message": {
                "id": "assistant-msg",
                "author": {"role": "assistant"},
                "content": {
                    "content_type": "text",
                    "blocks": [
                        {
                            "segments": [
                                {"text": "在时光的渡口，"},
                                {"text": "与美好温柔相逢。"},
                            ]
                        }
                    ],
                },
            }
        }

        rendered = server_mod.extract_content(event)

        self.assertEqual(rendered, "在时光的渡口，与美好温柔相逢。")

    def test_parse_sse_events_renders_handoff_asset_pointer_field_patch(self) -> None:
        events = [
            json.dumps(
                {
                    "o": "add",
                    "conversation_id": "conv-1",
                    "v": {
                        "message": {
                            "id": "assistant-msg",
                            "author": {"role": "assistant"},
                            "status": "in_progress",
                            "content": {"content_type": "multimodal_text", "parts": [{}]},
                        }
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "o": "replace",
                    "p": "/content/parts/0/asset_pointer",
                    "v": "file-service://file-generated-image",
                },
                ensure_ascii=False,
            ),
        ]

        text, conversation_id, message_id, _handoff = server_mod.parse_sse_events(events, {})

        self.assertEqual(
            text,
            "![generated image](https://chatgpt.com/backend-api/files/file-generated-image/download)",
        )
        self.assertEqual(conversation_id, "conv-1")
        self.assertEqual(message_id, "assistant-msg")

    def test_complete_chatgpt_web_uses_handoff_topic_before_conversation_fetch(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_handoff = server_mod._fetch_handoff_topic_text
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {"type": "resume_conversation_token", "kind": "topic", "token": "resume-token", "conversation_id": "conv-1"},
                    {
                        "type": "stream_handoff",
                        "conversation_id": "conv-1",
                        "options": [{"type": "subscribe_ws_topic", "topic_id": "conversation-turn-1"}],
                    },
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        captured = {}

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        def fake_fetch_handoff(session, config, access_token, dynamic_headers, context):
            captured["topic_id"] = server_mod._handoff_topic_id(context)
            return "handoff text", "conv-1", "assistant-msg"

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_handoff_topic_text = fake_fetch_handoff
            server_mod._fetch_conversation_with_retry = lambda *args, **kwargs: self.fail("不应继续轮询 conversation")

            result = server_mod.complete_chatgpt_web(
                [{"role": "user", "content": "写一段简短介绍"}],
                "gpt-5-5-thinking",
                {},
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_handoff_topic_text = original_fetch_handoff
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(captured["topic_id"], "conversation-turn-1")
        self.assertEqual(result.text, "handoff text")
        self.assertEqual(result.conversation_id, "conv-1")
        self.assertEqual(result.message_id, "assistant-msg")

    def test_complete_chatgpt_web_prefers_handoff_text_over_partial_sse(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_handoff = server_mod._fetch_handoff_topic_text
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "in_progress",
                                "content": {"content_type": "text", "parts": ["半成品内容"]},
                            }
                        },
                    },
                    {
                        "type": "stream_handoff",
                        "conversation_id": "conv-1",
                        "options": [{"type": "subscribe_ws_topic", "topic_id": "conversation-turn-1"}],
                    },
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_handoff_topic_text = lambda *args, **kwargs: ("完整内容", "conv-1", "assistant-msg")
            server_mod._fetch_conversation_with_retry = lambda *args, **kwargs: self.fail("不应继续轮询 conversation")

            result = server_mod.complete_chatgpt_web(
                [{"role": "user", "content": "今日AI新闻"}],
                "gpt-5-5-thinking",
                {},
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_handoff_topic_text = original_fetch_handoff
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(result.text, "完整内容")
        self.assertEqual(result.conversation_id, "conv-1")
        self.assertEqual(result.message_id, "assistant-msg")

    def test_complete_chatgpt_web_validates_short_thinking_handoff_text(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_handoff = server_mod._fetch_handoff_topic_text
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        short_text = "AI时代学习的利用AI放大自身能力的人。"
        full_text = (
            "AI时代学习的核心已经从“记住知识”转变为“驾驭知识”。\n\n"
            "一、哪些能力越来越重要\n"
            "真正领先的，往往是既懂专业领域、又懂如何利用AI放大自身能力的人。"
        )

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "type": "stream_handoff",
                        "conversation_id": "conv-1",
                        "options": [{"type": "subscribe_ws_topic", "topic_id": "conversation-turn-1"}],
                    }
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        fetch_calls = []

        def fake_fetch_retry(*args, **kwargs):
            fetch_calls.append(kwargs)
            return full_text, "assistant-msg"

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_handoff_topic_text = lambda *args, **kwargs: (short_text, "conv-1", "assistant-msg")
            server_mod._fetch_conversation_with_retry = fake_fetch_retry

            result = server_mod.complete_chatgpt_web(
                [{"role": "user", "content": "ai时代如何学习"}],
                "gpt-5-5-thinking",
                {},
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_handoff_topic_text = original_fetch_handoff
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(result.text, full_text)
        self.assertEqual(result.message_id, "assistant-msg")
        self.assertEqual(fetch_calls[0]["assistant_message_id"], "assistant-msg")

    def test_stream_chatgpt_web_uses_handoff_topic_before_conversation_fetch(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_handoff = server_mod._fetch_handoff_topic_text
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {"type": "resume_conversation_token", "kind": "topic", "token": "resume-token", "conversation_id": "conv-1"},
                    {
                        "type": "stream_handoff",
                        "conversation_id": "conv-1",
                        "options": [{"type": "subscribe_ws_topic", "topic_id": "conversation-turn-1"}],
                    },
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        captured = {}

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        def fake_fetch_handoff(session, config, access_token, dynamic_headers, context):
            captured["topic_id"] = server_mod._handoff_topic_id(context)
            return "handoff text", "conv-1", "assistant-msg"

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_handoff_topic_text = fake_fetch_handoff
            server_mod._fetch_conversation_with_retry = lambda *args, **kwargs: self.fail("不应继续轮询 conversation")

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "写一段简短介绍"}],
                    "gpt-5-5-thinking",
                    {},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_handoff_topic_text = original_fetch_handoff
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(captured["topic_id"], "conversation-turn-1")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "handoff text")
        self.assertEqual(chunks[0].conversation_id, "conv-1")
        self.assertEqual(chunks[0].message_id, "assistant-msg")

    def test_stream_chatgpt_web_buffers_and_emits_full_text_when_handoff_follows(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_handoff = server_mod._fetch_handoff_topic_text
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "in_progress",
                                "content": {
                                    "content_type": "text",
                                    "parts": ["今日 AI 新闻汇总（2026年6月24日界对AI训练数据隐私"],
                                },
                            }
                        },
                    },
                    {
                        "type": "stream_handoff",
                        "conversation_id": "conv-1",
                        "options": [{"type": "subscribe_ws_topic", "topic_id": "conversation-turn-1"}],
                    },
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480, **(request_options or {})},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_handoff_topic_text = lambda *args, **kwargs: (
                "今日 AI 新闻汇总（2026年6月24日）\n\n🏆 1. Meta AI训练项目因数据泄露紧急暂停",
                "conv-1",
                "assistant-msg",
            )
            server_mod._fetch_conversation_with_retry = lambda *args, **kwargs: self.fail("不应继续轮询 conversation")

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "今日AI新闻"}],
                    "gpt-5-5-thinking",
                    {"buffer_stream_until_handoff": True},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_handoff_topic_text = original_fetch_handoff
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(len(chunks), 1)
        self.assertEqual(
            chunks[0].text,
            "今日 AI 新闻汇总（2026年6月24日）\n\n🏆 1. Meta AI训练项目因数据泄露紧急暂停",
        )

    def test_stream_chatgpt_web_final_fetch_replaces_short_completed_sse(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "finished_successfully",
                                "content": {"content_type": "text", "parts": ["再为你写一篇风息。"]},
                            }
                        },
                    }
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480, **(request_options or {})},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_conversation_with_retry = lambda *args, **kwargs: (
                "再为你写一篇风格偏唯美、带有淡淡哲思的散文。\n\n《月光落在旧时光里》",
                "assistant-msg",
            )

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "继续再写一篇"}],
                    "gpt-5-5-thinking",
                    {"buffer_stream_until_handoff": True, "final_fetch_after_stream_completion": True},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(len(chunks), 1)
        self.assertEqual(
            chunks[0].text,
            "再为你写一篇风格偏唯美、带有淡淡哲思的散文。\n\n《月光落在旧时光里》",
        )
        self.assertEqual(chunks[-1].conversation_id, "conv-1")
        self.assertEqual(chunks[-1].message_id, "assistant-msg")

    def test_stream_chatgpt_web_final_fetch_replaces_prefix_suffix_gap(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        partial_text = "AI时代学习的利用AI放大自身能力的人。"
        full_text = (
            "AI时代学习的核心已经从“记住知识”转变为“驾驭知识”。\n\n"
            "过去：\n学习 = 获取信息 + 记忆信息\n\n"
            "现在：\n学习 = 提出问题 + 理解原理 + 利用AI解决问题\n\n"
            "掌握AI的人，未来大概率会替代不会使用AI的人；但真正领先的，"
            "往往是既懂专业领域、又懂如何利用AI放大自身能力的人。"
        )

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "finished_successfully",
                                "content": {"content_type": "text", "parts": [partial_text]},
                            }
                        },
                    }
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480, **(request_options or {})},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_conversation_with_retry = lambda *args, **kwargs: (full_text, "assistant-msg")

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "ai时代如何学习"}],
                    "gpt-5-5-thinking",
                    {"buffer_stream_until_handoff": True, "final_fetch_after_stream_completion": True},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, full_text)
        self.assertIn("现在：\n学习 = 提出问题", chunks[0].text)
        self.assertEqual(chunks[-1].conversation_id, "conv-1")
        self.assertEqual(chunks[-1].message_id, "assistant-msg")

    def test_stream_chatgpt_web_final_fetch_waits_for_stable_complete_snapshot(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        partial_text = "今日行业三大趋势\n① 中国 AI 已进入“十强争霸”阶段\n行业已从 2024 年数十家大模型混战，逐步收敛到：\n-包\n小米 AI\n等头部玩家。"
        full_text = (
            "今日行业三大趋势\n"
            "① 中国 AI 已进入“十强争霸”阶段\n\n"
            "行业已从 2024 年数十家大模型混战，逐步收敛到：\n\n"
            "DeepSeek\n阿里 Qwen\n智谱 Z.ai\nMoonshot Kimi\n腾讯混元\n百度文心\nMiniMax\nStepFun\n字节豆包\n小米 AI\n\n"
            "等头部玩家。\n\n"
            "② 开源成为中国厂商核心武器\n\n"
            "与美国闭源路线不同，中国头部厂商越来越倾向于：\n\n"
            "开源模型\n开放权重\n低价 API\n\n"
            "以快速扩大生态影响力。"
        )

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "finished_successfully",
                                "content": {"content_type": "text", "parts": [partial_text]},
                            }
                        },
                    }
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480, **(request_options or {})},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        fetch_calls = []

        def fake_fetch_retry(*args, **kwargs):
            fetch_calls.append(kwargs)
            return full_text, "assistant-msg"

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_conversation_with_retry = fake_fetch_retry

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "今日 AI 新闻"}],
                    "gpt-5-5-thinking",
                    {"buffer_stream_until_handoff": True, "final_fetch_after_stream_completion": True},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, full_text)
        self.assertEqual(fetch_calls[0]["stable_after_text_attempts"], 3)

    def test_gpt55thinking_buffers_and_emits_better_snapshot(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        partial_text = "今日 AI 新闻汇总（2026年6月24日）\n2. 韩国出台金融行业 AI 新规\n韩国金融监管、风险控制、投资建议等场景中使用AI时，需要保留人工监督机制。"
        full_text = (
            "今日 AI 新闻汇总（2026年6月24日）\n"
            "2. 韩国出台金融行业 AI 新规\n\n"
            "韩国金融监管机构发布新原则，明确要求：\n\n"
            "AI可以辅助决策，但最终责任必须由人承担。\n\n"
            "金融机构在贷款审批、风险控制、投资建议等场景中使用AI时，需要保留人工监督机制。"
        )

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "finished_successfully",
                                "content": {"content_type": "text", "parts": [partial_text]},
                            }
                        },
                    }
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480, **(request_options or {})},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_conversation_with_retry = lambda *args, **kwargs: (full_text, "assistant-msg")

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "今日 AI 新闻"}],
                    "gpt-5-5-thinking",
                    {"buffer_stream_until_handoff": True, "final_fetch_after_stream_completion": True},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, full_text)

    def test_stream_chatgpt_web_skips_disconnect_fallback_after_finished_successfully_text(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_iter_delta = server_mod.iter_delta_events
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="auto",
            )

        def fake_iter_delta(_events, context=None):
            if isinstance(context, dict):
                context["conversation_id"] = "conv-1"
                context["message_id"] = "assistant-msg"
                context["message_status"] = "finished_successfully"
                context["message_finished_successfully"] = True
            yield server_mod.ChatGPTWebCompletion("答案已经完整返回。", "auto", "conv-1", "assistant-msg")
            raise RuntimeError("stream socket closed after terminal payload")

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod.iter_delta_events = fake_iter_delta
            server_mod._fetch_conversation_with_retry = lambda *args, **kwargs: self.fail("已成功结束时不应再触发断线兜底轮询")

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "继续"}],
                    "auto",
                    {},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod.iter_delta_events = original_iter_delta
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "答案已经完整返回。")
        self.assertEqual(chunks[0].conversation_id, "conv-1")
        self.assertEqual(chunks[0].message_id, "assistant-msg")

    def test_stream_chatgpt_web_skips_handoff_fallback_after_finished_successfully_text(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_handoff = server_mod._fetch_handoff_topic_text
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "finished_successfully",
                                "content": {"content_type": "text", "parts": ["答案已经完整返回。"]},
                            }
                        },
                    },
                    {
                        "type": "stream_handoff",
                        "conversation_id": "conv-1",
                        "options": [{"type": "subscribe_ws_topic", "topic_id": "conversation-turn-1"}],
                    },
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="auto",
            )

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_handoff_topic_text = lambda *args, **kwargs: self.fail("已成功结束时不应订阅 handoff 续流")
            server_mod._fetch_conversation_with_retry = lambda *args, **kwargs: self.fail("已成功结束时不应继续轮询 conversation")

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "继续"}],
                    "auto",
                    {},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_handoff_topic_text = original_fetch_handoff
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "答案已经完整返回。")
        self.assertEqual(chunks[0].conversation_id, "conv-1")
        self.assertEqual(chunks[0].message_id, "assistant-msg")

    def test_stream_chatgpt_web_skips_handoff_fallback_after_terminal_marker(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_handoff = server_mod._fetch_handoff_topic_text
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "in_progress",
                                "content": {"content_type": "text", "parts": ["答案已经完整返回。"]},
                            }
                        },
                    },
                    {"type": "message_stream_complete", "conversation_id": "conv-1"},
                    {
                        "type": "stream_handoff",
                        "conversation_id": "conv-1",
                        "options": [{"type": "subscribe_ws_topic", "topic_id": "conversation-turn-1"}],
                    },
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="auto",
            )

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_handoff_topic_text = lambda *args, **kwargs: self.fail("终止事件已到达时不应订阅 handoff 续流")
            server_mod._fetch_conversation_with_retry = lambda *args, **kwargs: self.fail("终止事件已到达时不应继续轮询 conversation")

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "继续"}],
                    "auto",
                    {},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_handoff_topic_text = original_fetch_handoff
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "答案已经完整返回。")
        self.assertEqual(chunks[0].conversation_id, "conv-1")
        self.assertEqual(chunks[0].message_id, "assistant-msg")

    def test_stream_chatgpt_web_thinking_handoff_fetches_full_text_after_terminal_marker(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_handoff = server_mod._fetch_handoff_topic_text
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        full_text = (
            "我不能提供内部“juice number”或推理预算。\n\n"
            "关于“AI时代如何学习”，建议把重点从“记忆知识”转向“驾驭知识”：\n\n"
            "学习基础原理\n"
            "培养提问能力\n"
            "以项目驱动学习\n"
            "建立知识体系\n"
            "训练不可替代能力\n"
            "学习AI本身\n\n"
            "AI时代，比拼谁提问更好、判断更准、行动更快。"
        )

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "finished_successfully",
                                "content": {"content_type": "text", "parts": ["我不能提供"]},
                            }
                        },
                    },
                    {
                        "type": "stream_handoff",
                        "conversation_id": "conv-1",
                        "options": [{"type": "subscribe_ws_topic", "topic_id": "conversation-turn-1"}],
                    },
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        def fake_fetch_handoff(session, config, access_token, dynamic_headers, context):
            self.assertEqual(server_mod._handoff_topic_id(context), "conversation-turn-1")
            return full_text, "conv-1", "assistant-msg"

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_handoff_topic_text = fake_fetch_handoff
            server_mod._fetch_conversation_with_retry = lambda *args, **kwargs: self.fail("handoff 已返回完整文本时不应继续轮询 conversation")

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "AI时代如何学习"}],
                    "gpt-5-5-thinking",
                    {},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_handoff_topic_text = original_fetch_handoff
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertIn(len(chunks), (1, 2))
        if len(chunks) == 2:
            self.assertEqual(chunks[0].text, "我不能提供")
            self.assertEqual(chunks[1].text, full_text[len("我不能提供"):])
        else:
            self.assertEqual(chunks[-1].text, full_text)
        self.assertEqual(chunks[-1].conversation_id, "conv-1")
        self.assertEqual(chunks[-1].message_id, "assistant-msg")

    def test_stream_chatgpt_web_validates_short_thinking_handoff_text_with_conversation_fetch(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_handoff = server_mod._fetch_handoff_topic_text
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        short_text = "AI时代学习的利用AI放大自身能力的人。"
        full_text = (
            "AI时代学习的核心已经从“记住知识”转变为“驾驭知识”。\n\n"
            "一、哪些能力越来越不重要\n"
            "二、哪些能力越来越重要\n"
            "真正领先的，往往是既懂专业领域、又懂如何利用AI放大自身能力的人。"
        )

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "type": "stream_handoff",
                        "conversation_id": "conv-1",
                        "options": [{"type": "subscribe_ws_topic", "topic_id": "conversation-turn-1"}],
                    }
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480, **(request_options or {})},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        fetch_calls = []

        def fake_fetch_retry(*args, **kwargs):
            fetch_calls.append(kwargs)
            return full_text, "assistant-msg"

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_handoff_topic_text = lambda *args, **kwargs: (short_text, "conv-1", "assistant-msg")
            server_mod._fetch_conversation_with_retry = fake_fetch_retry

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "ai时代如何学习"}],
                    "gpt-5-5-thinking",
                    {"stream_snapshot_replacements": True},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_handoff_topic_text = original_fetch_handoff
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertIn(len(chunks), (1, 2))
        if len(chunks) == 2:
            self.assertEqual(chunks[0].text, short_text)
            self.assertEqual(chunks[1].text, full_text)
        else:
            self.assertEqual(chunks[0].text, full_text)
        self.assertEqual(chunks[-1].message_id, "assistant-msg")
        self.assertEqual(fetch_calls[0]["assistant_message_id"], "assistant-msg")

    def test_handoff_timeout_is_capped_at_180_seconds(self) -> None:
        self.assertEqual(server_mod._handoff_timeout_sec({"handoff_stream_timeout_sec": 999}), 180.0)
        self.assertEqual(
            server_mod._handoff_timeout_sec({"fallback_fetch_attempts": 84, "fallback_fetch_interval_sec": 5}),
            180.0,
        )
        self.assertEqual(server_mod._handoff_parallel_poll_delay_sec({}), 15.0)

    def test_iter_handoff_topic_chunks_uses_parallel_conversation_poll_when_it_wins(self) -> None:
        original_fetch_handoff = server_mod._fetch_handoff_topic_text
        original_fetch_conversation = server_mod.fetch_conversation
        result = server_mod.HandoffStreamResult()
        fetch_calls = []

        def fake_fetch_handoff(*args, **kwargs):
            time.sleep(0.5)
            return None, None, None

        def fake_fetch_conversation(
            session,
            config,
            conversation_id,
            access_token,
            dynamic_headers=None,
            user_message_id=None,
            assistant_message_id=None,
            suppress_transient_logs=False,
        ):
            fetch_calls.append(
                {
                    "conversation_id": conversation_id,
                    "user_message_id": user_message_id,
                    "suppress_transient_logs": suppress_transient_logs,
                }
            )
            return "conversation text", "assistant-msg"

        try:
            server_mod._fetch_handoff_topic_text = fake_fetch_handoff
            server_mod.fetch_conversation = fake_fetch_conversation

            chunks = list(
                server_mod._iter_handoff_topic_chunks(
                    object(),
                    {
                        "base_url": "https://chatgpt.com",
                        "handoff_parallel_poll_delay_sec": 0,
                        "handoff_parallel_poll_attempts": 1,
                    },
                    "access-token",
                    {},
                    {"conversation_id": "conv-1", "handoff": True},
                    model_id="gpt-5-5-thinking",
                    conversation_id_for_poll="conv-1",
                    user_message_id="user-msg",
                    result=result,
                )
            )
        finally:
            server_mod._fetch_handoff_topic_text = original_fetch_handoff
            server_mod.fetch_conversation = original_fetch_conversation

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "conversation text")
        self.assertEqual(chunks[0].conversation_id, "conv-1")
        self.assertEqual(chunks[0].message_id, "assistant-msg")
        self.assertEqual(result.text, "conversation text")
        self.assertEqual(fetch_calls[0]["conversation_id"], "conv-1")
        self.assertEqual(fetch_calls[0]["user_message_id"], "user-msg")
        self.assertTrue(fetch_calls[0]["suppress_transient_logs"])

    def test_stream_chatgpt_web_skips_handoff_for_image_generation_prompt_and_polls_conversation(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_handoff = server_mod._fetch_handoff_topic_text
        original_fetch_retry = server_mod._fetch_conversation_with_retry
        image_text = "![generated image](file:///D:/Tencent%20Files/generated-images/chatgpt_web_file.png)"

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                event = {
                    "type": "stream_handoff",
                    "conversation_id": "conv-image",
                    "options": [{"type": "subscribe_ws_topic", "topic_id": "conversation-turn-image"}],
                }
                yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        fetch_calls = []

        def fake_fetch_retry(*args, **kwargs):
            fetch_calls.append(kwargs)
            return image_text, "assistant-image-msg"

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_handoff_topic_text = lambda *args, **kwargs: self.fail("生图请求不应等待 stream_handoff")
            server_mod._fetch_conversation_with_retry = fake_fetch_retry

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "生成一张美女图片，高清全身照，真实感。9:16"}],
                    "gpt-5-5-thinking",
                    {},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_handoff_topic_text = original_fetch_handoff
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, image_text)
        self.assertEqual(chunks[0].conversation_id, "conv-image")
        self.assertEqual(chunks[0].message_id, "assistant-image-msg")
        self.assertEqual(len(fetch_calls), 1)

    def test_stream_chatgpt_web_validates_corrupted_handoff_text_with_conversation_fetch(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_handoff = server_mod._fetch_handoff_topic_text
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        corrupted_text = (
            "今日 AI 新闻大汇总（2026年6月24日）\n"
            "🔥 1. OpenAI 与 Anthropic 进入 IPO 冲刺阶段\n"
            "近期 AI 行业最大的资本市场事件，是两大头部模型"
            "# 今日 AI 新闻大汇总（2026年6月24公司：\n"
            "OpenAI 已秘密提交上市申请（S-1）。\n"
            "Anthropic 同样已启动 IPO 流程。\n"
            "未来竞争重点逐渐从：\n"
            "谁模型更强\n"
            "转变为：\n"
            "谁能让 AI 真正替用户完成工作。\n"
            "未来竞争重点逐渐从：\n"
            "谁模型更强\n"
            "转变为：\n"
            "谁能让 AI 真正替用户完成工作。"
        )
        clean_text = (
            "今日 AI 新闻大汇总（2026年6月24日）\n"
            "🔥 1. OpenAI 与 Anthropic 进入 IPO 冲刺阶段\n\n"
            "近期 AI 行业最大的资本市场事件，是两大头部模型公司：\n\n"
            "OpenAI 已秘密提交上市申请（S-1）。\n"
            "Anthropic 同样已启动 IPO 流程。\n"
            "市场普遍认为，两家公司可能成为 AI 历史上规模最大的科技上市案例之一。"
        )

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "type": "stream_handoff",
                        "conversation_id": "conv-1",
                        "options": [{"type": "subscribe_ws_topic", "topic_id": "conversation-turn-1"}],
                    }
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480, **(request_options or {})},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        fetch_calls = []

        def fake_fetch_retry(*args, **kwargs):
            fetch_calls.append(kwargs)
            return clean_text, "assistant-msg"

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_handoff_topic_text = lambda *args, **kwargs: (corrupted_text, "conv-1", "assistant-msg")
            server_mod._fetch_conversation_with_retry = fake_fetch_retry

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "今日AI新闻"}],
                    "gpt-5-5-thinking",
                    {"stream_snapshot_replacements": True},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_handoff_topic_text = original_fetch_handoff
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].text, corrupted_text)
        self.assertEqual(chunks[1].text, clean_text)
        self.assertEqual(chunks[1].snapshot_text, clean_text)
        self.assertEqual(fetch_calls[0]["assistant_message_id"], "assistant-msg")

    def test_extract_text_incremental_keeps_bare_value_appends_when_final_patch_joins_parts(self) -> None:
        """还原 gpt-5-5-thinking Celsius WS 续流的真实事件形态。

        parts/0 append 建立路径 → 裸 {"v": "..."} 续写 → 终结 patch 再 append。
        修复前裸 v 只累加 accumulated 不同步 parts_by_index，终结 patch 按 parts
        重新 join 时快照会塌缩成"首个 part + 末尾 patch"，造成正文缺失/重复。
        """
        events = [
            json.dumps(
                {
                    "v": {
                        "message": {
                            "id": "assistant-1",
                            "author": {"role": "assistant"},
                            "status": "in_progress",
                            "content": {"content_type": "text", "parts": [""]},
                        },
                        "conversation_id": "conv-1",
                    }
                },
                ensure_ascii=False,
            ),
            json.dumps({"p": "/message/content/parts/0", "o": "append", "v": "彩虹是阳光经过"}, ensure_ascii=False),
            json.dumps({"v": "空气中的水滴时"}, ensure_ascii=False),
            json.dumps({"v": "，由于折射与反射"}, ensure_ascii=False),
            json.dumps(
                {
                    "p": "",
                    "o": "patch",
                    "v": [
                        {"p": "/message/content/parts/0", "o": "append", "v": "而形成的光学现象。"},
                        {"p": "/message/status", "o": "replace", "v": "finished_successfully"},
                    ],
                },
                ensure_ascii=False,
            ),
        ]
        full_text = "彩虹是阳光经过空气中的水滴时，由于折射与反射而形成的光学现象。"

        text, _conv, _msg, _handoff = server_mod.parse_sse_events(list(events), {})
        self.assertEqual(text, full_text)

        # WS 每帧全量重放事件，逐帧快照必须保持追加单调（不可回退/改写）
        previous = ""
        for end in range(1, len(events) + 1):
            snapshot = server_mod.parse_sse_events(list(events[:end]), {})[0]
            if snapshot != previous:
                self.assertTrue(
                    snapshot.startswith(previous),
                    f"快照被改写: {previous!r} -> {snapshot!r}",
                )
            previous = snapshot
        self.assertEqual(previous, full_text)

        chunks = list(server_mod.iter_delta_events(iter(events), {}))
        self.assertEqual("".join(chunk.text for chunk in chunks), full_text)

    def test_stream_buffered_thinking_flushes_pending_before_final_fetch_delta(self) -> None:
        """缓冲模式下最终会话校验若只需追加，必须先冲刷缓冲正文再发增量。

        修复前 pending_chunks 被直接清空，客户端只收到 delta，开头整段缺失。
        """
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements
        original_fetch_retry = server_mod._fetch_conversation_with_retry

        sse_text = "第一段内容，"
        full_text = "第一段内容，第二段补全。"

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    {
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-1",
                                "author": {"role": "assistant"},
                                "status": "in_progress",
                                "content": {"content_type": "text", "parts": [sse_text]},
                            },
                            "conversation_id": "conv-1",
                        },
                    }
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480, **(request_options or {})},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        def fake_fetch_retry(*args, **kwargs):
            return full_text, "assistant-1"

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()
            server_mod._fetch_conversation_with_retry = fake_fetch_retry

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "测试"}],
                    "gpt-5-5-thinking",
                    {"buffer_stream_until_handoff": True, "final_fetch_after_stream_completion": True},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request
            server_mod._fetch_conversation_with_retry = original_fetch_retry

        texts = [chunk.text for chunk in chunks]
        self.assertEqual("".join(texts), full_text)
        self.assertEqual(texts[0], sse_text)

    def test_fallback_fetch_helpers_use_longer_handoff_defaults_and_request_overrides(self) -> None:
        self.assertEqual(server_mod._fallback_fetch_attempts({}, handoff=False), 15)
        self.assertEqual(server_mod._fallback_fetch_attempts({}, handoff=True), 36)
        self.assertEqual(server_mod._fallback_fetch_attempts({"fallback_fetch_attempts": 120}, handoff=True), 120)
        self.assertEqual(server_mod._fallback_fetch_interval_sec({"fallback_fetch_interval_sec": 0.01}), 0.1)
        self.assertEqual(server_mod._fallback_fetch_interval_sec({}, handoff=True), 5.0)
        self.assertEqual(
            server_mod._fallback_fetch_stable_after_text_attempts(
                {"fallback_fetch_stable_after_text_attempts": 5},
                stream=True,
            ),
            5,
        )

    def test_websocket_connect_kwargs_disable_socks_proxy_without_python_socks(self) -> None:
        original_has_python_socks = server_mod._has_python_socks

        class _WebSockets:
            @staticmethod
            def connect(url, *, proxy=True, additional_headers=None, origin=None, open_timeout=None, close_timeout=None, max_size=None):
                return None

        class _Session:
            headers = {"User-Agent": "UA", "Cookie": "a=b"}

        try:
            server_mod._has_python_socks = lambda: False
            kwargs = server_mod._websocket_connect_kwargs(
                _WebSockets,
                _Session(),
                {"proxy": "socks5://127.0.0.1:1080"},
                "access-token",
            )
        finally:
            server_mod._has_python_socks = original_has_python_socks

        self.assertIsNone(kwargs["proxy"])
        self.assertEqual(kwargs["additional_headers"]["Authorization"], "Bearer access-token")
        self.assertEqual(kwargs["additional_headers"]["Cookie"], "a=b")

    def test_extract_content_returns_generated_image_markdown_for_file_asset(self) -> None:
        event = {
            "message": {
                "author": {"role": "assistant"},
                "content": {
                    "content_type": "multimodal_text",
                    "parts": [
                        {
                            "asset_pointer": "file-service://file-generated-image",
                            "mime_type": "image/png",
                            "width": 1024,
                            "height": 1024,
                        }
                    ],
                },
            }
        }

        rendered = server_mod.extract_content(event)

        self.assertEqual(
            rendered,
            "![generated image](https://chatgpt.com/backend-api/files/file-generated-image/download)",
        )

    def test_extract_content_keeps_sediment_pointer_internal_until_resolved(self) -> None:
        event = {
            "message": {
                "author": {"role": "assistant"},
                "content": {
                    "content_type": "multimodal_text",
                    "parts": [
                        {
                            "asset_pointer": "sediment://file_00000000d67071fdb9effff21f9902ab",
                            "mime_type": "image/png",
                            "width": 1024,
                            "height": 1024,
                        }
                    ],
                },
            }
        }

        rendered = server_mod.extract_content(event)

        self.assertEqual(
            rendered,
            "![generated image](sediment://file_00000000d67071fdb9effff21f9902ab)",
        )
        self.assertNotIn("/backend-api/estuary/content?id=", rendered)

    def test_inline_chatgpt_file_images_drops_unresolved_sediment_pointer(self) -> None:
        original_download = server_mod._download_chatgpt_estuary_image
        try:
            server_mod._download_chatgpt_estuary_image = lambda *args, **kwargs: None
            rendered = server_mod._inline_chatgpt_file_images(
                "前文\n\n![generated image](sediment://file_00000000d67071fdb9effff21f9902ab)",
                type("Session", (), {"headers": {}, "proxies": {}})(),
                {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
                "access-token",
                None,
                conversation_id="conversation-id",
            )
        finally:
            server_mod._download_chatgpt_estuary_image = original_download

        self.assertEqual(rendered.strip(), "前文")
        self.assertNotIn("backend-api/estuary/content?id=", rendered)
        self.assertNotIn("sediment://", rendered)

    def test_extract_content_converts_citation_markers_to_markdown_links(self) -> None:
        event = {
            "message": {
                "author": {"role": "assistant"},
                "content": {
                    "content_type": "text",
                    "parts": ["隐形 AI \ue200cite\ue202turn0search9\ue201"],
                },
                "metadata": {
                    "content_references": [
                        {
                            "id": "turn0search9",
                            "title": "株式会社Biz Freak",
                            "url": "https://example.com/biz-freak",
                        }
                    ]
                },
            }
        }

        rendered = server_mod.extract_content(event)

        self.assertEqual(rendered, "隐形 AI [株式会社Biz Freak](https://example.com/biz-freak)")

    def test_classifies_http_429_as_quota_or_rate_limited(self) -> None:
        class _Response:
            status_code = 429
            headers = {"Retry-After": "120"}
            text = json.dumps(
                {
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "You've reached your usage limit. Try again later.",
                    }
                }
            )

            def json(self):
                return json.loads(self.text)

        exc = server_mod._upstream_error_from_response(_Response())

        self.assertEqual(exc.status_code, 429)
        self.assertEqual(exc.error_code, "quota_or_rate_limited")
        self.assertIn("使用上限或限流", str(exc))
        self.assertIn("Retry-After: 120", str(exc))

    def test_classifies_conversation_inaccessible_as_model_limited(self) -> None:
        class _Response:
            status_code = 404
            headers = {}
            text = json.dumps(
                {
                    "detail": {
                        "message": "你无权访问此对话。确保已登录正确的账户，或请对话所有者向你发送共享链接。",
                        "code": "conversation_inaccessible",
                        "can_retry": False,
                    }
                },
                ensure_ascii=False,
            )

            def json(self):
                return json.loads(self.text)

        exc = server_mod._upstream_error_from_response(_Response())

        self.assertEqual(exc.status_code, 429)
        self.assertEqual(exc.error_code, "model_limited")
        self.assertIn("账号/模型额度或权限受限", str(exc))

    def test_fetch_conversation_raises_model_limited_for_inaccessible_conversation(self) -> None:
        class _Response:
            ok = False
            status_code = 404
            headers = {}
            text = json.dumps(
                {
                    "detail": {
                        "message": "你无权访问此对话。确保已登录正确的账户，或请对话所有者向你发送共享链接。",
                        "code": "conversation_inaccessible",
                        "can_retry": False,
                    }
                },
                ensure_ascii=False,
            )

            def json(self):
                return json.loads(self.text)

        class _Session:
            headers = {}
            proxies = {}

            def get(self, url, headers=None, timeout=None):
                return _Response()

        original_make_curl_retry_session = server_mod._make_curl_retry_session
        try:
            server_mod._make_curl_retry_session = lambda source: None
            with self.assertRaises(server_mod.ChatGPTWebUpstreamError) as caught:
                server_mod.fetch_conversation(
                    _Session(),
                    {"base_url": "https://chatgpt.com", "fallback_fetch_timeout_sec": 0.5},
                    "conversation-id",
                    "access-token",
                )
        finally:
            server_mod._make_curl_retry_session = original_make_curl_retry_session

        self.assertEqual(caught.exception.error_code, "model_limited")

    def test_parse_sse_events_classifies_model_limit_error(self) -> None:
        data = json.dumps(
            {
                "error": {
                    "code": "model_cap_exceeded",
                    "message": "You've reached the GPT-5 Thinking limit.",
                }
            }
        )

        with self.assertRaises(server_mod.ChatGPTWebUpstreamError) as caught:
            server_mod.parse_sse_events([data])

        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.error_code, "model_limited")
        self.assertIn("所选模型", str(caught.exception))

    def test_handler_returns_models_and_chat_completion(self) -> None:
        original_complete = server_mod.complete_chatgpt_web
        calls = []

        def fake_complete(messages, model=None, request_options=None):
            calls.append((messages, model, request_options))
            return server_mod.ChatGPTWebCompletion("OK", str(model or "gpt-5-3"), "conv", "msg")

        server_mod.complete_chatgpt_web = fake_complete
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            with urllib.request.urlopen(f"http://{host}:{port}/v1/models", timeout=5) as response:
                models_payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(models_payload["object"], "list")
            model_ids = {item["id"] for item in models_payload["data"]}
            self.assertTrue({"gpt-5-5-thinking", "gpt-5-6", "gpt-6"}.issubset(model_ids))
            self.assertNotIn("gpt-5-3", model_ids)

            body = json.dumps(
                {"model": "gpt-5.5", "messages": [{"role": "user", "content": "Hello"}]},
                ensure_ascii=False,
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                chat_payload = json.loads(response.read().decode("utf-8"))

            self.assertEqual(chat_payload["choices"][0]["message"]["content"], "OK")
            self.assertEqual(chat_payload["model"], "gpt-5-5-thinking")
            self.assertEqual(chat_payload["conversation_id"], "conv")
            self.assertEqual(calls[0][1], "gpt-5-5-thinking")
        finally:
            server_mod.complete_chatgpt_web = original_complete
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_complete_chatgpt_web_retries_fast_bootstrap_curl28_twice(self) -> None:
        original_complete_once = server_mod._complete_chatgpt_web_once
        calls = []

        def fake_complete_once(messages, model=None, request_options=None):
            calls.append((messages, model, request_options))
            if len(calls) < 3:
                raise RuntimeError("Bootstrap 获取 PoW 资源失败: Failed to perform, curl: (28)")
            return server_mod.ChatGPTWebCompletion("OK", str(model or "auto"), "conv", "msg")

        try:
            server_mod._complete_chatgpt_web_once = fake_complete_once
            result = server_mod.complete_chatgpt_web(
                [{"role": "user", "content": "Hello"}],
                "auto",
                {"prompt_network_error_retry_delay_sec": 0},
            )
        finally:
            server_mod._complete_chatgpt_web_once = original_complete_once

        self.assertEqual(result.text, "OK")
        self.assertEqual(len(calls), 3)

    def test_complete_chatgpt_web_retries_fast_upstream_502_twice(self) -> None:
        original_complete_once = server_mod._complete_chatgpt_web_once
        calls = []

        def fake_complete_once(messages, model=None, request_options=None):
            calls.append((messages, model, request_options))
            if len(calls) < 3:
                raise server_mod.ChatGPTWebUpstreamError(
                    "ChatGPT Web 上游请求失败，HTTP 502",
                    502,
                    "upstream_error",
                )
            return server_mod.ChatGPTWebCompletion("OK", str(model or "auto"), "conv", "msg")

        try:
            server_mod._complete_chatgpt_web_once = fake_complete_once
            result = server_mod.complete_chatgpt_web(
                [{"role": "user", "content": "Hello"}],
                "auto",
                {"prompt_network_error_retry_delay_sec": 0},
            )
        finally:
            server_mod._complete_chatgpt_web_once = original_complete_once

        self.assertEqual(result.text, "OK")
        self.assertEqual(len(calls), 3)

    def test_stream_chatgpt_web_retries_friendly_curl28_before_first_chunk(self) -> None:
        original_stream_once = server_mod._stream_chatgpt_web_once
        calls = []

        def fake_stream_once(messages, model=None, request_options=None):
            calls.append((messages, model, request_options))
            if len(calls) < 3:
                raise server_mod.ChatGPTWebError(
                    "上游网络连接失败，请检查代理/网络后重试。 详情：Failed to perform, curl: (28)",
                    status_code=502,
                    error_code="upstream_network_error",
                )
            yield server_mod.ChatGPTWebCompletion("OK", str(model or "auto"), "conv", "msg")

        try:
            server_mod._stream_chatgpt_web_once = fake_stream_once
            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "Hello"}],
                    "auto",
                    {"prompt_network_error_retry_delay_sec": 0},
                )
            )
        finally:
            server_mod._stream_chatgpt_web_once = original_stream_once

        self.assertEqual([chunk.text for chunk in chunks], ["OK"])
        self.assertEqual(len(calls), 3)

    def test_stream_chatgpt_web_retries_fast_upstream_502_before_first_chunk(self) -> None:
        original_stream_once = server_mod._stream_chatgpt_web_once
        calls = []

        def fake_stream_once(messages, model=None, request_options=None):
            calls.append((messages, model, request_options))
            if len(calls) < 3:
                raise server_mod.ChatGPTWebUpstreamError(
                    "ChatGPT Web 上游请求失败，HTTP 502",
                    502,
                    "upstream_error",
                )
            yield server_mod.ChatGPTWebCompletion("OK", str(model or "auto"), "conv", "msg")

        try:
            server_mod._stream_chatgpt_web_once = fake_stream_once
            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "Hello"}],
                    "auto",
                    {"prompt_network_error_retry_delay_sec": 0},
                )
            )
        finally:
            server_mod._stream_chatgpt_web_once = original_stream_once

        self.assertEqual([chunk.text for chunk in chunks], ["OK"])
        self.assertEqual(len(calls), 3)

    def test_handler_supports_images_generations_endpoint(self) -> None:
        original_generate = server_mod.generate_chatgpt_web_image
        image_bytes = base64.b64decode(self.SAMPLE_PNG_DATA_URL.split(",", 1)[1])
        image_b64 = base64.b64encode(image_bytes).decode("ascii")

        def fake_generate(prompt, model=None, request_options=None, *, image_uploads=None):
            self.assertEqual(prompt, "画一张海报")
            self.assertEqual(model, "gpt-image-2")
            return server_mod.ChatGPTWebCompletion(
                text=f"![generated image](data:image/png;base64,{image_b64})",
                model="gpt-image-2",
            )

        server_mod.generate_chatgpt_web_image = fake_generate
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {"model": "gpt-image-2", "prompt": "画一张海报", "response_format": "b64_json"},
                ensure_ascii=False,
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/images/generations",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        finally:
            server_mod.generate_chatgpt_web_image = original_generate
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

        self.assertIn("created", payload)
        self.assertEqual(payload["data"][0]["b64_json"], image_b64)
        self.assertTrue(payload["data"][0]["url"].startswith("data:image/png;base64,"))

    def test_images_generations_passes_attachments_to_dedicated_image_flow(self) -> None:
        original_generate = server_mod.generate_chatgpt_web_image
        original_complete = server_mod.complete_chatgpt_web
        image_bytes = base64.b64decode(self.SAMPLE_PNG_DATA_URL.split(",", 1)[1])
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        captured = {}

        def fake_complete(*args, **kwargs):
            self.fail("带附件的专用生图请求不应重定向到普通 chat")

        def fake_generate(prompt, model=None, request_options=None, *, image_uploads=None):
            captured["prompt"] = prompt
            captured["model"] = model
            captured["image_uploads"] = list(image_uploads or [])
            return server_mod.ChatGPTWebCompletion(
                text=f"![generated image](data:image/png;base64,{image_b64})",
                model="gpt-image-2",
            )

        try:
            server_mod.complete_chatgpt_web = fake_complete
            server_mod.generate_chatgpt_web_image = fake_generate
            payload = server_mod.openai_image_generation_response(
                {
                    "model": "gpt-image-2",
                    "prompt": "参考附件生成图片",
                    "response_format": "b64_json",
                    "attachments": [
                        {"type": "image_url", "image_url": {"url": self.SAMPLE_PNG_DATA_URL}}
                    ],
                }
            )
        finally:
            server_mod.generate_chatgpt_web_image = original_generate
            server_mod.complete_chatgpt_web = original_complete

        self.assertEqual(captured["prompt"], "参考附件生成图片")
        self.assertEqual(captured["model"], "gpt-image-2")
        self.assertEqual(len(captured["image_uploads"]), 1)
        self.assertEqual(captured["image_uploads"][0].mime_type, "image/png")
        self.assertEqual(captured["image_uploads"][0].data, image_bytes)
        self.assertEqual(payload["data"][0]["b64_json"], image_b64)

    def test_images_generations_extracts_image_field_data_url(self) -> None:
        image_bytes = base64.b64decode(self.SAMPLE_PNG_DATA_URL.split(",", 1)[1])

        uploads = server_mod._image_uploads_from_request_body(
            {
                "model": "gpt-image-2",
                "prompt": "参考图片生成",
                "image": self.SAMPLE_PNG_DATA_URL,
            }
        )

        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].mime_type, "image/png")
        self.assertEqual(uploads[0].data, image_bytes)

    def test_images_generations_extracts_messages_multimodal_images(self) -> None:
        image_bytes = base64.b64decode(self.SAMPLE_PNG_DATA_URL.split(",", 1)[1])

        uploads = server_mod._image_uploads_from_request_body(
            {
                "model": "gpt-image-2",
                "prompt": "参考图片生成",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "参考图片生成"},
                            {"type": "image_url", "image_url": {"url": self.SAMPLE_PNG_DATA_URL}},
                        ],
                    }
                ],
            }
        )

        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].mime_type, "image/png")
        self.assertEqual(uploads[0].data, image_bytes)

    def test_images_generations_extracts_input_image_field(self) -> None:
        image_bytes = base64.b64decode(self.SAMPLE_PNG_DATA_URL.split(",", 1)[1])

        uploads = server_mod._image_uploads_from_request_body(
            {
                "model": "gpt-image-2",
                "prompt": "参考图片生成",
                "input_image": {"image_url": self.SAMPLE_PNG_DATA_URL},
            }
        )

        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].mime_type, "image/png")
        self.assertEqual(uploads[0].data, image_bytes)

    def test_chat_completions_routes_image_model_to_picture_flow(self) -> None:
        original_generate = server_mod.generate_chatgpt_web_image
        image_url = "file:///D:/Tencent%20Files/generated-images/chatgpt_web_file.png"

        def fake_generate(prompt, model=None, request_options=None, *, image_uploads=None):
            self.assertEqual(prompt, "画一张海报")
            self.assertEqual(model, "gpt-image-2")
            assert request_options is not None
            self.assertIs(request_options.get("localize_generated_images"), True)
            return server_mod.ChatGPTWebCompletion(
                text=f"![generated image]({image_url})",
                model="gpt-image-2",
                conversation_id="conv-image",
                message_id="msg-image",
            )

        server_mod.generate_chatgpt_web_image = fake_generate
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "gpt-image-2",
                    "messages": [{"role": "user", "content": "画一张海报"}],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        finally:
            server_mod.generate_chatgpt_web_image = original_generate
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

        content = payload["choices"][0]["message"]["content"]
        self.assertEqual(content, f"![generated image]({image_url})")
        self.assertEqual(payload["conversation_id"], "conv-image")
        self.assertEqual(payload["response_id"], "msg-image")

    def test_chat_completions_streams_image_model_file_markdown_without_b64_reencoding(self) -> None:
        original_generate = server_mod.generate_chatgpt_web_image
        image_url = "file:///D:/Tencent%20Files/generated-images/chatgpt_web_file.png"

        def fake_generate(prompt, model=None, request_options=None, *, image_uploads=None):
            self.assertEqual(prompt, "画一张海报")
            self.assertEqual(model, "gpt-image-2")
            assert request_options is not None
            self.assertIs(request_options.get("localize_generated_images"), True)
            return server_mod.ChatGPTWebCompletion(
                text=f"![generated image]({image_url})",
                model="gpt-image-2",
            )

        server_mod.generate_chatgpt_web_image = fake_generate
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "gpt-image-2",
                    "stream": True,
                    "messages": [{"role": "user", "content": "画一张海报"}],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = response.read().decode("utf-8")
        finally:
            server_mod.generate_chatgpt_web_image = original_generate
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

        self.assertIn(f"![generated image]({image_url})", payload)
        self.assertNotIn("data:image/png;base64", payload)
        self.assertIn('"finish_reason": "stop"', payload)
        self.assertIn("data: [DONE]", payload)

    def test_chat_completions_streaming_image_model_returns_complete_result_after_generation(self) -> None:
        original_generate = server_mod.generate_chatgpt_web_image
        image_url = "file:///D:/Tencent%20Files/generated-images/chatgpt_web_file.png"

        def fake_generate(prompt, model=None, request_options=None, *, image_uploads=None):
            self.assertEqual(prompt, "生成一张图")
            self.assertEqual(model, "gpt-image-2")
            time.sleep(0.05)
            return server_mod.ChatGPTWebCompletion(
                text=f"![generated image]({image_url})",
                model="gpt-image-2",
                conversation_id="conv-image",
                message_id="msg-image",
            )

        server_mod.generate_chatgpt_web_image = fake_generate
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "gpt-image-2",
                    "stream": True,
                    "messages": [{"role": "user", "content": "生成一张图"}],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = response.read().decode("utf-8")
        finally:
            server_mod.generate_chatgpt_web_image = original_generate
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

        self.assertIn(f"![generated image]({image_url})", payload)
        self.assertIn('"finish_reason": "stop"', payload)
        self.assertIn("data: [DONE]", payload)

    def test_handler_returns_friendly_502_for_upstream_network_error(self) -> None:
        original_complete = server_mod.complete_chatgpt_web

        def fake_complete(messages, model=None, request_options=None):
            raise server_mod.requests.RequestException("curl: (35) TLS connect error")

        server_mod.complete_chatgpt_web = fake_complete
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {"model": "auto", "messages": [{"role": "user", "content": "Hello"}]},
                ensure_ascii=False,
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(req, timeout=5)
            payload = json.loads(caught.exception.read().decode("utf-8"))
        finally:
            server_mod.complete_chatgpt_web = original_complete
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

        self.assertEqual(caught.exception.code, 502)
        self.assertEqual(payload["error"]["code"], "upstream_network_error")
        self.assertIn("上游网络连接失败，请检查代理/网络", payload["error"]["message"])

    def test_stream_network_error_returns_friendly_content_and_done(self) -> None:
        original_stream = server_mod.stream_chatgpt_web

        def fake_stream(messages, model=None, request_options=None):
            raise server_mod.requests.ConnectTimeout("Failed to connect to chatgpt.com")
            yield  # pragma: no cover

        server_mod.stream_chatgpt_web = fake_stream
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {"model": "auto", "stream": True, "messages": [{"role": "user", "content": "Hello"}]},
                ensure_ascii=False,
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=5) as response:
                payload = response.read().decode("utf-8")
        finally:
            server_mod.stream_chatgpt_web = original_stream
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

        self.assertIn("上游网络连接失败，请检查代理/网络", payload)
        self.assertIn('"finish_reason": "stop"', payload)
        self.assertIn("data: [DONE]", payload)

    def test_stream_handler_hashes_merged_snapshot_for_conversation_append(self) -> None:
        original_stream = server_mod.stream_chatgpt_web
        original_get_history_hash = server_mod._get_history_hash
        original_update_cache = server_mod._update_cache

        partial_text = "AI时代学习的利用AI放大自身能力的人。"
        full_text = (
            "AI时代学习的核心已经从“记住知识”转变为“驾驭知识”。\n\n"
            "一、哪些能力越来越重要\n"
            "真正领先的，往往是既懂专业领域、又懂如何利用AI放大自身能力的人。"
        )
        captured = {}

        def fake_stream(messages, model=None, request_options=None):
            yield server_mod.ChatGPTWebCompletion(partial_text, "gpt-5-5-thinking", "conv-1", "assistant-msg")
            yield server_mod.ChatGPTWebCompletion(
                full_text, "gpt-5-5-thinking", "conv-1", "assistant-msg", snapshot_text=full_text
            )

        def fake_get_history_hash(history):
            captured["assistant_content"] = history[-1]["content"]
            return "history-hash"

        try:
            server_mod.stream_chatgpt_web = fake_stream
            server_mod._get_history_hash = fake_get_history_hash
            server_mod._update_cache = lambda key, val: captured.setdefault("cache", (key, val))
            httpd, thread = self._start_server()
            try:
                host, port = httpd.server_address
                body = json.dumps(
                    {
                        "model": "gpt-5-5-thinking",
                        "stream": True,
                        "stream_snapshot_replacements": True,
                        "enable_conversation_append": True,
                        "messages": [{"role": "user", "content": "ai时代如何学习"}],
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                req = urllib.request.Request(
                    f"http://{host}:{port}/v1/chat/completions",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    payload = response.read().decode("utf-8")
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)
        finally:
            server_mod.stream_chatgpt_web = original_stream
            server_mod._get_history_hash = original_get_history_hash
            server_mod._update_cache = original_update_cache

        self.assertIn('"finish_reason": "stop"', payload)
        self.assertEqual(captured["assistant_content"], full_text)
        self.assertEqual(captured["cache"], ("history-hash", ("conv-1", "assistant-msg")))

    def test_handler_classifies_http_error_response_before_network_fallback(self) -> None:
        original_complete = server_mod.complete_chatgpt_web

        class _Response:
            status_code = 429
            headers = {}
            text = json.dumps(
                {
                    "error": {
                        "code": "model_cap_exceeded",
                        "message": "You've reached the GPT-5 Thinking limit.",
                    }
                }
            )

            def json(self):
                return json.loads(self.text)

        def fake_complete(messages, model=None, request_options=None):
            exc = server_mod.requests.HTTPError("429 Client Error")
            exc.response = _Response()
            raise exc

        server_mod.complete_chatgpt_web = fake_complete
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {"model": "gpt-5.5", "messages": [{"role": "user", "content": "Hello"}]},
                ensure_ascii=False,
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(req, timeout=5)
            payload = json.loads(caught.exception.read().decode("utf-8"))
        finally:
            server_mod.complete_chatgpt_web = original_complete
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

        self.assertEqual(caught.exception.code, 429)
        self.assertEqual(payload["error"]["code"], "model_limited")
        self.assertIn("所选模型", payload["error"]["message"])

    def test_local_server_and_settings_identify_chatgpt_web(self) -> None:
        cfg = {
            "base_url": "http://127.0.0.1:8082",
            "model_name": "gpt-5-3",
            "api_key": "cookie",
        }

        self.assertEqual(identify_spec(cfg), "chatgpt-web")
        self.assertEqual(infer_translator_model_type("ChatGPT Web", cfg), "chatgpt_web")
        self.assertEqual(infer_translator_model_provider("ChatGPT Web", cfg), "ChatGPT Web")

    def test_stream_chatgpt_web_thinking_to_text_transition(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    # 1. 思考过程消息，应被忽略
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "in_progress",
                                "content": {
                                    "content_type": "thinking",
                                    "parts": ["思考过程：风过人间的黄昏。"],
                                },
                            }
                        },
                    },
                    # 2. 相同消息ID，正式回复开始，应被允许
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "in_progress",
                                "content": {
                                    "content_type": "text",
                                    "parts": ["黄昏缓缓落下的时候"],
                                },
                            }
                        },
                    },
                    # 3. 继续追加文本
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "finished_successfully",
                                "content": {
                                    "content_type": "text",
                                    "parts": ["黄昏缓缓落下的时候，天边的云霞"],
                                },
                            }
                        },
                    },
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "写一篇散文"}],
                    "gpt-5-5-thinking",
                    {},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request

        full_text = "".join(c.text for c in chunks)
        self.assertEqual(full_text, "黄昏缓缓落下的时候，天边的云霞")

    def test_stream_chatgpt_web_nested_patch_list_without_o(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    # 1. 初始包，创建 assistant 回复消息
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "in_progress",
                                "content": {
                                    "content_type": "text",
                                    "parts": ["开头内容"],
                                },
                            }
                        },
                    },
                    # 2. 嵌套的 patch list 包，且外层没有 "o": "patch"
                    {
                        "conversation_id": "conv-1",
                        "v": [
                            {
                                "p": "/message/content/parts/0",
                                "o": "append",
                                "v": "，加上中间内容"
                            }
                        ]
                    },
                    # 3. 正常拼在后面的追加文本
                    {
                        "conversation_id": "conv-1",
                        "v": "，以及结尾内容"
                    }
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "测试嵌套 patch"}],
                    "gpt-5-5-thinking",
                    {},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request

        full_text = "".join(c.text for c in chunks)
        self.assertEqual(full_text, "开头内容，加上中间内容，以及结尾内容")

    def test_stream_chatgpt_web_editable_context_continuation(self) -> None:
        original_prepare = server_mod._prepare_chatgpt_request
        original_warmup = server_mod.warmup_chat_requirements
        original_upload = server_mod._upload_chatgpt_images
        original_request = server_mod.request_conversation_with_requirements

        class _Session:
            def close(self):
                pass

        class _Response:
            ok = True

            def iter_lines(self, decode_unicode=False):
                events = [
                    # 1. model_editable_context 携带历史文本，作为继续生成的基底
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "finished_successfully",
                                "content": {
                                    "content_type": "model_editable_context",
                                    "parts": ["前情提要。"],
                                },
                            }
                        },
                    },
                    # 2. 接着下发 text 包
                    {
                        "o": "add",
                        "conversation_id": "conv-1",
                        "v": {
                            "message": {
                                "id": "assistant-msg",
                                "author": {"role": "assistant"},
                                "status": "in_progress",
                                "content": {
                                    "content_type": "text",
                                    "parts": ["前情提要。"],
                                },
                            }
                        },
                    },
                    # 3. 追加新文本
                    {
                        "conversation_id": "conv-1",
                        "v": "这里是续写新内容。"
                    }
                ]
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")

            def close(self):
                pass

        def fake_prepare(messages, model=None, request_options=None):
            return server_mod.PreparedChatGPTRequest(
                session=_Session(),
                config={"base_url": "https://chatgpt.com", "timezone": "Asia/Shanghai", "timezone_offset_min": -480},
                prompt_messages=messages,
                conversation_id=None,
                parent_message_id="parent-msg",
                access_token="access-token",
                device_id="device-id",
                dynamic_headers={},
                model_id="gpt-5-5-thinking",
            )

        try:
            server_mod._prepare_chatgpt_request = fake_prepare
            server_mod.warmup_chat_requirements = lambda *args, **kwargs: None
            server_mod._upload_chatgpt_images = lambda *args, **kwargs: []
            server_mod.request_conversation_with_requirements = lambda *args, **kwargs: _Response()

            chunks = list(
                server_mod.stream_chatgpt_web(
                    [{"role": "user", "content": "继续写"}],
                    "gpt-5-5-thinking",
                    {},
                )
            )
        finally:
            server_mod._prepare_chatgpt_request = original_prepare
            server_mod.warmup_chat_requirements = original_warmup
            server_mod._upload_chatgpt_images = original_upload
            server_mod.request_conversation_with_requirements = original_request

        full_text = "".join(c.text for c in chunks)
        # 应该只 yield 增量部分，而不是历史部分
        self.assertEqual(full_text, "这里是续写新内容。")

    def test_citation_references_mangled_entities_and_urls(self) -> None:
        # 测试 entity 清洗
        text1 = "介绍一下■entity☆[\"people\",\"Noam Shazeer\"]↩。"
        res1 = server_mod._apply_citation_references(text1, [])
        self.assertEqual(res1, "介绍一下Noam Shazeer。")

        # 测试 url 清洗
        text2 = "链接是■url☆OpenAI☆https://openai.com↩。"
        res2 = server_mod._apply_citation_references(text2, [])
        self.assertEqual(res2, "链接是[OpenAI ↗](https://openai.com)。")

        # 测试☆多重引用拆分
        references = [
            {"markers": "turn0news27", "url": "https://news27.com", "label": "新闻27"},
            {"markers": "turn0news29", "url": "https://news29.com", "label": "新闻29"},
        ]
        text3 = "这是多重引用■cite☆turn0news27☆turn0news29↩。"
        res3 = server_mod._apply_citation_references(text3, references)
        self.assertEqual(res3, "这是多重引用[新闻27](https://news27.com) [新闻29](https://news29.com)。")

        # 测试匹配不到引用超链接时直接抹除
        text3_empty = "这是多重引用■cite☆turn0news27☆turn0news29↩。"
        res3_empty = server_mod._apply_citation_references(text3_empty, [])
        self.assertEqual(res3_empty, "这是多重引用。")

        # 测试 Unicode 原始标记形式
        text4 = "关于\ue200entity\ue202[\"known_celebrity\",\"Satya Nadella\",\"Microsoft CEO\"]\ue201的介绍。"
        res4 = server_mod._apply_citation_references(text4, [])
        self.assertEqual(res4, "关于Satya Nadella的介绍。")

        text5 = "官网是\ue200url\ue202OpenAI\ue202https://openai.com\ue201。"
        res5 = server_mod._apply_citation_references(text5, [])
        self.assertEqual(res5, "官网是[OpenAI ↗](https://openai.com)。")

        # 测试隐藏的控制标记（如 navlist 等）被完全擦除
        text6 = "\ue200navlist\ue202今日AI热点新闻\ue202turn0news3,turn0news10\ue201正文开始。"
        res6 = server_mod._apply_citation_references(text6, [])
        self.assertEqual(res6, "正文开始。")

    # ------------------------------------------------------------------
    # 首字延迟优化：Sentinel 令牌预取 / 登录态缓存 / 连接池
    # ------------------------------------------------------------------

    class _StubSession:
        """最小会话桩：只提供 warmup 需要的 headers / close。"""

        def __init__(self, headers: dict[str, str] | None = None) -> None:
            self.headers = dict(headers or {"Cookie": "c=1", "User-Agent": "ua"})
            self.closed = False

        def close(self) -> None:
            self.closed = True

    def test_warmup_reuses_prefetched_sentinel_token(self) -> None:
        key = "prefetch-key-test"
        session = self._StubSession()
        config = {"base_url": "https://chatgpt.com"}
        with server_mod._sentinel_prefetch_lock:
            server_mod._sentinel_prefetch.pop(key, None)
        server_mod._sentinel_prefetch_store(
            key, {"openai-sentinel-chat-requirements-token": "token-abc"},
        )
        try:
            headers: dict[str, str] = {}
            original_fetch = server_mod._fetch_sentinel_token
            original_prefetch = server_mod._prefetch_sentinel_async

            def fail_fetch(*_args: object, **_kwargs: object) -> bool:
                raise AssertionError("命中预取令牌时不应再跑一遍验证链")

            def noop_prefetch(*_args: object, **_kwargs: object) -> None:
                return None

            server_mod._fetch_sentinel_token = fail_fetch  # type: ignore[assignment]
            server_mod._prefetch_sentinel_async = noop_prefetch  # type: ignore[assignment]
            try:
                reused = server_mod.warmup_chat_requirements(
                    session, config, "access", "device", headers, state_key=key,
                )
            finally:
                server_mod._fetch_sentinel_token = original_fetch  # type: ignore[assignment]
                server_mod._prefetch_sentinel_async = original_prefetch  # type: ignore[assignment]
            self.assertTrue(reused)
            self.assertEqual(headers.get("openai-sentinel-chat-requirements-token"), "token-abc")
        finally:
            with server_mod._sentinel_prefetch_lock:
                server_mod._sentinel_prefetch.pop(key, None)

    def test_prefetched_sentinel_token_is_single_use(self) -> None:
        key = "prefetch-single-use"
        session = self._StubSession()
        config = {"base_url": "https://chatgpt.com"}
        with server_mod._sentinel_prefetch_lock:
            server_mod._sentinel_prefetch.pop(key, None)
        server_mod._sentinel_prefetch_store(
            key, {"openai-sentinel-chat-requirements-token": "token-once"},
        )
        first: dict[str, str] = {}
        second: dict[str, str] = {}
        calls = {"n": 0}

        def fake_fetch(_session: object, _config: object, _token: object, _device: object, headers: dict[str, str]) -> bool:
            calls["n"] += 1
            headers["openai-sentinel-chat-requirements-token"] = "token-fresh"
            return True

        original_fetch = server_mod._fetch_sentinel_token
        original_prefetch = server_mod._prefetch_sentinel_async
        server_mod._fetch_sentinel_token = fake_fetch  # type: ignore[assignment]
        server_mod._prefetch_sentinel_async = lambda *_a, **_k: None  # type: ignore[assignment]
        try:
            reused_first = server_mod.warmup_chat_requirements(
                session, config, "access", "device", first, state_key=key,
            )
            reused_second = server_mod.warmup_chat_requirements(
                session, config, "access", "device", second, state_key=key,
            )
        finally:
            server_mod._fetch_sentinel_token = original_fetch  # type: ignore[assignment]
            server_mod._prefetch_sentinel_async = original_prefetch  # type: ignore[assignment]
            with server_mod._sentinel_prefetch_lock:
                server_mod._sentinel_prefetch.pop(key, None)
        self.assertTrue(reused_first)
        self.assertFalse(reused_second)
        self.assertEqual(first.get("openai-sentinel-chat-requirements-token"), "token-once")
        self.assertEqual(second.get("openai-sentinel-chat-requirements-token"), "token-fresh")
        self.assertEqual(calls["n"], 1)

    def test_session_pool_reuses_same_session(self) -> None:
        from deepcat import chatgpt_transport as transport

        transport.clear_session_cache()
        stub = self._StubSession()
        creations = []

        def factory():
            creations.append(True)
            return stub

        try:
            first = transport.acquire_session("reuse-test", factory)
            first.close()
            second = transport.acquire_session("reuse-test", factory)
            self.assertIs(second.headers, stub.headers)
            self.assertEqual(len(creations), 1)
            second.close()
        finally:
            transport.clear_session_cache()

    def test_expired_pooled_session_is_dropped(self) -> None:
        from unittest.mock import patch
        from deepcat import chatgpt_transport as transport
        from deepcat.utils.http_client_pool import HttpClientPool

        pool = HttpClientPool(idle_seconds=0)
        stale = self._StubSession()
        fresh = self._StubSession()
        try:
            with patch.object(transport, "_pool", pool):
                first = transport.acquire_session("expiry-test", lambda: stale)
                first.close()
                second = transport.acquire_session("expiry-test", lambda: fresh)
                self.assertTrue(stale.closed, "过期连接应关闭")
                self.assertIs(second.headers, fresh.headers)
                second.close()
        finally:
            pool.close()

    def test_resolve_session_info_uses_cache(self) -> None:
        from unittest.mock import patch
        from deepcat import chatgpt_transport as transport

        transport.clear_session_cache()
        config = {"base_url": "https://chatgpt.com", "api_key": "Bearer cache-test"}
        auth = server_mod.parse_auth_value(config["api_key"])
        stub = self._StubSession()
        calls = []

        def checked(*args):
            calls.append(True)
            return {"user": {"id": "u-1"}, "accessToken": "access-fresh"}, "access-fresh", ""

        try:
            with patch.object(server_mod, "make_session", return_value=stub), patch.object(server_mod, "get_session_info", checked):
                first, info_a, token_a = server_mod._acquire_authenticated_session(config, auth)
                first.close()
                second, info_b, token_b = server_mod._acquire_authenticated_session(config, auth)
                second.close()
            self.assertEqual(len(calls), 1, "命中缓存后不应重复检查登录态")
            self.assertEqual(token_a, token_b)
            self.assertEqual(info_a, info_b)
        finally:
            transport.clear_session_cache()


if __name__ == "__main__":
    unittest.main()
