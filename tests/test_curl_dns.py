"""libcurl 预解析注入：直连时用 Python 的解析结果，配了代理则保持远端解析。"""

import socket

import pytest

from deepcat.utils import curl_dns


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例都从干净缓存开始，避免相互影响。"""
    curl_dns._CACHE.clear()
    yield
    curl_dns._CACHE.clear()


def _fake_getaddrinfo(addresses):
    def fake(host, port, family=0, type=0, proto=0, flags=0):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ]

    return fake


def test_resolve_entries_uses_python_side_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["142.251.150.2", "142.251.151.2"]))

    entries = curl_dns.resolve_entries("https://gemini.google.com/app")

    assert entries == ["gemini.google.com:443:142.251.150.2,142.251.151.2"]


def test_resolve_entries_uses_scheme_default_port(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34"]))

    assert curl_dns.resolve_entries("http://example.com/") == ["example.com:80:93.184.216.34"]


def test_resolve_entries_skipped_when_proxy_configured(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["142.251.150.2"]))

    # 走 socks5h 时域名本就该交给代理解析，注入反而会剥夺远端解析能力
    assert curl_dns.resolve_entries("https://gemini.google.com/app", proxy="socks5h://127.0.0.1:1080") == []


def test_resolve_entries_skips_ip_literals(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["142.251.150.2"]))

    assert curl_dns.resolve_entries("https://142.251.150.2/app") == []


def test_resolve_entries_returns_empty_on_dns_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", boom)

    # 解析失败时保持空结果，让请求退回 libcurl 自身的行为，而不是直接报错
    assert curl_dns.resolve_entries("https://nonexistent.example/") == []


def test_resolve_entries_caches_repeated_lookups(monkeypatch):
    hosts = []

    def counting(host, port, family=0, type=0, proto=0, flags=0):
        hosts.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.251.150.2", port))]

    monkeypatch.setattr(socket, "getaddrinfo", counting)

    curl_dns.resolve_entries("https://gemini.google.com/app")
    curl_dns.resolve_entries("https://gemini.google.com/app")

    assert hosts == ["gemini.google.com"]


class _FakeSession:
    def __init__(self):
        self.curl_options = {}


class _PlainSession:
    """模拟不支持 curl_options 的会话对象。"""


def test_apply_to_session_sets_curl_resolve(monkeypatch):
    from curl_cffi import CurlOpt

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["142.251.150.2"]))
    session = _FakeSession()

    curl_dns.apply_to_session(session, "https://gemini.google.com/app")

    assert session.curl_options[CurlOpt.RESOLVE] == ["gemini.google.com:443:142.251.150.2"]


def test_apply_to_session_keeps_existing_options(monkeypatch):
    from curl_cffi import CurlOpt

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["142.251.150.2"]))
    session = _FakeSession()
    session.curl_options = {CurlOpt.TIMEOUT: 30}

    curl_dns.apply_to_session(session, "https://gemini.google.com/app")

    assert session.curl_options[CurlOpt.TIMEOUT] == 30
    assert CurlOpt.RESOLVE in session.curl_options


def test_apply_to_session_ignores_sessions_without_curl_options(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["142.251.150.2"]))
    session = _PlainSession()

    curl_dns.apply_to_session(session, "https://gemini.google.com/app")

    assert not hasattr(session, "curl_options")
