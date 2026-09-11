"""待办事项的 SQLite 存储层。

将 todo 数据独立存储在 data/todo.db，与 settings.json 解耦。
支持从旧版 settings.json ui.todo_items 自动迁移。
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
    return d / "todo.db"


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS items (
    id        TEXT PRIMARY KEY,
    data      TEXT NOT NULL DEFAULT '{}',
    sort_order INTEGER NOT NULL DEFAULT 0
);
"""


class TodoStore:
    """待办事项 SQLite 存储管理器。"""

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
    # 读写接口
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        row = self._db.execute("SELECT COUNT(*) AS c FROM items").fetchone()
        return row["c"] == 0

    def load_items(self) -> list[dict[str, Any]]:
        """按 sort_order 加载所有 todo 条目。"""
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

    def save_items(self, items: list[dict[str, Any]]) -> None:
        """全量替换所有 todo 条目。"""
        self._db.execute("DELETE FROM items")
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", "") or f"todo-{i}")
            self._db.execute(
                "INSERT OR REPLACE INTO items (id, data, sort_order) VALUES (?, ?, ?)",
                (item_id, json.dumps(item, ensure_ascii=False), i),
            )
        self._db.commit()

    def upsert_item(self, item: dict[str, Any], sort_order: int = 0) -> None:
        """插入或更新单条 todo 条目。"""
        item_id = str(item.get("id", "") or "")
        if not item_id:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO items (id, data, sort_order) VALUES (?, ?, ?)",
            (item_id, json.dumps(item, ensure_ascii=False), sort_order),
        )
        self._db.commit()

    def delete_item(self, item_id: str) -> None:
        """删除单条 todo 条目。"""
        self._db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        self._db.commit()

    # ------------------------------------------------------------------
    # 从旧版 settings.json 迁移
    # ------------------------------------------------------------------

    def migrate_from_settings(self, todo_items: Any) -> bool:
        """从 settings.json 的 ui.todo_items 迁移到 SQLite。

        仅在数据库为空时执行。返回是否执行了迁移。
        """
        if not self.is_empty():
            return False
        if not isinstance(todo_items, list) or not todo_items:
            return False
        self.save_items(todo_items)
        logger.info("已从 settings.json 迁移 %d 条待办事项到 todo.db", len(todo_items))
        return True
