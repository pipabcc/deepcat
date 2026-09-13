"""验证连接复用、登录态隔离、失效刷新，以及异常和取消时的释放。"""

import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

import pytest

from deepcat import chatgpt_transport as transport
from deepcat import chatgpt_web2api as web


class Session:
    def __init__(self):
        self.headers = {}
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def clean_cache():
    transport.clear_session_cache()
    yield
    transport.clear_session_cache()


@pytest.fixture
def sessions(monkeypatch):
    created = []
    checked = []

    def make(*args):
        session = Session()
        created.append(session)
        return session

    def check(session, config, access_token):
        checked.append(config["api_key"])
        return {"user": {"id": "test-user"}, "accessToken": "refreshed-test-token"}, "refreshed-test-token", ""

    monkeypatch.setattr(web, "make_session", make)
    monkeypatch.setattr(web, "get_session_info", check)
    return created, checked


def acquire(**overrides):
    config = {"api_key": "Bearer test-token", "base_url": "https://chatgpt.com", **overrides}
    return web._acquire_authenticated_session(config, web.parse_auth_value(config["api_key"]))


def test_repeated_request_reuses_session_and_verified_auth(sessions):
    first, info, token = acquire()
    info["user"]["id"] = "caller-change"
    first.close()
    second, cached_info, cached_token = acquire()
    second.close()

    assert len(sessions[0]) == len(sessions[1]) == 1
    assert cached_info["user"]["id"] == "test-user"
    assert cached_token == token
    assert not sessions[0][0].closed


@pytest.mark.parametrize(
    "changed",
    [
        {"api_key": "Bearer another-token"},
        {"proxy": "socks5h://127.0.0.1:1089"},
        {"base_url": "https://another.example"},
        {"user_agent": "another-client"},
    ],
)
def test_auth_and_connections_do_not_cross_configuration_boundaries(sessions, changed):
    first, _, _ = acquire()
    first.close()
    second, _, _ = acquire(**changed)
    second.close()

    assert len(sessions[0]) == len(sessions[1]) == 2


def test_overlapping_requests_have_exclusive_sessions(sessions):
    first, _, _ = acquire()
    second, _, _ = acquire()
    assert len(sessions[0]) == 2
    second.close()
    first.close()


def test_expired_auth_is_refreshed_on_the_existing_connection(monkeypatch, sessions):
    monkeypatch.setattr(transport, "AUTH_CACHE_SECONDS", 0.001)
    first, _, _ = acquire()
    first.close()
    time.sleep(0.01)
    second, _, _ = acquire()
    second.close()

    assert len(sessions[0]) == 1
    assert len(sessions[1]) == 2


def test_nearly_expired_token_is_not_reused(monkeypatch, sessions):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 10}).encode()).decode().rstrip("=")
    token = "header." + payload + ".signature"
    checked = []

    def check(*args):
        checked.append(True)
        return {"user": {"id": "test-user"}, "accessToken": token}, token, ""

    monkeypatch.setattr(web, "get_session_info", check)
    first, _, _ = acquire()
    first.close()
    second, _, _ = acquire()
    second.close()
    assert len(checked) == 2


def test_failed_validation_does_not_cache_the_fallback_token(monkeypatch, sessions):
    checked = []

    def check(*args):
        checked.append(True)
        return None, "old-token", "temporary validation failure"

    monkeypatch.setattr(web, "get_session_info", check)
    for _ in range(2):
        session, _, _ = acquire()
        session.close()
    assert len(checked) == 2


def test_auth_error_discards_cached_session(sessions):
    with pytest.raises(web.ChatGPTWebUpstreamError):
        session, _, _ = acquire()
        try:
            raise web.ChatGPTWebUpstreamError("登录态被撤销", 401, "auth_error")
        finally:
            session.close()
    assert sessions[0][0].closed
    following, _, _ = acquire()
    following.close()
    assert len(sessions[0]) == len(sessions[1]) == 2


def test_success_status_without_issued_token_does_not_cache_fallback(monkeypatch, sessions):
    checked = []

    def check(*args):
        checked.append(True)
        return {}, "fallback-token", ""

    monkeypatch.setattr(web, "get_session_info", check)
    for _ in range(2):
        session, _, _ = acquire()
        session.close()
    assert len(checked) == 2


def test_cancelled_generator_discards_its_connection(sessions):
    def stream():
        session, _, _ = acquire()
        try:
            yield "第一段"
        finally:
            session.close()

    iterator = stream()
    assert next(iterator) == "第一段"
    iterator.close()
    assert sessions[0][0].closed


def test_clear_cache_closes_idle_and_later_returned_active_connections(sessions):
    active, _, _ = acquire()
    idle, _, _ = acquire()
    idle.close()
    transport.clear_session_cache()
    assert sessions[0][1].closed
    assert not sessions[0][0].closed
    active.close()
    assert sessions[0][0].closed


