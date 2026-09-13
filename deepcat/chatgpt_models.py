"""本地 ChatGPT Web 的文本模型目录，不承诺账号权限或网页模型额度。"""

from __future__ import annotations


DEFAULT_CHATGPT_WEB_MODEL = "auto"

CHATGPT_WEB_TEXT_MODELS = (
    {"id": DEFAULT_CHATGPT_WEB_MODEL, "name": "ChatGPT Web 自动（网页模式）"},
    {"id": "gpt-5-5-thinking", "name": "GPT-5.5 Thinking", "reasoning_type": "reasoning", "reasoning": True},
    # 网页端模型目录使用连字符 slug；gpt-5.6 在适配器中作为输入别名接受。
    {"id": "gpt-5-6", "name": "GPT-5.6 Luna（固定型号）"},
    # 作为用户指定的配置入口保留，实际可调用性由上游账号决定。
    {"id": "gpt-6", "name": "GPT-6"},
)

RETIRED_CHATGPT_WEB_MODELS = frozenset({
    "gpt-5",
    "gpt-5-1",
    "gpt-5-2",
    "gpt-5-3",
    "gpt-5-mini",
    "gpt-5-3-mini",
    "gpt-5-1-instant",
    "gpt-5-2-instant",
    "gpt-5-3-instant",
    "gpt-5-2-think",
    "gpt-5-2-thinking",
})


def upgrade_legacy_chatgpt_model(model_name: str) -> str:
    """只迁移已退下目录的型号，保留模型备注、凭据及用户自定义 ID。"""
    value = str(model_name or "").strip()
    normalized = value.lower().replace("_", "-").replace(".", "-")
    if normalized.startswith("gpt5"):
        normalized = "gpt-5" + normalized[4:]
    if normalized in RETIRED_CHATGPT_WEB_MODELS:
        return DEFAULT_CHATGPT_WEB_MODEL
    return value
