from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from contextlib import closing
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any, Optional

from deepcat.utils.paths import get_app_dir
from deepcat.core.restore_transaction import replace_restored_files
from deepcat.settings_store import (
    get_settings_path,
    get_model_catalog_path,
    default_model_catalog,
    save_settings,
    _defaults
)

logger = logging.getLogger(__name__)
_restore_lock = threading.Lock()

_SQLITE_BACKUP_TARGETS = {
    "clipboard": ("clipboard_history.db", "data/clipboard_history.db"),
    "table_notes": ("table_notes.db", "data/table_notes.db"),
    "later_read": ("later_read.db", "data/later_read.db"),
    "ai_chat_history": ("translation_history.db", "data/translation_history.db"),
    "todo": ("todo.db", "data/todo.db"),
}
_ALLOWED_ROOT_RESTORE_FILES = {"settings.json", "model_catalog.json"}
_ALLOWED_DATA_RESTORE_FILES = {
    "data/clipboard_history.db",
    "data/clipboard_history.db-wal",
    "data/clipboard_history.db-shm",
    "data/table_notes.db",
    "data/table_notes.db-wal",
    "data/table_notes.db-shm",
    "data/later_read.db",
    "data/later_read.db-wal",
    "data/later_read.db-shm",
    "data/translation_history.db",
    "data/translation_history.db-wal",
    "data/translation_history.db-shm",
    "data/todo.db",
    "data/todo.db-wal",
    "data/todo.db-shm",
}


def _sqlite_sidecar_paths(db_path: Path) -> tuple[Path, Path]:
    return (
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
    )


def _write_sqlite_snapshot(zipf: zipfile.ZipFile, db_path: Path, arcname: str, temp_dir: Path) -> None:
    if not db_path.exists():
        return
    snapshot_path = temp_dir / (db_path.name + ".snapshot")
    try:
        source_uri = db_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True, timeout=10.0)) as src:
            with closing(sqlite3.connect(str(snapshot_path), timeout=10.0)) as dst:
                src.backup(dst)
        zipf.write(snapshot_path, arcname)
    except Exception as e:
        logger.warning("SQLite online backup failed for %s, falling back to file copy: %s", db_path, e)
        zipf.write(db_path, arcname)
        for sidecar in _sqlite_sidecar_paths(db_path):
            if sidecar.exists():
                zipf.write(sidecar, f"data/{sidecar.name}")


def _remove_sqlite_sidecars(app_dir: Path, db_name: str) -> None:
    db_path = app_dir / "data" / db_name
    for extra in _sqlite_sidecar_paths(db_path):
        if extra.exists():
            try:
                extra.unlink()
            except Exception:
                pass


def _clear_log_files(log_dir: Path) -> int:
    """清空日志目录内文件，正在占用的日志文件退化为截断。"""
    if not log_dir.exists():
        return 0
    cleared = 0
    for path in sorted(log_dir.rglob("*"), reverse=True):
        try:
            if path.is_file():
                try:
                    path.unlink()
                except PermissionError:
                    path.write_text("", encoding="utf-8")
                cleared += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except Exception:
            logger.warning("清理日志文件失败: %s", path)
    log_dir.mkdir(parents=True, exist_ok=True)
    return cleared


def _iter_files_under(root: Path):
    if not root.exists():
        return
    for path in root.rglob("*"):
        try:
            if path.is_file():
                yield path
        except Exception:
            continue


def _light_cleanup_files(app_dir: Path) -> list[Path]:
    files: list[Path] = []
    files.extend(_iter_files_under(app_dir / "logs") or [])
    files.extend(_iter_files_under(app_dir / "data" / "temp") or [])
    return files


def estimate_light_cleanup_size(app_dir: Path | None = None) -> int:
    """估算一键轻量清理可处理的日志与临时文件大小。"""
    root = app_dir or get_app_dir()
    total = 0
    for path in _light_cleanup_files(root):
        try:
            total += path.stat().st_size
        except Exception:
            pass
    return total


def _clear_light_cleanup_files(app_dir: Path) -> tuple[int, int]:
    cleared = 0
    freed = 0
    for path in _light_cleanup_files(app_dir):
        try:
            before = path.stat().st_size
        except Exception:
            before = 0
        try:
            path.unlink()
            cleared += 1
            freed += before
        except PermissionError:
            try:
                path.write_text("", encoding="utf-8")
                cleared += 1
                freed += before
            except Exception:
                logger.warning("轻量清理文件失败: %s", path)
        except Exception:
            logger.warning("轻量清理文件失败: %s", path)

    for root in (app_dir / "logs", app_dir / "data" / "temp"):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*"), reverse=True):
            try:
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except Exception:
                pass
        root.mkdir(parents=True, exist_ok=True)
    return cleared, freed


