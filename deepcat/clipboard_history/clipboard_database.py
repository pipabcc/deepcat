from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Optional

from deepcat.clipboard_history.search_index import RECORD_COLUMNS, ensure_substring_index, query_records

from deepcat.settings_store import load_settings, normalize_clipboard_history_settings
from deepcat.utils.logger import get_logger
from deepcat.utils.paths import get_app_dir

logger = get_logger("clipboard_history")
_CLIPBOARD_STATS_VERSION = "3"
PINNED_RECORD_LIMIT = 8

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS clipboard_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    content       TEXT NOT NULL,
    content_type  TEXT DEFAULT 'text',
    file_type     TEXT DEFAULT '',
    file_path     TEXT DEFAULT '',
    file_size     INTEGER DEFAULT 0,
    source_app    TEXT DEFAULT 'Unknown',
    timestamp     DATETIME DEFAULT CURRENT_TIMESTAMP,
    usage_count   INTEGER DEFAULT 0,
    tags          TEXT DEFAULT '',
    is_favorite   INTEGER DEFAULT 0,
    is_pinned     INTEGER DEFAULT 0,
    error_info    TEXT DEFAULT '',
    metadata      TEXT DEFAULT '{}',
    normalized_hash TEXT DEFAULT '',
    image_hash      TEXT DEFAULT '',
    data_size_bytes INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_clipboard_records_timestamp
    ON clipboard_records(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_clipboard_records_pinned_ts
    ON clipboard_records(is_pinned DESC, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_clipboard_records_type_ts
    ON clipboard_records(content_type, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_clipboard_records_page
    ON clipboard_records(is_pinned, timestamp DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_clipboard_records_normalized_hash
    ON clipboard_records(normalized_hash);

CREATE INDEX IF NOT EXISTS idx_clipboard_records_image_hash
    ON clipboard_records(image_hash);

CREATE TABLE IF NOT EXISTS clipboard_images (
    content_hash TEXT PRIMARY KEY,
    file_path    TEXT NOT NULL,
    file_size    INTEGER NOT NULL DEFAULT 0,
    ref_count    INTEGER NOT NULL DEFAULT 0,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clipboard_stats (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ClipboardDatabase:
    """剪贴板记录数据库管理（线程安全 + WAL）"""

    @classmethod
    def read_initial_snapshot(
        cls, db_path: str = "data/clipboard_history.db", *, limit: int = 50
    ) -> dict[str, Any] | None:
        """只读首屏数据，避免页面首次显示依赖迁移、索引准备和异步查询。"""
        path = Path(db_path)
        if not path.is_absolute():
            path = get_app_dir() / path
        if not path.is_file():
            return {"records": [], "pinned": [], "has_more": False, "statistics": (0, "0 B")}
        limit = max(1, int(limit))
        columns = ", ".join(RECORD_COLUMNS)
        deadline = time.monotonic() + 0.12
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.05)) as conn:
                conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                conn.execute("BEGIN")
                records = conn.execute(
                    f"SELECT {columns} FROM clipboard_records WHERE is_pinned = 0 "
                    "ORDER BY timestamp DESC, id DESC LIMIT ?",
                    (limit + 1,),
                ).fetchall()
                pinned = conn.execute(
                    f"SELECT {columns} FROM clipboard_records WHERE is_pinned = 1 "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (PINNED_RECORD_LIMIT,),
                ).fetchall()
                try:
                    stats = dict(
                        conn.execute(
                            "SELECT key, value FROM clipboard_stats WHERE key IN (?, ?)",
                            ("records_total_count", "records_data_size_all_bytes"),
                        )
                    )
                    total = int(stats["records_total_count"])
                    data_size = cls._format_bytes(int(stats["records_data_size_all_bytes"]))
                except (sqlite3.Error, KeyError, ValueError):
                    total = None
                    data_size = "统计中…"
            return {
                "records": records[:limit],
                "pinned": pinned,
                "has_more": len(records) > limit,
                "statistics": (total, data_size),
            }
        except (OSError, sqlite3.Error) as error:
            logger.warning("暂时无法读取复制记录首屏：%s", error)
            return None

    def __init__(
        self,
        db_path: str = "data/clipboard_history.db",
        *,
        auto_cleanup_enabled: Optional[bool] = None,
        retention_days: Optional[int] = None,
        max_records: Optional[int] = None,
        max_capacity_mb: Optional[int] = None,
    ) -> None:
        self.last_error: Optional[str] = None
        self.db_path = self._resolve_db_path(db_path)
        self._lock = threading.RLock()
        self._fts_available = False
        self._substring_index_available = False
        explicit_policy = any(
            value is not None
            for value in (auto_cleanup_enabled, retention_days, max_records, max_capacity_mb)
        )
        try:
            defaults = normalize_clipboard_history_settings(load_settings().ui.get("clipboard_history"))
        except Exception:
            defaults = normalize_clipboard_history_settings({})
        self.auto_cleanup_enabled = bool(
            defaults["auto_cleanup_enabled"] if auto_cleanup_enabled is None else auto_cleanup_enabled
        )
        self.retention_days = max(
            0, int(defaults["retention_days"] if retention_days is None else retention_days)
        )
        self.max_records = max(0, int(defaults["max_records"] if max_records is None else max_records))
        self.max_capacity_bytes = max(
            0,
            int(defaults["max_capacity_mb"] if max_capacity_mb is None else max_capacity_mb) * 1024 * 1024,
        )
        self._cleanup_interval = 1 if explicit_policy else 25
        self._writes_since_cleanup = 0
        self.init_database()

    def _resolve_db_path(self, db_path: str) -> str:
        p = Path(db_path)
        if not p.is_absolute():
            p = get_app_dir() / db_path
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create db parent dir: {e}")
        return str(p)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.row_factory = sqlite3.Row
        except BaseException:
            conn.close()
            raise
        return conn

    def _managed_clipboard_image_dir(self) -> Path:
        return get_app_dir() / "data" / "clipboard_images"

    def _is_managed_clipboard_file(self, file_path: str) -> bool:
        if not str(file_path or "").strip():
            return False
        try:
            file_path_resolved = Path(file_path).resolve()
            managed_dir = self._managed_clipboard_image_dir().resolve()
            return managed_dir == file_path_resolved.parent or managed_dir in file_path_resolved.parents
        except Exception:
            return False

    def _cleanup_managed_files(self, file_paths: list[str]) -> None:
        for file_path in file_paths:
            if not self._is_managed_clipboard_file(file_path):
                continue
            try:
                Path(file_path).unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"cleanup clipboard file failed: {e}")

    @staticmethod
    def _file_sha256(file_path: str) -> str:
        path = Path(file_path)
        if not path.is_file():
            return ""
        digest = hashlib.sha256()
        with path.open("rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _record_payload_size_bytes(
        content: object,
        file_type: object,
        file_path: object,
        error_info: object,
        metadata: object,
    ) -> int:
        return sum(
            len(str(value or "").encode("utf-8"))
            for value in (content, file_type, file_path, error_info, metadata)
        )

    def _stat_int(self, conn: sqlite3.Connection, key: str) -> int:
        try:
            return int(self._get_stat(conn, key) or 0)
        except Exception:
            return 0

    def _adjust_stat(self, conn: sqlite3.Connection, key: str, delta: int) -> None:
        self._set_stat(conn, key, max(0, self._stat_int(conn, key) + int(delta)))

    def _apply_stats_delta(
        self,
        conn: sqlite3.Connection,
        content_type: str,
        *,
        count_delta: int = 0,
        bytes_delta: int = 0,
        pinned_delta: int = 0,
    ) -> None:
        normalized_type = self._normalize_content_type(content_type)
        for key in ("records_total_count", "records_total_count:char_count"):
            self._adjust_stat(conn, key, count_delta)
        for key in ("records_data_size_all_bytes", "records_data_size_bytes:char_count"):
            self._adjust_stat(conn, key, bytes_delta)
        if normalized_type:
            self._adjust_stat(conn, f"records_total_count:{normalized_type}", count_delta)
            self._adjust_stat(conn, f"records_data_size_bytes:{normalized_type}", bytes_delta)
        if pinned_delta:
            self._adjust_stat(conn, "records_pinned_count", pinned_delta)
        self._set_stat(conn, "records_data_size_version", _CLIPBOARD_STATS_VERSION)

    def _prepare_image_asset(self, file_path: str, file_size: int) -> tuple[str, str, int]:
        path_text = str(file_path or "").strip()
        if not self._is_managed_clipboard_file(path_text):
            return "", path_text, max(0, int(file_size or 0))
        try:
            content_hash = self._file_sha256(path_text)
            actual_size = int(Path(path_text).stat().st_size)
            return content_hash, path_text, actual_size
        except Exception as e:
            logger.warning(f"prepare clipboard image asset failed: {e}")
            return "", path_text, max(0, int(file_size or 0))

    def _retain_image_asset(
        self,
        conn: sqlite3.Connection,
        content_hash: str,
        file_path: str,
        file_size: int,
    ) -> tuple[str, int, Optional[str]]:
        if not content_hash:
            return str(file_path or ""), 0, None
        row = conn.execute(
            "SELECT file_path, file_size, ref_count FROM clipboard_images WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if row is not None:
            canonical_path = str(row["file_path"] or "")
            conn.execute(
                "UPDATE clipboard_images SET ref_count = ref_count + 1 WHERE content_hash = ?",
                (content_hash,),
            )
            duplicate_path = str(file_path or "") if str(file_path or "") != canonical_path else None
            return canonical_path, 0, duplicate_path
        conn.execute(
            """
            INSERT INTO clipboard_images(content_hash, file_path, file_size, ref_count)
            VALUES (?, ?, ?, 1)
            """,
            (content_hash, str(file_path or ""), max(0, int(file_size or 0))),
        )
        return str(file_path or ""), max(0, int(file_size or 0)), None

    def _release_image_asset(
        self, conn: sqlite3.Connection, content_hash: str, release_count: int = 1
    ) -> tuple[int, Optional[str]]:
        if not content_hash:
            return 0, None
        row = conn.execute(
            "SELECT file_path, file_size, ref_count FROM clipboard_images WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if row is None:
            return 0, None
        remaining = int(row["ref_count"] or 0) - max(1, int(release_count))
        if remaining > 0:
            conn.execute(
                "UPDATE clipboard_images SET ref_count = ? WHERE content_hash = ?",
                (remaining, content_hash),
            )
            return 0, None
        conn.execute("DELETE FROM clipboard_images WHERE content_hash = ?", (content_hash,))
        return max(0, int(row["file_size"] or 0)), str(row["file_path"] or "") or None

    def _record_rows_for_ids(self, conn: sqlite3.Connection, record_ids: list[int]) -> list[sqlite3.Row]:
        if not record_ids:
            return []
        placeholders = ",".join("?" * len(record_ids))
        return conn.execute(
            f"""
            SELECT id, content_type, data_size_bytes, image_hash, file_path, is_pinned
            FROM clipboard_records
            WHERE id IN ({placeholders})
            """,
            [int(record_id) for record_id in record_ids],
        ).fetchall()

    def _delete_records_in_connection(
        self, conn: sqlite3.Connection, record_ids: list[int]
    ) -> list[str]:
        ids = sorted({int(record_id) for record_id in record_ids})
        rows = self._record_rows_for_ids(conn, ids)
        if not rows:
            return []
        placeholders = ",".join("?" * len(ids))
        self._delete_fts(conn, ids)
        conn.execute(f"DELETE FROM clipboard_records WHERE id IN ({placeholders})", ids)
        cleanup_paths: list[str] = []
        image_release_counts: dict[str, int] = {}
        legacy_paths: list[str] = []
        for row in rows:
            content_type = str(row["content_type"] or "text")
            self._apply_stats_delta(
                conn,
                content_type,
                count_delta=-1,
                bytes_delta=-int(row["data_size_bytes"] or 0),
                pinned_delta=-1 if bool(row["is_pinned"]) else 0,
            )
            image_hash = str(row["image_hash"] or "")
            if image_hash:
                image_release_counts[image_hash] = image_release_counts.get(image_hash, 0) + 1
            elif content_type == "image":
                legacy_paths.append(str(row["file_path"] or ""))
        for image_hash, release_count in image_release_counts.items():
            released_size, released_path = self._release_image_asset(conn, image_hash, release_count)
            if released_size:
                self._apply_stats_delta(conn, "image", bytes_delta=-released_size)
            if released_path:
                cleanup_paths.append(released_path)
        for legacy_path in legacy_paths:
            if not legacy_path:
                continue
            still_used = conn.execute(
                "SELECT 1 FROM clipboard_records WHERE file_path = ? LIMIT 1", (legacy_path,)
            ).fetchone()
            if still_used is None:
                cleanup_paths.append(legacy_path)
        return cleanup_paths

    def _remaining_count(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COUNT(*) FROM clipboard_records").fetchone()
        return int(row[0]) if row else 0

    def _reclaim_storage(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        try:
            conn.execute("VACUUM")
        except Exception as e:
            logger.warning(f"clipboard database vacuum failed: {e}")
        try:
            conn.execute("PRAGMA optimize")
        except Exception:
            pass

    def _ensure_fts_and_stats(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clipboard_stats (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS clipboard_records_fts
                USING fts5(content, file_path, tags)
                """
            )
            record_count = int(conn.execute("SELECT COUNT(*) FROM clipboard_records").fetchone()[0] or 0)
            fts_count = int(conn.execute("SELECT COUNT(*) FROM clipboard_records_fts").fetchone()[0] or 0)
            if record_count > 0 and fts_count == 0:
                self._rebuild_fts(conn)
            self._fts_available = True
        except Exception as e:
            logger.warning(f"clipboard FTS5 unavailable: {e}")
        try:
            ensure_substring_index(conn)
            self._substring_index_available = True
        except sqlite3.Error:
            logger.warning("剪贴板子串索引不可用，使用兼容搜索", exc_info=True)
        stats_version = self._get_stat(conn, "records_data_size_version")
        if (
            self._get_stat(conn, "records_data_size_all_bytes") is None
            or stats_version != _CLIPBOARD_STATS_VERSION
        ):
            self._refresh_stats(conn)

    def _rebuild_fts(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("DELETE FROM clipboard_records_fts")
            rows = conn.execute(
                """
                SELECT id, content, file_path, tags
                FROM clipboard_records
                """
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO clipboard_records_fts(rowid, content, file_path, tags)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        int(row["id"]),
                        str(row["content"] or ""),
                        str(row["file_path"] or ""),
                        str(row["tags"] or ""),
                    ),
                )
        except Exception as e:
            logger.warning(f"clipboard FTS rebuild failed: {e}")

    def _upsert_fts(self, conn: sqlite3.Connection, record_id: int) -> None:
        try:
            row = conn.execute(
                """
                SELECT id, content, file_path, tags
                FROM clipboard_records
                WHERE id = ?
                """,
                (int(record_id),),
            ).fetchone()
            if row is None:
                return
            conn.execute("DELETE FROM clipboard_records_fts WHERE rowid = ?", (int(record_id),))
            conn.execute(
                """
                INSERT INTO clipboard_records_fts(rowid, content, file_path, tags)
                VALUES (?, ?, ?, ?)
                """,
                (
                    int(row["id"]),
                    str(row["content"] or ""),
                    str(row["file_path"] or ""),
                    str(row["tags"] or ""),
                ),
            )
        except Exception:
            pass

    def _delete_fts(self, conn: sqlite3.Connection, record_ids: list[int]) -> None:
        try:
            for record_id in record_ids:
                conn.execute("DELETE FROM clipboard_records_fts WHERE rowid = ?", (int(record_id),))
        except Exception:
            pass

    @staticmethod
    def _fts_query(keyword: str) -> str:
        parts = [x for x in re.split(r"\s+", str(keyword or "").strip()) if x]
        if not parts:
            return ""
        quoted = []
        for part in parts[:8]:
            quoted.append('"' + part.replace('"', '""') + '"')
        return " AND ".join(quoted)

    def _get_stat(self, conn: sqlite3.Connection, key: str) -> Optional[str]:
        try:
            row = conn.execute("SELECT value FROM clipboard_stats WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else None
        except Exception:
            return None

    def _set_stat(self, conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute(
            """
            INSERT INTO clipboard_stats(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(key), str(value)),
        )

    def _calculate_records_data_size_bytes(
        self, conn: sqlite3.Connection, content_type: Optional[str] = None
    ) -> int:
        where_sql = ""
        params: tuple[Any, ...] = ()
        if content_type:
            where_sql = "WHERE content_type = ? OR (? = 'text' AND content_type = 'code_snippet')"
            params = (content_type, content_type)

        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(data_size_bytes), 0) AS text_bytes
            FROM clipboard_records
            {where_sql}
            """,
            params,
        ).fetchone()
        total = int(row["text_bytes"] or 0) if row else 0

        if content_type is None or content_type == "image":
            image_row = conn.execute(
                "SELECT COALESCE(SUM(file_size), 0) AS image_bytes FROM clipboard_images WHERE ref_count > 0"
            ).fetchone()
            total += int(image_row["image_bytes"] or 0) if image_row else 0
        return int(total)

    def _refresh_stats(self, conn: sqlite3.Connection) -> None:
        try:
            total = self._calculate_records_data_size_bytes(conn, None)
            count = int(conn.execute("SELECT COUNT(*) FROM clipboard_records").fetchone()[0] or 0)
            self._set_stat(conn, "records_data_size_version", _CLIPBOARD_STATS_VERSION)
            self._set_stat(conn, "records_data_size_all_bytes", total)
            self._set_stat(conn, "records_total_count", count)
            pinned_count = int(
                conn.execute("SELECT COUNT(*) FROM clipboard_records WHERE is_pinned = 1").fetchone()[0] or 0
            )
            self._set_stat(conn, "records_pinned_count", pinned_count)
            # 同步更新虚拟的 'char_count' 排序分类的缓存数据
            self._set_stat(conn, "records_data_size_bytes:char_count", total)
            self._set_stat(conn, "records_total_count:char_count", count)
            type_rows = conn.execute(
                """
                SELECT
                    CASE WHEN content_type = 'code_snippet' THEN 'text' ELSE content_type END AS content_type,
                    COUNT(*) AS count
                FROM clipboard_records
                GROUP BY CASE WHEN content_type = 'code_snippet' THEN 'text' ELSE content_type END
                """
            ).fetchall()
            seen_types: set[str] = {"char_count"}
            for row in type_rows:
                content_type = str(row["content_type"] or "")
                if not content_type:
                    continue
                seen_types.add(content_type)
                self._set_stat(conn, f"records_total_count:{content_type}", int(row["count"] or 0))
                self._set_stat(
                    conn,
                    f"records_data_size_bytes:{content_type}",
                    self._calculate_records_data_size_bytes(conn, content_type),
                )
            for key_row in conn.execute("SELECT key FROM clipboard_stats WHERE key LIKE 'records_total_count:%' OR key LIKE 'records_data_size_bytes:%'").fetchall():
                key = str(key_row["key"] or "")
                suffix = key.split(":", 1)[-1] if ":" in key else ""
                if suffix and suffix not in seen_types:
                    self._set_stat(conn, key, 0)
        except Exception as e:
            logger.warning(f"clipboard stats refresh failed: {e}")

    @staticmethod
    def _format_bytes(size: int) -> str:
        size = max(0, int(size))
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    @staticmethod
    def _normalize_record_content(content: str) -> str:
        lines = [line.strip() for line in str(content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        return "\n".join(line for line in lines if line).strip()

    @staticmethod
    def _normalize_content_type(content_type: Optional[str]) -> str:
        value = str(content_type or "text").strip()
        return "text" if value == "code_snippet" else value

    def init_database(self) -> None:
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    conn.execute("PRAGMA journal_mode=WAL")
                    self._migrate_database(conn)
                    conn.executescript(SCHEMA_SQL)
                    self._backfill_normalized_hashes(conn)
                    duplicate_paths = self._backfill_record_metrics_and_images(conn)
                    self._ensure_fts_and_stats(conn)
                    conn.commit()
                self._cleanup_managed_files(duplicate_paths)
                if self.auto_cleanup_enabled:
                    self.cleanup_retention()
            except Exception as e:
                logger.error(f"Database init failed: {e}")
                self.last_error = str(e)
                # 仅在确认库文件损坏时才重建，避免瞬时错误（如 database is locked）
                # 触发整库删除导致剪贴板历史全丢。
                if self._confirm_database_corrupted(e):
                    self._try_rebuild()
                else:
                    logger.warning(
                        "Database init failed but corruption not confirmed; keep existing database untouched"
                    )

    @staticmethod
    def _error_indicates_corruption(error: Exception) -> bool:
        msg = str(error).lower()
        markers = (
            "file is not a database",
            "database disk image is malformed",
            "malformed",
            "encrypted",
            "corrupt",
        )
        return any(marker in msg for marker in markers)

    def _confirm_database_corrupted(self, init_error: Exception) -> bool:
        if self._error_indicates_corruption(init_error):
            return True
        try:
            with closing(self._connect()) as conn, conn:
                row = conn.execute("PRAGMA integrity_check").fetchone()
            result = str(row[0]).strip().lower() if row else ""
            if result == "ok":
                return False
            logger.warning(f"integrity_check reports: {result!r}")
            return True
        except Exception as check_error:
            # 连不上但错误信息不像损坏（典型为锁冲突）时保守处理：不销毁数据
            logger.warning(f"integrity_check failed: {check_error}")
            return self._error_indicates_corruption(check_error)

    def _try_rebuild(self) -> None:
        try:
            self._quarantine_database_files()
            with closing(self._connect()) as conn, conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA_SQL)
                self._ensure_fts_and_stats(conn)
                conn.commit()
            logger.warning("Database rebuilt after confirmed corruption")
        except Exception as e:
            logger.error(f"Database rebuild failed: {e}")

    def _quarantine_database_files(self) -> None:
        """把旧库与 WAL/SHM 残留改名隔离为 .bak，而不是直接删除。"""
        stamp = time.strftime("%Y%m%d_%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(self.db_path) + suffix)
            if not source.exists():
                continue
            try:
                if suffix:
                    # WAL/SHM 属于旧库的附属文件，直接删除即可（主库已被隔离）
                    source.unlink(missing_ok=True)
                else:
                    target = source.with_name(f"{source.name}.corrupt-{stamp}.bak")
                    os.replace(source, target)
                    logger.warning(f"Corrupt database quarantined: {target}")
            except Exception as e:
                logger.error(f"Failed to quarantine {source}: {e}")

    def _migrate_database(self, conn: sqlite3.Connection) -> None:
        try:
            # 检查表是否存在
            table_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='clipboard_records'"
            ).fetchone()
            if not table_exists:
                return

            cursor = conn.execute("PRAGMA table_info(clipboard_records)")
            columns = [row["name"] for row in cursor.fetchall()]
            if "normalized_hash" not in columns:
                logger.info("Migrating clipboard_records: adding normalized_hash column")
                conn.execute("ALTER TABLE clipboard_records ADD COLUMN normalized_hash TEXT DEFAULT ''")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_clipboard_records_normalized_hash ON clipboard_records(normalized_hash)")
                conn.commit()
                self._backfill_normalized_hashes(conn)
            if "image_hash" not in columns:
                conn.execute("ALTER TABLE clipboard_records ADD COLUMN image_hash TEXT DEFAULT ''")
            if "data_size_bytes" not in columns:
                conn.execute("ALTER TABLE clipboard_records ADD COLUMN data_size_bytes INTEGER DEFAULT 0")
            conn.execute("DROP INDEX IF EXISTS idx_clipboard_records_content")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_clipboard_records_image_hash ON clipboard_records(image_hash)"
            )
        except Exception as e:
            logger.error(f"Database migration failed: {e}")

    def _backfill_normalized_hashes(self, conn: sqlite3.Connection) -> None:
        try:
            import hashlib
            rows = conn.execute(
                "SELECT id, content FROM clipboard_records WHERE normalized_hash = '' OR normalized_hash IS NULL"
            ).fetchall()
            if not rows:
                return
            logger.info(f"Backfilling normalized_hash for {len(rows)} records")
            for row in rows:
                nid = row["id"]
                content = str(row["content"] or "")
                norm = self._normalize_record_content(content)
                h = hashlib.md5(norm.encode("utf-8")).hexdigest()
                conn.execute("UPDATE clipboard_records SET normalized_hash = ? WHERE id = ?", (h, nid))
            conn.commit()
        except Exception as e:
            logger.error(f"Backfilling normalized hashes failed: {e}")

    def _backfill_record_metrics_and_images(self, conn: sqlite3.Connection) -> list[str]:
        if self._get_stat(conn, "records_data_size_version") == _CLIPBOARD_STATS_VERSION:
            return []
        duplicate_paths: list[str] = []
        conn.execute("DELETE FROM clipboard_images")
        rows = conn.execute(
            """
            SELECT id, content, content_type, file_type, file_path, file_size,
                   error_info, metadata
            FROM clipboard_records
            ORDER BY id
            """
        ).fetchall()
        for row in rows:
            content_type = str(row["content_type"] or "text")
            file_path = str(row["file_path"] or "")
            file_size = max(0, int(row["file_size"] or 0))
            image_hash = ""
            if content_type == "image":
                image_hash, prepared_path, file_size = self._prepare_image_asset(file_path, file_size)
                canonical_path, _asset_delta, duplicate_path = self._retain_image_asset(
                    conn, image_hash, prepared_path, file_size
                )
                if image_hash:
                    file_path = canonical_path
                if duplicate_path:
                    duplicate_paths.append(duplicate_path)
            metadata = str(row["metadata"] or "{}")
            data_size = self._record_payload_size_bytes(
                row["content"], row["file_type"], file_path, row["error_info"], metadata
            )
            conn.execute(
                """
                UPDATE clipboard_records
                SET file_path = ?, file_size = ?, image_hash = ?, data_size_bytes = ?
                WHERE id = ?
                """,
                (file_path, file_size, image_hash, data_size, int(row["id"])),
            )
        self._set_stat(conn, "records_data_size_version", "")
        return duplicate_paths

    def add_record(
        self,
        content: str,
        content_type: str = "text",
        file_type: str = "",
        file_path: str = "",
        file_size: int = 0,
        source_app: str = "Unknown",
        is_pinned: int = 0,
        error_info: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[int]:
        content_type = self._normalize_content_type(content_type)
        normalized_content = self._normalize_record_content(content)
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        duplicate_paths: list[str] = []
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    h = hashlib.md5(normalized_content.encode("utf-8")).hexdigest() if normalized_content else ""
                    image_hash = ""
                    prepared_path = str(file_path or "")
                    prepared_size = max(0, int(file_size or 0))
                    if content_type == "image":
                        image_hash, prepared_path, prepared_size = self._prepare_image_asset(
                            prepared_path, prepared_size
                        )

                    row = None
                    if image_hash:
                        row = conn.execute(
                            """
                            SELECT id, file_path
                            FROM clipboard_records
                            WHERE image_hash = ? AND content_type = 'image'
                            LIMIT 1
                            """,
                            (image_hash,),
                        ).fetchone()
                    elif h:
                        # 走 normalized_hash 索引（O(log N)）；同哈希内优先精确原文，
                        # 避免对全表 TEXT 做逐行 content = ? 比对。
                        row = conn.execute(
                            """
                            SELECT id, file_path
                            FROM clipboard_records
                            WHERE normalized_hash = ? AND content_type = ?
                            ORDER BY (content = ?) DESC, timestamp DESC
                            LIMIT 1
                            """,
                            (h, content_type, content),
                        ).fetchone()
                    else:
                        row = conn.execute(
                            """
                            SELECT id, file_path
                            FROM clipboard_records
                            WHERE content = ? AND content_type = ?
                            LIMIT 1
                            """,
                            (content, content_type),
                        ).fetchone()

                    if row is not None:
                        record_id = int(row["id"])
                        conn.execute(
                            "UPDATE clipboard_records SET timestamp = CURRENT_TIMESTAMP WHERE id = ?",
                            (record_id,),
                        )
                        conn.commit()
                        if (
                            image_hash
                            and prepared_path
                            and prepared_path != str(row["file_path"] or "")
                        ):
                            duplicate_paths.append(prepared_path)
                        self._cleanup_managed_files(duplicate_paths)
                        return record_id

                    canonical_path = prepared_path
                    asset_bytes_delta = 0
                    if image_hash:
                        canonical_path, asset_bytes_delta, duplicate_path = self._retain_image_asset(
                            conn, image_hash, prepared_path, prepared_size
                        )
                        if duplicate_path:
                            duplicate_paths.append(duplicate_path)
                    data_size_bytes = self._record_payload_size_bytes(
                        content, file_type, canonical_path, error_info, metadata_json
                    )

                    cur = conn.execute(
                        """
                        INSERT INTO clipboard_records
                        (content, content_type, file_type, file_path, file_size,
                         source_app, is_pinned, error_info, metadata, normalized_hash,
                         image_hash, data_size_bytes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            content,
                            content_type,
                            file_type,
                            canonical_path,
                            prepared_size,
                            source_app,
                            is_pinned,
                            error_info,
                            metadata_json,
                            h,
                            image_hash,
                            data_size_bytes,
                        ),
                    )
                    record_id = int(cur.lastrowid)
                    self._upsert_fts(conn, record_id)
                    self._apply_stats_delta(
                        conn,
                        content_type,
                        count_delta=1,
                        bytes_delta=data_size_bytes,
                        pinned_delta=1 if bool(is_pinned) else 0,
                    )
                    if asset_bytes_delta:
                        self._apply_stats_delta(conn, "image", bytes_delta=asset_bytes_delta)
                    conn.commit()
                self._cleanup_managed_files(duplicate_paths)
                self._maybe_auto_cleanup()
                return record_id
            except Exception as e:
                logger.error(f"add_record failed: {e}")
                self.last_error = str(e)
                return None

    def get_records(
        self, limit: int = 50, offset: int = 0, content_type: Optional[str] = None,
        *, cancelled: Optional[Callable[[], bool]] = None,
    ) -> list[tuple]:
        is_char_count_order = (content_type == "char_count")
        actual_content_type = None if is_char_count_order else content_type
        actual_content_type = self._normalize_content_type(actual_content_type) if actual_content_type else None
        try:
            with closing(self._connect()) as conn, conn:
                order_clause = "LENGTH(content) DESC, timestamp DESC, id DESC" if is_char_count_order else "timestamp DESC, id DESC"
                if cancelled is not None:
                    conn.set_progress_handler(lambda: int(cancelled()), 1000)
                if actual_content_type:
                    rows = conn.execute(
                        f"""
                        SELECT id, content, content_type, file_type, file_path, file_size,
                               source_app, timestamp, usage_count, tags, is_favorite, is_pinned,
                               error_info, metadata
                        FROM clipboard_records
                        WHERE is_pinned = 0
                          AND (content_type = ? OR (? = 'text' AND content_type = 'code_snippet'))
                        ORDER BY {order_clause}
                        LIMIT ? OFFSET ?
                        """,
                        (actual_content_type, actual_content_type, limit, offset),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"""
                        SELECT id, content, content_type, file_type, file_path, file_size,
                               source_app, timestamp, usage_count, tags, is_favorite, is_pinned,
                               error_info, metadata
                        FROM clipboard_records
                        WHERE is_pinned = 0
                        ORDER BY {order_clause}
                        LIMIT ? OFFSET ?
                        """,
                        (limit, offset),
                    ).fetchall()
                return [tuple(row) for row in rows]
        except Exception as e:
            logger.error(f"get_records failed: {e}")
            self.last_error = str(e)
            return []

    def get_pinned_records(self) -> list[tuple]:
        try:
            with closing(self._connect()) as conn, conn:
                rows = conn.execute(
                    """
                    SELECT id, content, content_type, file_type, file_path, file_size,
                           source_app, timestamp, usage_count, tags, is_favorite, is_pinned,
                           error_info, metadata
                    FROM clipboard_records
                    WHERE is_pinned = 1
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (PINNED_RECORD_LIMIT,),
                ).fetchall()
                return [tuple(row) for row in rows]
        except Exception as e:
            logger.error(f"get_pinned_records failed: {e}")
            self.last_error = str(e)
            return []

    def get_record_by_id(self, record_id: int) -> Optional[tuple]:
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    row = conn.execute(
                        """
                        SELECT id, content, content_type, file_type, file_path, file_size,
                               source_app, timestamp, usage_count, tags, is_favorite, is_pinned,
                               error_info, metadata
                        FROM clipboard_records
                        WHERE id = ?
                        """,
                        (record_id,),
                    ).fetchone()
                    return tuple(row) if row else None
            except Exception as e:
                logger.error(f"get_record_by_id failed: {e}")
                self.last_error = str(e)
                return None

    def get_total_count(self, content_type: Optional[str] = None) -> int:
        is_char_count_order = (content_type == "char_count")
        actual_content_type = None if is_char_count_order else content_type
        actual_content_type = self._normalize_content_type(actual_content_type) if actual_content_type else None
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    cached_key = f"records_total_count:{content_type}" if content_type else "records_total_count"
                    cached = self._get_stat(conn, cached_key)
                    if cached is not None:
                        return int(cached)
                    if actual_content_type:
                        row = conn.execute(
                            "SELECT COUNT(*) FROM clipboard_records WHERE content_type = ? OR (? = 'text' AND content_type = 'code_snippet')",
                            (actual_content_type, actual_content_type),
                        ).fetchone()
                    else:
                        row = conn.execute(
                            "SELECT COUNT(*) FROM clipboard_records"
                        ).fetchone()
                    total = int(row[0]) if row else 0
                    self._set_stat(conn, cached_key, total)
                    conn.commit()
                    return total
            except Exception as e:
                logger.error(f"get_total_count failed: {e}")
                self.last_error = str(e)
                return 0

    def get_pinned_count(self) -> int:
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    cached = self._get_stat(conn, "records_pinned_count")
                    if cached is not None:
                        return int(cached)
                    row = conn.execute(
                        "SELECT COUNT(*) FROM clipboard_records WHERE is_pinned = 1"
                    ).fetchone()
                    total = int(row[0]) if row else 0
                    self._set_stat(conn, "records_pinned_count", total)
                    conn.commit()
                    return total
            except Exception as e:
                logger.error(f"get_pinned_count failed: {e}")
                self.last_error = str(e)
                return 0

    def get_database_size(self) -> str:
        try:
            size = 0
            for suffix in ("", "-wal", "-shm"):
                path = f"{self.db_path}{suffix}"
                if os.path.exists(path):
                    size += os.path.getsize(path)
            return self._format_bytes(size)
        except Exception:
            return "0 KB"

    def get_records_data_size(self, content_type: Optional[str] = None) -> str:
        is_char_count_order = (content_type == "char_count")
        actual_content_type = None if is_char_count_order else content_type
        actual_content_type = self._normalize_content_type(actual_content_type) if actual_content_type else None
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    if not actual_content_type:
                        cached = self._get_stat(conn, f"records_data_size_bytes:{content_type}" if is_char_count_order else "records_data_size_all_bytes")
                        if cached is not None:
                            return self._format_bytes(int(cached))
                    else:
                        cached = self._get_stat(conn, f"records_data_size_bytes:{actual_content_type}")
                        if cached is not None:
                            return self._format_bytes(int(cached))
                    total = self._calculate_records_data_size_bytes(conn, actual_content_type)
                    if not actual_content_type:
                        self._set_stat(conn, f"records_data_size_bytes:{content_type}" if is_char_count_order else "records_data_size_all_bytes", total)
                    else:
                        self._set_stat(conn, f"records_data_size_bytes:{actual_content_type}", total)
                    conn.commit()
                return self._format_bytes(total)
            except Exception as e:
                logger.error(f"get_records_data_size failed: {e}")
                self.last_error = str(e)
                return "0 B"

    def get_statistics(self, content_type: Optional[str] = None) -> tuple[int, str]:
        """一次短查询读取增量统计，字数排序与全部记录共用统计。"""
        actual_type = None if content_type == "char_count" else content_type
        actual_type = self._normalize_content_type(actual_type) if actual_type else None
        count_key = f"records_total_count:{actual_type}" if actual_type else "records_total_count"
        size_key = f"records_data_size_bytes:{actual_type}" if actual_type else "records_data_size_all_bytes"
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT key, value FROM clipboard_stats WHERE key IN (?, ?)", (count_key, size_key))
            values = {row[0]: int(row[1]) for row in rows}
        return values.get(count_key, 0), self._format_bytes(values.get(size_key, 0))

    def search_records(
        self, keyword: str, content_type: Optional[str] = None, limit: int = 100, offset: int = 0,
        *, include_pinned: bool = True, cancelled: Optional[Callable[[], bool]] = None,
    ) -> list[tuple]:
        """WAL 独立读连接执行分页，取消旧搜索不会阻塞界面写入。"""
        order_by_length = content_type == "char_count"
        actual_type = None if order_by_length else content_type
        actual_type = self._normalize_content_type(actual_type) if actual_type else None
        try:
            with closing(self._connect()) as conn:
                if cancelled is not None:
                    if cancelled():
                        return []
                    conn.set_progress_handler(lambda: int(cancelled()), 1000)
                try:
                    return query_records(
                        conn, keyword, self._fts_query(keyword), actual_type, limit, offset,
                        include_pinned=include_pinned, order_by_length=order_by_length,
                        use_fts=self._fts_available, use_substrings=self._substring_index_available,
                    )
                except sqlite3.OperationalError:
                    if cancelled is not None and cancelled():
                        return []
                    # 旧版 SQLite 或被外部工具删除的索引仍能使用原有子串语义。
                    return query_records(
                        conn, keyword, "", actual_type, limit, offset,
                        include_pinned=include_pinned, order_by_length=order_by_length,
                        use_fts=False, use_substrings=False,
                    )
        except Exception as e:
            if cancelled is None or not cancelled():
                logger.error("search_records failed: %s", e)
                self.last_error = str(e)
            return []

    def update_pin_status(self, record_id: int, is_pinned: bool) -> bool:
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    row = conn.execute(
                        "SELECT content_type, is_pinned FROM clipboard_records WHERE id = ?",
                        (record_id,),
                    ).fetchone()
                    if row is None:
                        return False
                    old_pinned = bool(row["is_pinned"])
                    new_pinned = bool(is_pinned)
                    if old_pinned == new_pinned:
                        return True
                    conn.execute(
                        "UPDATE clipboard_records SET is_pinned = ? WHERE id = ?",
                        (1 if new_pinned else 0, record_id),
                    )
                    self._apply_stats_delta(
                        conn,
                        str(row["content_type"] or "text"),
                        pinned_delta=1 if new_pinned else -1,
                    )
                    conn.commit()
                    return True
            except Exception as e:
                logger.error(f"update_pin_status failed: {e}")
                self.last_error = str(e)
                return False

    def update_usage_count(self, record_id: int) -> bool:
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    conn.execute(
                        "UPDATE clipboard_records SET usage_count = usage_count + 1 WHERE id = ?",
                        (record_id,),
                    )
                    conn.commit()
                    return True
            except Exception as e:
                logger.error(f"update_usage_count failed: {e}")
                self.last_error = str(e)
                return False

    def touch_timestamp(self, record_id: int) -> bool:
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    conn.execute(
                        "UPDATE clipboard_records SET timestamp = CURRENT_TIMESTAMP WHERE id = ?",
                        (record_id,),
                    )
                    conn.commit()
                    return True
            except Exception as e:
                logger.error(f"touch_timestamp failed: {e}")
                self.last_error = str(e)
                return False

    def delete_record(self, record_id: int) -> bool:
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    paths = self._delete_records_in_connection(conn, [int(record_id)])
                    conn.commit()
                    self._cleanup_managed_files(paths)
                    if self._remaining_count(conn) == 0:
                        self._reclaim_storage(conn)
                    return True
            except Exception as e:
                logger.error(f"delete_record failed: {e}")
                self.last_error = str(e)
                return False

    def update_record(
        self,
        record_id: int,
        content: str,
        content_type: str = "",
        file_path: str = "",
    ) -> bool:
        content_type = self._normalize_content_type(content_type) if content_type else ""
        cleanup_paths: list[str] = []
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    old = conn.execute(
                        """
                        SELECT content_type, file_type, file_path, file_size, error_info,
                               metadata, normalized_hash, image_hash, data_size_bytes, is_pinned
                        FROM clipboard_records WHERE id = ?
                        """,
                        (record_id,),
                    ).fetchone()
                    if old is None:
                        return False
                    old_type = str(old["content_type"] or "text")
                    new_type = content_type or old_type
                    new_path = str(file_path or old["file_path"] or "")
                    new_size = max(0, int(old["file_size"] or 0))
                    old_image_hash = str(old["image_hash"] or "")
                    new_image_hash = ""
                    asset_bytes_delta = 0
                    if new_type == "image":
                        new_image_hash, new_path, new_size = self._prepare_image_asset(new_path, new_size)
                    if new_image_hash and new_image_hash != old_image_hash:
                        new_path, retained_bytes, duplicate_path = self._retain_image_asset(
                            conn, new_image_hash, new_path, new_size
                        )
                        asset_bytes_delta += retained_bytes
                        if duplicate_path:
                            cleanup_paths.append(duplicate_path)
                    elif new_image_hash == old_image_hash and old_image_hash:
                        if new_path and new_path != str(old["file_path"] or ""):
                            cleanup_paths.append(new_path)
                        new_path = str(old["file_path"] or new_path)

                    if old_image_hash and old_image_hash != new_image_hash:
                        released_bytes, released_path = self._release_image_asset(conn, old_image_hash)
                        asset_bytes_delta -= released_bytes
                        if released_path:
                            cleanup_paths.append(released_path)

                    normalized = self._normalize_record_content(content)
                    normalized_hash = hashlib.md5(normalized.encode("utf-8")).hexdigest() if normalized else ""
                    metadata_json = str(old["metadata"] or "{}")
                    data_size_bytes = self._record_payload_size_bytes(
                        content, old["file_type"], new_path, old["error_info"], metadata_json
                    )
                    conn.execute(
                        """
                        UPDATE clipboard_records
                        SET content = ?, content_type = ?, file_path = ?, file_size = ?,
                            normalized_hash = ?, image_hash = ?, data_size_bytes = ?
                        WHERE id = ?
                        """,
                        (
                            content,
                            new_type,
                            new_path,
                            new_size,
                            normalized_hash,
                            new_image_hash,
                            data_size_bytes,
                            record_id,
                        ),
                    )
                    self._upsert_fts(conn, int(record_id))
                    pinned = 1 if bool(old["is_pinned"]) else 0
                    old_data_size = int(old["data_size_bytes"] or 0)
                    if old_type == new_type:
                        self._apply_stats_delta(
                            conn, new_type, bytes_delta=data_size_bytes - old_data_size
                        )
                    else:
                        self._apply_stats_delta(
                            conn,
                            old_type,
                            count_delta=-1,
                            bytes_delta=-old_data_size,
                            pinned_delta=-pinned,
                        )
                        self._apply_stats_delta(
                            conn,
                            new_type,
                            count_delta=1,
                            bytes_delta=data_size_bytes,
                            pinned_delta=pinned,
                        )
                    if asset_bytes_delta:
                        self._apply_stats_delta(conn, "image", bytes_delta=asset_bytes_delta)
                    conn.commit()
                self._cleanup_managed_files(cleanup_paths)
                return True
            except Exception as e:
                logger.error(f"update_record failed: {e}")
                self.last_error = str(e)
                return False

    def delete_records(self, record_ids: list[int]) -> bool:
        with self._lock:
            try:
                if not record_ids:
                    return True
                with closing(self._connect()) as conn, conn:
                    ids = sorted({int(x) for x in record_ids})
                    paths: list[str] = []
                    for start in range(0, len(ids), 500):
                        paths.extend(self._delete_records_in_connection(conn, ids[start : start + 500]))
                    conn.commit()
                    self._cleanup_managed_files(paths)
                    if self._remaining_count(conn) == 0:
                        self._reclaim_storage(conn)
                    return True
            except Exception as e:
                logger.error(f"delete_records failed: {e}")
                self.last_error = str(e)
                return False

    def clear_all(self) -> bool:
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    paths: list[str] = []
                    while True:
                        ids = [
                            int(row["id"])
                            for row in conn.execute(
                                "SELECT id FROM clipboard_records ORDER BY id LIMIT 500"
                            ).fetchall()
                        ]
                        if not ids:
                            break
                        paths.extend(self._delete_records_in_connection(conn, ids))
                    try:
                        conn.execute("DELETE FROM clipboard_records_fts")
                    except Exception:
                        pass
                    conn.execute("DELETE FROM clipboard_images")
                    for row in conn.execute("SELECT key FROM clipboard_stats").fetchall():
                        key = str(row["key"] or "")
                        self._set_stat(
                            conn,
                            key,
                            _CLIPBOARD_STATS_VERSION if key == "records_data_size_version" else 0,
                        )
                    conn.commit()
                    self._cleanup_managed_files(paths)
                    self._reclaim_storage(conn)
                    return True
            except Exception as e:
                logger.error(f"clear_all failed: {e}")
                self.last_error = str(e)
                return False

    def _maybe_auto_cleanup(self) -> None:
        if not self.auto_cleanup_enabled:
            return
        self._writes_since_cleanup += 1
        if self._writes_since_cleanup < self._cleanup_interval:
            return
        self._writes_since_cleanup = 0
        self.cleanup_retention()

    def cleanup_retention(self) -> int:
        """按保留时间、记录数和容量上限清理最旧的非置顶记录。"""
        if not self.auto_cleanup_enabled:
            return 0
        deleted_count = 0
        cleanup_paths: list[str] = []
        with self._lock:
            try:
                with closing(self._connect()) as conn, conn:
                    if self.retention_days > 0:
                        rows = conn.execute(
                            """
                            SELECT id FROM clipboard_records
                            WHERE is_pinned = 0
                              AND timestamp < datetime('now', ?)
                            ORDER BY timestamp ASC, id ASC
                            """,
                            (f"-{self.retention_days} days",),
                        ).fetchall()
                        ids = [int(row["id"]) for row in rows]
                        for start in range(0, len(ids), 500):
                            batch = ids[start : start + 500]
                            cleanup_paths.extend(self._delete_records_in_connection(conn, batch))
                            deleted_count += len(batch)

                    if self.max_records > 0:
                        total = self._stat_int(conn, "records_total_count")
                        excess = max(0, total - self.max_records)
                        if excess:
                            rows = conn.execute(
                                """
                                SELECT id FROM clipboard_records
                                WHERE is_pinned = 0
                                ORDER BY timestamp ASC, id ASC
                                LIMIT ?
                                """,
                                (excess,),
                            ).fetchall()
                            ids = [int(row["id"]) for row in rows]
                            for start in range(0, len(ids), 500):
                                batch = ids[start : start + 500]
                                cleanup_paths.extend(self._delete_records_in_connection(conn, batch))
                                deleted_count += len(batch)

                    if self.max_capacity_bytes > 0:
                        while self._stat_int(conn, "records_data_size_all_bytes") > self.max_capacity_bytes:
                            rows = conn.execute(
                                """
                                SELECT id FROM clipboard_records
                                WHERE is_pinned = 0
                                ORDER BY timestamp ASC, id ASC
                                LIMIT 100
                                """
                            ).fetchall()
                            ids = [int(row["id"]) for row in rows]
                            if not ids:
                                break
                            cleanup_paths.extend(self._delete_records_in_connection(conn, ids))
                            deleted_count += len(ids)
                    conn.commit()
                self._cleanup_managed_files(cleanup_paths)
                return deleted_count
            except Exception as e:
                logger.error(f"cleanup_retention failed: {e}")
                self.last_error = str(e)
                return deleted_count
