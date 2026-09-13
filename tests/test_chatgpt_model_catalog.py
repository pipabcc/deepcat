"""ChatGPT Web 模型目录及本地旧配置迁移。"""

import pytest

from deepcat import chatgpt_web2api as web
from deepcat.settings_store import default_translator_settings, normalize_translator_settings


def test_catalog_keeps_55_and_adds_requested_new_models():
    models = {item["id"]: item for item in web.openai_model_list()}
    assert {"gpt-5-5-thinking", "gpt-5-6", "gpt-6"} <= models.keys()
    assert not {"gpt-5", "gpt-5-1", "gpt-5-2", "gpt-5-3", "gpt-5-3-mini", "gpt-5-mini"} & models.keys()
    assert "gpt-image-2" in models
    assert "max_tokens" not in models["gpt-6"]["metadata"]


def test_browser_auto_routing_is_an_explicit_choice():
    models = {item["id"] for item in web.openai_model_list()}
    assert "auto" in models
    assert web.normalize_model("auto") == "auto"
    assert web.normalize_model("gpt-5-6") == "gpt-5-6"


@pytest.mark.parametrize("requested,expected", [("gpt-5.6", "gpt-5-6"), ("gpt-5-6", "gpt-5-6"), ("gpt-6", "gpt-6")])
def test_new_model_names_do_not_fall_back_to_55(requested, expected):
    assert web.normalize_model(requested) == expected


def test_default_chatgpt_configuration_uses_browser_auto_routing():
    config = default_translator_settings()["model_configs"]["ChatGPT Web"]
    assert config["model_name"] == "auto"
    assert web.DEFAULT_MODEL == "auto"
    assert web.normalize_model(None) == "auto"
    assert web.normalize_model("") == "auto"


@pytest.mark.parametrize("old_model", ["gpt-5", "gpt-5-1", "gpt-5-2", "gpt-5-3", "gpt-5-3-mini", "gpt-5-mini"])
def test_local_old_model_config_migrates_without_losing_credentials_or_selection(old_model):
    incoming = {
        "qa_model": "保留的模型备注",
        "translate_model": "保留的模型备注",
        "model_configs": {
            "保留的模型备注": {
                "base_url": "http://127.0.0.1:8082",
                "model_name": old_model,
                "api_key": "test-session",
                "use_proxy": True,
                "model_type": "chatgpt_web",
                "provider": "ChatGPT Web",
            },
            "其他服务": {
                "base_url": "https://api.example.com/v1",
                "model_name": old_model,
                "model_type": "glm",
                "api_key": "other-test-key",
            },
        },
    }
    result = normalize_translator_settings(incoming)
    local = result["model_configs"]["保留的模型备注"]
    assert local["model_name"] == "auto"
    assert local["api_key"] == "test-session"
    assert local["use_proxy"] is True
    assert result["qa_model"] == result["translate_model"] == "保留的模型备注"
    assert result["model_configs"]["其他服务"]["model_name"] == old_model
    assert incoming["model_configs"]["保留的模型备注"]["model_name"] == old_model


def test_remote_chatgpt_proxy_model_and_custom_ids_are_preserved():
    incoming = {
        "model_configs": {
            "远程代理": {
                "base_url": "https://proxy.example.com/v1",
                "model_name": "gpt-5-3",
                "model_type": "chatgpt_web",
            },
            "自定义编号": {
                "base_url": "http://127.0.0.1:8082",
                "model_name": "custom-gpt-5-3",
                "model_type": "chatgpt_web",
            },
        }
    }
    result = normalize_translator_settings(incoming)["model_configs"]
    assert result["远程代理"]["model_name"] == "gpt-5-3"
    assert result["自定义编号"]["model_name"] == "custom-gpt-5-3"
