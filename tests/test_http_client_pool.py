"""连接复用不能让取消一个请求误关闭其他请求。"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from deepcat.utils.http_client_pool import HttpClientPool
import deepcat.translator_engine as engine


class Client:
    is_closed = False

    def close(self):
        self.is_closed = True


def test_idle_clients_are_reused_but_active_leases_are_exclusive():
    pool = HttpClientPool(max_idle=1)
    try:
        with pool.lease("one", Client) as first:
            with pool.lease("one", Client) as second:
                assert first is not second
        assert second.is_closed
        with pool.lease("one", Client) as reused:
            assert reused is first
        assert not first.is_closed
    finally:
        pool.close()
    assert first.is_closed


def test_failed_request_discards_its_client():
    pool = HttpClientPool()
    with pytest.raises(ValueError):
        with pool.lease("key", Client) as client:
            raise ValueError("模拟请求失败")
    assert client.is_closed
    pool.close()


def runtime():
    return engine.TranslatorRuntime("auto", "en", "", "test", "glm", "", "test", "", False)


def test_cancellable_request_does_not_close_pooled_client(monkeypatch):
    import httpx

    pool = HttpClientPool()
    monkeypatch.setattr(engine, "_http_client_pool", pool)
    config = runtime()
    try:
        with engine._request_client("http://127.0.0.1", config, 2, cancel_event=None) as shared:
            assert isinstance(shared, httpx.Client)
        with engine._request_client("http://127.0.0.1", config, 2, cancel_event=threading.Event()) as dedicated:
            assert dedicated is not shared
            dedicated.close()
        assert not shared.is_closed
        with engine._request_client("http://127.0.0.1", config, 2, cancel_event=None) as reused:
            assert reused is shared
    finally:
        pool.close()


def test_repeated_requests_reuse_one_tcp_connection(monkeypatch):
    connections = []

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def get_request(self):
            result = super().get_request()
            connections.append(result[1])
            return result

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    pool = HttpClientPool()
    monkeypatch.setattr(engine, "_http_client_pool", pool)
    try:
        for _ in range(5):
            response = engine._request("GET", f"http://127.0.0.1:{server.server_port}/", runtime(), timeout=2)
            assert response.text == "ok"
        assert len(connections) == 1
    finally:
        pool.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
