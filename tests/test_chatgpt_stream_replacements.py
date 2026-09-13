"""覆盖网页重写、引用补全和 HTTP 传输中的流式重复问题。"""

from __future__ import annotations

from contextlib import contextmanager
import http.client
from http.server import ThreadingHTTPServer
import json
import threading

import pytest

from deepcat import chatgpt_web2api as web


def _message(text: str, *, finished: bool = False) -> dict:
    return {
        "conversation_id": "test-conversation",
        "message": {
            "id": "answer",
            "author": {"role": "assistant"},
            "recipient": "all",
            "channel": "final",
            "status": "finished_successfully" if finished else "in_progress",
            "content": {"content_type": "text", "parts": [text]},
        },
    }


def _apply_chunks(chunks) -> str:
    text = ""
    for chunk in chunks:
        text = chunk.snapshot_text if chunk.snapshot_text is not None else text + chunk.text
    return text


NEWS_PREFIX = "今日 AI 新闻汇总\n\n## 1. 模型能力\n第一条新闻。\n\n## 2. 智能体\n第二条新闻。"
NEWS_SNAPSHOT = "# " + NEWS_PREFIX + "\n\n## 3. 机器人\n第三条新闻。"


@pytest.mark.parametrize("replacement_kind", ["message", "message_patch", "part_patch", "parts_patch"])
def test_rewritten_news_snapshot_replaces_previous_text(replacement_kind):
    partial = NEWS_PREFIX + "\ue200cite\ue202turn0search0"
    if replacement_kind == "message":
        replacement = _message(NEWS_SNAPSHOT, finished=True)
    elif replacement_kind == "message_patch":
        replacement = {"p": "/message", "o": "replace", "v": _message(NEWS_SNAPSHOT)["message"]}
    elif replacement_kind == "parts_patch":
        replacement = {"p": "/message/content/parts", "o": "replace", "v": [NEWS_SNAPSHOT]}
    else:
        replacement = {"p": "/message/content/parts/0", "o": "replace", "v": NEWS_SNAPSHOT}
    events = [_message(partial), replacement, {"p": "/message/content/parts/0", "o": "append", "v": "\n结束。"}]

    chunks = list(web.iter_delta_events(json.dumps(event, ensure_ascii=False) for event in events))

    assert _apply_chunks(chunks) == NEWS_SNAPSHOT + "\n结束。"
    assert chunks[0].text == NEWS_PREFIX
    assert chunks[1].snapshot_text == NEWS_SNAPSHOT
    assert chunks[-1].text == "\n结束。"


def test_same_snapshot_replay_does_not_repeat_but_real_repeated_deltas_remain():
    pieces = ["哈", "哈", " ", " ", "\n", "\n", "**", "**"]
    events = [_message("")]
    events.extend({"p": "/message/content/parts/0", "o": "append", "v": piece} for piece in pieces)
    events.extend([_message("".join(pieces)), _message("".join(pieces), finished=True)])

    chunks = list(web.iter_delta_events(json.dumps(event, ensure_ascii=False) for event in events))

    assert [chunk.text for chunk in chunks] == pieces
    assert _apply_chunks(chunks) == "".join(pieces)


def test_bare_patch_continues_last_path_instead_of_last_part():
    event = _message("第一部分")
    event["message"]["content"]["parts"].append("第二部分")
    events = [event, {"p": "/message/content/parts/0", "o": "append", "v": "甲"}, {"v": "乙"}]

    chunks = list(web.iter_delta_events(json.dumps(item, ensure_ascii=False) for item in events))

    assert _apply_chunks(chunks) == "第一部分甲乙第二部分"
    assert web.parse_sse_events(json.dumps(item, ensure_ascii=False) for item in events)[0] == "第一部分甲乙第二部分"


def test_authoritative_snapshot_removes_obsolete_parts():
    event = _message("待修正内容")
    event["message"]["content"]["parts"].append("已经删除的尾段")
    events = [event, _message("修正后的唯一内容", finished=True)]

    chunks = list(web.iter_delta_events(json.dumps(item, ensure_ascii=False) for item in events))

    assert _apply_chunks(chunks) == "修正后的唯一内容"


