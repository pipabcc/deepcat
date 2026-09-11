"""表格/笔记数据的 SQLite 存储层。

将表格 tab 和笔记 tab 数据独立存储在 SQLite 数据库中，
与 settings.json 解耦。支持从旧版 settings.json 自动迁移。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from deepcat.utils.paths import get_app_dir
from deepcat.utils.secret_store import protect_text, unprotect_text

logger = logging.getLogger(__name__)

_DB_DIR_NAME = "data"
_DB_FILE_NAME = "table_notes.db"


def _get_db_path() -> Path:
    """返回数据库文件路径: get_app_dir() / data / table_notes.db"""
    d = get_app_dir() / _DB_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d / _DB_FILE_NAME


def default_ima_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "auto_sync_enabled": False,
        "client_id": "",
        "api_key": "",
        "note_folder_id": "",
        "note_folder_name": "",
        "remote_note_id": "",
        "remote_note_title": "",
        "knowledge_base_enabled": False,
        "knowledge_base_id": "",
        "knowledge_base_name": "",
        "knowledge_folder_id": "",
        "knowledge_folder_name": "",
        "knowledge_media_id": "",
        "last_sync_at": "",
    }


def normalize_ima_config(value: Any, *, protect: bool = False) -> dict[str, Any]:
    data = dict(value) if isinstance(value, dict) else {}
    default = default_ima_config()
    result: dict[str, Any] = {}
    for key, default_value in default.items():
        if isinstance(default_value, bool):
            result[key] = bool(data.get(key, default_value))
        else:
            result[key] = str(data.get(key, default_value) or "")
    if protect:
        result["client_id"] = protect_text(result["client_id"])
        result["api_key"] = protect_text(result["api_key"])
    else:
        result["client_id"] = unprotect_text(result["client_id"])
        result["api_key"] = unprotect_text(result["api_key"])
    return result


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS table_tabs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tab_index INTEGER NOT NULL,
    name      TEXT    NOT NULL DEFAULT '表格',
    data      TEXT    NOT NULL DEFAULT '[]',
    group_name TEXT   NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS note_tabs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tab_index INTEGER NOT NULL,
    name      TEXT    NOT NULL DEFAULT '记事本',
    html      TEXT    NOT NULL DEFAULT '',
    group_name TEXT   NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


class TableNotesStore:
    """表格/笔记数据的 SQLite 存储管理器。"""

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
        for col, table in [
            ("password", "table_tabs"),
            ("password", "note_tabs"),
            ("group_name", "table_tabs"),
            ("group_name", "note_tabs"),
            ("ima_config", "note_tabs"),
            ("user_renamed", "note_tabs"),
        ]:
            try:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
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
    # 元数据
    # ------------------------------------------------------------------

    def _get_meta(self, key: str, default: str = "") -> str:
        row = self._db.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else default

    def _set_meta(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._db.commit()

    # ------------------------------------------------------------------
    # 表格 tab 读写
    # ------------------------------------------------------------------

    def load_table_tabs(self) -> tuple[list[dict[str, Any]], int]:
        """加载所有表格 tab，返回 (tabs, active_index)。"""
        rows = self._db.execute(
            "SELECT tab_index, name, data, password, group_name FROM table_tabs ORDER BY tab_index"
        ).fetchall()
        tabs: list[dict[str, Any]] = []
        for r in rows:
            try:
                data = json.loads(r["data"])
            except Exception:
                data = []
            if isinstance(data, dict):
                rows_data = data.get("rows", [])
                widths_data = data.get("column_widths", [])
            else:
                rows_data = data if isinstance(data, list) else []
                widths_data = []
            tabs.append({
                "name": str(r["name"]),
                "data": rows_data,
                "column_widths": widths_data,
                "password": str(r["password"]) if "password" in r.keys() else "",
                "group_name": str(r["group_name"]) if "group_name" in r.keys() else ""
            })
        active = int(self._get_meta("active_table_tab", "0"))
        if not tabs:
            tabs = [{"name": "表格1", "data": [], "column_widths": [], "group_name": ""}]
        if active >= len(tabs):
            active = 0
        return tabs, active

    def load_note_tabs(self) -> tuple[list[dict[str, Any]], int]:
        """加载所有笔记 tab，返回 (tabs, active_index)。"""
        rows = self._db.execute(
            "SELECT tab_index, name, html, password, group_name, ima_config, user_renamed FROM note_tabs ORDER BY tab_index"
        ).fetchall()
        tabs: list[dict[str, Any]] = []
        for r in rows:
            try:
                ima_config_raw = json.loads(str(r["ima_config"] or "{}")) if "ima_config" in r.keys() else {}
            except Exception:
                ima_config_raw = {}
            tabs.append({
                "name": str(r["name"]),
                "html": str(r["html"] or ""),
                "password": str(r["password"]) if "password" in r.keys() else "",
                "group_name": str(r["group_name"]) if "group_name" in r.keys() else "",
                "ima_config": normalize_ima_config(ima_config_raw),
                "_user_renamed": (str(r["user_renamed"]) == "1") if "user_renamed" in r.keys() else False,
            })
        active = int(self._get_meta("active_note_tab", "0"))
        if not tabs:
            tabs = [{"name": "记事本1", "html": "", "group_name": "", "ima_config": default_ima_config()}]
        if active >= len(tabs):
            active = 0
        return tabs, active

    def save_table_tab(self, index: int, name: str | dict[str, Any], data: Optional[list] = None) -> None:
        """保存单个表格 tab；传入完整字典时同步全部持久化字段。"""
        db = self._db
        existing = db.execute(
            "SELECT id, data, password, group_name FROM table_tabs WHERE tab_index = ?", (index,)
        ).fetchone()
        if isinstance(name, dict):
            tab = name
            payload = {
                "rows": tab.get("data", []),
                "column_widths": tab.get("column_widths", []),
            }
            tab_name = str(tab.get("name", f"表格{index + 1}"))
            password = str(tab.get("password", "") or "")
            group_name = str(tab.get("group_name", "") or "")
        else:
            widths: list[Any] = []
            if existing is not None:
                try:
                    old_payload = json.loads(str(existing["data"] or "[]"))
                    if isinstance(old_payload, dict) and isinstance(old_payload.get("column_widths"), list):
                        widths = old_payload["column_widths"]
                except Exception:
                    pass
            payload = {"rows": data or [], "column_widths": widths}
            tab_name = str(name)
            password = str(existing["password"] or "") if existing is not None else ""
            group_name = str(existing["group_name"] or "") if existing is not None else ""

        values = (tab_name, json.dumps(payload, ensure_ascii=False), password, group_name)
        with db:
            if existing is not None:
                db.execute(
                    "UPDATE table_tabs SET name = ?, data = ?, password = ?, group_name = ? WHERE tab_index = ?",
                    (*values, index),
                )
            else:
                db.execute(
                    "INSERT INTO table_tabs (tab_index, name, data, password, group_name) VALUES (?, ?, ?, ?, ?)",
                    (index, *values),
                )

    def save_note_tab(
        self,
        index: int,
        name: str | dict[str, Any],
        html: Optional[str] = None,
        group_name: Optional[str] = None,
    ) -> None:
        """保存单个笔记 tab；传入完整字典时同步全部持久化字段。"""
        db = self._db
        existing = db.execute(
            "SELECT id, password, group_name, ima_config, user_renamed FROM note_tabs WHERE tab_index = ?", (index,)
        ).fetchone()
        if isinstance(name, dict):
            tab = name
            tab_name = str(tab.get("name", f"记事本{index + 1}"))
            html_content = str(tab.get("html", "") or "")
            password = str(tab.get("password", "") or "")
            saved_group_name = str(tab.get("group_name", "") or "")
            ima_json = json.dumps(normalize_ima_config(tab.get("ima_config"), protect=True), ensure_ascii=False)
            user_renamed = "1" if tab.get("_user_renamed") else "0"
        else:
            tab_name = str(name)
            html_content = str(html or "")
            password = str(existing["password"] or "") if existing is not None else ""
            saved_group_name = (
                str(group_name)
                if group_name is not None
                else (str(existing["group_name"] or "") if existing is not None else "")
            )
            ima_json = (
                str(existing["ima_config"] or "{}")
                if existing is not None
                else json.dumps(normalize_ima_config({}, protect=True), ensure_ascii=False)
            )
            user_renamed = str(existing["user_renamed"] or "") if existing is not None else "0"

        values = (tab_name, html_content, password, saved_group_name, ima_json, user_renamed)
        with db:
            if existing is not None:
                db.execute(
                    "UPDATE note_tabs SET name = ?, html = ?, password = ?, group_name = ?, ima_config = ?, "
                    "user_renamed = ? WHERE tab_index = ?",
                    (*values, index),
                )
            else:
                db.execute(
                    "INSERT INTO note_tabs (tab_index, name, html, password, group_name, ima_config, user_renamed) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (index, *values),
                )

    def replace_table_tabs(self, tabs: list[dict[str, Any]]) -> None:
        """在一个事务中替换全部表格页签，不修改活动索引。"""
        db = self._db
        with db:
            db.execute("DELETE FROM table_tabs")
            for index, tab in enumerate(tabs):
                payload = {
                    "rows": tab.get("data", []),
                    "column_widths": tab.get("column_widths", []),
                }
                db.execute(
                    "INSERT INTO table_tabs (tab_index, name, data, password, group_name) VALUES (?, ?, ?, ?, ?)",
                    (
                        index,
                        str(tab.get("name", f"表格{index + 1}")),
                        json.dumps(payload, ensure_ascii=False),
                        str(tab.get("password", "") or ""),
                        str(tab.get("group_name", "") or ""),
                    ),
                )

    def replace_note_tabs(self, tabs: list[dict[str, Any]]) -> None:
        """在一个事务中替换全部笔记页签，不修改活动索引。"""
        db = self._db
        with db:
            db.execute("DELETE FROM note_tabs")
            for index, tab in enumerate(tabs):
                db.execute(
                    "INSERT INTO note_tabs (tab_index, name, html, password, group_name, ima_config, user_renamed) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        index,
                        str(tab.get("name", f"记事本{index + 1}")),
                        str(tab.get("html", "") or ""),
                        str(tab.get("password", "") or ""),
                        str(tab.get("group_name", "") or ""),
                        json.dumps(normalize_ima_config(tab.get("ima_config"), protect=True), ensure_ascii=False),
                        "1" if tab.get("_user_renamed") else "0",
                    ),
                )

    def save_all_table_tabs(self, tabs: list[dict[str, Any]], active: int) -> None:
        """全量保存所有表格 tab（先清空再插入）。"""
        db = self._db
        with db:
            db.execute("DELETE FROM table_tabs")
            for index, tab in enumerate(tabs):
                payload = {
                    "rows": tab.get("data", []),
                    "column_widths": tab.get("column_widths", []),
                }
                db.execute(
                    "INSERT INTO table_tabs (tab_index, name, data, password, group_name) VALUES (?, ?, ?, ?, ?)",
                    (
                        index,
                        str(tab.get("name", f"表格{index + 1}")),
                        json.dumps(payload, ensure_ascii=False),
                        str(tab.get("password", "") or ""),
                        str(tab.get("group_name", "") or ""),
                    ),
                )
            db.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ("active_table_tab", str(active)),
            )

    def save_all_note_tabs(self, tabs: list[dict[str, Any]], active: int) -> None:
        """全量保存所有笔记 tab。"""
        db = self._db
        with db:
            db.execute("DELETE FROM note_tabs")
            for index, tab in enumerate(tabs):
                db.execute(
                    "INSERT INTO note_tabs (tab_index, name, html, password, group_name, ima_config, user_renamed) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        index,
                        str(tab.get("name", f"记事本{index + 1}")),
                        str(tab.get("html", "") or ""),
                        str(tab.get("password", "") or ""),
                        str(tab.get("group_name", "") or ""),
                        json.dumps(normalize_ima_config(tab.get("ima_config"), protect=True), ensure_ascii=False),
                        "1" if tab.get("_user_renamed") else "0",
                    ),
                )
            db.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ("active_note_tab", str(active)),
            )

    def set_active_table_tab(self, index: int) -> None:
        self._set_meta("active_table_tab", str(index))

    def set_active_note_tab(self, index: int) -> None:
        self._set_meta("active_note_tab", str(index))

    def delete_table_tab(self, index: int) -> None:
        """删除指定索引的表格 tab 并重新编号。"""
        self._db.execute("DELETE FROM table_tabs WHERE tab_index = ?", (index,))
        self._db.execute(
            "UPDATE table_tabs SET tab_index = tab_index - 1 WHERE tab_index > ?",
            (index,),
        )
        self._db.commit()

    def delete_note_tab(self, index: int) -> None:
        """删除指定索引的笔记 tab 并重新编号。"""
        self._db.execute("DELETE FROM note_tabs WHERE tab_index = ?", (index,))
        self._db.execute(
            "UPDATE note_tabs SET tab_index = tab_index - 1 WHERE tab_index > ?",
            (index,),
        )
        self._db.commit()

    # ------------------------------------------------------------------
    # 是否有数据（用于判断是否需要迁移）
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        """数据库中是否没有任何 tab 数据。"""
        t = self._db.execute("SELECT COUNT(*) AS c FROM table_tabs").fetchone()
        n = self._db.execute("SELECT COUNT(*) AS c FROM note_tabs").fetchone()
        return (t["c"] == 0) and (n["c"] == 0)

    def has_table_tabs(self) -> bool:
        row = self._db.execute("SELECT 1 FROM table_tabs LIMIT 1").fetchone()
        return row is not None

    def has_note_tabs(self) -> bool:
        row = self._db.execute("SELECT 1 FROM note_tabs LIMIT 1").fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # 从旧版 settings.json 迁移
    # ------------------------------------------------------------------

    def migrate_from_settings(self, table_notes_data: dict[str, Any]) -> bool:
        """从 settings.json 的 ui.table_notes 数据迁移到 SQLite。

        仅在数据库为空时执行迁移。返回是否执行了迁移。
        """
        if not self.is_empty():
            return False

        if not isinstance(table_notes_data, dict):
            return False

        # 解析表格 tabs
        table_tabs: list[dict[str, Any]] = []
        if "table_tabs" in table_notes_data and isinstance(table_notes_data.get("table_tabs"), list):
            for i, t in enumerate(table_notes_data["table_tabs"]):
                if isinstance(t, dict):
                    table_tabs.append({
                        "name": str(t.get("name", f"表格{i + 1}")),
                        "data": t.get("data", []) if isinstance(t.get("data"), list) else [],
                        "column_widths": t.get("column_widths", []) if isinstance(t.get("column_widths"), list) else [],
                    })
        else:
            old_rows = table_notes_data.get("table", [])
            if isinstance(old_rows, list) and old_rows:
                table_tabs = [{"name": "表格1", "data": old_rows}]

        # 解析笔记 tabs
        note_tabs: list[dict[str, Any]] = []
        if "note_tabs" in table_notes_data and isinstance(table_notes_data.get("note_tabs"), list):
            for i, t in enumerate(table_notes_data["note_tabs"]):
                if isinstance(t, dict):
                    note_tabs.append({
                        "name": str(t.get("name", f"记事本{i + 1}")),
                        "html": str(t.get("html", "") or ""),
                        "ima_config": normalize_ima_config(t.get("ima_config")),
                    })
        else:
            old_html = str(table_notes_data.get("note_html", "") or "")
            if old_html:
                note_tabs = [{"name": "记事本1", "html": old_html}]

        active_table = int(table_notes_data.get("active_table_tab", 0))
        active_note = int(table_notes_data.get("active_note_tab", 0))

        # 没有任何数据，跳过
        if not table_tabs and not note_tabs:
            return False

        # 写入 SQLite
        if table_tabs:
            self.save_all_table_tabs(table_tabs, active_table)
        if note_tabs:
            self.save_all_note_tabs(note_tabs, active_note)

        logger.info(
            "已从 settings.json 迁移表格/笔记数据: %d 个表格 tab, %d 个笔记 tab",
            len(table_tabs),
            len(note_tabs),
        )
        return True
