"""Regression tests for dual-stack loopback listening of the local API.

Covers: Windows hosts resolves localhost to ::1 first, so an IPv4-only listener
leaves Chromium-based clients stuck in SYN_SENT until ERR_CONNECTION_TIMED_OUT.
"""

from __future__ import annotations

import socket
import urllib.request

import pytest

from deepcat.translation_server import (
    TranslationHTTPServer,
    _service_info_payload,
    background_translation_server_address,
    start_background_translation_server,
    stop_background_translation_server,
)

API_KEY = "dual-stack-test-key"


def _service_status(url: str, timeout: float = 5.0) -> int:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(response.status)


def _ipv6_stack_available() -> bool:
    try:
        probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        return False
    probe.close()
    return True


@pytest.fixture()
def running_service():
    host, port = start_background_translation_server(host="127.0.0.1", port=0, api_key=API_KEY)
    try:
        yield host, port
    finally:
        stop_background_translation_server()


def test_ipv4_loopback_is_reachable(running_service) -> None:
    host, port = running_service
    assert _service_status(f"http://{host}:{port}/v1/models") == 200


@pytest.mark.skipif(not _ipv6_stack_available(), reason="system has no IPv6 stack")
def test_ipv6_loopback_is_reachable_on_same_port(running_service) -> None:
    _, port = running_service
    assert _service_status(f"http://[::1]:{port}/v1/models") == 200


def test_reported_address_keeps_ipv4_form(running_service) -> None:
    host, port = running_service
    assert background_translation_server_address() == (host, port)


def test_service_info_reports_bracketed_ipv6_base_url() -> None:
    class _StubHandler:
        pass

    handler = _StubHandler()
    handler.server = TranslationHTTPServer.__new__(TranslationHTTPServer)
    handler.server.server_address = ("::1", 11888, 0, 0)
    payload = _service_info_payload(handler)
    assert payload["base_url"] == "http://[::1]:11888"
    assert payload["endpoints"]["models"] == "http://[::1]:11888/v1/models"
