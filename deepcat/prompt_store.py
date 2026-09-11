"""自定义提示词的 SQLite 存储层。

将提示词数据独立存储在 data/prompt.db，与 settings.json 解耦。
支持从旧版 settings.json ui 中的提示词与占位卡片自动迁移。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from deepcat.utils.paths import get_app_dir

logger = logging.getLogger(__name__)

# 默认卡片数据定义
DEFAULT_CHAT_PLACEHOLDER_CARDS = [
    {
        "id": "translate",
        "title": "多语翻译",
        "prompt": "请帮我将以下文本翻译成地道的英文，并给出难点解析：\n"
    },
    {
        "id": "search",
        "title": "AI搜索",
        "prompt": "请帮我搜索以下内容，并对搜索结果进行系统性的整理和深度总结：\n"
    },
    {
        "id": "summarize",
        "title": "长文提炼",
        "prompt": "请帮我提炼以下长文的核心内容，用条理清晰的列表输出关键要点：\n"
    },
    {
        "id": "write",
        "title": "文本润色",
        "prompt": "请帮我润色以下文本，纠正语法错误并提升其学术流畅度，同时列出主要修改点：\n"
    }
]

# 默认的提示词与按钮名称，用于迁移及备用
PROMPT_DEFAULTS = {
    "reply_prompt": (
        "你是一个真实的人，正在用手机快速回复消息。\n\n"
        "根据下面的内容，写一条自然的回复：\n\n"
        "【内容】\n[划词内容]\n\n"
        "回复规则：\n\n"
        "像真人发消息，不是机器人客服\n"
        "口语化，可以用\"哈\"\"呀\"\"嗯\"\"好的\"\"没问题\"这类日常用词\n"
        "简短有温度，20～50字，不废话\n"
        "直接输出回复，不加引号，不做任何解释\n"
        "几个反面示例（不要这样写）：\n\n"
        "❌ \"您好，感谢您的反馈，我们会认真对待……\"\n"
        "❌ \"非常感谢您的分享，这对我很有帮助……\"\n"
        "❌ 任何听起来像模板、公告、客服话术的句子\n"
        "正面风格参考：\n\n"
        "✅ 好嘞，我待会儿看看，有问题再找你\n"
        "✅ 哈哈这个我也不确定，你问问别人？\n"
        "✅ 行，明白了，我这边处理一下\n"
        "现在，根据上面【内容】写回复：。"
    ),
    "explain_prompt": (
        "请对下面划词内容进行系统性解释。\n\n"
        "## 解释目标\n"
        "- 用一句话先给出核心定义，避免绕圈。\n"
        "- 拆解它的底层逻辑、关键要素和成立条件。\n"
        "- 用一个生活化类比帮助建立直觉。\n"
        "- 给出 2 到 3 个典型场景示例。\n"
        "- 列出常见误区，并给出纠正。\n"
        "- 补充相关概念、上位概念、下位概念和容易混淆的概念。\n\n"
        "## 输出风格\n"
        "- 用中文回答，语言简洁准确，讲人话。\n"
        "- 层次清晰，重点词加粗。\n"
        "- 不要空泛套话，不要自称 AI。\n\n"
        "划词内容：\n[划词内容]"
    ),
    "summary_prompt": (
        "请对下面划词内容做高质量总结。\n\n"
        "## 总结目标\n"
        "- 先用 1 句话概括核心观点。\n"
        "- 提炼 3 到 6 条关键要点，按重要性排序。\n"
        "- 保留必要的事实、数字、条件和结论。\n"
        "- 如果内容包含步骤、因果或对比，请整理成清晰结构。\n"
        "- 最后给出一句“可直接记住的结论”。\n\n"
        "## 输出风格\n"
        "- 用中文回答，简洁、准确、信息密度高。\n"
        "- 不添加原文没有的事实。\n"
        "- 不要空泛套话，不要自称 AI。\n\n"
        "划词内容：\n[划词内容]"
    ),
    "ai_search_prompt": (
        "请对下面的划词内容进行深度AI搜索 and 网络分析，搜集并整理相关的核心定义、"
        "背景知识、最新动态及详细解释，并以结构清晰的格式给出解答：\n\n"
        "划词内容：\n[划词内容]"
    ),
    "optimize_prompt": (
        "请对下面的划词内容进行深度AI搜索 and 网络分析，搜集并整理相关的核心定义、"
        "背景知识、最新动态及详细解释，并以结构清晰的格式给出解答：\n\n"
        "划词内容：\n[划词内容]"
    ),
    "reply_prompt_button_name": "回复",
    "ai_search_prompt_button_name": "搜索",
    "explain_prompt_button_name": "解释",
    "summary_prompt_button_name": "总结",
    "chat_placeholder_cards": json.dumps(DEFAULT_CHAT_PLACEHOLDER_CARDS, ensure_ascii=False)
}

# 缓存机制，避免频繁读盘
_PROMPT_CACHE: dict[str, str] = {}
_CACHE_INITIALIZED = False


def _get_db_path() -> Path:
    d = get_app_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "prompt.db"


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS prompts (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class PromptStore:
    """提示词 SQLite 存储管理器。"""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or _get_db_path()
        self._conn: Optional[sqlite3.Connection] = None
        self._open()
        self._ensure_schema()

    def _open(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), timeout=5.0, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row

    def _ensure_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._open()
            self._ensure_schema()
        assert self._conn is not None
        return self._conn

    def is_empty(self) -> bool:
        row = self._db.execute("SELECT COUNT(*) AS c FROM prompts").fetchone()
        return row["c"] == 0

    def load_all_prompts(self) -> dict[str, str]:
        """加载所有提示词，使用内存缓存避免频繁读库。"""
        global _PROMPT_CACHE, _CACHE_INITIALIZED
        if _CACHE_INITIALIZED:
            return dict(_PROMPT_CACHE)

        rows = self._db.execute("SELECT key, value FROM prompts").fetchall()
        db_data = {r["key"]: r["value"] for r in rows}

        # 用默认值补全没有的 key
        merged = dict(PROMPT_DEFAULTS)
        merged.update(db_data)

        _PROMPT_CACHE = merged
        _CACHE_INITIALIZED = True
        return dict(_PROMPT_CACHE)

    def save_prompt(self, key: str, value: str) -> None:
        """更新单个提示词，并刷新缓存。"""
        global _CACHE_INITIALIZED
        self._db.execute(
            "INSERT OR REPLACE INTO prompts (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._db.commit()
        # 刷新缓存
        _CACHE_INITIALIZED = False
        self.load_all_prompts()

    def save_all_prompts(self, prompts: dict[str, str]) -> None:
        """全量保存，并刷新缓存。"""
        global _CACHE_INITIALIZED
        self._db.execute("BEGIN TRANSACTION")
        try:
            for k, v in prompts.items():
                self._db.execute(
                    "INSERT OR REPLACE INTO prompts (key, value) VALUES (?, ?)",
                    (k, v),
                )
            self._db.commit()
        except Exception as e:
            self._db.rollback()
            logger.error("Failed to save prompts to SQLite: %s", e)
            raise e
        _CACHE_INITIALIZED = False
        self.load_all_prompts()

    def migrate_from_settings(self, ui_settings: Any) -> bool:
        """从 settings.json 的 ui 数据迁移到 SQLite。

        仅在数据库为空时执行。返回是否执行了迁移。
        """
        if not self.is_empty():
            return False
        if not isinstance(ui_settings, dict):
            return False

        to_migrate: dict[str, str] = {}

        # 1. 迁移翻译相关的提示词配置
        translator = ui_settings.get("translator")
        if isinstance(translator, dict):
            for key in PROMPT_DEFAULTS:
                if key != "chat_placeholder_cards" and key in translator:
                    to_migrate[key] = str(translator[key])

        # 2. 迁移聊天卡片的配置
        chat_cards = ui_settings.get("chat_placeholder_cards")
        if isinstance(chat_cards, list):
            to_migrate["chat_placeholder_cards"] = json.dumps(chat_cards, ensure_ascii=False)

        if to_migrate:
            self.save_all_prompts(to_migrate)
            logger.info("已从 settings.json 迁移提示词与聊天卡片数据到 prompt.db: %d 条目", len(to_migrate))
            return True
        return False