@pytest.fixture
def upstream(monkeypatch):
    class Response:
        ok = True

        def __init__(self, events):
            self.events = events
            self.release = threading.Event()

        def iter_lines(self, **kwargs):
            for index, event in enumerate(self.events):
                if index == 1 and not self.release.wait(5):
                    raise AssertionError("首段正文未送达，测试无法释放后续快照")
                yield ("data: " + json.dumps(event, ensure_ascii=False)).encode("utf-8")
            yield b"data: [DONE]"

        def close(self):
            self.release.set()

    class Session:
        def close(self):
            pass

    responses = []

    def install(events):
        response = Response(events)
        responses.append(response)
        monkeypatch.setattr(web, "request_conversation_with_requirements", lambda *args, **kwargs: response)
        return response

    def prepare(messages, model=None, request_options=None):
        return web.PreparedChatGPTRequest(
            session=Session(),
            config={"base_url": "https://chatgpt.com", **(request_options or {})},
            prompt_messages=messages,
            conversation_id=None,
            parent_message_id="parent",
            access_token="test-token",
            device_id="test-device",
            dynamic_headers={},
            model_id="gpt-5-5-thinking",
        )

    monkeypatch.setattr(web, "_prepare_chatgpt_request", prepare)
    monkeypatch.setattr(web, "warmup_chat_requirements", lambda *args, **kwargs: None)
    monkeypatch.setattr(web, "_upload_chatgpt_images", lambda *args, **kwargs: [])
    monkeypatch.setattr(web, "log", lambda *args, **kwargs: None)
    yield install
    for response in responses:
        response.release.set()


