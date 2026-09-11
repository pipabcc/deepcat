"""translator_engine 的 SSE 解析与流式连接池复用回归。"""

from __future__ import annotations

from typing import Any, Iterator, List

import httpx
import pytest


class _FakeSSEResponse:
    """只实现 _iter_sse_json 需要的 iter_lines 接口。"""

    def __init__(self, lines: List[bytes]) -> None:
        self._lines = lines

    def iter_lines(self) -> Iterator[bytes]:
        yield from self._lines


def _anthropic_style_lines() -> List[bytes]:
    return [
        b"event: message_start",
        b'data: {"type": "message_start"}',
        b"",
        b"event: content_block_delta",
        b'data: {"type": "content_block_delta", "delta": {"text": "hi"}}',
        b"",
        b"event: message_stop",
        b'data: {"type": "message_stop"}',
        b"",
    ]


def test_iter_sse_json_skips_anthropic_event_fields() -> None:
    from deepcat.translator_engine import _iter_sse_json

    items = list(_iter_sse_json(_FakeSSEResponse(_anthropic_style_lines())))
    assert [item["type"] for item in items] == ["message_start", "content_block_delta", "message_stop"]


def test_iter_sse_json_ignores_invalid_buffered_payload() -> None:
    from deepcat.translator_engine import _iter_sse_json

    lines = [
        b'data: {"a": 1}',
        b"",
        b"event: heartbeat",
        b"data: not-json-at-all",
        b"",
        b'data: {"b": 2}',
        b"",
    ]
    items = list(_iter_sse_json(_FakeSSEResponse(lines)))
    assert items == [{"a": 1}, {"b": 2}]


def test_iter_sse_json_terminates_on_done_and_skips_comments() -> None:
    from deepcat.translator_engine import _iter_sse_json

    lines = [
        b": keep-alive comment",
        b'data: {"a": 1}',
        b"",
        b"data: [DONE]",
        b'data: {"never": true}',
        b"",
    ]
    items = list(_iter_sse_json(_FakeSSEResponse(lines)))
    assert items == [{"a": 1}]


def test_stream_request_client_reuses_pooled_client(monkeypatch) -> None:
    from deepcat import translator_engine as te
    from deepcat.utils.http_client_pool import HttpClientPool

    runtime = te.TranslatorRuntime(
        source_lang="auto",
        target_lang="Chinese",
        proxy_url="",
        display_name="test",
        model_type="openai",
        base_url="https://upstream.example/v1",
        model_name="m",
        api_key="k",
        use_proxy=False,
    )
    url = "https://upstream.example/v1/chat/completions"
    created: List[bool] = []
    clients = []

    def fake_client(url_arg: str, runtime_arg: Any, timeout: Any, *, stream: bool = False) -> httpx.Client:
        created.append(stream)
        client = httpx.Client()
        clients.append(client)
        return client

    # 使用独立池实例，避免污染进程级单例
    monkeypatch.setattr(te, "_http_client_pool", HttpClientPool())
    monkeypatch.setattr(te, "_client", fake_client)
    with te._stream_request_client(url, runtime, 30) as first:
        pass
    with te._stream_request_client(url, runtime, 30) as second:
        pass
    # 流式客户端只创建一次，第二次从池里复用（连接复用）
    assert created == [True]
    assert first is second


def test_stream_request_client_keeps_stream_clients_separate_from_regular(monkeypatch) -> None:
    from deepcat import translator_engine as te
    from deepcat.utils.http_client_pool import HttpClientPool

    runtime = te.TranslatorRuntime(
        source_lang="auto",
        target_lang="Chinese",
        proxy_url="",
        display_name="test",
        model_type="openai",
        base_url="https://upstream.example/v1",
        model_name="m",
        api_key="k",
        use_proxy=False,
    )
    url = "https://upstream.example/v1/chat/completions"
    streams: List[bool] = []

    def fake_client(url_arg: str, runtime_arg: Any, timeout: Any, *, stream: bool = False) -> httpx.Client:
        streams.append(stream)
        return httpx.Client()

    monkeypatch.setattr(te, "_http_client_pool", HttpClientPool())
    monkeypatch.setattr(te, "_client", fake_client)
    with te._stream_request_client(url, runtime, 30) as stream_client:
        pass
    with te._request_client(url, runtime, 30, cancel_event=None) as regular_client:
        pass
    # 流式与普通客户端使用不同的池键，超时配置互不污染
    assert stream_client is not regular_client
    assert streams == [True, False]


@pytest.mark.parametrize(
    "payload, expected",
    [
        ([b"event: ping", b"", b"data: [DONE]", b""], []),
        ([b"retry: 5000", b"id: 42", b'data: {"ok": true}', b""], [{"ok": True}]),
    ],
)
def test_iter_sse_json_handles_control_fields_without_crash(payload: List[bytes], expected: list) -> None:
    from deepcat.translator_engine import _iter_sse_json

    items = list(_iter_sse_json(_FakeSSEResponse(payload)))
    assert items == expected
