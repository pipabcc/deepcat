from __future__ import annotations

from deepcat.ui.main_window.search_logic import (
    highlight_search_html,
    search_highlight_terms,
    search_match_snippet,
)
from deepcat.ui.post_capture_actions.context_logic import (
    context_message_identity,
    trim_messages_to_recent_rounds,
)
from deepcat.ui.settings_dialog.model_config_logic import (
    model_config_identity,
    same_model_config_scope,
    unique_model_note_name,
)


def test_search_logic_builds_snippet_and_safe_highlight() -> None:
    record = {
        "source_text": "前置内容 Python 重构 后置内容",
        "result_text": "",
        "prompt_text": "",
    }

    assert search_highlight_terms(" Python   重构 ") == ["Python 重构", "Python", "重构"]
    assert "Python 重构" in search_match_snippet(record, "Python 重构", limit=16)
    assert "<span" in highlight_search_html("<Python>", "Python")
    assert "&lt;" in highlight_search_html("<Python>", "Python")


def test_model_config_logic_normalizes_identity_and_scope() -> None:
    left = model_config_identity(
        "配置A",
        {"provider": "OpenAI", "base_url": "https://example.com/v1/", "model_name": "gpt-x"},
    )
    right = model_config_identity(
        "配置B",
        {"provider": "OpenAI", "base_url": "https://example.com/v1", "model_name": "gpt-x"},
    )

    assert left["base_url"] == "https://example.com/v1"
    assert same_model_config_scope(left, right) is True
    assert unique_model_note_name("模型", ["模型", "模型-A"]) == "模型-B"


def test_context_logic_trims_rounds_and_stabilizes_identity() -> None:
    messages = [
        {"role": "system", "content": "规则"},
        {"role": "user", "content": "问题1"},
        {"role": "assistant", "content": "回答1"},
        {"role": "user", "content": "问题2"},
        {"role": "assistant", "content": "回答2"},
    ]

    assert trim_messages_to_recent_rounds(messages, 1) == messages[-2:]
    assert context_message_identity({"role": "user", "content": {"b": 2, "a": 1}}) == (
        "user",
        '{"a": 1, "b": 2}',
    )
