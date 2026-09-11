"""翻译/问答历史记录的 SQLite 存储层。

将翻译和问答结果独立存储在 SQLite 数据库中，
支持搜索、收藏、自动清理等功能。
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from deepcat.utils.paths import get_app_dir

logger = logging.getLogger(__name__)

_DB_DIR_NAME = "data"
_DB_FILE_NAME = "translation_history.db"


def _get_db_path() -> Path:
    """返回数据库文件路径: get_app_dir() / data / translation_history.db"""
    d = get_app_dir() / _DB_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d / _DB_FILE_NAME


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS history_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type     TEXT    NOT NULL DEFAULT 'translate',
    source_text   TEXT    NOT NULL DEFAULT '',
    result_text   TEXT    NOT NULL DEFAULT '',
    model_name    TEXT    NOT NULL DEFAULT '',
    source_lang   TEXT    NOT NULL DEFAULT '自动检测',
    target_lang   TEXT    NOT NULL DEFAULT '中英互译',
    elapsed_secs  REAL    NOT NULL DEFAULT 0.0,
    prompt_text   TEXT    NOT NULL DEFAULT '',
    is_success    INTEGER NOT NULL DEFAULT 1,
    is_starred    INTEGER NOT NULL DEFAULT 0,
    title         TEXT    NOT NULL DEFAULT '',
    is_pinned     INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_history_created   ON history_records (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_task_type  ON history_records (task_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_starred    ON history_records (is_starred, created_at DESC);
"""

# 所有列名，用于将 sqlite3.Row 转为 dict
_ALL_COLUMNS = (
    "id", "task_type", "source_text", "result_text", "model_name",
    "source_lang", "target_lang", "elapsed_secs", "prompt_text",
    "is_success", "is_starred", "title", "is_pinned", "created_at",
)


def _row_to_dict(row: sqlite3.Row) -> dict:
    """将 sqlite3.Row 转换为普通 dict。"""
    return {col: row[col] for col in _ALL_COLUMNS}


def _row_to_summary_dict(row: sqlite3.Row) -> dict:
    """将摘要查询结果转换为与历史记录兼容的轻量 dict。"""
    return {
        "id": row["id"],
        "task_type": row["task_type"],
        "source_text": row["source_text"],
        "result_text": row["result_text"],
        "model_name": row["model_name"],
        "source_lang": row["source_lang"],
        "target_lang": row["target_lang"],
        "elapsed_secs": row["elapsed_secs"],
        "prompt_text": "",
        "is_success": row["is_success"],
        "is_starred": row["is_starred"],
        "title": row["title"],
        "is_pinned": row["is_pinned"],
        "created_at": row["created_at"],
    }


