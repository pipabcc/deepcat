"""验证正文在上游结束前到达客户端，并正确隔离网页协议中的非正文事件。"""

from __future__ import annotations

import http.client
from http.server import ThreadingHTTPServer
import json
import threading

import pytest

from deepcat import chatgpt_web2api as web


def _message(text: str, *, message_id: str = "answer", channel: str = "final", finished: bool = False) -> dict:
    return {
        "conversation_id": "test-conversation",
        "message": {
            "id": message_id,
            "author": {"role": "assistant"},
            "recipient": "all",
            "channel": channel,
            "status": "finished_successfully" if finished else "in_progress",
            "content": {"content_type": "text", "parts": [text]},
        },
    }


class _GatedResponse:
    ok = True

    def __init__(self):
        self.release = threading.Event()
        self.closed = False

    def iter_lines(self, **kwargs):
        yield ("data: " + json.dumps(_message("第一段。"), ensure_ascii=False)).encode("utf-8")
        if not self.release.wait(4):
            raise AssertionError("测试没有释放上游后续内容")
        yield ("data: " + json.dumps(_message("第一段。第二段。", finished=True), ensure_ascii=False)).encode("utf-8")
        yield b"data: [DONE]"

    def close(self):
        self.closed = True


@pytest.fixture
def upstream(monkeypatch):
    response = _GatedResponse()

    class Session:
        closed = False

        def close(self):
            self.closed = True

    session = Session()

    def prepare(messages, model=None, request_options=None):
        return web.PreparedChatGPTRequest(
            session=session,
            config={"base_url": "https://chatgpt.com"},
            prompt_messages=messages,
            conversation_id=None,
            parent_message_id="parent",
            access_token="test-token",
            device_id="test-device",
            dynamic_headers={},
            model_id=web.normalize_model(model),
        )

    def reject_unnecessary_fetch(*args, **kwargs):
        raise AssertionError("正常完成的 SSE 不应再通过轮询延迟返回")

    monkeypatch.setattr(web, "_prepare_chatgpt_request", prepare)
    monkeypatch.setattr(web, "warmup_chat_requirements", lambda *args, **kwargs: None)
    monkeypatch.setattr(web, "_upload_chatgpt_images", lambda *args, **kwargs: [])
    monkeypatch.setattr(web, "request_conversation_with_requirements", lambda *args, **kwargs: response)
    monkeypatch.setattr(web, "_fetch_conversation_with_retry", reject_unnecessary_fetch)
    monkeypatch.setattr(web, "log", lambda *args, **kwargs: None)
    yield response, session
    response.release.set()


@pytest.mark.parametrize("model", ["gpt-5-5-thinking", "gpt-5.6", "gpt-6"])
def test_first_answer_delta_arrives_before_upstream_finishes(upstream, model):
    response, session = upstream
    chunks = []
    failures = []
    first_chunk = threading.Event()
    finished = threading.Event()

    def consume():
        try:
            for chunk in web.stream_chatgpt_web([{"role": "user", "content": "测试分段输出"}], model):
                chunks.append(chunk.text)
                first_chunk.set()
        except Exception as error:
            failures.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=consume, daemon=True)
    worker.start()
    try:
        assert first_chunk.wait(1.5), "上游仍在生成时，客户端没有收到正文"
        assert chunks == ["第一段。"]
        assert not finished.is_set()
    finally:
        response.release.set()
        worker.join(timeout=5)
    assert finished.is_set()
    assert not failures
    assert "".join(chunks) == "第一段。第二段。"
    assert response.closed and session.closed


def test_http_client_receives_body_before_upstream_finishes(upstream):
    response, _session = upstream
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.ChatGPTWeb2APIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps(
                {"model": "gpt-5-5-thinking", "messages": [{"role": "user", "content": "hello"}], "stream": True}
            ),
            headers={"Content-Type": "application/json"},
        )
        result = connection.getresponse()
        assert result.status == 200
        assert result.getheader("X-Accel-Buffering") == "no"
        first_text = ""
        while not first_text:
            line = result.readline().decode("utf-8").strip()
            if line.startswith("data: {"):
                event = json.loads(line[6:])
                first_text = event["choices"][0]["delta"].get("content", "")
        assert first_text == "第一段。"
        assert not response.release.is_set()
        response.release.set()
        remainder = result.read().decode("utf-8")
        assert "第二段。" in remainder
        assert "data: [DONE]" in remainder
    finally:
        response.release.set()
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sse_accepts_data_without_space_and_stops_at_done():
    class Response:
        def iter_lines(self, **kwargs):
            yield b'data:{"v":"hello"}'
            yield b"data:[DONE]"
            raise AssertionError("结束标记之后不应继续等待读取")

    assert list(web.iter_sse_lines(Response())) == ['{"v":"hello"}', "[DONE]"]


def test_analysis_channel_is_not_mixed_into_answer():
    events = [
        _message("内部推理不应显示为正文", message_id="thought", channel="analysis"),
        _message("可见答案", message_id="answer", channel="final", finished=True),
    ]
    chunks = list(web.iter_delta_events(json.dumps(event, ensure_ascii=False) for event in events))
    assert "".join(chunk.text for chunk in chunks) == "可见答案"


def test_metadata_only_patches_switch_from_analysis_to_final():
    events = [
        {
            "p": "",
            "o": "patch",
            "v": [
                {"p": "/message/id", "o": "replace", "v": "thought"},
                {"p": "/message/author/role", "o": "replace", "v": "assistant"},
                {"p": "/message/channel", "o": "replace", "v": "analysis"},
                {"p": "/message/content/parts/0", "o": "append", "v": "不输出的推理"},
            ],
        },
        {
            "p": "",
            "o": "patch",
            "v": [
                {"p": "/message/id", "o": "replace", "v": "answer"},
                {"p": "/message/channel", "o": "replace", "v": "final"},
                {"p": "/message/content/content_type", "o": "replace", "v": "text"},
                {"p": "/message/content/parts/0", "o": "append", "v": "第一段。"},
            ],
        },
        {"v": "第二段。"},
    ]
    chunks = list(web.iter_delta_events(json.dumps(event, ensure_ascii=False) for event in events))
    assert "".join(chunk.text for chunk in chunks) == "第一段。第二段。"
    assert all(chunk.message_id == "answer" for chunk in chunks)


def test_whole_message_patch_is_streamed_as_answer():
    event = {"p": "/message", "o": "replace", "v": _message("完整消息补丁")["message"]}
    chunks = list(web.iter_delta_events([json.dumps(event, ensure_ascii=False)]))
    assert "".join(chunk.text for chunk in chunks) == "完整消息补丁"


def test_buffered_compatibility_is_explicitly_opt_in():
    assert not web._buffer_stream_until_handoff_decision({}, "gpt-5-5-thinking")
    assert not web._final_fetch_after_stream_completion({}, "gpt-5-5-thinking")
    assert web._buffer_stream_until_handoff_decision({"buffer_stream_until_handoff": True}, "gpt-5-5-thinking")
    assert web._final_fetch_after_stream_completion({"final_fetch_after_stream_completion": True}, "gpt-5-5-thinking")
