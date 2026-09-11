from __future__ import annotations

import unittest
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace


class TestTranslationServiceHelpers(unittest.TestCase):
    def test_normalizes_local_service_settings(self) -> None:
        from deepcat.settings_store import normalize_translator_settings

        settings = normalize_translator_settings(
            {
                "local_translation_service_enabled": True,
                "local_translation_service_host": "127.0.0.1",
                "local_translation_service_port": 99999,
                "local_translation_service_api_key": "",
            }
        )
        self.assertTrue(settings["local_translation_service_enabled"])
        self.assertEqual(settings["local_translation_service_host"], "127.0.0.1")
        self.assertEqual(settings["local_translation_service_port"], 65535)
        self.assertEqual(settings["local_translation_service_api_key"], "")

    def test_extracts_tagged_translate_input(self) -> None:
        from deepcat.translator_engine import extract_text_from_messages

        text = extract_text_from_messages(
            [
                {
                    "role": "user",
                    "content": "Please translate:\n<translate_input>Hello world</translate_input>",
                }
            ]
        )
        self.assertEqual(text, "Hello world")

    def test_extracts_fenced_text(self) -> None:
        from deepcat.translator_engine import extract_text_from_messages

        text = extract_text_from_messages(
            [
                {
                    "role": "user",
                    "content": "Translate this text:\n\n```text\nHello world\n```",
                }
            ]
        )
        self.assertEqual(text, "Hello world")

    def test_extracts_text_from_immersive_translate_prompt(self) -> None:
        from deepcat.translator_engine import extract_text_from_messages

        text = extract_text_from_messages(
            [
                {"role": "system", "content": "Only output translated content."},
                {
                    "role": "user",
                    "content": "\u7ffb\u8bd1\u4e3a\u7b80\u4f53\u4e2d\u6587\uff1a\n\nIn Partnership with",
                },
            ]
        )
        self.assertEqual(text, "In Partnership with")

    def test_openai_chat_response_shape(self) -> None:
        from deepcat.translator_engine import openai_chat_response

        payload = openai_chat_response("deepcat-translate", "你好", created=123)
        self.assertEqual(payload["object"], "chat.completion")
        self.assertEqual(payload["model"], "deepcat-translate")
        self.assertEqual(payload["choices"][0]["message"]["content"], "你好")

    def test_codex_responses_client_detection(self) -> None:
        import deepcat.translation_server as server_mod

        self.assertTrue(
            server_mod._is_codex_responses_client(
                {"User-Agent": "Codex Desktop/0.137.0"},
                {"input": "Hi"},
            )
        )
        self.assertTrue(
            server_mod._is_codex_responses_client(
                {},
                {
                    "instructions": "You are Codex.",
                    "tools": [{"type": "namespace", "name": "codex_app", "tools": []}],
                    "parallel_tool_calls": True,
                },
            )
        )
        self.assertFalse(server_mod._is_codex_responses_client({}, {"input": "Hi"}))

    def test_local_api_service_alias_defaults_chat_to_qa_model(self) -> None:
        import deepcat.translator_engine as engine

        original_load_translator_settings = engine._load_translator_settings_cached
        original_chat_glm_response = engine._chat_glm_response
        captured: dict[str, str] = {}

        def fake_load_settings(*args, **kwargs):
            return SimpleNamespace(
                ui={
                    "translator": {
                        "translate_model": "translate-model",
                        "qa_model": "qa-model",
                        "model_configs": {
                            "translate-model": {
                                "base_url": "https://translate.example/v1",
                                "model_name": "translate-upstream",
                                "api_key": "test",
                                "model_type": "glm",
                            },
                            "qa-model": {
                                "base_url": "https://qa.example/v1",
                                "model_name": "qa-upstream",
                                "api_key": "test",
                                "model_type": "glm",
                            },
                        },
                    }
                }
            )

        def fake_chat_glm_response(messages, runtime, *, request_options=None, cancel_event=None):
            captured["display_name"] = runtime.display_name
            captured["model_name"] = runtime.model_name
            return engine.openai_chat_response(runtime.display_name, "ok")

        # 隔离配置读取边界，避免其他测试填充的进程缓存影响模型选择断言。
        engine._load_translator_settings_cached = lambda: fake_load_settings().ui["translator"]
        engine._chat_glm_response = fake_chat_glm_response  # type: ignore[assignment]
        try:
            engine.chat_completion_response(
                [{"role": "user", "content": "Hello"}],
                model_name=engine.SERVICE_MODEL_ID,
            )
            self.assertEqual(captured["display_name"], "qa-model")
            self.assertEqual(captured["model_name"], "qa-upstream")

            engine.chat_completion_response(
                [{"role": "user", "content": "Hello"}],
                model_name="gpt-5.4",
            )
            self.assertEqual(captured["display_name"], "qa-model")
            self.assertEqual(captured["model_name"], "qa-upstream")

            self.assertEqual(engine.load_translator_runtime().display_name, "translate-model")
        finally:
            engine._load_translator_settings_cached = original_load_translator_settings
            engine._chat_glm_response = original_chat_glm_response  # type: ignore[assignment]

    def test_http_chat_completion_endpoint(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion = server_mod.chat_completion
        server_mod.chat_completion = lambda *args, **kwargs: "translated"
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "messages": [{"role": "user", "content": "Hello"}],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["choices"][0]["message"]["content"], "translated")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion = original_chat_completion

    def test_http_chat_completion_endpoint_does_not_use_responses_conversion(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion = server_mod.chat_completion
        original_chat_completion_response = server_mod.chat_completion_response

        def fail_responses_conversion(*args, **kwargs):
            raise AssertionError("chat/completions endpoint should not use Responses conversion")

        server_mod.chat_completion = lambda *args, **kwargs: "translated"
        server_mod.chat_completion_response = fail_responses_conversion
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "messages": [{"role": "user", "content": "Hello"}],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["object"], "chat.completion")
            self.assertEqual(payload["choices"][0]["message"]["content"], "translated")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion = original_chat_completion
            server_mod.chat_completion_response = original_chat_completion_response

    def test_http_chat_completion_stream_endpoint(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion = server_mod.chat_completion
        original_chat_completion_stream = server_mod.chat_completion_stream

        def fake_stream(*args, **kwargs):
            yield "hel"
            yield "lo"

        def fail_sync(*args, **kwargs):
            raise AssertionError("sync chat path should not be used for stream=true")

        server_mod.chat_completion = fail_sync
        server_mod.chat_completion_stream = fake_stream
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "stream": True,
                    "messages": [{"role": "user", "content": "Hello"}],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read().decode("utf-8")
            events = []
            done = False
            for block in raw.split("\n\n"):
                if not block.startswith("data: "):
                    continue
                data = block[6:].strip()
                if data == "[DONE]":
                    done = True
                    continue
                events.append(json.loads(data))
            chunks = [event["choices"][0]["delta"].get("content") for event in events]
            self.assertIn("hel", chunks)
            self.assertIn("lo", chunks)
            self.assertTrue(done)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion = original_chat_completion
            server_mod.chat_completion_stream = original_chat_completion_stream

    def test_http_translate_endpoint(self) -> None:
        import deepcat.translation_server as server_mod

        original_translate_text = server_mod.translate_text
        server_mod.translate_text = lambda *args, **kwargs: "你好"
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps({"text": "Hello", "target_lang": "zh-CN"}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/translate",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["translation"], "你好")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.translate_text = original_translate_text

    def test_http_translate_endpoint_accepts_common_text_aliases(self) -> None:
        import deepcat.translation_server as server_mod

        original_translate_text = server_mod.translate_text
        captured = {}

        def fake_translate(text, *args, **kwargs):
            captured["text"] = text
            captured["target_lang"] = kwargs.get("target_lang")
            return "translated"

        server_mod.translate_text = fake_translate
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps({"sourceText": "Hello", "to": "zh-CN"}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/translate",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(captured["text"], "Hello")
            self.assertEqual(captured["target_lang"], "zh-CN")
            self.assertEqual(payload["translatedText"], "translated")
            self.assertEqual(payload["data"]["translatedText"], "translated")
            self.assertEqual(payload["choices"][0]["message"]["content"], "translated")
            self.assertEqual(payload["choices"][0]["text"], "translated")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.translate_text = original_translate_text

    def test_http_translate_endpoint_accepts_nested_text_and_duplicate_path(self) -> None:
        import deepcat.translation_server as server_mod

        original_translate_text = server_mod.translate_text
        captured = {}

        def fake_translate(text, *args, **kwargs):
            captured["text"] = text
            return "translated"

        server_mod.translate_text = fake_translate
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps({"data": {"text": "Hello nested"}}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/local/translate",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(captured["text"], "Hello nested")
            self.assertEqual(payload["translation"], "translated")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.translate_text = original_translate_text

    def test_http_chat_completion_endpoint_accepts_duplicate_path_suffix(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion = server_mod.chat_completion
        server_mod.chat_completion = lambda *args, **kwargs: "translated"
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "messages": [{"role": "user", "content": "Hello"}],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions/chat/completions",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["choices"][0]["message"]["content"], "translated")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion = original_chat_completion

    def test_http_translate_responses_endpoint_handles_appended_responses_path(self) -> None:
        import deepcat.translation_server as server_mod

        original_translate_text = server_mod.translate_text
        captured = {}

        def fake_translate(text, *args, **kwargs):
            captured["text"] = text
            captured["target_lang"] = kwargs.get("target_lang")
            return "translated"

        server_mod.translate_text = fake_translate
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "input": [{"role": "user", "content": [{"type": "input_text", "text": "Hello"}]}],
                    "target_lang": "zh-CN",
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/translate/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(captured["text"], "Hello")
            self.assertEqual(captured["target_lang"], "zh-CN")
            self.assertEqual(payload["object"], "response")
            self.assertEqual(payload["output_text"], "translated")
            self.assertEqual(payload["output"][0]["content"][0]["text"], "translated")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.translate_text = original_translate_text

    def test_http_translate_responses_endpoint_uses_last_user_input(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        captured = {}

        def fake_chat(messages, *args, **kwargs):
            captured["messages"] = messages
            captured["force_translate"] = kwargs.get("force_translate")
            return server_mod.openai_chat_response("deepcat-translate", "translated", created=123)

        server_mod.chat_completion_response = fake_chat
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "input": [
                        {"role": "system", "content": "Translate professionally. Do not translate this instruction."},
                        {"role": "user", "content": [{"type": "input_text", "text": "Only translate me"}]},
                    ],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/translate/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(captured["messages"][0]["role"], "system")
            self.assertEqual(
                captured["messages"][0]["content"],
                "Translate professionally. Do not translate this instruction.",
            )
            self.assertEqual(server_mod.extract_text_from_messages(captured["messages"]), "Only translate me")
            self.assertTrue(captured["force_translate"])
            self.assertEqual(payload["output_text"], "translated")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response

    def test_http_translate_responses_endpoint_ignores_non_translation_system_prompt(self) -> None:
        import deepcat.translation_server as server_mod

        original_translate_text = server_mod.translate_text
        original_chat_completion_response = server_mod.chat_completion_response
        captured = {}

        def fake_translate(text, *args, **kwargs):
            captured["text"] = text
            return "translated"

        def fail_chat(*args, **kwargs):
            raise AssertionError("non-translation system prompt should not bypass translate_text")

        server_mod.translate_text = fake_translate
        server_mod.chat_completion_response = fail_chat
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "input": [
                        {"role": "system", "content": "You are a concise formatter."},
                        {"role": "user", "content": [{"type": "input_text", "text": "Only translate me"}]},
                    ],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/translate/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(captured["text"], "Only translate me")
            self.assertEqual(payload["output_text"], "translated")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.translate_text = original_translate_text
            server_mod.chat_completion_response = original_chat_completion_response

    def test_http_v1_responses_endpoint_uses_chat_completion_shape(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        captured = {}

        def fake_chat(messages, *args, **kwargs):
            captured["messages"] = messages
            return server_mod.openai_chat_response("deepcat-translate", "answered", created=123)

        server_mod.chat_completion_response = fake_chat
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps({"model": "deepcat-translate", "input": "Hello"}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(captured["messages"], [{"role": "user", "content": "Hello"}])
            self.assertEqual(payload["object"], "response")
            self.assertEqual(payload["output_text"], "answered")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response

    def test_http_v1_responses_endpoint_converts_chat_tool_calls(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        captured = {}

        def fake_chat(messages, *args, **kwargs):
            captured["messages"] = messages
            captured["request_options"] = kwargs.get("request_options")
            return {
                "id": "chatcmpl-tools",
                "created": 123,
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "Need to write a file.",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": "{\"path\":\"a.txt\",\"content\":\"hi\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
            }

        server_mod.chat_completion_response = fake_chat
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "input": [
                        {"role": "user", "content": [{"type": "input_text", "text": "Write a file"}]},
                        {"type": "function_call", "call_id": "call_prev", "name": "read_file", "arguments": {"path": "a.txt"}},
                        {"type": "function_call_output", "call_id": "call_prev", "output": "old contents"},
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "name": "write_file",
                            "description": "Write a file",
                            "parameters": {"type": "object"},
                        }
                    ],
                    "tool_choice": {"type": "function", "name": "write_file"},
                    "max_output_tokens": 256,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            # 原生 function calling：tools 经 request_options 透传，不注入 system prompt
            self.assertEqual([message["role"] for message in captured["messages"]], ["user", "assistant", "tool"])
            self.assertEqual(captured["messages"][1]["tool_calls"][0]["function"]["name"], "read_file")
            self.assertEqual(captured["messages"][2]["tool_call_id"], "call_prev")
            self.assertEqual(captured["request_options"]["tools"][0]["function"]["name"], "write_file")
            self.assertEqual(captured["request_options"]["tool_choice"]["function"]["name"], "write_file")
            self.assertEqual(captured["request_options"]["max_output_tokens"], 256)
            self.assertEqual(payload["object"], "response")
            self.assertEqual(payload["output_text"], "")
            self.assertEqual([item["type"] for item in payload["output"]], ["reasoning", "function_call"])
            self.assertEqual(payload["output"][1]["call_id"], "call_1")
            self.assertEqual(payload["output"][1]["name"], "write_file")
            self.assertEqual(payload["usage"]["input_tokens"], 9)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response

    def test_http_v1_responses_endpoint_restores_custom_apply_patch_call(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        patch_text = "*** Begin Patch\n*** Add File: docs/test.md\n+hello\n*** End Patch"

        def fake_chat(messages, *args, **kwargs):
            return {
                "id": "chatcmpl-patch",
                "created": 123,
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_patch",
                                    "type": "function",
                                    "function": {
                                        "name": "apply_patch",
                                        "arguments": json.dumps({"input": patch_text}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }

        server_mod.chat_completion_response = fake_chat
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "input": "Edit file",
                    "tools": [{"type": "custom", "name": "apply_patch"}],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["output"][0]["type"], "custom_tool_call")
            self.assertEqual(payload["output"][0]["name"], "apply_patch")
            self.assertEqual(payload["output"][0]["input"], patch_text)
            self.assertEqual(payload["tools"][0]["type"], "custom")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response

    def test_normalize_responses_tools_injects_apply_patch_proxies(self) -> None:
        from deepcat.translation_server import _normalize_responses_tools

        for tool_type in ("custom", "freeform"):
            tools = _normalize_responses_tools(
                [
                    {"type": tool_type, "name": "apply_patch", "description": "Edit files."},
                    {"type": "function", "name": "shell_command", "parameters": {"type": "object"}},
                ]
            )
            names = [tool["function"]["name"] for tool in tools]
            self.assertEqual(names[0], "apply_patch", tool_type)
            for suffix in ("add_file", "replace_file", "update_file", "delete_file"):
                self.assertIn(f"apply_patch_{suffix}", names, tool_type)
            self.assertIn("shell_command", names, tool_type)
            self.assertEqual(len(names), len(set(names)), tool_type)
            add_file = next(tool for tool in tools if tool["function"]["name"] == "apply_patch_add_file")
            self.assertEqual(
                sorted(add_file["function"]["parameters"]["properties"].keys()),
                ["content", "path"],
            )
            self.assertIn("NEVER write files via shell", add_file["function"]["description"])

    def test_normalize_responses_tools_no_proxies_for_other_custom_tools(self) -> None:
        from deepcat.translation_server import _normalize_responses_tools

        tools = _normalize_responses_tools([{"type": "custom", "name": "run_query"}])
        self.assertEqual([tool["function"]["name"] for tool in tools], ["run_query"])

    def test_messages_with_apply_patch_policy_inserted_after_system(self) -> None:
        from deepcat.translation_server import (
            _messages_with_apply_patch_policy,
            _normalize_responses_tools,
        )

        tools = _normalize_responses_tools([{"type": "custom", "name": "apply_patch"}])
        messages = [
            {"role": "system", "content": "You are Codex."},
            {"role": "user", "content": "Create a page"},
        ]
        augmented = _messages_with_apply_patch_policy(messages, tools)
        self.assertEqual(len(augmented), 3)
        self.assertEqual(augmented[1]["role"], "system")
        self.assertIn("MANDATORY FILE-EDIT POLICY", augmented[1]["content"])
        self.assertEqual(augmented[2]["role"], "user")

        plain_tools = _normalize_responses_tools([{"type": "function", "name": "shell", "parameters": {"type": "object"}}])
        untouched = _messages_with_apply_patch_policy(messages, plain_tools)
        self.assertEqual(untouched, messages)

    def test_shell_file_write_detection(self) -> None:
        from deepcat.translation_server import (
            _looks_like_shell_file_write,
            _shell_file_write_patch_input,
            _tool_calls_violate_file_write_policy,
        )

        heredoc = json.dumps({"command": "cat > outputs/index.html <<'EOF'\n<html></html>\nEOF"})
        self.assertTrue(_looks_like_shell_file_write(heredoc))
        self.assertTrue(_looks_like_shell_file_write(json.dumps({"command": "@'\n<html>\n'@ | Set-Content outputs/a.html"})))
        long_html = "<html>" + "x" * 150 + "</html>"
        self.assertTrue(_looks_like_shell_file_write(json.dumps({"command": f"echo '{long_html}' > outputs/index.html"})))
        self.assertFalse(_looks_like_shell_file_write(json.dumps({"command": "ls -la outputs"})))
        self.assertFalse(_looks_like_shell_file_write(json.dumps({"command": "python -m pytest -q 2>/dev/null"})))
        echo_patch = _shell_file_write_patch_input(json.dumps({"command": f"echo '{long_html}' > outputs/index.html"}))
        self.assertIn("File: outputs/index.html", echo_patch)
        self.assertIn(f"+{long_html}", echo_patch)

        violating_call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "shell_command", "arguments": heredoc},
        }
        self.assertTrue(_tool_calls_violate_file_write_policy([violating_call]))
        patch_call = {
            "id": "call_2",
            "type": "function",
            "function": {"name": "apply_patch_add_file", "arguments": json.dumps({"path": "a", "content": "b"})},
        }
        self.assertFalse(_tool_calls_violate_file_write_policy([patch_call]))

    def test_shell_file_write_skips_powershell_flag_values(self) -> None:
        from deepcat.translation_server import _shell_file_write_operations

        arguments = json.dumps(
            {"command": "@'\n<html></html>\n'@ | Set-Content -Encoding UTF8 \"C:\\out\\index.html\""}
        )
        operations = _shell_file_write_operations(arguments)
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0]["path"], "C:\\out\\index.html")
        self.assertEqual(operations[0]["content"], "<html></html>")

    def test_shell_command_text_supports_argv_command(self) -> None:
        from deepcat.translation_server import _shell_command_text

        wrapped = json.dumps({"command": ["bash", "-lc", "cat > a.txt <<'EOF'\nhi\nEOF"]})
        self.assertEqual(_shell_command_text(wrapped), "cat > a.txt <<'EOF'\nhi\nEOF")

    def test_shell_apply_patch_command_not_flagged_as_violation(self) -> None:
        from deepcat.translation_server import _looks_like_shell_file_write

        command = json.dumps({"command": "apply_patch <<'EOF'\n*** Begin Patch\n*** End Patch\nEOF"})
        self.assertFalse(_looks_like_shell_file_write(command))

    def test_codex_tool_context_synthesizes_shell_apply_patch_target(self) -> None:
        from deepcat.translation_server import (
            _codex_apply_patch_emit_mode,
            _codex_is_custom_tool_proxy,
            _codex_tool_context,
        )

        shell_only = [
            {"type": "function", "name": "shell_command", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}},
        ]
        context = _codex_tool_context(shell_only)
        self.assertEqual(_codex_apply_patch_emit_mode(context), "shell")
        self.assertEqual(context["apply_patch_shell_tool"], {"name": "shell_command", "command_is_array": False})
        self.assertTrue(_codex_is_custom_tool_proxy(context, "apply_patch_add_file"))

        declared = [{"type": "custom", "name": "apply_patch"}]
        self.assertEqual(_codex_apply_patch_emit_mode(_codex_tool_context(declared)), "custom")
        self.assertIsNone(_codex_apply_patch_emit_mode(_codex_tool_context([{"type": "function", "name": "update_plan"}])))

    def test_function_call_output_item_rewrites_shell_write_without_apply_patch_tool(self) -> None:
        from deepcat.translation_server import _codex_tool_context, _function_call_output_item

        tools = [
            {"type": "function", "name": "shell_command", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}},
        ]
        context = _codex_tool_context(tools)
        item = _function_call_output_item(
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "shell_command",
                    "arguments": json.dumps({"command": "@'\n<html></html>\n'@ | Set-Content -Encoding UTF8 \"C:\\out\\index.html\""}),
                },
                "_tool_context": context,
            }
        )
        self.assertEqual(item["type"], "function_call")
        self.assertEqual(item["name"], "shell_command")
        arguments = json.loads(item["arguments"])
        self.assertTrue(arguments["command"].startswith("apply_patch <<'"))
        self.assertIn("*** Add File: C:\\out\\index.html", arguments["command"])

    def test_function_call_output_item_rewrites_proxy_call_to_shell_argv(self) -> None:
        from deepcat.translation_server import _codex_tool_context, _function_call_output_item

        tools = [
            {
                "type": "function",
                "name": "shell",
                "parameters": {"type": "object", "properties": {"command": {"type": "array", "items": {"type": "string"}}}},
            },
        ]
        context = _codex_tool_context(tools)
        item = _function_call_output_item(
            {
                "id": "call_2",
                "type": "function",
                "function": {
                    "name": "apply_patch_add_file",
                    "arguments": json.dumps({"path": "outputs/index.html", "content": "<html></html>"}),
                },
                "_tool_context": context,
            }
        )
        self.assertEqual(item["type"], "function_call")
        self.assertEqual(item["name"], "shell")
        arguments = json.loads(item["arguments"])
        self.assertEqual(arguments["command"][0], "apply_patch")
        self.assertIn("*** Add File: outputs/index.html", arguments["command"][1])

    def test_normalize_responses_tools_injects_proxies_for_shell_only_client(self) -> None:
        from deepcat.translation_server import _normalize_responses_tools

        tools = [{"type": "function", "name": "shell_command", "parameters": {"type": "object"}}]
        injected = _normalize_responses_tools(tools, inject_shell_apply_patch_proxies=True)
        names = [tool["function"]["name"] for tool in injected]
        self.assertIn("apply_patch_add_file", names)
        self.assertIn("apply_patch_update_file", names)
        # chat completions 端点不带注入开关，普通 chat 客户端不应被注入
        plain = _normalize_responses_tools(tools)
        self.assertEqual([tool["function"]["name"] for tool in plain], ["shell_command"])

    def test_http_v1_responses_rewrites_shell_write_without_apply_patch_tool(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        calls: list[list[dict]] = []
        upstream_options: list[dict] = []

        def fake_chat(messages, *args, **kwargs):
            calls.append(list(messages))
            upstream_options.append(dict(kwargs.get("request_options") or {}))
            return {
                "id": "chatcmpl-shell-only",
                "created": 123,
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_shell",
                                    "type": "function",
                                    "function": {
                                        "name": "shell_command",
                                        "arguments": json.dumps(
                                            {"command": "cat > outputs/index.html <<'EOF'\n<html></html>\nEOF"}
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }

        server_mod.chat_completion_response = fake_chat
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "input": "Create a simple HTML page",
                    "tools": [
                        {
                            "type": "function",
                            "name": "shell_command",
                            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
                        }
                    ],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            # 代理工具已注入上游请求
            injected_names = [
                tool["function"]["name"] for tool in (upstream_options[0].get("tools") or [])
            ]
            self.assertIn("apply_patch_add_file", injected_names)
            output = payload["output"][0]
            self.assertEqual(output["type"], "function_call")
            self.assertEqual(output["name"], "shell_command")
            arguments = json.loads(output["arguments"])
            self.assertTrue(arguments["command"].startswith("apply_patch <<'"))
            self.assertIn("*** Add File: outputs/index.html", arguments["command"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response

    def test_http_v1_responses_enforces_apply_patch_over_shell_write(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        calls: list[list[dict]] = []

        def fake_chat(messages, *args, **kwargs):
            calls.append(list(messages))
            corrected = any(
                "FILE-EDIT POLICY VIOLATION" in str(message.get("content") or "")
                for message in messages
            )
            if not corrected:
                return {
                    "id": "chatcmpl-shell",
                    "created": 123,
                    "model": "chat-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_shell",
                                        "type": "function",
                                        "function": {
                                            "name": "shell_command",
                                            "arguments": json.dumps(
                                                {"command": "cat > outputs/index.html <<'EOF'\n<html></html>\nEOF"}
                                            ),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            return {
                "id": "chatcmpl-fixed",
                "created": 124,
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_fixed",
                                    "type": "function",
                                    "function": {
                                        "name": "apply_patch_add_file",
                                        "arguments": json.dumps({"path": "outputs/index.html", "content": "<html></html>"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }

        server_mod.chat_completion_response = fake_chat
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "input": "Create a simple HTML page",
                    "tools": [
                        {"type": "custom", "name": "apply_patch"},
                        {"type": "function", "name": "shell_command", "parameters": {"type": "object"}},
                    ],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(len(calls), 2)
            self.assertEqual(payload["output"][0]["type"], "custom_tool_call")
            self.assertEqual(payload["output"][0]["name"], "apply_patch")
            self.assertIn("*** Add File: outputs/index.html", payload["output"][0]["input"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response

    def test_http_v1_responses_converts_stubborn_shell_write_to_apply_patch(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        calls: list[list[dict]] = []

        def fake_chat(messages, *args, **kwargs):
            calls.append(list(messages))
            return {
                "id": f"chatcmpl-shell-{len(calls)}",
                "created": 123,
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": f"call_shell_{len(calls)}",
                                    "type": "function",
                                    "function": {
                                        "name": "shell_command",
                                        "arguments": json.dumps(
                                            {
                                                "command": (
                                                    "$html = @'\n"
                                                    "<!DOCTYPE html>\n"
                                                    "<html lang=\"zh-CN\">\n"
                                                    "<body>ok</body>\n"
                                                    "</html>\n"
                                                    "'@\n"
                                                    "Set-Content -LiteralPath outputs/stubborn.html -Value $html -Encoding UTF8"
                                                )
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }

        server_mod.chat_completion_response = fake_chat
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "input": "Create a simple HTML page",
                    "tools": [
                        {"type": "custom", "name": "apply_patch"},
                        {"type": "function", "name": "shell_command", "parameters": {"type": "object"}},
                    ],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))

            self.assertEqual(len(calls), 2)
            self.assertEqual(payload["output"][0]["type"], "custom_tool_call")
            self.assertEqual(payload["output"][0]["name"], "apply_patch")
            self.assertIn("File: outputs/stubborn.html", payload["output"][0]["input"])
            self.assertIn("+<body>ok</body>", payload["output"][0]["input"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response

    def test_http_v1_responses_endpoint_reconstructs_patch_from_proxy_tool_call(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        captured: dict[str, object] = {}

        def fake_chat(messages, *args, **kwargs):
            captured["request_options"] = kwargs.get("request_options") or {}
            captured["messages"] = messages
            return {
                "id": "chatcmpl-proxy-patch",
                "created": 123,
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_proxy",
                                    "type": "function",
                                    "function": {
                                        "name": "apply_patch_add_file",
                                        "arguments": json.dumps(
                                            {"path": "outputs/simple.html", "content": "<html>\nhi\n</html>"}
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }

        server_mod.chat_completion_response = fake_chat
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "input": "Create a simple HTML page",
                    "tools": [{"type": "custom", "name": "apply_patch"}],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            request_options = captured["request_options"]
            upstream_tool_names = [
                tool["function"]["name"] for tool in request_options.get("tools", [])
            ]
            self.assertIn("apply_patch_add_file", upstream_tool_names)
            upstream_messages = captured["messages"]
            policy_messages = [
                message
                for message in upstream_messages
                if message.get("role") == "system" and "MANDATORY FILE-EDIT POLICY" in str(message.get("content") or "")
            ]
            self.assertEqual(len(policy_messages), 1)
            self.assertEqual(payload["output"][0]["type"], "custom_tool_call")
            self.assertEqual(payload["output"][0]["name"], "apply_patch")
            self.assertEqual(
                payload["output"][0]["input"],
                "*** Begin Patch\n*** Add File: outputs/simple.html\n+<html>\n+hi\n+</html>\n*** End Patch",
            )
            self.assertEqual(payload["output"][0]["call_id"], "call_proxy")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response

    def test_tool_retry_messages_do_not_reinject_full_invalid_response(self) -> None:
        import deepcat.translation_server as server_mod

        tail_marker = "TAIL_SHOULD_NOT_REENTER_CONTEXT"
        previous_text = "A" * 5000 + tail_marker
        retry_messages = server_mod._tool_retry_messages(
            [{"role": "user", "content": "Create a simple HTML page"}],
            previous_text,
            [{"type": "function", "name": "shell_command", "parameters": {"type": "object"}}],
        )

        joined = "\n".join(str(message.get("content") or "") for message in retry_messages)
        self.assertIn("previous invalid response", str(retry_messages[-1]["content"]).lower())
        self.assertNotIn(tail_marker, joined)
        self.assertFalse(any(message.get("content") == previous_text for message in retry_messages))
        self.assertLess(len(joined), len(previous_text))

    def test_translation_tool_parser_accepts_jsonish_html_command(self) -> None:
        import deepcat.translation_server as server_mod

        text = (
            '```tool_call\n'
            '{"name":"shell_command","arguments":{"command":"$code = @\'\n'
            '<!DOCTYPE html>\n<html lang="zh-CN">\n<body class="page">ok</body>\n'
            "'@ | Set-Content -LiteralPath index.html\"}}\n"
            '```'
        )
        clean, calls = server_mod._parse_tool_calls_from_text(
            text,
            [{"type": "function", "name": "shell_command", "parameters": {"type": "object"}}],
        )

        self.assertEqual(clean, "")
        self.assertEqual(len(calls), 1)
        command = json.loads(calls[0]["function"]["arguments"])["command"]
        self.assertIn('<body class="page">', command)

    def test_http_v1_responses_endpoint_retries_tool_intent_prose_into_tool_call(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        calls = []

        def fake_chat(messages, *args, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                return server_mod.openai_chat_response(
                    "chat-model",
                    "I'll use shell_command to list files, but shell_command wasn't found.",
                    created=123,
                )
            return {
                "id": "chatcmpl-retry",
                "created": 124,
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '```tool_call\n{"name":"shell_command","arguments":{"command":"Get-ChildItem"}}\n```',
                        },
                        "finish_reason": "stop",
                    }
                ],
            }

        server_mod.chat_completion_response = fake_chat
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "input": "List files",
                    "tools": [
                        {
                            "type": "function",
                            "name": "shell_command",
                            "description": "Run a shell command",
                            "parameters": {"type": "object"},
                        }
                    ],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(len(calls), 2)
            # 首次调用走原生 function calling：仅注入 apply_patch 策略 system 消息，
            # 不注入工具指令 system prompt
            self.assertEqual(calls[0][0]["role"], "system")
            self.assertIn("apply_patch", str(calls[0][0]["content"]))
            self.assertEqual(calls[0][1]["role"], "user")
            # 重试调用回退为 prompt 注入：首条为工具指令，末条为纠错提示
            self.assertIn("Allowed tool names: shell_command", calls[1][0]["content"])
            self.assertIn("previous invalid response", str(calls[1][-1]["content"]).lower())
            self.assertEqual([item["type"] for item in payload["output"]], ["function_call"])
            self.assertEqual(payload["output"][0]["name"], "shell_command")
            self.assertEqual(json.loads(payload["output"][0]["arguments"]), {"command": "Get-ChildItem"})
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response

    def test_http_responses_stream_retries_tool_intent_prose_into_tool_call(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        calls = []

        def fake_chat(messages, *args, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                return server_mod.openai_chat_response(
                    "chat-model",
                    "I'll use shell_command to list files.",
                    created=123,
                )
            return {
                "id": "chatcmpl-retry-stream",
                "created": 124,
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"recipient_name":"functions.shell_command","parameters":{"command":"Get-ChildItem"}}',
                        },
                        "finish_reason": "stop",
                    }
                ],
            }

        server_mod.chat_completion_response = fake_chat
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "input": "List files",
                    "stream": True,
                    "tools": [
                        {
                            "type": "function",
                            "name": "shell_command",
                            "description": "Run a shell command",
                            "parameters": {"type": "object"},
                        }
                    ],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read().decode("utf-8")
            events = []
            for block in raw.split("\n\n"):
                for line in block.splitlines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        continue
                    events.append(json.loads(data))
            event_types = [event["type"] for event in events]
            self.assertEqual(len(calls), 2)
            self.assertIn("response.function_call_arguments.done", event_types)
            self.assertIn("response.completed", event_types)
            done = next(event for event in events if event["type"] == "response.function_call_arguments.done")
            self.assertEqual(done["name"], "shell_command")
            self.assertEqual(json.loads(done["arguments"]), {"command": "Get-ChildItem"})
            completed = next(event for event in events if event["type"] == "response.completed")
            self.assertEqual(completed["response"]["output"][0]["type"], "function_call")
            self.assertIn("data: [DONE]", raw)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response

    def test_http_responses_stream_restores_custom_apply_patch_call(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        patch_text = "*** Begin Patch\n*** Add File: docs/test.md\n+hello\n*** End Patch"

        def fake_chat(messages, *args, **kwargs):
            return {
                "id": "chatcmpl-patch-stream",
                "created": 123,
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_patch",
                                    "type": "function",
                                    "function": {
                                        "name": "apply_patch",
                                        "arguments": json.dumps({"input": patch_text}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }

        server_mod.chat_completion_response = fake_chat
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "deepcat-translate",
                    "input": "Edit file",
                    "stream": True,
                    "tools": [{"type": "custom", "name": "apply_patch"}],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read().decode("utf-8")
            events = []
            for block in raw.split("\n\n"):
                for line in block.splitlines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        continue
                    events.append(json.loads(data))
            event_types = [event["type"] for event in events]
            self.assertIn("response.custom_tool_call_input.delta", event_types)
            self.assertIn("response.custom_tool_call_input.done", event_types)
            added = next(event for event in events if event["type"] == "response.output_item.added" and event["item"]["type"] == "custom_tool_call")
            self.assertEqual(added["item"]["name"], "apply_patch")
            delta = next(event for event in events if event["type"] == "response.custom_tool_call_input.delta")
            self.assertEqual(delta["delta"], patch_text)
            done = next(event for event in events if event["type"] == "response.custom_tool_call_input.done")
            self.assertEqual(done["input"], patch_text)
            self.assertEqual(done["call_id"], "call_patch")
            completed = next(event for event in events if event["type"] == "response.completed")
            self.assertEqual(completed["response"]["output"][0]["type"], "custom_tool_call")
            self.assertEqual(completed["response"]["output"][0]["input"], patch_text)
            self.assertIn("data: [DONE]", raw)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response

    def test_http_responses_stream_declares_text_part_before_delta(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response_stream = server_mod.chat_completion_response_stream

        def fake_stream(*args, **kwargs):
            yield {
                "id": "chatcmpl-stream",
                "created": 123,
                "model": "deepcat-translate",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield {
                "id": "chatcmpl-stream",
                "created": 123,
                "model": "deepcat-translate",
                "choices": [{"index": 0, "delta": {"content": "hel"}, "finish_reason": None}],
            }
            yield {
                "id": "chatcmpl-stream",
                "created": 123,
                "model": "deepcat-translate",
                "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": None}],
            }
            yield {
                "id": "chatcmpl-stream",
                "created": 123,
                "model": "deepcat-translate",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }

        server_mod.chat_completion_response_stream = fake_stream
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps({"model": "deepcat-translate", "input": "Hello", "stream": True}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read().decode("utf-8")
            events = []
            for block in raw.split("\n\n"):
                for line in block.splitlines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        continue
                    events.append(json.loads(data))
            event_types = [event["type"] for event in events]
            self.assertLess(event_types.index("response.output_item.added"), event_types.index("response.output_text.delta"))
            self.assertLess(event_types.index("response.content_part.added"), event_types.index("response.output_text.delta"))
            part_added = next(event for event in events if event["type"] == "response.content_part.added")
            deltas = [event for event in events if event["type"] == "response.output_text.delta"]
            self.assertTrue(deltas)
            self.assertTrue(all(delta["item_id"] == part_added["item_id"] for delta in deltas))
            self.assertEqual("".join(delta["delta"] for delta in deltas), "hello")
            done = next(event for event in events if event["type"] == "response.output_text.done")
            self.assertEqual(done["item_id"], part_added["item_id"])
            self.assertEqual(done["text"], "hello")
            self.assertIn("response.completed", event_types)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response_stream = original_chat_completion_response_stream

    def test_http_responses_stream_forwards_delta_before_upstream_finishes(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response_stream = server_mod.chat_completion_response_stream
        allow_finish = threading.Event()

        def fake_stream(*args, **kwargs):
            yield {
                "id": "chatcmpl-stream-live",
                "created": 123,
                "model": "deepcat-translate",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield {
                "id": "chatcmpl-stream-live",
                "created": 123,
                "model": "deepcat-translate",
                "choices": [{"index": 0, "delta": {"content": "hel"}, "finish_reason": None}],
            }
            allow_finish.wait(timeout=5)
            yield {
                "id": "chatcmpl-stream-live",
                "created": 123,
                "model": "deepcat-translate",
                "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": None}],
            }
            yield {
                "id": "chatcmpl-stream-live",
                "created": 123,
                "model": "deepcat-translate",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }

        server_mod.chat_completion_response_stream = fake_stream
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps({"model": "deepcat-translate", "input": "Hello", "stream": True}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            raw_lines: list[str] = []
            with urllib.request.urlopen(req, timeout=1) as response:
                first_delta = None
                while first_delta is None:
                    line = response.readline().decode("utf-8")
                    raw_lines.append(line)
                    self.assertNotEqual(line, "")
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    if event.get("type") == "response.output_text.delta":
                        first_delta = event
                self.assertEqual(first_delta["delta"], "hel")
                self.assertFalse(allow_finish.is_set())
                allow_finish.set()
                tail = response.read().decode("utf-8")
            raw = "".join(raw_lines) + tail
            self.assertIn("response.completed", raw)
            self.assertIn("data: [DONE]", raw)
        finally:
            allow_finish.set()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response_stream = original_chat_completion_response_stream

    def test_http_responses_stream_completes_when_upstream_stream_raises(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response_stream = server_mod.chat_completion_response_stream

        def fake_stream(*args, **kwargs):
            yield {
                "id": "chatcmpl-stream-error",
                "created": 123,
                "model": "deepcat-translate",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            raise RuntimeError("upstream closed early")

        server_mod.chat_completion_response_stream = fake_stream
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps({"model": "deepcat-translate", "input": "Hello", "stream": True}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read().decode("utf-8")
            events = []
            for block in raw.split("\n\n"):
                for line in block.splitlines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        continue
                    events.append(json.loads(data))
            event_types = [event["type"] for event in events]
            self.assertIn("response.completed", event_types)
            completed = next(event for event in events if event["type"] == "response.completed")
            self.assertIn("upstream closed early", completed["response"]["output_text"])
            self.assertIn("data: [DONE]", raw)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response_stream = original_chat_completion_response_stream

    def test_http_responses_stream_converts_chat_tool_call_chunks(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response_stream = server_mod.chat_completion_response_stream

        def fake_stream(*args, **kwargs):
            yield {
                "id": "chatcmpl-tools",
                "created": 123,
                "model": "chat-model",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield {
                "id": "chatcmpl-tools",
                "created": 123,
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "write_file", "arguments": "{\"path\":"},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            }
            yield {
                "id": "chatcmpl-tools",
                "created": 123,
                "model": "chat-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"a.txt\"}"}}]},
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }

        server_mod.chat_completion_response_stream = fake_stream
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps({"model": "deepcat-translate", "input": "Write", "stream": True}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read().decode("utf-8")
            events = []
            for block in raw.split("\n\n"):
                for line in block.splitlines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        continue
                    events.append(json.loads(data))
            event_types = [event["type"] for event in events]
            self.assertIn("response.function_call_arguments.delta", event_types)
            done = next(event for event in events if event["type"] == "response.function_call_arguments.done")
            self.assertEqual(done["call_id"], "call_1")
            self.assertEqual(done["name"], "write_file")
            self.assertEqual(done["arguments"], "{\"path\":\"a.txt\"}")
            completed = next(event for event in events if event["type"] == "response.completed")
            self.assertEqual(completed["response"]["output"][0]["type"], "function_call")
            self.assertEqual(completed["response"]["usage"]["input_tokens"], 3)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response_stream = original_chat_completion_response_stream

    def test_http_responses_expands_namespace_tools_and_restores_namespace_call(self) -> None:
        import deepcat.translation_server as server_mod

        tools = [
            {
                "type": "namespace",
                "name": "codex_app",
                "description": "Codex desktop helpers",
                "tools": [
                    {
                        "type": "function",
                        "name": "read_thread_terminal",
                        "description": "Read terminal output",
                        "parameters": {},
                    }
                ],
            }
        ]

        normalized = server_mod._normalize_responses_tools(tools)

        self.assertEqual(len(normalized), 1)
        fn = normalized[0]["function"]
        self.assertEqual(fn["name"], "codex_app__read_thread_terminal")
        self.assertEqual(fn["parameters"]["type"], "object")
        self.assertEqual(fn["parameters"]["properties"], {})
        self.assertEqual(fn["parameters"]["required"], [])

        item = server_mod._function_call_output_item({
            "id": "call_terminal",
            "type": "function",
            "function": {
                "name": "codex_app__read_thread_terminal",
                "arguments": "{}",
            },
            "_tool_context": server_mod._codex_tool_context(tools),
        })

        self.assertEqual(item["type"], "function_call")
        self.assertEqual(item["name"], "read_thread_terminal")
        self.assertEqual(item["namespace"], "codex_app")
        self.assertEqual(item["arguments"], "{}")

        chat_payload = {
            "id": "chatcmpl-dot-tool",
            "created": 123,
            "model": "deepcat-translate",
            "choices": [{
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": "call_terminal",
                        "type": "function",
                        "function": {
                            "name": "codex_app.read_thread_terminal",
                            "arguments": "{}",
                        },
                    }],
                },
            }],
        }
        finalized = server_mod._finalize_chat_tool_calls(chat_payload, normalized, {"tools": normalized})
        payload = server_mod._chat_completion_to_response_payload(finalized, original_request={"tools": tools})

        self.assertEqual(payload["output"][0]["type"], "function_call")
        self.assertEqual(payload["output"][0]["name"], "read_thread_terminal")
        self.assertEqual(payload["output"][0]["namespace"], "codex_app")

    def test_http_responses_tool_stream_sends_heartbeat_while_waiting(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        original_heartbeat = server_mod.LOCAL_API_SSE_HEARTBEAT_SEC
        allow_finish = threading.Event()

        def fake_response(*args, **kwargs):
            allow_finish.wait(timeout=5)
            return {
                "id": "chatcmpl-tools-heartbeat",
                "created": 123,
                "model": "deepcat-translate",
                "choices": [{
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call_terminal",
                            "type": "function",
                            "function": {
                                "name": "codex_app__read_thread_terminal",
                                "arguments": "{}",
                            },
                        }],
                    },
                }],
            }

        server_mod.chat_completion_response = fake_response
        server_mod.LOCAL_API_SSE_HEARTBEAT_SEC = 0.05
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps({
                "model": "deepcat-translate",
                "input": "read terminal",
                "stream": True,
                "tools": [{
                    "type": "namespace",
                    "name": "codex_app",
                    "tools": [{"type": "function", "name": "read_thread_terminal", "parameters": {}}],
                }],
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            raw_lines: list[str] = []
            with urllib.request.urlopen(req, timeout=1) as response:
                heartbeat_seen = False
                while not heartbeat_seen:
                    line = response.readline().decode("utf-8")
                    raw_lines.append(line)
                    self.assertNotEqual(line, "")
                    if line.startswith(": keep-alive"):
                        heartbeat_seen = True
                self.assertFalse(allow_finish.is_set())
                allow_finish.set()
                tail = response.read().decode("utf-8")
            raw = "".join(raw_lines) + tail
            self.assertGreaterEqual(raw.count('"type": "response.in_progress"'), 2)
            self.assertIn("response.completed", raw)
            self.assertIn('"namespace": "codex_app"', raw)
            self.assertIn("data: [DONE]", raw)
        finally:
            allow_finish.set()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response
            server_mod.LOCAL_API_SSE_HEARTBEAT_SEC = original_heartbeat

    def test_http_responses_tool_stream_cancels_when_client_disconnects(self) -> None:
        import deepcat.translation_server as server_mod

        original_chat_completion_response = server_mod.chat_completion_response
        original_heartbeat = server_mod.LOCAL_API_SSE_HEARTBEAT_SEC
        allow_finish = threading.Event()
        cancelled = threading.Event()
        started = threading.Event()

        def fake_response(*args, **kwargs):
            cancel_event = kwargs.get("cancel_event")
            started.set()
            while not allow_finish.wait(timeout=0.05):
                if cancel_event is not None and cancel_event.is_set():
                    cancelled.set()
                    raise RuntimeError("cancelled")
            return {
                "id": "chatcmpl-tools-cancel",
                "created": 123,
                "model": "deepcat-translate",
                "choices": [{
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call_terminal",
                            "type": "function",
                            "function": {
                                "name": "codex_app__read_thread_terminal",
                                "arguments": "{}",
                            },
                        }],
                    },
                }],
            }

        server_mod.chat_completion_response = fake_response
        server_mod.LOCAL_API_SSE_HEARTBEAT_SEC = 0.05
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps({
                "model": "deepcat-translate",
                "input": "read terminal",
                "stream": True,
                "tools": [{
                    "type": "namespace",
                    "name": "codex_app",
                    "tools": [{"type": "function", "name": "read_thread_terminal", "parameters": {}}],
                }],
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            response = urllib.request.urlopen(req, timeout=1)
            try:
                self.assertTrue(started.wait(timeout=1))
            finally:
                response.close()
            self.assertTrue(cancelled.wait(timeout=2))
            self.assertFalse(allow_finish.is_set())
        finally:
            allow_finish.set()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.chat_completion_response = original_chat_completion_response
            server_mod.LOCAL_API_SSE_HEARTBEAT_SEC = original_heartbeat

    def test_options_preflight_echoes_browser_headers(self) -> None:
        import deepcat.translation_server as server_mod

        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            req = urllib.request.Request(
                f"http://{host}:{port}/translate",
                headers={
                    "Origin": "https://example.test",
                    "Access-Control-Request-Headers": "authorization, x-requested-with",
                    "Access-Control-Request-Private-Network": "true",
                },
                method="OPTIONS",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "https://example.test")
                self.assertEqual(
                    response.headers.get("Access-Control-Allow-Headers"),
                    "authorization, x-requested-with",
                )
                self.assertEqual(response.headers.get("Access-Control-Allow-Private-Network"), "true")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_http_translate_stream_endpoint(self) -> None:
        import deepcat.translation_server as server_mod

        original_translate_text = server_mod.translate_text
        original_translate_stream = server_mod.translate_stream

        def fake_stream(*args, **kwargs):
            yield "ni"
            yield "hao"

        def fail_sync(*args, **kwargs):
            raise AssertionError("sync translate path should not be used for stream=true")

        server_mod.translate_text = fail_sync
        server_mod.translate_stream = fake_stream
        httpd = server_mod.TranslationHTTPServer(
            ("127.0.0.1", 0),
            server_mod.TranslationRequestHandler,
            api_key="test-key",
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            body = json.dumps({"text": "Hello", "target_lang": "zh-CN", "stream": True}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/translate",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-key"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read().decode("utf-8")
            events = []
            done = False
            for block in raw.split("\n\n"):
                if not block.startswith("data: "):
                    continue
                data = block[6:].strip()
                if data == "[DONE]":
                    done = True
                    continue
                events.append(json.loads(data))
            self.assertEqual([event.get("delta") for event in events if event.get("delta")], ["ni", "hao"])
            self.assertTrue(done)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.translate_text = original_translate_text
            server_mod.translate_stream = original_translate_stream

    def test_engine_chat_glm_stream_reads_upstream_sse(self) -> None:
        from deepcat.translator_engine import TranslatorRuntime, _chat_glm_stream

        class StubHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                self.server.last_body = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for text in ("one", "two"):
                    payload = {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": text},
                                "finish_reason": None,
                            }
                        ]
                    }
                    self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        httpd.last_body = {}
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            runtime = TranslatorRuntime(
                source_lang="auto",
                target_lang="Chinese",
                proxy_url="",
                display_name="stub",
                model_type="glm",
                base_url=f"http://{host}:{port}",
                model_name="stub-model",
                api_key="test-key",
                use_proxy=False,
            )
            chunks = list(_chat_glm_stream([{"role": "user", "content": "Hello"}], runtime))
            self.assertEqual(chunks, ["one", "two"])
            self.assertTrue(httpd.last_body["stream"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_engine_openai_responses_non_stream_converts_to_chat_payload(self) -> None:
        from deepcat.translator_engine import TranslatorRuntime, _chat_openai_responses_as_chat_response

        class StubHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                self.server.last_path = self.path
                self.server.last_auth = self.headers.get("Authorization")
                self.server.last_body = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "id": "resp_123",
                            "object": "response",
                            "created_at": 123,
                            "status": "completed",
                            "model": "gpt-5",
                            "output": [
                                {
                                    "type": "message",
                                    "content": [{"type": "output_text", "text": "OK"}],
                                }
                            ],
                            "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                        }
                    ).encode("utf-8")
                )

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        httpd.last_body = {}
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            runtime = TranslatorRuntime(
                source_lang="auto",
                target_lang="Chinese",
                proxy_url="",
                display_name="openai-responses",
                model_type="openai_responses",
                base_url=f"http://{host}:{port}/v1/responses",
                model_name="gpt-5",
                api_key="test-key",
                use_proxy=False,
            )
            payload = _chat_openai_responses_as_chat_response(
                [
                    {"role": "system", "content": "你是助手"},
                    {"role": "user", "content": "Hello"},
                ],
                runtime,
            )
            self.assertEqual(payload["choices"][0]["message"]["content"], "OK")
            self.assertEqual(payload["usage"]["prompt_tokens"], 2)
            self.assertEqual(httpd.last_path, "/v1/responses")
            self.assertEqual(httpd.last_auth, "Bearer test-key")
            self.assertEqual(httpd.last_body["model"], "gpt-5")
            self.assertEqual(httpd.last_body["input"][0]["role"], "developer")
            self.assertNotIn("messages", httpd.last_body)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_engine_openai_responses_stream_reads_output_text_delta(self) -> None:
        from deepcat.translator_engine import TranslatorRuntime, _chat_openai_responses_stream

        class StubHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                self.server.last_path = self.path
                self.server.last_body = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for text in ("one", "two"):
                    payload = {"type": "response.output_text.delta", "delta": text}
                    self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        httpd.last_body = {}
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            runtime = TranslatorRuntime(
                source_lang="auto",
                target_lang="Chinese",
                proxy_url="",
                display_name="openai-responses",
                model_type="openai_responses",
                base_url=f"http://{host}:{port}",
                model_name="gpt-5",
                api_key="test-key",
                use_proxy=False,
            )
            chunks = list(_chat_openai_responses_stream([{"role": "user", "content": "Hello"}], runtime))
            self.assertEqual(chunks, ["one", "two"])
            self.assertEqual(httpd.last_path, "/v1/responses")
            self.assertTrue(httpd.last_body["stream"])
            self.assertEqual(httpd.last_body["input"], [{"role": "user", "content": "Hello"}])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_engine_anthropic_messages_uses_messages_url(self) -> None:
        from deepcat.translator_engine import TranslatorRuntime, _chat_anthropic

        class StubHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                self.server.last_path = self.path
                self.server.last_key = self.headers.get("x-api-key")
                self.server.last_body = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "id": "msg_test",
                            "type": "message",
                            "role": "assistant",
                            "model": "claude-test",
                            "content": [{"type": "text", "text": "OK"}],
                            "stop_reason": "end_turn",
                            "usage": {"input_tokens": 3, "output_tokens": 1},
                        }
                    ).encode("utf-8")
                )

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        httpd.last_path = ""
        httpd.last_key = ""
        httpd.last_body = {}
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            runtime = TranslatorRuntime(
                source_lang="auto",
                target_lang="Chinese",
                proxy_url="",
                display_name="claude",
                model_type="anthropic",
                base_url=f"http://{host}:{port}/v1/messages",
                model_name="claude-test",
                api_key="test-key",
                use_proxy=False,
            )
            result = _chat_anthropic([{"role": "user", "content": "Hello"}], runtime)
            self.assertEqual(result, "OK")
            self.assertEqual(httpd.last_path, "/v1/messages")
            self.assertEqual(httpd.last_key, "test-key")
            self.assertEqual(httpd.last_body["model"], "claude-test")
            self.assertEqual(httpd.last_body["messages"], [{"role": "user", "content": "Hello"}])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_engine_openai_images_generates_markdown_from_b64(self) -> None:
        from deepcat.translator_engine import TranslatorRuntime, _generate_image_openai

        import base64

        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        b64_png = base64.b64encode(png_bytes).decode("ascii")

        class StubHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                self.server.last_path = self.path
                self.server.last_auth = self.headers.get("Authorization")
                self.server.last_body = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {"data": [{"b64_json": b64_png, "revised_prompt": "a cute cat, studio light"}]}
                    ).encode("utf-8")
                )

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        httpd.last_body = {}
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            runtime = TranslatorRuntime(
                source_lang="auto",
                target_lang="Chinese",
                proxy_url="",
                display_name="生图",
                model_type="openai_images",
                base_url=f"http://{host}:{port}/v1/images/generations",
                model_name="agnes-image-2.1-flash",
                api_key="test-key",
                use_proxy=False,
            )
            result = _generate_image_openai([{"role": "user", "content": "画一只猫"}], runtime)
            self.assertIn(f"![generated image](data:image/png;base64,{b64_png})", result)
            self.assertIn("模型修订后的提示词：a cute cat, studio light", result)
            self.assertEqual(httpd.last_path, "/v1/images/generations")
            self.assertEqual(httpd.last_auth, "Bearer test-key")
            self.assertEqual(httpd.last_body["model"], "agnes-image-2.1-flash")
            self.assertEqual(httpd.last_body["prompt"], "画一只猫")
            self.assertEqual(httpd.last_body["response_format"], "b64_json")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_engine_openai_images_retries_without_response_format(self) -> None:
        from deepcat.translator_engine import TranslatorRuntime, _generate_image_openai

        class StubHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                self.server.bodies.append(body)
                if "response_format" in body:
                    payload = {"error": {"message": "Unknown parameter: response_format"}}
                    self.send_response(400)
                else:
                    payload = {"data": [{"url": "https://cdn.example.com/img/1.png"}]}
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode("utf-8"))

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        httpd.bodies = []
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            runtime = TranslatorRuntime(
                source_lang="auto",
                target_lang="Chinese",
                proxy_url="",
                display_name="生图",
                model_type="openai_images",
                base_url=f"http://{host}:{port}/v1/images/generations",
                model_name="gpt-image-1",
                api_key="test-key",
                use_proxy=False,
            )
            result = _generate_image_openai([{"role": "user", "content": "draw a dog"}], runtime)
            self.assertIn("![generated image](https://cdn.example.com/img/1.png)", result)
            self.assertEqual(len(httpd.bodies), 2)
            self.assertIn("response_format", httpd.bodies[0])
            self.assertNotIn("response_format", httpd.bodies[1])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_engine_image_model_rejects_translation_and_dispatches_chat(self) -> None:
        import deepcat.translator_engine as engine

        runtime = engine.TranslatorRuntime(
            source_lang="auto",
            target_lang="Chinese",
            proxy_url="",
            display_name="生图",
            model_type="openai_images",
            base_url="https://api.example.com/v1/images/generations",
            model_name="agnes-image-2.1-flash",
            api_key="test-key",
            use_proxy=False,
        )
        original_loader = engine.load_translator_runtime
        original_generate = engine._generate_image_openai
        engine.load_translator_runtime = lambda *args, **kwargs: runtime  # type: ignore[assignment]
        try:
            # 翻译路径：明确报错而不是把翻译 prompt 发给生图端点
            with self.assertRaises(engine.TranslationError) as ctx:
                engine.translate_text("hello world")
            self.assertIn("图像生成模型", str(ctx.exception))
            with self.assertRaises(engine.TranslationError):
                list(engine.translate_stream("hello world"))

            # QA/chat 路径：分发到生图函数
            calls: list[list[dict]] = []
            engine._generate_image_openai = (  # type: ignore[assignment]
                lambda messages, rt, **kwargs: (calls.append(list(messages)) or "![generated image](data:image/png;base64,AAAA)")
            )
            result = engine.chat_completion([{"role": "user", "content": "画一只猫"}])
            self.assertIn("![generated image]", result)
            chunks = list(engine.chat_completion_stream([{"role": "user", "content": "画一只猫"}]))
            self.assertEqual(len(chunks), 1)
            self.assertIn("![generated image]", chunks[0])
            payload = engine.chat_completion_response([{"role": "user", "content": "画一只猫"}])
            self.assertIn("![generated image]", payload["choices"][0]["message"]["content"])
            self.assertEqual(len(calls), 3)
        finally:
            engine.load_translator_runtime = original_loader  # type: ignore[assignment]
            engine._generate_image_openai = original_generate  # type: ignore[assignment]

    def test_engine_request_closes_upstream_client_on_cancel(self) -> None:
        from deepcat.translator_engine import TranslatorRuntime, _request

        class SlowHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                if length:
                    self.rfile.read(length)
                self.server.started.set()
                self.server.release.wait(timeout=5)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok": true}')
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        httpd.started = threading.Event()
        httpd.release = threading.Event()
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        cancel_event = threading.Event()
        done = threading.Event()
        errors: list[BaseException] = []
        try:
            host, port = httpd.server_address
            runtime = TranslatorRuntime(
                source_lang="auto",
                target_lang="Chinese",
                proxy_url="",
                display_name="stub",
                model_type="glm",
                base_url=f"http://{host}:{port}",
                model_name="stub-model",
                api_key="test-key",
                use_proxy=False,
            )

            def call_request() -> None:
                try:
                    _request(
                        "POST",
                        f"http://{host}:{port}/chat/completions",
                        runtime,
                        headers={"Content-Type": "application/json"},
                        json={"messages": []},
                        timeout=None,
                        cancel_event=cancel_event,
                    )
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    done.set()

            worker = threading.Thread(target=call_request, daemon=True)
            worker.start()
            self.assertTrue(httpd.started.wait(timeout=2))
            cancel_event.set()
            self.assertTrue(done.wait(timeout=2))
            self.assertTrue(errors)
        finally:
            httpd.release.set()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_stream_text_chunks(self) -> None:
        from deepcat.translation_server import _stream_text_chunks

        chunks = _stream_text_chunks("a" * 250, chunk_size=100)
        self.assertEqual("".join(chunks), "a" * 250)
        self.assertGreater(len(chunks), 1)


class TestResponsesToolCallPairing(unittest.TestCase):
    def test_custom_tool_call_output_maps_to_tool_role(self) -> None:
        from deepcat.translation_server import _responses_messages

        payload = {
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "fix the bug"}]},
                {
                    "type": "custom_tool_call",
                    "call_id": "call_abc",
                    "name": "apply_patch",
                    "input": "*** Begin Patch\n*** End Patch",
                },
                {"type": "custom_tool_call_output", "call_id": "call_abc", "output": "Done!"},
            ]
        }
        messages = _responses_messages(payload)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool"])
        self.assertEqual(messages[1]["tool_calls"][0]["id"], "call_abc")
        self.assertEqual(messages[2]["tool_call_id"], "call_abc")
        self.assertEqual(messages[2]["content"], "Done!")

    def test_reasoning_history_attached_as_reasoning_content(self) -> None:
        from deepcat.translation_server import _responses_messages

        payload = {
            "input": [
                {"role": "user", "content": "Create an HTML file"},
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "我先看下目录结构"}]},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "好的，我先查看目录。"}],
                },
                {"type": "function_call", "call_id": "call_ls", "name": "shell_command", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_ls", "output": "outputs\nwork"},
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "目录已确认，直接写文件"}]},
                {"role": "user", "content": "继续"},
            ]
        }
        messages = _responses_messages(payload)
        roles = [m["role"] for m in messages]
        # reasoning 不再生成独立文本消息，而是挂在 assistant 的 reasoning_content 上
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant", "user"])
        joined = json.dumps(messages, ensure_ascii=False)
        self.assertNotIn("[Reasoning summary]", joined)
        assistant = messages[1]
        self.assertEqual(assistant["reasoning_content"], "我先看下目录结构")
        # message 与 tool_calls 合并为同一条 assistant 消息
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_ls")
        self.assertIn("好的，我先查看目录。", str(assistant["content"]))
        # 工具结果之后的孤立 reasoning 挂在新的 assistant 占位消息上
        self.assertEqual(messages[3]["reasoning_content"], "目录已确认，直接写文件")

    def test_consecutive_tool_calls_merge_into_single_assistant_message(self) -> None:
        from deepcat.translation_server import _responses_messages

        payload = {
            "input": [
                {"role": "user", "content": "do it"},
                {"type": "function_call", "call_id": "call_1", "name": "shell_command", "arguments": "{}"},
                {"type": "function_call", "call_id": "call_2", "name": "shell_command", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_1", "output": "a"},
                {"type": "function_call_output", "call_id": "call_2", "output": "b"},
            ]
        }
        messages = _responses_messages(payload)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool", "tool"])
        self.assertEqual([call["id"] for call in messages[1]["tool_calls"]], ["call_1", "call_2"])

    def test_normalize_messages_preserves_reasoning_content(self) -> None:
        from deepcat.translator_engine import _normalize_messages

        normalized = _normalize_messages(
            [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "先查目录",
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "shell_command", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
            ]
        )
        self.assertEqual(normalized[1]["reasoning_content"], "先查目录")
        self.assertEqual(normalized[1]["tool_calls"][0]["id"], "call_1")
        self.assertNotIn("reasoning_content", normalized[0])

    def test_missing_tool_output_gets_placeholder(self) -> None:
        from deepcat.translation_server import _responses_messages

        payload = {
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": "{}"},
                {"role": "user", "content": "continue"},
            ]
        }
        messages = _responses_messages(payload)
        self.assertEqual([m["role"] for m in messages], ["assistant", "tool", "user"])
        self.assertEqual(messages[1]["tool_call_id"], "call_1")
        self.assertTrue(messages[1]["content"])

    def test_trailing_tool_call_without_output_gets_placeholder(self) -> None:
        from deepcat.translation_server import _responses_messages

        payload = {
            "input": [
                {"role": "user", "content": "do it"},
                {"type": "function_call", "call_id": "call_9", "name": "shell", "arguments": "{}"},
            ]
        }
        messages = _responses_messages(payload)
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-1]["tool_call_id"], "call_9")

    def test_orphan_tool_output_downgraded_to_user(self) -> None:
        from deepcat.translation_server import _responses_messages

        payload = {
            "input": [
                {"type": "function_call_output", "call_id": "call_x", "output": "stale result"},
                {"role": "user", "content": "hi"},
            ]
        }
        messages = _responses_messages(payload)
        self.assertTrue(all(m["role"] != "tool" for m in messages))
        self.assertIn("stale result", messages[0]["content"])

    def test_tool_output_without_call_id_paired_in_order(self) -> None:
        from deepcat.translation_server import _responses_messages

        payload = {
            "input": [
                {"type": "function_call", "call_id": "call_a", "name": "shell", "arguments": "{}"},
                {"type": "function_call_output", "output": "ok"},
            ]
        }
        messages = _responses_messages(payload)
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-1]["tool_call_id"], "call_a")


class TestToolIntentDetection(unittest.TestCase):
    _TOOLS = [{"type": "function", "name": "shell_command", "parameters": {"type": "object"}}]

    def test_detects_chinese_regenerate_intent(self) -> None:
        from deepcat.translation_server import _text_looks_like_tool_intent

        text = '找到问题了！文件中的引号被转义成了 `\\"`，导致 HTML 语法错误。让我重新生成正确的文件：'
        self.assertTrue(_text_looks_like_tool_intent(text, self._TOOLS))

    def test_detects_trailing_colon_announcement(self) -> None:
        from deepcat.translation_server import _text_looks_like_tool_intent

        self.assertTrue(_text_looks_like_tool_intent("好的，下面开始处理这个任务：", self._TOOLS))
        self.assertTrue(_text_looks_like_tool_intent("Fixing the file now:", self._TOOLS))

    def test_plain_final_answer_not_flagged(self) -> None:
        from deepcat.translation_server import _text_looks_like_tool_intent

        self.assertFalse(_text_looks_like_tool_intent("所有改动已完成，页面可以正常打开。", self._TOOLS))


if __name__ == "__main__":
    unittest.main()