def test_failed_prepare_closes_the_connection(monkeypatch, sessions):
    monkeypatch.setattr(web, "get_session_info", lambda *args: (None, None, "no active session"))
    with pytest.raises(web.ChatGPTWebError, match="登录态校验失败"):
        acquire()
    assert sessions[0][0].closed


def test_expired_prefetch_is_not_used(monkeypatch):
    key = "expired-prefetch"
    web.clear_sentinel_prefetch()
    monkeypatch.setattr(web, "_SENTINEL_PREFETCH_TTL_SEC", -1)
    web._sentinel_prefetch_store(key, {"openai-sentinel-chat-requirements-token": "expired"})
    calls = []

    def fetch(_session, _config, _token, _device, headers):
        calls.append(True)
        headers["openai-sentinel-chat-requirements-token"] = "fresh"
        return True

    monkeypatch.setattr(web, "_fetch_sentinel_token", fetch)
    headers = {}
    reused = web.warmup_chat_requirements(Session(), {}, "token", "device", headers, state_key=key)
    assert not reused
    assert calls == [True]
    assert headers["openai-sentinel-chat-requirements-token"] == "fresh"


def test_cleared_prefetch_rejects_late_background_result():
    web.clear_sentinel_prefetch()
    generation = web._sentinel_prefetch_generation
    web.clear_sentinel_prefetch()
    web._sentinel_prefetch_store("late", {"openai-sentinel-chat-requirements-token": "stale"}, generation=generation)
    assert "late" not in web._sentinel_prefetch


def test_prefetch_key_is_bound_to_account_token_and_device():
    config = {"base_url": "https://chatgpt.com", "api_key": "Bearer configured-token"}
    keys = {
        web._sentinel_state_key(config, "token-a", "device-a"),
        web._sentinel_state_key(config, "token-b", "device-a"),
        web._sentinel_state_key(config, "token-a", "device-b"),
        web._sentinel_state_key(dict(config, proxy="socks5h://127.0.0.1:1089"), "token-a", "device-a"),
    }
    assert len(keys) == 4
    assert web._sentinel_state_key(dict(config, enable_sentinel_prefetch=False), "token-a", "device-a") is None


def test_background_start_failure_does_not_fail_the_accepted_request(monkeypatch):
    web.clear_sentinel_prefetch()

    def fail_start(self):
        raise RuntimeError("没有可用线程")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    web._prefetch_sentinel_async(
        Session(),
        {"base_url": "https://chatgpt.com", "api_key": "Bearer test-token"},
        "test-token",
        "test-device",
        {},
    )
    assert not web._sentinel_prefetch_inflight


@pytest.mark.parametrize("status,retries", [(403, 1), (429, 0)])
def test_only_rejected_prefetch_is_refreshed_not_quota_errors(monkeypatch, status, retries):
    from types import SimpleNamespace

    detail = {"detail": "Sentinel token expired"} if status == 403 else {"detail": "You've hit your limit"}
    rejected = SimpleNamespace(
        ok=False, status_code=status, headers={}, text=json.dumps(detail), json=lambda: detail, close=lambda: None
    )
    accepted = SimpleNamespace(ok=True, status_code=200)
    requests = []
    warmed = []

    def request(*args):
        requests.append(True)
        return rejected if len(requests) == 1 else accepted

    monkeypatch.setattr(web, "request_conversation", request)
    monkeypatch.setattr(web, "warmup_chat_requirements", lambda session, config, *args: warmed.append(config))
    monkeypatch.setattr(web, "_prefetch_sentinel_async", lambda *args: None)
    result = web.request_conversation_with_requirements(
        Session(), {"_used_sentinel_prefetch": True}, {}, "token", "device", {}
    )
    assert len(warmed) == retries
    assert len(requests) == 1 + retries
    assert result is (accepted if retries else rejected)
    if warmed:
        assert warmed[0]["enable_sentinel_prefetch"] is False


def test_separate_worker_threads_reuse_one_tcp_connection(monkeypatch):
    connections = []
    auth_checks = []

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def get_request(self):
            connected = super().get_request()
            connections.append(connected[1])
            return connected

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            if self.path == "/api/auth/session":
                auth_checks.append(True)
                payload = b'{"user":{"id":"test-user"},"accessToken":"test-token"}'
            else:
                payload = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = Server(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    failures = []
    base_url = f"http://127.0.0.1:{server.server_port}"

    def request():
        try:
            session, _, _ = acquire(base_url=base_url, proxy="")
            try:
                response = session.get(base_url + "/test", timeout=2)
                assert response.status_code == 200
                response.close()
            finally:
                session.close()
        except BaseException as error:
            failures.append(error)

    try:
        for _ in range(3):
            worker = threading.Thread(target=request, daemon=True)
            worker.start()
            worker.join(timeout=5)
            assert not worker.is_alive()
        assert not failures
        assert len(auth_checks) == 1
        assert len(connections) == 1
    finally:
        transport.clear_session_cache()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
