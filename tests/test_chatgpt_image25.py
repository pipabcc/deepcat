"""网页生图 2.5 别名、上游选择和搜图来源识别。"""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from deepcat import chatgpt_web2api as web
from deepcat.settings_store import default_model_catalog, normalize_translator_settings


def test_image25_is_advertised_without_inventing_model_limits():
    models = {model["id"]: model for model in web.openai_model_list()}
    assert "gpt-image-2.5" in models
    assert web._is_chatgpt_web_image_model("gpt-image-2.5")
    assert "max_tokens" not in models["gpt-image-2.5"]["metadata"]
    assert "codex-gpt-image-2.5" not in models
    assert web.DEFAULT_MODEL == "auto"


def test_default_catalog_exposes_image25_without_changing_existing_selection():
    catalog = default_model_catalog()
    config = catalog["ChatGPT Web 生图 2.5"]
    assert config["model_name"] == "gpt-image-2.5"
    assert config["model_type"] == "openai_images"
    assert config["base_url"] == "http://127.0.0.1:8082/v1/images/generations"
    assert catalog["ChatGPT Web 生图"]["model_name"] == "gpt-image-2"
    normalized = normalize_translator_settings({"qa_model": "ChatGPT Web"})
    assert normalized["qa_model"] == "ChatGPT Web"


@pytest.mark.parametrize("images_endpoint,stream", [(True, False), (False, False), (False, True)])
def test_image25_http_requests_use_dedicated_image_flow(monkeypatch, images_endpoint, stream):
    calls = []

    def generate(prompt, model=None, request_options=None, *, image_uploads=None):
        calls.append((prompt, model))
        return web.ChatGPTWebCompletion("![generated image](data:image/png;base64,QUJDRA==)", model)

    monkeypatch.setattr(web, "generate_chatgpt_web_image", generate)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.ChatGPTWeb2APIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = {"model": "gpt-image-2.5", "stream": stream}
    if images_endpoint:
        path = "/v1/images/generations"
        body["prompt"] = "画猫"
    else:
        path = "/v1/chat/completions"
        body["messages"] = [{"role": "user", "content": "画猫"}]
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = response.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert calls == [("画猫", "gpt-image-2.5")]
    if images_endpoint:
        assert json.loads(payload)["data"][0]["b64_json"] == "QUJDRA=="
    else:
        assert "gpt-image-2.5" in payload
        assert "data:image/png;base64,QUJDRA==" in payload
        if stream:
            assert "data: [DONE]" in payload


@pytest.mark.parametrize(
    "config,expected",
    [
        ({}, "auto"),
        ({"default_upstream_model_name": "gpt-5-6"}, "gpt-5-6"),
        ({"default_upstream_model_name": "gpt-5-6", "default_upstream_model_name_25": "gpt-6"}, "gpt-6"),
        ({"default_upstream_model_name": "gpt-5-6", "default_upstream_model_name_25": " "}, "gpt-5-6"),
    ],
)
def test_image25_prepare_and_generation_choose_the_same_upstream(config, expected):
    captured = []

    def post(_url, **kwargs):
        captured.append(json.loads(kwargs["data"]))
        return SimpleNamespace(ok=True, headers={}, json=lambda: {"conduit_token": "test-conduit"})

    token = web._prepare_image_conversation(
        SimpleNamespace(post=post), config, "画一只猫", None, "device", {}, "gpt-image-2.5"
    )
    body = web._build_image_generation_body("画一只猫", config, "gpt-image-2.5")
    assert token == "test-conduit"
    assert captured[0]["model"] == body["model"] == expected
    assert captured[0]["system_hints"] == body["system_hints"] == ["picture_v2"]
    assert body["messages"][0]["content"]["parts"][-1] == "画一只猫"


def test_image25_override_does_not_change_older_image_aliases():
    config = {"default_upstream_model_name_25": "gpt-6"}
    assert web._chatgpt_web_image_model_slug("gpt-image-2", config) == "auto"
    assert web._chatgpt_web_image_model_slug("codex-gpt-image-2", config) == "codex-gpt-image-2"


def test_request_can_set_image25_upstream_without_mutating_global_config(monkeypatch):
    session = SimpleNamespace(headers={})
    monkeypatch.setattr(
        web, "CONFIG", {"api_key": "oai-did=test-device; __Secure-next-auth.session-token=test-session"}
    )
    monkeypatch.setattr(web, "_acquire_authenticated_session", lambda *_args: (session, {}, "test-token"))
    prepared = web._prepare_chatgpt_request(
        [{"role": "user", "content": "画猫"}],
        "gpt-image-2.5",
        {"default_upstream_model_name_25": "gpt-6"},
    )
    assert prepared.model_id == "gpt-image-2.5"
    assert prepared.config["default_upstream_model_name_25"] == "gpt-6"
    assert "default_upstream_model_name_25" not in web.CONFIG


def test_generation_result_retains_image25_request_id(monkeypatch):
    closed, captured = [], []
    image = "![generated image](data:image/png;base64,QUJDRA==)"
    session = SimpleNamespace(close=lambda: closed.append("session"))
    response = SimpleNamespace(ok=True, close=lambda: closed.append("response"))

    def prepare(_messages, model, _options):
        captured.append(model)
        return SimpleNamespace(session=session, config={}, access_token=None, device_id="device", dynamic_headers={})

    monkeypatch.setattr(web, "_prepare_chatgpt_request", prepare)
    monkeypatch.setattr(web, "warmup_chat_requirements", lambda *_args: None)
    monkeypatch.setattr(web, "_upload_chatgpt_images", lambda *_args: [])
    monkeypatch.setattr(web, "_prepare_image_conversation", lambda *_args: "conduit")
    monkeypatch.setattr(web, "_request_image_generation", lambda *_args: response)
    monkeypatch.setattr(web, "iter_sse_lines", lambda *_args: iter(()))
    monkeypatch.setattr(web, "parse_sse_events", lambda *_args: (image, "conversation", "message", None))
    result = web.generate_chatgpt_web_image("画猫", "gpt-image-2.5")
    assert captured == ["gpt-image-2.5"]
    assert result.model == "gpt-image-2.5"
    assert result.text == image
    assert closed == ["response", "session"]


@pytest.mark.parametrize("nested", [False, True])
def test_search_image_sources_are_not_rendered_as_extra_images(nested):
    thumbnail = "https://images.openai.com/static/opaque-image"
    record = {
        "type": "image",
        "url": "https://medium.com/example/article",
        "image_url": {"url": thumbnail} if nested else thumbnail,
        "source": {"url": "https://example.com/source"},
        "attribution": {"url": "https://example.com/author"},
    }
    assert web._collect_image_urls({"images": [record, record]}) == [thumbnail]


def test_explicit_image_urls_and_file_pointers_are_preserved():
    records = [
        {"type": "image", "url": "https://cdn.example/no-extension"},
        {"url": "https://cdn.example/photo.png"},
        {"thumbnail_url": "https://images.openai.com/static/thumbnail"},
        {"content_type": "image_asset_pointer", "asset_pointer": "file-service://file-generated"},
    ]
    assert web._collect_image_urls(records) == [
        "https://cdn.example/no-extension",
        "https://cdn.example/photo.png",
        "https://images.openai.com/static/thumbnail",
        "https://chatgpt.com/backend-api/files/file-generated/download",
    ]


def test_thumbnail_only_record_does_not_include_article_source():
    record = {
        "type": "image",
        "url": "https://example.com/article",
        "thumbnail_url": "https://images.openai.com/static/thumbnail",
    }
    assert web._collect_image_urls(record) == [record["thumbnail_url"]]
