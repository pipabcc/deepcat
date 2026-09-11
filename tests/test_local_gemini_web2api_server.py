"""内置 Gemini 代理服务的行为：识别、进程内直调、不占用端口。"""

import socket

import httpx

from deepcat.local_gemini_web2api_server import (
    ensure_server,
    identify_spec,
    local_gemini_httpx_transport,
    local_gemini_response,
    release_server,
)


def test_identify_spec_requires_local_8081_base_url():
    assert identify_spec({
        "base_url": "https://www.ai8.my/v1",
        "model_name": "gemini-3.5-flash-thinking",
    }) is None

    assert identify_spec({
        "base_url": "http://127.0.0.1:8081",
        "model_name": "gpt-5",
    }) == "gemini-3.5-flash-thinking"

    assert identify_spec({
        "base_url": "localhost:8081/v1",
        "model_name": "gemini-3.5-flash-thinking",
    }) == "gemini-3.5-flash-thinking"


def test_ensure_server_only_prepares_config():
    cfg = {"base_url": "http://127.0.0.1:8081", "use_proxy": False}

    # 进程内直调没有可启动的服务器：既不阻塞，也不可能因防火墙而超时
    ensure_server(cfg)
    release_server(cfg)


def test_local_gemini_response_serves_health_check_in_process():
    response = local_gemini_response("GET", "http://127.0.0.1:8081/", timeout=5)

    assert response is not None
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "gemini-3.5-flash-thinking" in payload["models"]


def test_local_gemini_response_ignores_non_local_urls():
    assert local_gemini_response("GET", "https://example.com/v1") is None
    assert local_gemini_response("GET", "http://127.0.0.1:8082/v1") is None
    assert local_gemini_httpx_transport("http://127.0.0.1:8082/v1") is None


def test_local_gemini_response_posts_body_without_binding_socket(monkeypatch):
    def fail_bind(*args, **kwargs):
        raise AssertionError("进程内直调不应绑定任何端口")

    monkeypatch.setattr(socket.socket, "bind", fail_bind)

    response = local_gemini_response(
        "POST",
        "http://127.0.0.1:8081/__debug/echo",
        json={"hello": "世界"},
    )

    assert response is not None
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["body_bytes"] > 0
    assert payload["client"][0] == "127.0.0.1"


def test_local_gemini_httpx_transport_serves_in_process():
    transport = local_gemini_httpx_transport("http://127.0.0.1:8081/v1")
    assert transport is not None

    with httpx.Client(transport=transport, trust_env=False) as client:
        response = client.get("http://127.0.0.1:8081/v1/models")

    assert response.status_code == 200
    assert len(response.json()["data"]) > 0