class TranslationHistoryStore:
    """翻译/问答历史记录的 SQLite 存储管理器。"""

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
        columns = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(history_records)").fetchall()
        }
        migrations = {
            "title": "TEXT NOT NULL DEFAULT ''",
            "is_pinned": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_sql in migrations.items():
            if column_name not in columns:
                self._conn.execute(
                    f"ALTER TABLE history_records ADD COLUMN {column_name} {column_sql}"
                )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_pinned ON history_records (is_pinned, created_at DESC)"
        )
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
    # 添加记录
    # ------------------------------------------------------------------

    def add_record(
        self,
        task_type: str,
        source_text: str,
        result_text: str,
        model_name: str,
        source_lang: str,
        target_lang: str,
        elapsed_secs: float,
        prompt_text: str = "",
        is_success: bool = True,
    ) -> int:
        """添加一条历史记录，返回新记录的 id。"""
        cursor = self._db.execute(
            """INSERT INTO history_records
               (task_type, source_text, result_text, model_name,
                source_lang, target_lang, elapsed_secs, prompt_text, is_success)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_type,
                source_text,
                result_text,
                model_name,
                source_lang,
                target_lang,
                elapsed_secs,
                prompt_text,
                1 if is_success else 0,
            ),
        )
        self._db.commit()
        record_id = cursor.lastrowid
        logger.debug("添加历史记录: id=%s, task_type=%s", record_id, task_type)
        return record_id  # type: ignore[return-value]

    def update_record(
        self,
        record_id: int,
        source_text: str,
        result_text: str,
        elapsed_secs: float,
        prompt_text: str = "",
        is_success: bool = True,
    ) -> bool:
        """更新一条历史记录，多用于多轮对话追加历史。"""
        cursor = self._db.execute(
            """UPDATE history_records
               SET source_text = ?, result_text = ?, elapsed_secs = ?, prompt_text = ?, is_success = ?, created_at = datetime('now','localtime')
               WHERE id = ?""",
            (
                source_text,
                result_text,
                elapsed_secs,
                prompt_text,
                1 if is_success else 0,
                record_id,
            ),
        )
        self._db.commit()
        updated = cursor.rowcount > 0
        if updated:
            logger.debug("更新历史记录: id=%s", record_id)
        return updated

    def rename_record(self, record_id: int, title: str) -> bool:
        """重命名历史记录在侧栏中的展示标题，不改写原始对话内容。"""
        clean_title = " ".join(str(title or "").split()).strip()
        if not clean_title:
            return False
        cursor = self._db.execute(
            "UPDATE history_records SET title = ? WHERE id = ?",
            (clean_title[:120], record_id),
        )
        self._db.commit()
        renamed = cursor.rowcount > 0
        if renamed:
            logger.debug("重命名历史记录: id=%s", record_id)
        return renamed

    def set_pinned(self, record_id: int, pinned: bool) -> bool:
        """设置历史记录置顶状态。"""
        cursor = self._db.execute(
            "UPDATE history_records SET is_pinned = ? WHERE id = ?",
            (1 if pinned else 0, record_id),
        )
        self._db.commit()
        updated = cursor.rowcount > 0
        if updated:
            logger.debug("设置历史记录置顶: id=%s, is_pinned=%s", record_id, bool(pinned))
        return updated

    def toggle_pinned(self, record_id: int) -> bool:
        """切换置顶状态，返回切换后的新状态。"""
        row = self._db.execute(
            "SELECT is_pinned FROM history_records WHERE id = ?", (record_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"记录不存在: id={record_id}")

        new_state = 0 if row["is_pinned"] else 1
        self._db.execute(
            "UPDATE history_records SET is_pinned = ? WHERE id = ?",
            (new_state, record_id),
        )
        self._db.commit()
        logger.debug("切换置顶: id=%s, is_pinned=%s", record_id, bool(new_state))
        return bool(new_state)

    # ------------------------------------------------------------------
    # 分页查询 + 搜索
    # ------------------------------------------------------------------

    def _record_query_parts(
        self,
        task_type_filter: Optional[str],
        search_query: Optional[str],
        starred_only: bool,
        model_name_filter: Optional[str] = None,
    ) -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []

        if task_type_filter:
            clauses.append("task_type = ?")
            params.append(task_type_filter)
        if model_name_filter:
            clauses.append("model_name = ?")
            params.append(model_name_filter)
        if starred_only:
            clauses.append("is_starred = 1")
        if search_query:
            clauses.append("(title LIKE ? OR source_text LIKE ? OR result_text LIKE ? OR prompt_text LIKE ? OR model_name LIKE ?)")
            like = f"%{search_query}%"
            params.extend([like, like, like, like, like])

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def get_records(
        self,
        limit: int = 50,
        offset: int = 0,
        task_type_filter: Optional[str] = None,
        search_query: Optional[str] = None,
        starred_only: bool = False,
        pinned_first: bool = False,
        model_name_filter: Optional[str] = None,
    ) -> list[dict]:
        """分页查询历史记录，可按 task_type 过滤、搜索 source_text/result_text。"""
        where, params = self._record_query_parts(task_type_filter, search_query, starred_only, model_name_filter)
        order_by = "is_pinned DESC, created_at DESC" if pinned_first else "created_at DESC"
        sql = f"SELECT * FROM history_records {where} ORDER BY {order_by} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._db.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_record_summaries(
        self,
        limit: int = 50,
        offset: int = 0,
        task_type_filter: Optional[str] = None,
        search_query: Optional[str] = None,
        starred_only: bool = False,
        pinned_first: bool = False,
        text_limit: int = 240,
        model_name_filter: Optional[str] = None,
    ) -> list[dict]:
        """分页查询历史摘要，不加载完整 prompt_text 和长回答正文。"""
        where, params = self._record_query_parts(task_type_filter, search_query, starred_only, model_name_filter)
        order_by = "is_pinned DESC, created_at DESC" if pinned_first else "created_at DESC"
        summary_limit = max(40, int(text_limit))
        sql = f"""
            SELECT
                id,
                task_type,
                CASE
                    WHEN length(source_text) > ? THEN substr(source_text, 1, ?) || '...'
                    ELSE source_text
                END AS source_text,
                CASE
                    WHEN length(result_text) > ? THEN substr(result_text, 1, ?) || '...'
                    ELSE result_text
                END AS result_text,
                model_name,
                source_lang,
                target_lang,
                elapsed_secs,
                is_success,
                is_starred,
                title,
                is_pinned,
                created_at
            FROM history_records
            {where}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
        """
        query_params = [summary_limit, summary_limit, summary_limit, summary_limit]
        query_params.extend(params)
        query_params.extend([limit, offset])

        rows = self._db.execute(sql, query_params).fetchall()
        return [_row_to_summary_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 单条查询
    # ------------------------------------------------------------------

    def get_record(self, record_id: int) -> Optional[dict]:
        """根据 id 获取单条记录，不存在返回 None。"""
        row = self._db.execute(
            "SELECT * FROM history_records WHERE id = ?", (record_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    # ------------------------------------------------------------------
    # 删除记录
    # ------------------------------------------------------------------

    def delete_record(self, record_id: int) -> bool:
        """删除指定 id 的记录，返回是否成功删除。"""
        cursor = self._db.execute(
            "DELETE FROM history_records WHERE id = ?", (record_id,)
        )
        self._db.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.debug("删除历史记录: id=%s", record_id)
        return deleted

    def delete_records(self, record_ids: list[int]) -> int:
        """批量删除指定 id 的记录，返回删除数量。"""
        normalized_ids: set[int] = set()
        for value in record_ids:
            try:
                record_id = int(value)
            except (TypeError, ValueError):
                continue
            if record_id > 0:
                normalized_ids.add(record_id)
        ids = sorted(normalized_ids)
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cursor = self._db.execute(
            f"DELETE FROM history_records WHERE id IN ({placeholders})",
            ids,
        )
        self._db.commit()
        deleted = int(cursor.rowcount or 0)
        if deleted:
            logger.debug("批量删除历史记录: count=%s", deleted)
        return deleted

    def clear_task_type(self, task_type: str) -> int:
        """清空指定任务类型的历史记录，返回删除数量。"""
        clean_task_type = str(task_type or "").strip()
        if not clean_task_type:
            return 0
        cursor = self._db.execute(
            "DELETE FROM history_records WHERE task_type = ?",
            (clean_task_type,),
        )
        self._db.commit()
        deleted = max(0, int(cursor.rowcount or 0))
        if deleted:
            logger.info("已清空 %s 历史记录: %d 条", clean_task_type, deleted)
        return deleted

    # ------------------------------------------------------------------
    # 收藏切换
    # ------------------------------------------------------------------

    def toggle_starred(self, record_id: int) -> bool:
        """切换指定记录的收藏状态，返回切换后的新状态。

        Raises:
            ValueError: 记录不存在时抛出。
        """
        row = self._db.execute(
            "SELECT is_starred FROM history_records WHERE id = ?", (record_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"记录不存在: id={record_id}")

        new_state = 0 if row["is_starred"] else 1
        self._db.execute(
            "UPDATE history_records SET is_starred = ? WHERE id = ?",
            (new_state, record_id),
        )
        self._db.commit()
        logger.debug("切换收藏: id=%s, is_starred=%s", record_id, bool(new_state))
        return bool(new_state)

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """在标题、正文、提示词和模型名中搜索包含关键词的记录。"""
        like = f"%{query}%"
        rows = self._db.execute(
            """SELECT * FROM history_records
               WHERE title LIKE ? OR source_text LIKE ? OR result_text LIKE ? OR prompt_text LIKE ? OR model_name LIKE ?
               ORDER BY created_at DESC LIMIT ?""",
            (like, like, like, like, like, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 记录计数
    # ------------------------------------------------------------------

    def get_count(
        self,
        task_type_filter: Optional[str] = None,
        starred_only: bool = False,
        model_name_filter: Optional[str] = None,
    ) -> int:
        """获取记录总数，支持按 task_type 和收藏过滤。"""
        clauses: list[str] = []
        params: list = []

        if task_type_filter:
            clauses.append("task_type = ?")
            params.append(task_type_filter)
        if model_name_filter:
            clauses.append("model_name = ?")
            params.append(model_name_filter)
        if starred_only:
            clauses.append("is_starred = 1")

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        row = self._db.execute(
            f"SELECT COUNT(*) AS c FROM history_records {where}", params
        ).fetchone()
        return row["c"] if row else 0

    def get_model_names(
        self,
        task_type_filter: Optional[str] = None,
        search_query: Optional[str] = None,
        starred_only: bool = False,
    ) -> list[str]:
        """返回当前条件下出现过的模型名，用于历史侧栏筛选。"""
        where, params = self._record_query_parts(task_type_filter, search_query, starred_only)
        if where:
            sql = f"""
            SELECT DISTINCT model_name
            FROM history_records
            {where}
            AND model_name <> ''
            ORDER BY model_name COLLATE NOCASE ASC
            """
        else:
            sql = """
            SELECT DISTINCT model_name
            FROM history_records
            WHERE model_name <> ''
            ORDER BY model_name COLLATE NOCASE ASC
            """
        rows = self._db.execute(sql, params).fetchall()
        return [str(row["model_name"] or "").strip() for row in rows if str(row["model_name"] or "").strip()]

    # ------------------------------------------------------------------
    # 清空所有记录
    # ------------------------------------------------------------------

    def clear_all(self) -> None:
        """清空所有历史记录。"""
        self._db.execute("DELETE FROM history_records")
        self._db.commit()
        logger.info("已清空所有翻译历史记录")

    # ------------------------------------------------------------------
    # 自动清理
    # ------------------------------------------------------------------

    def auto_cleanup(self, max_records: int = 500) -> int:
        """清理超过 max_records 的最旧非收藏记录，返回删除数量。"""
        total = self.get_count()
        if total <= max_records:
            return 0

        overflow = total - max_records
        # 删除最旧的非收藏记录
        cursor = self._db.execute(
            """DELETE FROM history_records WHERE id IN (
                   SELECT id FROM history_records
                   WHERE is_starred = 0 AND is_pinned = 0
                   ORDER BY created_at ASC
                   LIMIT ?
               )""",
            (overflow,),
        )
        self._db.commit()
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info("自动清理翻译历史: 删除 %d 条旧记录", deleted)
        return deleted

    # ------------------------------------------------------------------
    # 导出记录
    # ------------------------------------------------------------------

    def export_records(self, record_ids: list[int]) -> str:
        """将指定 id 的记录导出为格式化文本。"""
        if not record_ids:
            return ""

        placeholders = ",".join("?" for _ in record_ids)
        rows = self._db.execute(
            f"SELECT * FROM history_records WHERE id IN ({placeholders}) ORDER BY created_at DESC",
            record_ids,
        ).fetchall()

        if not rows:
            return ""

        parts: list[str] = []
        parts.append("=" * 60)
        parts.append("翻译/问答历史记录导出")
        parts.append(f"共 {len(rows)} 条记录")
        parts.append("=" * 60)

        for i, row in enumerate(rows, 1):
            record = _row_to_dict(row)
            starred_mark = " ⭐" if record["is_starred"] else ""
            success_mark = "✓" if record["is_success"] else "✗"

            parts.append("")
            parts.append(f"--- 记录 #{i} [{success_mark}]{starred_mark} ---")
            parts.append(f"时间: {record['created_at']}")
            parts.append(f"类型: {record['task_type']}")
            parts.append(f"模型: {record['model_name']}")
            parts.append(f"语言: {record['source_lang']} → {record['target_lang']}")
            parts.append(f"耗时: {record['elapsed_secs']:.2f}s")

            if record["prompt_text"]:
                parts.append(f"提示词: {record['prompt_text']}")

            parts.append("")
            parts.append("【原文】")
            parts.append(record["source_text"])
            parts.append("")
            parts.append("【结果】")
            parts.append(record["result_text"])
            parts.append("")

        parts.append("=" * 60)
        return "\n".join(parts)

    def get_db_file_size_str(self) -> str:
        """获取数据库文件的大小，返回可读的字符串，如 2.4 MB, 512 KB 等。"""
        try:
            p = self._db_path
            if p.exists():
                size_bytes = p.stat().st_size
                if size_bytes < 1024:
                    return f"{size_bytes} B"
                elif size_bytes < 1024 * 1024:
                    return f"{size_bytes / 1024:.1f} KB"
                else:
                    return f"{size_bytes / (1024 * 1024):.2f} MB"
        except Exception:
            pass
        return "0 B"