@contextmanager
def _http_response(options):
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.ChatGPTWeb2APIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps(
                {
                    "model": "gpt-5-5-thinking",
                    "messages": [{"role": "user", "content": "测试流式修正"}],
                    "stream": True,
                    **options,
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        yield response
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _next_content_choice(response):
    while line := response.readline():
        if not line.startswith(b"data: {"):
            continue
        choice = json.loads(line[6:])["choices"][0]
        if choice.get("delta", {}).get("content") or "message" in choice:
            return choice
    raise AssertionError("HTTP 响应未包含正文")


def test_http_stream_delivers_first_text_then_replaces_snapshot_without_duplication(upstream):
    response = upstream(
        [
            _message(NEWS_PREFIX + "\ue200cite\ue202turn0search0"),
            _message(NEWS_SNAPSHOT),
            _message(NEWS_SNAPSHOT),
            _message(NEWS_SNAPSHOT + "\n结束。", finished=True),
        ]
    )
    with _http_response({"stream_snapshot_replacements": True}) as http_response:
        first = _next_content_choice(http_response)
        assert first["delta"]["content"] == NEWS_PREFIX
        assert not response.release.is_set(), "流式首段必须在上游生成完之前送达"
        response.release.set()
        replacement = _next_content_choice(http_response)
        assert replacement.get("deepcat_replace") is True
        assert replacement["message"]["content"] == NEWS_SNAPSHOT
        assert "delta" not in replacement
        last = _next_content_choice(http_response)
        assert last["delta"]["content"] == "\n结束。"
        remainder = http_response.read().decode("utf-8")
        assert "data: [DONE]" in remainder
        assert NEWS_PREFIX not in remainder


def test_http_cache_keeps_legitimate_repeated_deltas(monkeypatch):
    pieces = ["哈", "哈", "\n", "\n", "**", "标题", "**"]

    def fake_stream(*args, **kwargs):
        for piece in pieces:
            yield web.ChatGPTWebCompletion(piece, "gpt-5-5-thinking", "conversation", "answer")

    histories = []
    monkeypatch.setattr(web, "stream_chatgpt_web", fake_stream)
    monkeypatch.setattr(web, "_get_history_hash", lambda history: histories.append(history) or "test-history")
    monkeypatch.setattr(web, "_update_cache", lambda *args: None)
    with _http_response({"enable_conversation_append": True}) as response:
        payload = response.read().decode("utf-8")
    assert "data: [DONE]" in payload
    assert histories[-1][-1]["content"] == "".join(pieces)


def test_handoff_replays_and_corrections_update_existing_http_answer(upstream, monkeypatch):
    response = upstream(
        [
            _message(NEWS_PREFIX),
            {
                "type": "stream_handoff",
                "conversation_id": "test-conversation",
                "options": [{"type": "subscribe_ws_topic", "topic_id": "test-topic"}],
            },
        ]
    )

    def fetch_handoff(*args):
        callback = web._get_handoff_stream_snapshot_callback()
        callback(NEWS_PREFIX, "test-conversation", "answer")
        callback(NEWS_SNAPSHOT, "test-conversation", "answer")
        callback(NEWS_SNAPSHOT, "test-conversation", "answer")
        return NEWS_SNAPSHOT + "\n结束。", "test-conversation", "answer"

    def reject_poll(*args, **kwargs):
        raise AssertionError("完整的续流快照不应额外轮询")

    monkeypatch.setattr(web, "_fetch_handoff_topic_text", fetch_handoff)
    monkeypatch.setattr(web, "_fetch_conversation_with_retry", reject_poll)
    with _http_response({"stream_snapshot_replacements": True}) as http_response:
        assert _next_content_choice(http_response)["delta"]["content"] == NEWS_PREFIX
        response.release.set()
        replacement = _next_content_choice(http_response)
        assert replacement["deepcat_replace"] is True
        assert replacement["message"]["content"] == NEWS_SNAPSHOT
        assert _next_content_choice(http_response)["delta"]["content"] == "\n结束。"
        assert "data: [DONE]" in http_response.read().decode("utf-8")


def test_final_fetch_correction_preserves_streaming_prefix(upstream, monkeypatch):
    response = upstream([_message(NEWS_PREFIX, finished=True)])
    monkeypatch.setattr(web, "_fetch_conversation_with_retry", lambda *args, **kwargs: (NEWS_SNAPSHOT, "answer"))
    with _http_response({"stream_snapshot_replacements": True, "final_fetch_after_stream_completion": True}) as result:
        assert _next_content_choice(result)["delta"]["content"] == NEWS_PREFIX
        replacement = _next_content_choice(result)
        assert replacement["deepcat_replace"] is True
        assert replacement["message"]["content"] == NEWS_SNAPSHOT
        assert "data: [DONE]" in result.read().decode("utf-8")
    response.release.set()


def test_plain_openai_client_does_not_receive_full_snapshot_as_delta(upstream):
    response = upstream([_message(NEWS_PREFIX), _message(NEWS_SNAPSHOT, finished=True)])
    with _http_response({}) as result:
        text = _next_content_choice(result)["delta"]["content"]
        response.release.set()
        payload = result.read().decode("utf-8")
    for line in payload.splitlines():
        if line.startswith("data: {"):
            choice = json.loads(line[6:])["choices"][0]
            assert "deepcat_replace" not in choice
            text += choice.get("delta", {}).get("content", "")
    assert text == NEWS_SNAPSHOT.removeprefix("# ")
    assert text.count("今日 AI 新闻汇总") == 1


@pytest.mark.parametrize("has_offset,expected", [(True, "开头哈哈。"), (False, "开头哈哈哈。")])
def test_websocket_replay_uses_offset_without_deleting_legitimate_repeated_text(monkeypatch, has_offset, expected):
    import asyncio
    import sys
    from types import SimpleNamespace

    def frame(offset, event):
        message = {
            "type": "message",
            "topic_id": "test-topic",
            "payload": {"encoded_item": "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"},
        }
        if has_offset:
            message["offset"] = str(offset)
        return message

    append = {"p": "/message/content/parts/0", "o": "append", "v": "哈"}
    repeated = frame(1, append)
    frames = [
        {"type": "reply", "reply": {"type": "connect"}},
        {"type": "reply", "reply": {"type": "subscribe", "catchups": [frame(0, _message("开头")), repeated]}},
        repeated,
        frame(2, append),
        frame(
            3,
            {
                "p": "",
                "o": "patch",
                "v": [
                    {"p": "/message/content/parts/0", "o": "append", "v": "。"},
                    {"p": "/message/status", "o": "replace", "v": "finished_successfully"},
                ],
            },
        ),
        frame(4, {"type": "message_stream_complete"}),
    ]

    class WebSocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send(self, message):
            pass

        async def recv(self):
            if frames:
                return json.dumps(frames.pop(0), ensure_ascii=False)
            await asyncio.sleep(5)

    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace(connect=lambda *args, **kwargs: WebSocket()))
    monkeypatch.setattr(web, "log", lambda *args, **kwargs: None)
    snapshots = []
    text, _conversation, _message_id = asyncio.run(
        web._consume_handoff_ws_topic(
            "wss://example.invalid/test",
            "test-topic",
            SimpleNamespace(headers={}, cookies={}),
            {"handoff_terminal_quiet_sec": 1},
            None,
            30,
            on_snapshot=lambda snapshot, *args: snapshots.append(snapshot),
        )
    )

    assert text == expected
    assert snapshots[-1] == expected