def _iter_ai_history_panels(main_window: Any) -> list[Any]:
    panels: list[Any] = []
    seen: set[int] = set()

    def add_panel(panel: Any) -> None:
        if panel is None:
            return
        try:
            panel_id = id(panel)
            if panel_id in seen:
                return
            getattr(panel, "objectName", lambda: "")()
        except Exception:
            return
        seen.add(panel_id)
        panels.append(panel)

    add_panel(getattr(main_window, "_selection_translate_panel", None))

    post_actions = getattr(main_window, "_post_actions", None)
    live_panel_getter = getattr(post_actions, "_live_ocr_panel", None)
    if callable(live_panel_getter):
        try:
            add_panel(live_panel_getter())
        except Exception:
            pass

    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            for widget in app.topLevelWidgets():
                if hasattr(widget, "_history_store"):
                    add_panel(widget)
    except Exception:
        pass

    return panels


def _close_ai_history_stores(main_window: Any) -> None:
    for panel in _iter_ai_history_panels(main_window):
        store = getattr(panel, "_history_store", None)
        if store is not None:
            try:
                store.close()
            except Exception:
                pass


def _refresh_ai_history_panels_after_clear(main_window: Any) -> None:
    for panel in _iter_ai_history_panels(main_window):
        try:
            panel._current_session_record_id = None
        except Exception:
            pass
        sidebar = getattr(panel, "_history_sidebar", None)
        if sidebar is not None:
            try:
                sidebar.refresh()
            except Exception:
                pass
        try:
            if getattr(panel, "_history_showing", False):
                panel._ensure_history_output_area()
                panel._reposition()
        except Exception:
            pass


def _normalize_restore_member_name(name: str) -> str | None:
    raw = str(name or "").replace("\\", "/").strip()
    if not raw:
        return None
    pure = PurePosixPath(raw)
    if pure.is_absolute():
        return None
    parts = pure.parts
    if any(part in {"", ".", ".."} or ":" in part for part in parts):
        return None
    normalized = str(pure)
    if normalized in _ALLOWED_ROOT_RESTORE_FILES:
        return normalized
    if normalized in _ALLOWED_DATA_RESTORE_FILES:
        return normalized
    if normalized.startswith("data/clipboard_images/"):
        return normalized
    return None


def _validated_restore_members(zipf: zipfile.ZipFile) -> tuple[list[tuple[zipfile.ZipInfo, str]], str]:
    members: list[tuple[zipfile.ZipInfo, str]] = []
    seen: set[str] = set()
    for info in zipf.infolist():
        if info.is_dir():
            continue
        normalized = _normalize_restore_member_name(info.filename)
        if normalized is None:
            return [], f"备份包含不允许的路径: {info.filename}"
        if normalized.casefold() in seen:
            return [], f"备份包含重复文件: {normalized}"
        seen.add(normalized.casefold())
        members.append((info, normalized))
    return members, ""


def _close_restore_sources(main_window: Any, names: set[str]) -> None:
    if main_window is None:
        return
    if any(name.startswith("data/clipboard_") for name in names):
        page = getattr(main_window, "_clipboard_history_page", None)
        pause_queries = getattr(page, "suspend_queries", None)
        if callable(pause_queries):
            pause_queries()
        monitor = getattr(page, "_monitor", None)
        if monitor is not None:
            monitor.stop()
    for filename, attribute in (
        ("table_notes.db", "_table_notes_store"),
        ("later_read.db", "_later_read_store"),
        ("todo.db", "_todo_store"),
    ):
        if f"data/{filename}" in names:
            store = getattr(main_window, attribute, None)
            if store is not None:
                store.close()
    if "data/translation_history.db" in names:
        for panel in _iter_ai_history_panels(main_window):
            store = getattr(panel, "_history_store", None)
            if store is not None:
                store.close()


def _replace_from_staging(staging_dir: Path, app_dir: Path, arcname: str) -> None:
    src = staging_dir / arcname
    dst = app_dir / arcname
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dst)


