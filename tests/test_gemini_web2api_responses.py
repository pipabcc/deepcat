from __future__ import annotations

import json
import threading
import unittest
import urllib.request
import urllib.parse
from http.server import HTTPServer
from socketserver import ThreadingMixIn

from deepcat import gemini_web2api as server_mod


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _gemini_raw(text: str) -> str:
    inner = [None, None, None, None, [[None, [text]]]]
    return json.dumps([["wrb.fr", None, json.dumps(inner), "x" * 240]], ensure_ascii=False)


class TestGeminiWeb2APIResponses(unittest.TestCase):
    def _start_server(self):
        httpd = _ThreadedHTTPServer(("127.0.0.1", 0), server_mod.GeminiHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread

    def test_gemini_37_model_configuration(self) -> None:
        self.assertIn("gemini-3.7-flash", server_mod.MODELS)
        model = server_mod.MODELS["gemini-3.7-flash"]
        self.assertEqual(model["mode"], 1)
        # 与相邻的 3.6/3.8-flash 一致：FAST 档默认关闭深度思考（think_mode=0）。
        self.assertEqual(model["think"], 0)
        self.assertIn("Gemini 3.7 Flash", model["desc"])

    def test_gemini_38_model_configuration_uses_web_route_id(self) -> None:
        self.assertIn("gemini-3.8-flash", server_mod.MODELS)
        model = server_mod.MODELS["gemini-3.8-flash"]
        self.assertEqual(model["mode"], 1)
        self.assertEqual(model["think"], 0)
        self.assertEqual(model["hex_id"], "56fdd199312815e2")
        self.assertIn("Gemini 3.8 Flash", model["desc"])

        headers = server_mod.build_gemini_headers(model_hex_id=model["hex_id"])
        self.assertEqual(
            headers["x-goog-ext-525001261-jspb"],
            '[1,null,null,null,"56fdd199312815e2"]',
        )

    def test_extract_gemini_web_context_decodes_dynamic_startup_fields(self) -> None:
        html = (
            r'<script type="application/json">'
            r'{"cfb2h":"boq_assistant-bard-web-server_20260831.15_p2",'
            r'"FdrFJe":3102336271788313810,'
            r'"SNlM0e":"AOvx0lI2C7IIfl3nnFVP2t-QJG90:1788401900700"}'
            r"</script>"
        )

        context = server_mod._extract_gemini_web_context(html)

        self.assertEqual(context["bl"], "boq_assistant-bard-web-server_20260831.15_p2")
        self.assertEqual(context["f_sid"], "3102336271788313810")
        self.assertTrue(context["at"].startswith("AOvx"))

    def test_build_gemini_request_includes_dynamic_web_context(self) -> None:
        original_load_cookie = server_mod.load_cookie
        original_request_context = server_mod._gemini_request_context
        server_mod.load_cookie = lambda: ("SID=example", "sapisid-example")
        server_mod._gemini_request_context = lambda cookie, timeout_sec=None: {
            "bl": "boq_assistant-bard-web-server_20260831.15_p2",
            "f_sid": "3102336271788313810",
            "at": "AOvx0lI2C7IIfl3nnFVP2t-QJG90:1788401900700",
        }
        try:
            url, body, headers, context = server_mod._build_gemini_request(
                "Hello",
                1,
                0,
                model_hex_id="56fdd199312815e2",
            )
        finally:
            server_mod.load_cookie = original_load_cookie
            server_mod._gemini_request_context = original_request_context

        parsed_url = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed_url.query)
        form = urllib.parse.parse_qs(body)
        inner = json.loads(json.loads(form["f.req"][0])[1])

        self.assertEqual(query["bl"], ["boq_assistant-bard-web-server_20260831.15_p2"])
        self.assertEqual(query["f.sid"], ["3102336271788313810"])
        self.assertEqual(form["at"], ["AOvx0lI2C7IIfl3nnFVP2t-QJG90:1788401900700"])
        self.assertEqual(inner[0][0], "Hello")
        self.assertEqual(inner[17], [[0]])
        self.assertEqual(inner[79], 1)
        self.assertEqual(headers["Cookie"], "SID=example")
        self.assertTrue(headers["Authorization"].startswith("SAPISIDHASH "))
        self.assertEqual(
            headers["x-goog-ext-525001261-jspb"],
            '[1,null,null,null,"56fdd199312815e2"]',
        )
        self.assertEqual(context["f_sid"], "3102336271788313810")

    def test_extract_response_text_raises_on_bard_error_info(self) -> None:
        raw_error = ')]}\'\n\n121\n[["wrb.fr",null,null,null,null,[9,null,[["type.googleapis.com/assistant.boq.bard.application.BardErrorInfo",[1060]]]]]]\n'
        with self.assertRaises(RuntimeError) as ctx:
            server_mod.extract_response_text(raw_error)
        self.assertIn("BardErrorInfo [1060]", str(ctx.exception))

    def test_gemini_36_model_uses_authoritative_model_header(self) -> None:
        model = server_mod.MODELS["gemini-3.6-flash"]

        headers = server_mod.build_gemini_headers(
            model_hex_id=model["hex_id"],
        )

        self.assertEqual(model["mode"], 1)
        self.assertEqual(model["think"], 0)
        self.assertEqual(model["hex_id"], "fbb127bbb056c959")
        self.assertEqual(
            headers["x-goog-ext-525001261-jspb"],
            '[1,null,null,null,"fbb127bbb056c959"]',
        )

    def test_nonstream_gemini_36_request_sends_authoritative_model_header(self) -> None:
        original_has_curl = server_mod.HAS_CURL_CFFI
        had_curl_requests = hasattr(server_mod, "curl_requests")
        original_curl_requests = getattr(server_mod, "curl_requests", None)
        original_load_cookie = server_mod.load_cookie
        captured: dict[str, object] = {}

        class FakeResponse:
            status_code = 200
            content = _gemini_raw("Gemini 3.6 response").encode("utf-8")

            def raise_for_status(self):
                return None

        class FakeCurlRequests:
            @staticmethod
            def post(*args, **kwargs):
                captured["headers"] = kwargs["headers"]
                return FakeResponse()

        server_mod.HAS_CURL_CFFI = True
        server_mod.curl_requests = FakeCurlRequests
        server_mod.load_cookie = lambda: ("", "")
        try:
            raw = server_mod.gemini_stream_generate(
                "Hello",
                1,
                0,
                model_hex_id="fbb127bbb056c959",
            )
        finally:
            server_mod.HAS_CURL_CFFI = original_has_curl
            server_mod.load_cookie = original_load_cookie
            if had_curl_requests:
                server_mod.curl_requests = original_curl_requests
            elif hasattr(server_mod, "curl_requests"):
                delattr(server_mod, "curl_requests")

        self.assertEqual(server_mod.extract_response_text(raw), "Gemini 3.6 response")
        self.assertEqual(
            captured["headers"]["x-goog-ext-525001261-jspb"],
            '[1,null,null,null,"fbb127bbb056c959"]',
        )

    def test_stream_gemini_36_request_sends_authoritative_model_header(self) -> None:
        original_has_curl = server_mod.HAS_CURL_CFFI
        had_curl_requests = hasattr(server_mod, "curl_requests")
        original_curl_requests = getattr(server_mod, "curl_requests", None)
        original_load_cookie = server_mod.load_cookie
        captured: dict[str, object] = {}
        payload = (_gemini_raw("Gemini 3.6 stream") + "\n").encode("utf-8")

        class FakeResponse:
            status_code = 200
            headers = {"content-type": "application/json; charset=utf-8"}

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=None):
                yield payload

            def close(self):
                return None

        class FakeCurlRequests:
            @staticmethod
            def post(*args, **kwargs):
                captured["headers"] = kwargs["headers"]
                return FakeResponse()

        server_mod.HAS_CURL_CFFI = True
        server_mod.curl_requests = FakeCurlRequests
        server_mod.load_cookie = lambda: ("", "")
        try:
            text = "".join(server_mod.gemini_stream_generate_iter(
                "Hello",
                1,
                0,
                model_hex_id="fbb127bbb056c959",
            ))
        finally:
            server_mod.HAS_CURL_CFFI = original_has_curl
            server_mod.load_cookie = original_load_cookie
            if had_curl_requests:
                server_mod.curl_requests = original_curl_requests
            elif hasattr(server_mod, "curl_requests"):
                delattr(server_mod, "curl_requests")

        self.assertEqual(text, "Gemini 3.6 stream")
        self.assertEqual(
            captured["headers"]["x-goog-ext-525001261-jspb"],
            '[1,null,null,null,"fbb127bbb056c959"]',
        )

    def test_models_endpoint_lists_gemini_36_flash(self) -> None:
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            with urllib.request.urlopen(f"http://{host}:{port}/v1/models", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))

            models = {item["id"]: item for item in payload["data"]}
            self.assertEqual(response.status, 200)
            self.assertIn("gemini-3.7-flash", models)
            self.assertEqual(models["gemini-3.7-flash"]["owned_by"], "google")
            self.assertIn("gemini-3.6-flash", models)
            self.assertEqual(models["gemini-3.6-flash"]["owned_by"], "google")
            self.assertIn("gemini-3.8-flash", models)
            self.assertEqual(models["gemini-3.8-flash"]["owned_by"], "google")
            # 默认必须只返回原生 Gemini 模型，不泄露外部兼容的 gpt/o3 别名
            self.assertNotIn("gpt-4.1", models)
            self.assertNotIn("gpt-5", models)
            self.assertNotIn("o3", models)
            self.assertNotIn("o4-mini", models)
            self.assertTrue(all(mid.startswith("gemini-") for mid in models))

            # 显式带有 include_aliases=true 时，应能额外获取到兼容别名
            with urllib.request.urlopen(f"http://{host}:{port}/v1/models?include_aliases=true", timeout=5) as response_aliased:
                payload_aliased = json.loads(response_aliased.read().decode("utf-8"))
            models_aliased = {item["id"]: item for item in payload_aliased["data"]}
            self.assertIn("gpt-4.1", models_aliased)
            self.assertIn("o3", models_aliased)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_model_alias_resolution_preserves_external_compatibility(self) -> None:
        handler = server_mod.GeminiHandler
        dummy = type("Dummy", (), {"_debug": lambda *a, **kw: None})()
        req_model, mode, think, hex_id, err = handler._resolve_model(dummy, "o3")
        self.assertIsNone(err)
        self.assertEqual(req_model, "o3")
        self.assertEqual(mode, 2)  # gemini-3.5-flash-thinking mode

        req_model, mode, think, hex_id, err = handler._resolve_model(dummy, "gpt-4.1-mini")
        self.assertIsNone(err)
        self.assertEqual(req_model, "gpt-4.1-mini")
        self.assertEqual(mode, 1)  # gemini-3.5-flash mode

    def test_responses_routes_gemini_36_hex_id_to_upstream(self) -> None:
        original_call = server_mod.GeminiHandler._call_gemini
        captured: dict[str, object] = {}

        def fake_call(
            self,
            prompt,
            model_id,
            think_mode,
            tools,
            options=None,
            timeout_sec=None,
            cancel_event=None,
            model_hex_id=None,
        ):
            captured.update({
                "model_id": model_id,
                "think_mode": think_mode,
                "model_hex_id": model_hex_id,
            })
            return "Gemini 3.6 answered", None

        server_mod.GeminiHandler._call_gemini = fake_call
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps({"model": "gemini-3.6-flash", "input": "Hi"}).encode("utf-8")
            request = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))

            self.assertEqual(response.status, 200)
            self.assertEqual(payload["model"], "gemini-3.6-flash")
            self.assertEqual(payload["output_text"], "Gemini 3.6 answered")
            self.assertEqual(captured["model_id"], 1)
            self.assertEqual(captured["think_mode"], 0)
            self.assertEqual(captured["model_hex_id"], "fbb127bbb056c959")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.GeminiHandler._call_gemini = original_call

    def test_stream_generate_curl_decodes_utf8_split_across_byte_chunks(self) -> None:
        original_has_curl = server_mod.HAS_CURL_CFFI
        had_curl_requests = hasattr(server_mod, "curl_requests")
        original_curl_requests = getattr(server_mod, "curl_requests", None)
        original_load_cookie = server_mod.load_cookie

        expected = "Sakana AI 推出名为 Fugu 的多代理 AI 模型🙂"
        payload = (_gemini_raw(expected) + "\n").encode("utf-8")
        byte_chunks = [payload[index:index + 1] for index in range(len(payload))]

        class FakeResponse:
            status_code = 200
            headers = {"content-type": "application/json; charset=utf-8"}

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=None):
                yield from byte_chunks

            def close(self):
                return None

        class FakeCurlRequests:
            @staticmethod
            def post(*args, **kwargs):
                return FakeResponse()

        server_mod.HAS_CURL_CFFI = True
        server_mod.curl_requests = FakeCurlRequests
        server_mod.load_cookie = lambda: ("", "")
        try:
            actual = "".join(server_mod.gemini_stream_generate_iter("提示", 1, 0))
        finally:
            server_mod.HAS_CURL_CFFI = original_has_curl
            server_mod.load_cookie = original_load_cookie
            if had_curl_requests:
                server_mod.curl_requests = original_curl_requests
            elif hasattr(server_mod, "curl_requests"):
                delattr(server_mod, "curl_requests")

        self.assertEqual(actual, expected)
        self.assertNotIn("�", actual)

    def test_stream_generate_streams_stable_prefix_without_repeating_rewritten_snapshot(self) -> None:
        original_has_curl = server_mod.HAS_CURL_CFFI
        had_curl_requests = hasattr(server_mod, "curl_requests")
        original_curl_requests = getattr(server_mod, "curl_requests", None)
        original_load_cookie = server_mod.load_cookie

        prefix = (
            "测试在中国大陆是否能成功连通外网。"
            + "这是用于确认稳定前缀能够在上游结束前持续送达客户端的说明。" * 3
            + "\nCDN 节点检测："
        )
        provisional_link = prefix + "`https://1"
        provisional_follow_up = (
            prefix
            + "[https://1.1.1.1](https://1.1.1.1)\n"
            + '<ElicitationsGroup message="继续排查"><Elicitation label="诊断 VPS" '
            + 'query="如何诊断 VPS" />{/ Reason: pending'
        )
        final_snapshot = (
            prefix
            + "[https://1.1.1.1](https://1.1.1.1)\n"
            + '<ElicitationsGroup message="继续排查"><Elicitation label="诊断 VPS" '
            + 'query="如何诊断 VPS" /></ElicitationsGroup>'
        )
        payload = (
            _gemini_raw(provisional_link)
            + "\n"
            + _gemini_raw(provisional_follow_up)
            + "\n"
            + _gemini_raw(final_snapshot)
            + "\n"
        ).encode("utf-8")

        class FakeResponse:
            status_code = 200
            headers = {"content-type": "application/json; charset=utf-8"}

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=None):
                yield payload

            def close(self):
                return None

        class FakeCurlRequests:
            @staticmethod
            def post(*args, **kwargs):
                return FakeResponse()

        server_mod.HAS_CURL_CFFI = True
        server_mod.curl_requests = FakeCurlRequests
        server_mod.load_cookie = lambda: ("", "")
        try:
            chunks = list(server_mod.gemini_stream_generate_iter("提示", 2, 0))
        finally:
            server_mod.HAS_CURL_CFFI = original_has_curl
            server_mod.load_cookie = original_load_cookie
            if had_curl_requests:
                server_mod.curl_requests = original_curl_requests
            elif hasattr(server_mod, "curl_requests"):
                delattr(server_mod, "curl_requests")

        expected = server_mod.clean_gemini_text(final_snapshot)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual("".join(chunks), expected)
        self.assertEqual(expected.count(prefix), 1)
        self.assertNotIn("Reason:", expected)
        self.assertNotIn("<ElicitationsGroup", expected)

    def test_parse_tool_calls_accepts_loose_gemini_shapes_and_filters_unknown_tools(self) -> None:
        tools = [{"type": "function", "function": {"name": "shell_command", "parameters": {"type": "object"}}}]

        text, calls = server_mod.parse_tool_calls(
            'I need to inspect files.\n```json\n{"name":"functions.shell_command","arguments":{"command":"Get-ChildItem"}}\n```',
            tools,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "shell_command")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["command"], "Get-ChildItem")
        self.assertNotIn("shell_command", text)

        text, calls = server_mod.parse_tool_calls(
            '{"function_call":{"name":"shell_command","arguments":"{\\"command\\":\\"pwd\\"}"}}',
            tools,
        )
        self.assertEqual(text, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["command"], "pwd")

        text, calls = server_mod.parse_tool_calls(
            '```tool_call\n{"name":"unknown_tool","arguments":{}}\n```',
            tools,
        )
        self.assertEqual(calls, [])
        self.assertIn("unknown_tool", text)

    def test_parse_tool_calls_accepts_embedded_json_and_function_syntax(self) -> None:
        tools = [{"type": "function", "function": {"name": "shell_command", "parameters": {"type": "object"}}}]

        text, calls = server_mod.parse_tool_calls(
            'Let me inspect now: {"recipient_name":"functions.shell_command","parameters":{"command":"rg -n tool ."}}',
            tools,
        )
        self.assertEqual(text, "Let me inspect now:")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "shell_command")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["command"], "rg -n tool .")

        text, calls = server_mod.parse_tool_calls(
            'functions.shell_command({"command":"Get-ChildItem"})',
            tools,
        )
        self.assertEqual(text, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["command"], "Get-ChildItem")

    def test_parse_tool_calls_accepts_jsonish_code_with_unescaped_html_quotes(self) -> None:
        tools = [{"type": "function", "function": {"name": "shell_command", "parameters": {"type": "object"}}}]
        invalid_jsonish = (
            '```tool_call\n'
            '{"name":"shell_command","arguments":{"command":"$code = @\'\n'
            '<!DOCTYPE html>\n<html lang="zh-CN">\n<body class="page">ok</body>\n'
            "'@ | Set-Content -LiteralPath index.html\"}}\n"
            '```'
        )

        text, calls = server_mod.parse_tool_calls(invalid_jsonish, tools)

        self.assertEqual(text, "")
        self.assertEqual(len(calls), 1)
        command = json.loads(calls[0]["function"]["arguments"])["command"]
        self.assertIn('<html lang="zh-CN">', command)
        self.assertIn("Set-Content", command)

    def test_clean_gemini_text_converts_elicitations_group(self) -> None:
        raw = (
            '<ElicitationsGroup message="想要深入了解哪一条新闻？">\n'
            '<Elicitation label="详细了解券商研报“AI幻觉”事件及行业规范" '
            'query="详细介绍方正证券研报因AI幻觉出错的事件，以及金融行业目前对AI投研有哪些监管要求？"/>\n'
            '<Elicitation label="看一看网络机器人流量超越人类的背后技术影响" '
            'query="Cloudflare宣布Agent流量超越人类流量，这意味着什么？对未来的网站运营和AI抓取有什么影响？"/>\n'
            "</ElicitationsGroup>"
        )

        text = server_mod.clean_gemini_text(raw)

        self.assertIn("想要深入了解哪一条新闻？", text)
        self.assertIn("- 详细了解券商研报", text)
        self.assertIn("金融行业目前对AI投研有哪些监管要求", text)
        self.assertNotIn("<ElicitationsGroup", text)
        self.assertNotIn("<Elicitation", text)

    def test_clean_gemini_text_dedupes_unclosed_elicitations_group(self) -> None:
        message = "如果您想继续深入了解某条国内新闻的细节，可以点击下方方向："
        label = "了解2026医保目录初审通过的药品亮点"
        query = "2026年国家医保目录初审通过的557个药品中，有哪些备受关注的创新药或罕见病药？"
        raw = (
            f'<ElicitationsGroup message="{message}">\n'
            "{/ Reason: Procedural requirement to allow user to deep-dive. /}\n"
            f'<Elicitation label="{label}" query="{query}"/>\n'
            "区自律公约，平台已对该账号采取无限期封禁的处置。\n\n"
            f"{message}\n"
            f"{label}：{query}\n"
            "查看中柬、中孟密集外交的核心成果：2026年6月中国与孟加拉国、柬埔寨高层会晤有哪些新动作？"
        )

        text = server_mod.clean_gemini_text(raw)

        self.assertNotIn("<ElicitationsGroup", text)
        self.assertNotIn("<Elicitation", text)
        self.assertNotIn("Reason:", text)
        self.assertIn("区自律公约，平台已对该账号采取无限期封禁的处置。", text)
        self.assertEqual(text.count(message), 1)
        self.assertEqual(text.count(label), 1)

    def test_clean_gemini_text_converts_follow_up(self) -> None:
        raw = (
            '<FollowUp label="想深入了解 AMD 如何在微型 PC 上跑通 2350 亿参数模型吗？" '
            'query="AMD 发布的能跑 2350 亿参数大模型的微型 PC 采用了什么技术？对离线 AI 有什么影响？"/>'
        )

        text = server_mod.clean_gemini_text(raw)

        self.assertIn("- 想深入了解 AMD 如何在微型 PC 上跑通 2350 亿参数模型吗？", text)
        self.assertIn("AMD 发布的能跑 2350 亿参数大模型的微型 PC", text)
        self.assertNotIn("<FollowUp", text)

    def test_call_gemini_retries_tool_intent_prose_into_tool_call(self) -> None:
        original_generate = server_mod.gemini_stream_generate
        calls: list[str] = []

        def fake_generate(prompt, model_id, think_mode, timeout_sec=None, cancel_event=None):
            calls.append(prompt)
            if len(calls) == 1:
                return _gemini_raw("I'll use shell_command to list files, but shell_command wasn't found.")
            return _gemini_raw('```tool_call\n{"name":"shell_command","arguments":{"command":"Get-ChildItem"}}\n```')

        server_mod.gemini_stream_generate = fake_generate
        handler = object.__new__(server_mod.GeminiHandler)
        handler._request_id = "test"
        try:
            text, tool_calls = handler._call_gemini(
                "inspect files",
                1,
                0,
                [{"type": "function", "function": {"name": "shell_command", "parameters": {"type": "object"}}}],
            )
        finally:
            server_mod.gemini_stream_generate = original_generate

        self.assertEqual(text, "")
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["function"]["name"], "shell_command")
        self.assertEqual(json.loads(tool_calls[0]["function"]["arguments"])["command"], "Get-ChildItem")
        self.assertEqual(len(calls), 2)
        self.assertIn("You MUST respond with ONLY a tool_call block", calls[1])

    def test_responses_output_items_split_think_and_preserve_text_before_tool_call(self) -> None:
        output = server_mod.responses_output_items(
            "<think>\nNeed to inspect first.\n</think>\nI will inspect.",
            [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "shell_command", "arguments": "{\"command\":\"pwd\"}"},
            }],
        )

        self.assertEqual([item["type"] for item in output], ["reasoning", "message", "function_call"])
        self.assertEqual(output[0]["summary"][0]["text"], "Need to inspect first.")
        self.assertEqual(output[1]["content"][0]["text"], "I will inspect.")
        self.assertEqual(output[2]["name"], "shell_command")
        self.assertEqual(server_mod.response_output_text(output), "I will inspect.")

    def test_chat_completion_to_response_payload_maps_reasoning_text_tool_calls_and_usage(self) -> None:
        payload = server_mod.chat_completion_to_response_payload({
            "id": "chatcmpl_1",
            "created": 123,
            "model": "gpt-5.4",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "reasoning_content": "Need to inspect.",
                    "content": "Let me inspect.",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "shell_command",
                            "arguments": "{\"command\":\"Get-ChildItem\"}",
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {"cached_tokens": 3},
                "completion_tokens_details": {"reasoning_tokens": 2},
            },
        })

        self.assertEqual(payload["id"], "resp_chatcmpl_1")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual([item["type"] for item in payload["output"]], ["reasoning", "message", "function_call"])
        self.assertEqual(payload["output_text"], "Let me inspect.")
        self.assertEqual(payload["output"][2]["call_id"], "call_1")
        self.assertEqual(payload["usage"]["input_tokens"], 10)
        self.assertEqual(payload["usage"]["input_tokens_details"]["cached_tokens"], 3)
        self.assertEqual(payload["usage"]["output_tokens_details"]["reasoning_tokens"], 2)

    def test_chat_stream_chunks_to_response_payload_merges_tool_call_deltas(self) -> None:
        payload = server_mod.chat_stream_chunks_to_response_payload(
            [
                {
                    "id": "chatcmpl_stream",
                    "created": 123,
                    "model": "gpt-5.4",
                    "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "Need "}}],
                },
                {
                    "id": "chatcmpl_stream",
                    "created": 123,
                    "model": "gpt-5.4",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "content": "Calling.",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "shell_command", "arguments": "{\"command\":"},
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "id": "chatcmpl_stream",
                    "created": 123,
                    "model": "gpt-5.4",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "reasoning_content": "tool.",
                                "tool_calls": [{"index": 0, "function": {"arguments": "\"pwd\"}"}}],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
                },
            ]
        )

        self.assertEqual(payload["id"], "resp_chatcmpl_stream")
        self.assertEqual([item["type"] for item in payload["output"]], ["reasoning", "message", "function_call"])
        self.assertEqual(payload["output"][0]["summary"][0]["text"], "Need tool.")
        self.assertEqual(payload["output"][1]["content"][0]["text"], "Calling.")
        self.assertEqual(payload["output"][2]["call_id"], "call_1")
        self.assertEqual(json.loads(payload["output"][2]["arguments"])["command"], "pwd")
        self.assertEqual(payload["usage"]["input_tokens"], 4)

    def test_responses_stream_emits_reasoning_events_for_leading_think_block(self) -> None:
        original_iter = server_mod.gemini_stream_generate_iter

        def fake_iter(prompt, model_id, think_mode, cancel_event=None):
            yield "<think>Need "
            yield "context.</think>\nDone."

        server_mod.gemini_stream_generate_iter = fake_iter
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps({"model": "gemini-3.5-flash", "input": "Hi", "stream": True}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)

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
            self.assertIn("response.reasoning_summary_text.delta", event_types)
            self.assertIn("response.reasoning_summary_text.done", event_types)
            self.assertIn("response.output_text.delta", event_types)
            completed = next(event for event in events if event["type"] == "response.completed")
            self.assertEqual([item["type"] for item in completed["response"]["output"]], ["reasoning", "message"])
            self.assertEqual(completed["response"]["output_text"], "Done.")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.gemini_stream_generate_iter = original_iter

    def test_responses_non_stream_handles_codex_input_and_tools(self) -> None:
        original_call = server_mod.GeminiHandler._call_gemini
        captured: dict[str, object] = {}

        def fake_call(self, prompt, model_id, think_mode, tools, options=None, timeout_sec=None, cancel_event=None):
            captured["prompt"] = prompt
            captured["tools"] = tools
            return "answered", None

        server_mod.GeminiHandler._call_gemini = fake_call
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "gemini-3.5-flash",
                    "instructions": "Be concise.",
                    "input": [
                        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Hi"}]},
                        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "prior summary"}]},
                        {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": {"path": "a.txt"}},
                        {"type": "function_call_output", "call_id": "call_1", "output": {"text": "file contents"}},
                    ],
                    "tools": [
                        {"type": "function", "name": "read_file", "description": "Read a file", "parameters": {"type": "object"}},
                        {"type": "web_search_preview"},
                    ],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))

            self.assertEqual(payload["object"], "response")
            self.assertEqual(payload["output_text"], "answered")
            self.assertEqual(payload["output"][0]["content"][0]["text"], "answered")
            self.assertIn("Be concise.", str(captured["prompt"]))
            self.assertIn("Hi", str(captured["prompt"]))
            self.assertIn("file contents", str(captured["prompt"]))
            tools = captured["tools"]
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0]["function"]["name"], "read_file")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.GeminiHandler._call_gemini = original_call

    def test_responses_applies_tool_choice_max_tokens_and_parallel_policy(self) -> None:
        original_call = server_mod.GeminiHandler._call_gemini
        captured: dict[str, object] = {}

        def fake_call(self, prompt, model_id, think_mode, tools, options=None, timeout_sec=None, cancel_event=None):
            captured["prompt"] = prompt
            captured["tools"] = tools
            return "", [
                {
                    "id": "call_shell",
                    "type": "function",
                    "function": {"name": "shell_command", "arguments": json.dumps({"command": "pwd"})},
                },
                {
                    "id": "call_read",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps({"path": "a.txt"})},
                },
            ]

        server_mod.GeminiHandler._call_gemini = fake_call
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps(
                {
                    "model": "gemini-3.5-flash",
                    "input": "inspect",
                    "max_output_tokens": 64,
                    "parallel_tool_calls": False,
                    "tool_choice": {"type": "function", "name": "shell_command"},
                    "tools": [
                        {"type": "function", "name": "shell_command", "parameters": {"type": "object"}},
                        {"type": "function", "name": "read_file", "parameters": {"type": "object"}},
                    ],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))

            self.assertEqual(len(captured["tools"]), 1)
            self.assertEqual(captured["tools"][0]["function"]["name"], "shell_command")
            prompt = str(captured["prompt"])
            self.assertIn("64 output tokens", prompt)
            self.assertIn("requires function `shell_command`", prompt)
            self.assertIn("at most one tool call", prompt)
            self.assertEqual([item["type"] for item in payload["output"]], ["function_call"])
            self.assertEqual(payload["output"][0]["name"], "shell_command")
            self.assertEqual(payload["output"][0]["call_id"], "call_shell")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.GeminiHandler._call_gemini = original_call

    def test_responses_expands_namespace_tools_and_restores_namespace_calls(self) -> None:
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

        normalized = server_mod.normalize_responses_tools(tools)

        self.assertEqual(len(normalized), 1)
        fn = normalized[0]["function"]
        self.assertEqual(fn["name"], "codex_app__read_thread_terminal")
        self.assertEqual(fn["parameters"]["type"], "object")
        self.assertEqual(fn["parameters"]["properties"], {})
        self.assertEqual(fn["parameters"]["required"], [])
        self.assertIn("Codex desktop helpers", fn["description"])

        output = server_mod.responses_output_items(
            "",
            [{
                "id": "call_terminal",
                "type": "function",
                "function": {
                    "name": "codex_app__read_thread_terminal",
                    "arguments": "{}",
                },
            }],
            server_mod.codex_tool_context(tools),
        )

        self.assertEqual(output[0]["type"], "function_call")
        self.assertEqual(output[0]["name"], "read_thread_terminal")
        self.assertEqual(output[0]["namespace"], "codex_app")
        self.assertEqual(output[0]["arguments"], "{}")

        clean, calls = server_mod.parse_tool_calls(
            '```tool_call\n{"name":"codex_app.read_thread_terminal","arguments":{}}\n```',
            normalized,
        )
        self.assertEqual(clean, "")
        self.assertEqual(calls[0]["function"]["name"], "codex_app__read_thread_terminal")

        chat_payload = {
            "id": "chatcmpl-dot-tool",
            "created": 123,
            "model": "gemini-3.5-flash-thinking",
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
        payload = server_mod.chat_completion_to_response_payload(chat_payload, original_request={"tools": tools})
        self.assertEqual(payload["output"][0]["type"], "function_call")
        self.assertEqual(payload["output"][0]["name"], "read_thread_terminal")
        self.assertEqual(payload["output"][0]["namespace"], "codex_app")

    def test_responses_non_stream_restores_custom_apply_patch_call(self) -> None:
        original_call = server_mod.GeminiHandler._call_gemini
        patch_text = "*** Begin Patch\n*** Add File: docs/test.md\n+hello\n*** End Patch"

        def fake_call(self, prompt, model_id, think_mode, tools, options=None, timeout_sec=None, cancel_event=None):
            return "", [{
                "id": "call_patch",
                "type": "function",
                "function": {
                    "name": "apply_patch",
                    "arguments": json.dumps({"input": patch_text}),
                },
            }]

        server_mod.GeminiHandler._call_gemini = fake_call
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps({
                "model": "gemini-3.5-flash-thinking",
                "input": "write a file",
                "tools": [{"type": "custom", "name": "apply_patch"}],
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json"},
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
            server_mod.GeminiHandler._call_gemini = original_call

    def test_responses_accepts_openai_model_alias(self) -> None:
        original_call = server_mod.GeminiHandler._call_gemini
        captured: dict[str, object] = {}

        def fake_call(self, prompt, model_id, think_mode, tools, options=None, timeout_sec=None, cancel_event=None):
            captured["model_id"] = model_id
            captured["think_mode"] = think_mode
            return "alias answered", None

        server_mod.GeminiHandler._call_gemini = fake_call
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps({"model": "gpt-5.4", "input": "Hi"}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))

            self.assertEqual(response.status, 200)
            self.assertEqual(payload["model"], "gpt-5.4")
            self.assertEqual(payload["output_text"], "alias answered")
            self.assertEqual(captured["model_id"], 2)
            self.assertEqual(captured["think_mode"], 0)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.GeminiHandler._call_gemini = original_call

    def test_responses_stream_emits_function_call_items_for_codex(self) -> None:
        original_call = server_mod.GeminiHandler._call_gemini

        def fake_call(self, prompt, model_id, think_mode, tools, options=None, timeout_sec=None, cancel_event=None):
            return "", [{
                "id": "call_write",
                "type": "function",
                "function": {
                    "name": "shell_command",
                    "arguments": json.dumps({"command": "Set-Content -Path out.txt -Value ok"}),
                },
            }]

        server_mod.GeminiHandler._call_gemini = fake_call
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps({
                "model": "gemini-3.5-flash",
                "input": "write a file",
                "stream": True,
                "tools": [{"type": "function", "name": "shell_command", "parameters": {"type": "object"}}],
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)

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
            self.assertIn("response.output_item.added", event_types)
            self.assertIn("response.function_call_arguments.delta", event_types)
            self.assertIn("response.function_call_arguments.done", event_types)
            done = next(event for event in events if event["type"] == "response.function_call_arguments.done")
            self.assertEqual(done["name"], "shell_command")
            self.assertEqual(json.loads(done["arguments"])["command"], "Set-Content -Path out.txt -Value ok")
            completed = next(event for event in events if event["type"] == "response.completed")
            self.assertEqual(completed["response"]["output"][0]["type"], "function_call")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.GeminiHandler._call_gemini = original_call

    def test_responses_stream_sends_heartbeat_while_waiting_for_tool_result(self) -> None:
        original_call = server_mod.GeminiHandler._call_gemini
        original_heartbeat = server_mod.CONFIG.get("response_stream_heartbeat_sec")
        allow_finish = threading.Event()

        def fake_call(self, prompt, model_id, think_mode, tools, options=None, timeout_sec=None, cancel_event=None):
            allow_finish.wait(timeout=5)
            return "", [{
                "id": "call_terminal",
                "type": "function",
                "function": {
                    "name": "codex_app__read_thread_terminal",
                    "arguments": "{}",
                },
            }]

        server_mod.GeminiHandler._call_gemini = fake_call
        server_mod.CONFIG["response_stream_heartbeat_sec"] = 0.05
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps({
                "model": "gemini-3.5-flash",
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
                headers={"Content-Type": "application/json"},
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
            self.assertIn("response.completed", raw)
            self.assertIn('"namespace": "codex_app"', raw)
            self.assertIn("data: [DONE]", raw)
        finally:
            allow_finish.set()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.GeminiHandler._call_gemini = original_call
            if original_heartbeat is None:
                server_mod.CONFIG.pop("response_stream_heartbeat_sec", None)
            else:
                server_mod.CONFIG["response_stream_heartbeat_sec"] = original_heartbeat

    def test_responses_stream_restores_custom_apply_patch_call(self) -> None:
        original_call = server_mod.GeminiHandler._call_gemini
        patch_text = "*** Begin Patch\n*** Add File: docs/test.md\n+hello\n*** End Patch"

        def fake_call(self, prompt, model_id, think_mode, tools, options=None, timeout_sec=None, cancel_event=None):
            return "", [{
                "id": "call_patch",
                "type": "function",
                "function": {
                    "name": "apply_patch",
                    "arguments": json.dumps({"input": patch_text}),
                },
            }]

        server_mod.GeminiHandler._call_gemini = fake_call
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps({
                "model": "gemini-3.5-flash-thinking",
                "input": "write a file",
                "stream": True,
                "tools": [{"type": "custom", "name": "apply_patch"}],
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json"},
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
            added = next(
                event for event in events
                if event["type"] == "response.output_item.added"
                and event["item"]["type"] == "custom_tool_call"
            )
            self.assertEqual(added["item"]["name"], "apply_patch")
            delta = next(event for event in events if event["type"] == "response.custom_tool_call_input.delta")
            self.assertEqual(delta["delta"], patch_text)
            completed = next(event for event in events if event["type"] == "response.completed")
            self.assertEqual(completed["response"]["output"][0]["type"], "custom_tool_call")
            self.assertEqual(completed["response"]["output"][0]["input"], patch_text)
            self.assertIn("data: [DONE]", raw)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.GeminiHandler._call_gemini = original_call

    def test_responses_stream_forwards_stable_delta_before_upstream_finishes(self) -> None:
        original_iter = server_mod.gemini_stream_generate_iter
        allow_finish = threading.Event()

        def fake_iter(prompt, model_id, think_mode, cancel_event=None):
            yield "hel"
            allow_finish.wait(timeout=5)
            yield "lo"

        server_mod.gemini_stream_generate_iter = fake_iter
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps({
                "model": "gemini-3.5-flash",
                "input": "Hello",
                "stream": True,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json"},
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
                    streamed_event = json.loads(data)
                    if streamed_event.get("type") == "response.output_text.delta":
                        first_delta = streamed_event
                self.assertEqual(first_delta["delta"], "hel")
                self.assertFalse(allow_finish.is_set())
                allow_finish.set()
                tail = response.read().decode("utf-8")
            raw = "".join(raw_lines) + tail
            events = []
            for line in raw.splitlines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if not data or data == "[DONE]":
                    continue
                events.append(json.loads(data))
            output_text = "".join(
                event.get("delta", "")
                for event in events
                if event.get("type") == "response.output_text.delta"
            )
            self.assertEqual(output_text, "hello")
            self.assertIn("response.completed", raw)
            self.assertIn("data: [DONE]", raw)
        finally:
            allow_finish.set()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.gemini_stream_generate_iter = original_iter

    def test_responses_stream_uses_codex_event_sequence_and_masks_upstream_502(self) -> None:
        original_iter = server_mod.gemini_stream_generate_iter

        def fake_fail(prompt, model_id, think_mode, cancel_event=None):
            raise TimeoutError("simulated upstream timeout")

        server_mod.gemini_stream_generate_iter = fake_fail
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps({"model": "gemini-3.5-flash", "input": "Hi", "stream": True}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)

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
            self.assertIn("response.completed", event_types)
            completed = next(event for event in events if event["type"] == "response.completed")
            self.assertEqual(completed["response"]["status"], "completed")
            self.assertTrue(completed["response"]["output_text"])
            self.assertIn("data: [DONE]", raw)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.gemini_stream_generate_iter = original_iter

    def test_responses_non_stream_masks_upstream_502(self) -> None:
        original_call = server_mod.GeminiHandler._call_gemini

        def fake_fail(self, prompt, model_id, think_mode, tools):
            raise TimeoutError("simulated upstream timeout")

        server_mod.GeminiHandler._call_gemini = fake_fail
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps({"model": "gemini-3.5-flash", "input": "Hi"}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/responses",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)

            self.assertEqual(payload["object"], "response")
            self.assertEqual(payload["status"], "completed")
            self.assertTrue(payload["output_text"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.GeminiHandler._call_gemini = original_call

    def test_responses_input_image_string_url_becomes_placeholder_without_base64(self) -> None:
        data_url = "data:image/png;base64,QUJDREVGR0hJSg=="
        messages = server_mod.responses_to_messages({
            "input": [{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Describe this."},
                    {"type": "input_image", "image_url": data_url},
                ],
            }],
        })

        prompt = server_mod.messages_to_prompt(messages)

        self.assertIn("Describe this.", prompt)
        self.assertTrue("image" in prompt.lower() or "图片" in prompt)
        self.assertNotIn("QUJDREVGR0hJSg", prompt)
        self.assertNotIn("data:image/png;base64", prompt)
        self.assertNotIn("QUJDREVGR0hJSg", server_mod.response_content_text({
            "type": "input_image",
            "image_url": data_url,
        }))

    def test_preview_text_redacts_inline_data_urls(self) -> None:
        preview = server_mod.preview_text(
            '{"image_url":"data:image/png;base64,QUJDREVGR0hJSg=="}',
            200,
        )

        self.assertIn("Inline data URL redacted", preview)
        self.assertNotIn("QUJDREVGR0hJSg", preview)

    def test_latest_gemini_snapshot_replaces_revised_markdown_prefix(self) -> None:
        provisional = "CDN 节点检测：`https://1"
        final = "CDN 节点检测：[https://1.1.1.1](https://1.1.1.1)"

        snapshot = server_mod._select_latest_gemini_text_snapshot("", provisional)
        snapshot = server_mod._select_latest_gemini_text_snapshot(snapshot, final)

        self.assertEqual(snapshot, final)
        self.assertEqual(snapshot.count("CDN 节点检测"), 1)

    def test_latest_gemini_snapshot_ignores_empty_terminal_candidate(self) -> None:
        snapshot = server_mod._select_latest_gemini_text_snapshot("answer", "  \n")

        self.assertEqual(snapshot, "answer")

    def test_stable_gemini_stream_emits_confirmed_prefix_before_finish(self) -> None:
        provisional = "A" * 80 + "`https://1"
        final = "A" * 80 + "[https://1.1.1.1](https://1.1.1.1)"
        stream = server_mod._GeminiStableTextStream(tail_chars=8)

        self.assertEqual(stream.push(provisional), "")
        early_delta = stream.push(final)
        final_delta = stream.finish()

        self.assertTrue(early_delta)
        self.assertLess(len(early_delta), len(final))
        self.assertEqual(early_delta + final_delta, final)
        self.assertNotIn("`https://1", early_delta + final_delta)

    def test_stable_gemini_stream_allows_rewrite_inside_uncommitted_tail(self) -> None:
        committed_prefix = "A" * 30
        first = committed_prefix + "old tail"
        second = first + " extended"
        revised = committed_prefix + "new tail with revised wording"
        final = revised + " and a stable continuation"
        stream = server_mod._GeminiStableTextStream(tail_chars=8)

        self.assertEqual(stream.push(first), "")
        initial_delta = stream.push(second)
        self.assertEqual(initial_delta, committed_prefix)
        self.assertEqual(stream.push(revised), "")
        continued_delta = stream.push(final)
        final_delta = stream.finish()

        self.assertTrue(continued_delta)
        self.assertEqual(initial_delta + continued_delta + final_delta, final)
        self.assertEqual(stream.rewrite_conflicts, 0)

    def test_stream_boundary_does_not_commit_partial_private_marker(self) -> None:
        prefix = "A" * 40
        partial_marker = "```javascript?code_reference&code_event"
        snapshot = prefix + partial_marker

        boundary = server_mod._gemini_stream_safe_boundary(snapshot, len(snapshot) - 4)

        self.assertEqual(boundary, len(prefix))

    def test_chat_completions_stream_emits_rewritten_answer_once(self) -> None:
        original_iter = server_mod.gemini_stream_generate_iter
        final_text = (
            "测试在中国大陆是否能成功连通外网。\n"
            "CDN 节点检测：[https://1.1.1.1](https://1.1.1.1)"
        )

        def fake_iter(prompt, model_id, think_mode, cancel_event=None):
            yield final_text

        server_mod.gemini_stream_generate_iter = fake_iter
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps({
                "model": "gemini-3.5-flash-thinking",
                "messages": [{"role": "user", "content": "测试外网"}],
                "stream": True,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                raw = response.read().decode("utf-8")

            chunks = []
            for line in raw.splitlines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if not data or data == "[DONE]":
                    continue
                event = json.loads(data)
                delta = event.get("choices", [{}])[0].get("delta", {})
                if delta.get("content"):
                    chunks.append(delta["content"])

            actual = "".join(chunks)
            self.assertEqual(actual, final_text)
            self.assertEqual(actual.count("测试在中国大陆"), 1)
            self.assertIn("data: [DONE]", raw)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.gemini_stream_generate_iter = original_iter

    def test_chat_completions_stream_forwards_delta_before_upstream_finishes(self) -> None:
        original_iter = server_mod.gemini_stream_generate_iter
        allow_finish = threading.Event()

        def fake_iter(prompt, model_id, think_mode, cancel_event=None):
            yield "hel"
            allow_finish.wait(timeout=5)
            yield "lo"

        server_mod.gemini_stream_generate_iter = fake_iter
        httpd, thread = self._start_server()
        try:
            host, port = httpd.server_address
            body = json.dumps({
                "model": "gemini-3.5-flash-thinking",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://{host}:{port}/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
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
                    streamed_event = json.loads(data)
                    delta = streamed_event.get("choices", [{}])[0].get("delta", {})
                    if delta.get("content"):
                        first_delta = delta["content"]
                self.assertEqual(first_delta, "hel")
                self.assertFalse(allow_finish.is_set())
                allow_finish.set()
                tail = response.read().decode("utf-8")

            raw = "".join(raw_lines) + tail
            content = []
            for line in raw.splitlines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if not data or data == "[DONE]":
                    continue
                streamed_event = json.loads(data)
                delta = streamed_event.get("choices", [{}])[0].get("delta", {})
                if delta.get("content"):
                    content.append(delta["content"])
            self.assertEqual("".join(content), "hello")
            self.assertIn("data: [DONE]", raw)
        finally:
            allow_finish.set()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            server_mod.gemini_stream_generate_iter = original_iter


    def test_chat_stream_chunks_splits_large_html_delta(self) -> None:
        original_size = server_mod.CONFIG.get("chat_stream_chunk_chars")
        try:
            server_mod.CONFIG["chat_stream_chunk_chars"] = 12
            chunks = list(server_mod.chat_stream_chunks("<main>" + "x" * 40 + "</main>"))
        finally:
            if original_size is None:
                server_mod.CONFIG.pop("chat_stream_chunk_chars", None)
            else:
                server_mod.CONFIG["chat_stream_chunk_chars"] = original_size

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), "<main>" + "x" * 40 + "</main>")

    def test_run_with_response_heartbeats_cancels_upstream_on_client_disconnect(self) -> None:
        handler = object.__new__(server_mod.GeminiHandler)
        handler._request_id = "test"
        handler._client_disconnected = lambda: True
        handler._write_response_sse_heartbeat = lambda: None

        observed: dict = {}
        worker_saw_cancel = threading.Event()

        def slow_upstream(cancel_event):
            observed["cancel_event"] = cancel_event
            if cancel_event.wait(5.0):
                worker_saw_cancel.set()
            return "should not be returned"

        original_heartbeat = server_mod.CONFIG.get("response_stream_heartbeat_sec")
        try:
            server_mod.CONFIG["response_stream_heartbeat_sec"] = 0.05
            with self.assertRaises(ConnectionAbortedError):
                handler._run_with_response_heartbeats(slow_upstream, label="test")
        finally:
            if original_heartbeat is None:
                server_mod.CONFIG.pop("response_stream_heartbeat_sec", None)
            else:
                server_mod.CONFIG["response_stream_heartbeat_sec"] = original_heartbeat

        self.assertTrue(observed["cancel_event"].is_set())
        self.assertTrue(worker_saw_cancel.wait(2.0))

    def test_gemini_stream_generate_skips_retries_after_cancel(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()
        with self.assertRaises(ConnectionAbortedError):
            server_mod.gemini_stream_generate("hello", 1, 0, cancel_event=cancel_event)


if __name__ == "__main__":
    unittest.main()
