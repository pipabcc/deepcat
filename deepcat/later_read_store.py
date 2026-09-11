"""稍后阅读数据的 SQLite 存储层。

将 later_read 的条目（items）和设置（enabled/filter_keywords）
独立存储在 data/later_read.db，与 settings.json 解耦。
支持从旧版 settings.json ui.later_read 自动迁移。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from deepcat.utils.paths import get_app_dir

logger = logging.getLogger(__name__)


def _get_db_path() -> Path:
    d = get_app_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "later_read.db"


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS items (
    id         TEXT PRIMARY KEY,
    data       TEXT NOT NULL DEFAULT '{}',
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

# settings 表默认值
_DEFAULT_ENABLED = "true"
_DEFAULT_KEYWORDS = '["纯水","人工智能","tag","快问快答","分钟","个人资料"]'


class LaterReadStore:
    """稍后阅读 SQLite 存储管理器。"""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or _get_db_path()
        self._conn: Optional[sqlite3.Connection] = None
        self._open()
        self._ensure_schema()

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def _open(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), timeout=5.0)
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

    # ------------------------------------------------------------------
    # 元数据读写
    # ------------------------------------------------------------------

    def _get_setting(self, key: str, default: str = "") -> str:
        row = self._db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else default

    def _set_setting(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._db.commit()

    # ------------------------------------------------------------------
    # 读写接口
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        """items 和 settings 都为空才算空。"""
        items_count = self._db.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]
        settings_count = self._db.execute("SELECT COUNT(*) AS c FROM settings").fetchone()["c"]
        return items_count == 0 and settings_count == 0

    def load_settings(self) -> dict[str, Any]:
        """加载完整的 later_read 设置（enabled + filter_keywords + items）。"""
        enabled_str = self._get_setting("enabled", _DEFAULT_ENABLED)
        keywords_str = self._get_setting("filter_keywords", _DEFAULT_KEYWORDS)
        try:
            enabled = json.loads(enabled_str)
        except Exception:
            enabled = True
        try:
            filter_keywords = json.loads(keywords_str)
            if not isinstance(filter_keywords, list):
                filter_keywords = []
        except Exception:
            filter_keywords = []
        items = self.load_items()
        return {
            "enabled": bool(enabled),
            "filter_keywords": filter_keywords,
            "items": items,
        }

    def load_items(self) -> list[dict[str, Any]]:
        """按 sort_order 加载所有稍后阅读条目。"""
        rows = self._db.execute(
            "SELECT data FROM items ORDER BY sort_order, rowid"
        ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            try:
                item = json.loads(r["data"])
                if isinstance(item, dict):
                    result.append(item)
            except Exception:
                pass
        return result

    def save_settings_meta(self, enabled: bool, filter_keywords: list[str]) -> None:
        """保存 enabled 和 filter_keywords（不含 items）。"""
        self._db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("enabled", json.dumps(bool(enabled))),
        )
        self._db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("filter_keywords", json.dumps(filter_keywords, ensure_ascii=False)),
        )
        self._db.commit()

    def save_items(self, items: list[dict[str, Any]]) -> None:
        """全量替换所有稍后阅读条目。"""
        self._db.execute("DELETE FROM items")
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", "") or f"read-{i}")
            self._db.execute(
                "INSERT OR REPLACE INTO items (id, data, sort_order) VALUES (?, ?, ?)",
                (item_id, json.dumps(item, ensure_ascii=False), i),
            )
        self._db.commit()

    def save_full(self, later_read: dict[str, Any]) -> None:
        """全量保存 later_read 字典（enabled + filter_keywords + items）。"""
        enabled = bool(later_read.get("enabled", True))
        filter_keywords = later_read.get("filter_keywords", [])
        if not isinstance(filter_keywords, list):
            filter_keywords = []
        items = later_read.get("items", [])
        if not isinstance(items, list):
            items = []
        self.save_settings_meta(enabled, filter_keywords)
        self.save_items(items)

    def delete_item(self, item_id: str) -> None:
        self._db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        self._db.commit()

    # ------------------------------------------------------------------
    # 从旧版 settings.json 迁移
    # ------------------------------------------------------------------

    def migrate_from_settings(self, later_read: Any) -> bool:
        """从 settings.json 的 ui.later_read 迁移到 SQLite。

        仅在数据库为空时执行。返回是否执行了迁移。
        """
        if not self.is_empty():
            return False
        if not isinstance(later_read, dict):
            return False
        self.save_full(later_read)
        items_count = len(later_read.get("items", []))
        logger.info(
            "已从 settings.json 迁移稍后阅读数据到 later_read.db: %d 条目", items_count
        )
        return True