def backup_data(dest_zip_path: Path, options: dict[str, bool]) -> tuple[bool, str]:
    """
    根据勾选的项目打包数据为 ZIP 文件
    """
    try:
        app_dir = get_app_dir()
        dest_zip_path.parent.mkdir(parents=True, exist_ok=True)

        # 写入临时文件，最后重命名，防止写入过程中损坏文件
        temp_zip = dest_zip_path.with_suffix(".zip.tmp")

        with tempfile.TemporaryDirectory(prefix="deepcat_backup_") as d:
            snapshot_dir = Path(d)
            with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
                # 1-3. SQLite 数据使用在线快照，避免漏掉 WAL 中已经提交但尚未 checkpoint 的记录。
                for option_key, (db_name, arcname) in _SQLITE_BACKUP_TARGETS.items():
                    if options.get(option_key):
                        _write_sqlite_snapshot(zipf, app_dir / "data" / db_name, arcname, snapshot_dir)

                # 4. 设置数据
                if options.get("settings"):
                    settings_path = get_settings_path()
                    if settings_path.exists():
                        zipf.write(settings_path, "settings.json")

                # 5. 模型配置
                if options.get("model_catalog"):
                    catalog_path = get_model_catalog_path()
                    if catalog_path.exists():
                        zipf.write(catalog_path, "model_catalog.json")

        # 替换为最终文件
        temp_zip.replace(dest_zip_path)
        return True, "备份打包完成"
    except Exception as e:
        logger.error(f"backup_data failed: {e}")
        return False, f"备份失败: {e}"


def restore_data(src_zip_path: Path, main_window: Any = None) -> tuple[bool, str]:
    """先完整解压校验，再保护旧文件并切换；任一步失败则回滚。"""
    if not src_zip_path.exists():
        return False, "备份文件不存在"
    try:
        with _restore_lock:
            app_dir = get_app_dir()
            with tempfile.TemporaryDirectory(prefix="deepcat_restore_", dir=app_dir) as d:
                staging_dir = Path(d)
                with zipfile.ZipFile(src_zip_path, "r") as zipf:
                    members, validation_error = _validated_restore_members(zipf)
                    if validation_error:
                        return False, validation_error
                    if not members:
                        return False, "备份没有可恢复的数据"
                    # 完整读取会校验 ZIP CRC；在这一步成功前不触碰旧库和 WAL。
                    for info, arcname in members:
                        target = staging_dir / arcname
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zipf.open(info, "r") as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                names = [name for _, name in members]
                for name in names:
                    if name.endswith((".db-wal", ".db-shm")) and name[:-4] not in names:
                        raise ValueError(f"数据库附属文件缺少主库: {name}")
                _close_restore_sources(main_window, set(names))
                replace_restored_files(staging_dir, app_dir, names, _replace_from_staging)
        return True, "备份还原成功，请重启软件以应用更改"
    except Exception as e:
        logger.error(f"restore_data failed: {e}")
        return False, f"恢复失败: {e}"


def clear_data_disk_heavy(data_type: str, app_dir: Optional[Path] = None) -> tuple[bool, str]:
    """只执行纯磁盘的重清理（大目录删除/遍历），可在工作线程中调用。

    与 clear_data_class 分离的原因：后者涉及 GUI 与数据库连接，
    只能在主线程执行；剪贴板图片目录可达 1GB，整目录删除必须离开 GUI 线程。
    """
    try:
        root = Path(app_dir) if app_dir is not None else get_app_dir()
        if data_type == "clipboard":
            img_dir = root / "data" / "clipboard_images"
            if img_dir.exists():
                shutil.rmtree(img_dir)
                img_dir.mkdir(parents=True, exist_ok=True)
            return True, "剪贴板图片目录已清空"
        if data_type == "logs":
            cleared = _clear_log_files(root / "logs")
            return True, f"日志文件已清空（处理 {cleared} 个文件）"
        if data_type == "light_cleanup":
            cleared, freed = _clear_light_cleanup_files(root)
            return True, f"轻量清理已完成（处理 {cleared} 个文件，释放 {freed} 字节）"
        return False, "该数据类型没有可后台清理的文件"
    except Exception as e:
        logger.error(f"clear_data_disk_heavy failed: {e}")
        return False, f"清理失败: {e}"


