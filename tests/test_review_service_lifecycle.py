"""服务资源生命周期和日志脱敏的回归场景。"""

import threading
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

import deepcat.translation_server as service
from deepcat.utils.log_redaction import redact_query, redact_url


def make_handler():
    handler = object.__new__(service.TranslationRequestHandler)
    handler.path = "/v1/responses"
    handler._client_disconnected = lambda: True
    handler._write_sse_heartbeat = lambda: None
    return handler


def test_disconnected_upstream_keeps_slot_until_it_exits(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(service, "_sse_worker_slots", slots)
    release = threading.Event()
    workers = []

    def upstream(cancel_event):
        workers.append(threading.current_thread())
        release.wait(3)

    handler = make_handler()
    try:
        with pytest.raises(service._ClientDisconnectedError):
            handler._run_with_sse_heartbeats(upstream)
        with pytest.raises(service._SSEWorkerLimitError):
            handler._run_with_sse_heartbeats(upstream)
        assert len(workers) == 1 and workers[0].is_alive()
    finally:
        release.set()
        for worker in workers:
            worker.join(2)
    assert slots.acquire(blocking=False)


def test_worker_start_failure_returns_slot(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(service, "_sse_worker_slots", slots)

    def fail_start(self):
        raise RuntimeError("模拟线程启动失败")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="启动失败"):
        make_handler()._run_with_sse_heartbeats(lambda event: None)
    assert slots.acquire(blocking=False)


@pytest.mark.parametrize("key", ["api_key", "APIKEY", "key", "token", "access_token", "client-secret"])
def test_query_keys_are_redacted_without_changing_other_values(key):
    redacted = parse_qs(redact_query(f"{key}=secret-value&model=test-model"))
    assert redacted[key] == ["[REDACTED]"]
    assert redacted["model"] == ["test-model"]


def test_request_logging_redacts_query_and_referrer(monkeypatch):
    handler = make_handler()
    handler.path += "?api%5Fkey=secret-value"
    handler.client_address = ("127.0.0.1", 1)
    handler.headers = {"Referer": "https://user:password@example.test/?token=referrer-secret"}
    logs = []
    monkeypatch.setattr(service.logger, "info", lambda fmt, *args: logs.append(fmt % args))
    handler._log_inbound_request()
    assert "secret-value" not in logs[0]
    assert "referrer-secret" not in logs[0]
    assert "password" not in logs[0]
    assert "example.test" in logs[0]
    assert redact_url("http://[invalid") == "[invalid URL]"


def test_existing_query_authentication_is_preserved():
    handler = make_handler()
    handler.server = SimpleNamespace(api_key="test-token")
    handler.headers = {}
    handler.path += "?api_key=test-token"
    assert handler._is_authorized()
