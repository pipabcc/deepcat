"""兼容全文前缀与子串语义的 SQLite 搜索索引。"""

from __future__ import annotations

import sqlite3


RECORD_COLUMNS = (
    "id",
    "content",
    "content_type",
    "file_type",
    "file_path",
    "file_size",
    "source_app",
    "timestamp",
    "usage_count",
    "tags",
    "is_favorite",
    "is_pinned",
    "error_info",
    "metadata",
)


def ensure_substring_index(conn: sqlite3.Connection) -> None:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'clipboard_substrings'").fetchone()
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS clipboard_substrings USING fts5(
            content, file_path, tags, content='clipboard_records', content_rowid='id',
            tokenize='trigram', detail='none'
        )
    """
    )
    # 触发器与正文在同一事务更新，覆盖编辑、批量清理和数据库内部写入。
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS clipboard_substrings_insert AFTER INSERT ON clipboard_records BEGIN
            INSERT INTO clipboard_substrings(rowid, content, file_path, tags)
            VALUES (new.id, new.content, new.file_path, new.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS clipboard_substrings_delete AFTER DELETE ON clipboard_records BEGIN
            INSERT INTO clipboard_substrings(clipboard_substrings, rowid, content, file_path, tags)
            VALUES ('delete', old.id, old.content, old.file_path, old.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS clipboard_substrings_update
        AFTER UPDATE OF content, file_path, tags ON clipboard_records BEGIN
            INSERT INTO clipboard_substrings(clipboard_substrings, rowid, content, file_path, tags)
            VALUES ('delete', old.id, old.content, old.file_path, old.tags);
            INSERT INTO clipboard_substrings(rowid, content, file_path, tags)
            VALUES (new.id, new.content, new.file_path, new.tags);
        END;
    """
    )
    version = conn.execute("SELECT value FROM clipboard_stats WHERE key = 'substring_index_version'").fetchone()
    if not exists or version is None or version[0] != "1":
        conn.execute("INSERT INTO clipboard_substrings(clipboard_substrings) VALUES ('rebuild')")
        conn.execute("INSERT OR REPLACE INTO clipboard_stats(key, value) VALUES ('substring_index_version', '1')")


def query_records(
    conn: sqlite3.Connection,
    keyword: str,
    fts_query: str,
    content_type: str | None,
    limit: int,
    offset: int,
    *,
    include_pinned: bool,
    order_by_length: bool,
    use_fts: bool,
    use_substrings: bool,
) -> list[tuple]:
    candidates: list[str] = []
    params: list[object] = []
    if fts_query and use_fts:
        candidates.append("SELECT rowid FROM clipboard_records_fts WHERE clipboard_records_fts MATCH ?")
        params.append(fts_query)
    pattern = f"%{keyword}%"
    if use_substrings:
        # 分列 UNION 使每个 LIKE 都使用 trigram 索引；短于三字符时 SQLite 自动回退扫描。
        for column in ("content", "file_path", "tags"):
            candidates.append(f"SELECT rowid FROM clipboard_substrings WHERE {column} LIKE ?")
            params.append(pattern)
    else:
        candidates.append("SELECT id FROM clipboard_records WHERE content LIKE ? OR file_path LIKE ? OR tags LIKE ?")
        params.extend([pattern] * 3)
    filters = [f"r.id IN ({' UNION '.join(candidates)})"]
    if not include_pinned:
        filters.append("r.is_pinned = 0")
    if content_type:
        filters.append("(r.content_type = ? OR (? = 'text' AND r.content_type = 'code_snippet'))")
        params.extend((content_type, content_type))
    ordering = "LENGTH(r.content) DESC, " if order_by_length else ""
    columns = ", ".join(f"r.{column}" for column in RECORD_COLUMNS)
    params.extend((max(0, int(limit)), max(0, int(offset))))
    rows = conn.execute(
        f"SELECT {columns} FROM clipboard_records r WHERE {' AND '.join(filters)} "
        f"ORDER BY {ordering}r.timestamp DESC, r.id DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [tuple(row) for row in rows]