def clear_data_class(data_type: str, main_window, *, clear_disk_heavy: bool = True) -> tuple[bool, str]:
    """
    清除哪类数据（危险操作，有二次确认警告）

    clear_disk_heavy=False 时跳过纯磁盘重清理部分（由 clear_data_disk_heavy
    在工作线程执行），仅完成主线程必须做的数据库与 GUI 部分。
    """
    try:
        app_dir = get_app_dir()
        if data_type == "clipboard":
            clipboard_page = getattr(main_window, "_clipboard_history_page", None)
            if clipboard_page is not None:
                db = getattr(clipboard_page, "_database", None)
                if db is not None:
                    db.clear_all()
                try:
                    clipboard_page._refresh_list()
                except Exception:
                    pass
            else:
                # 页面未实例化，直接删除文件
                db_path = app_dir / "data" / "clipboard_history.db"
                if db_path.exists():
                    db_path.unlink()
                for extra in ("-wal", "-shm"):
                    p = db_path.with_name(db_path.name + extra)
                    if p.exists():
                        p.unlink()
            # 彻底清空图片目录（大目录删除由 clear_data_disk_heavy 在工作线程执行）
            if clear_disk_heavy:
                img_dir = app_dir / "data" / "clipboard_images"
                if img_dir.exists():
                    shutil.rmtree(img_dir)
                    img_dir.mkdir(parents=True, exist_ok=True)
            return True, "复制记录已清空"

        elif data_type == "table_notes":
            store = getattr(main_window, "_table_notes_store", None)
            if store is not None:
                db_conn = store._db
                with db_conn:
                    db_conn.execute("DELETE FROM table_tabs")
                    db_conn.execute("DELETE FROM note_tabs")
                    db_conn.execute("DELETE FROM metadata")
                    db_conn.commit()
                main_window._load_table_notes_settings()
            return True, "表格记事已清空"

        elif data_type == "later_read":
            store = getattr(main_window, "_later_read_store", None)
            if store is not None:
                db_conn = store._db
                with db_conn:
                    db_conn.execute("DELETE FROM items")
                    db_conn.execute("DELETE FROM settings")
                    db_conn.commit()
                main_window._refresh_later_read_list()
            return True, "稍后阅读已清空"

        elif data_type == "ai_chat_history":
            deleted = False
            for panel in _iter_ai_history_panels(main_window):
                store = getattr(panel, "_history_store", None)
                if store is None:
                    continue
                clear_all = getattr(store, "clear_all", None)
                if callable(clear_all):
                    clear_all()
                else:
                    db_conn = store._db
                    with db_conn:
                        db_conn.execute("DELETE FROM history_records")
                        db_conn.commit()
                deleted = True
                break

            if not deleted:
                from deepcat.translation_history_store import TranslationHistoryStore

                store = TranslationHistoryStore()
                try:
                    store.clear_all()
                finally:
                    store.close()

            _refresh_ai_history_panels_after_clear(main_window)
            return True, "AI对话记录已清空"

        elif data_type == "todo":
            store = getattr(main_window, "_todo_store", None)
            if store is not None:
                store.save_items([])
            else:
                db_path = app_dir / "data" / "todo.db"
                if db_path.exists():
                    db_path.unlink()
                for extra in _sqlite_sidecar_paths(db_path):
                    if extra.exists():
                        extra.unlink()

            try:
                main_window._todo_items = []
            except Exception:
                pass
            try:
                todo_popup = getattr(main_window, "_todo_popup", None)
                if todo_popup is not None:
                    todo_popup.close()
                    main_window._todo_popup = None
            except Exception:
                pass
            todo_page_ready = all(
                hasattr(main_window, attr)
                for attr in ("_todo_date_label", "_todo_list")
            )
            if todo_page_ready:
                method = getattr(main_window, "_refresh_todo_list", None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass
            for method_name in ("_refresh_tray_tooltip", "_schedule_todo_checks"):
                method = getattr(main_window, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass
            return True, "休息待办已清空"

        elif data_type == "logs":
            cleared = _clear_log_files(app_dir / "logs")
            return True, f"日志文件已清空（处理 {cleared} 个文件）"

        elif data_type == "light_cleanup":
            cleared, freed = _clear_light_cleanup_files(app_dir)
            return True, f"轻量清理已完成（处理 {cleared} 个文件，释放 {freed} 字节）"

        elif data_type == "settings":
            # 恢复出厂设置，但保留我们的“数据管理”配置，防止用户 WebDAV 信息丢失
            current_dm = {}
            if getattr(main_window, "_current", None) is not None:
                ui = getattr(main_window._current, "ui", {})
                if isinstance(ui, dict):
                    current_dm = ui.get("data_management", {})

            default_s = _defaults()
            if not isinstance(default_s.ui, dict):
                default_s.ui = {}
            default_s.ui["data_management"] = current_dm

            save_settings(default_s)
            # 恢复出厂设置后将引导重启，无需在运行中冒着崩溃风险热重载 UI 控制器
            return True, "设置数据已恢复默认"

        elif data_type == "model_catalog":
            catalog_path = get_model_catalog_path()
            if catalog_path.exists():
                catalog_path.unlink()
            # 重新写入内置的默认模型列表文件
            with open(catalog_path, "w", encoding="utf-8") as f:
                json.dump(default_model_catalog(), f, ensure_ascii=False, indent=2)
            # 同样引导重启以安全载入
            return True, "模型配置已恢复默认"

        return False, "未知数据类型"
    except Exception as e:
        logger.error(f"clear_data_class failed: {e}")
        return False, f"清空失败: {e}"
