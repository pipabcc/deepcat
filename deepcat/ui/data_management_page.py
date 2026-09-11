from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import replace as dataclass_replace
from pathlib import Path, PurePosixPath
import os
import time
from typing import Any, Optional

from PyQt6.QtCore import QThread, pyqtSignal, Qt, QSize, QTimer
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QCheckBox,
    QPushButton,
    QLineEdit,
    QFileDialog,
    QGroupBox,
    QFormLayout,
    QMessageBox,
    QTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QStyle,
    QSizePolicy,
)

from deepcat.utils.paths import get_app_dir
from deepcat.utils.webdav import WebDAVClient
from deepcat.core.data_manager import restore_data, clear_data_class, clear_data_disk_heavy, backup_data, estimate_light_cleanup_size
from deepcat.settings_store import coerce_auto_backup_interval_minutes
from deepcat.ui.popup_behavior import POPUP_EXACT_WIDTH_PROPERTY
from deepcat.ui.post_capture_actions import ModernMouseClickOnlyComboBox
from deepcat.ui.timer_scope import single_shot_scoped

logger = logging.getLogger(__name__)

BACKUP_FREQUENCY_OPTIONS: tuple[tuple[str, int], ...] = (
    ("1分钟", 1),
    ("5分钟", 5),
    ("30分钟", 30),
    ("3小时", 180),
    ("18小时", 1080),
    ("72小时", 4320),
)
DEFAULT_BACKUP_FREQUENCY_LABEL = "18小时"
USER_DATA_OVERVIEW_TYPES = ("clipboard", "table_notes", "later_read", "ai_chat_history", "todo")
DATA_SIZE_LABELS = {
    "clipboard": "复制记录",
    "table_notes": "表格记事",
    "later_read": "稍后阅读",
    "ai_chat_history": "AI对话",
    "todo": "休息待办",
    "logs": "Log日志",
    "settings": "界面设置",
    "model_catalog": "模型配置",
    "light_cleanup": "轻量清理",
}


def _create_modern_combo(items: list[str] | tuple[str, ...], minimum_width: int = 84) -> ModernMouseClickOnlyComboBox:
    combo = ModernMouseClickOnlyComboBox()
    combo.addItems(list(items))
    combo.setMinimumWidth(minimum_width)
    combo.setProperty(POPUP_EXACT_WIDTH_PROPERTY, True)
    combo.setProperty("activeIndicator", "background")
    return combo


def _backup_frequency_label(interval_minutes: Any) -> str:
    minutes = coerce_auto_backup_interval_minutes(interval_minutes)
    for label, option_minutes in BACKUP_FREQUENCY_OPTIONS:
        if minutes == option_minutes:
            return label
    return min(BACKUP_FREQUENCY_OPTIONS, key=lambda item: abs(item[1] - minutes))[0]


def _backup_frequency_minutes(label: str) -> int:
    label = str(label or "").strip()
    for option_label, minutes in BACKUP_FREQUENCY_OPTIONS:
        if label == option_label:
            return minutes
    return dict(BACKUP_FREQUENCY_OPTIONS)[DEFAULT_BACKUP_FREQUENCY_LABEL]


def _legacy_backup_interval_hours(interval_minutes: int) -> int:
    return max(1, (int(interval_minutes) + 59) // 60)


def _format_size(total_bytes: int) -> str:
    if total_bytes <= 0:
        return "0 KB"
    if total_bytes < 1024:
        return f"{total_bytes} B"
    if total_bytes < 1024 * 1024:
        return f"{round(total_bytes / 1024, 1)} KB"
    return f"{round(total_bytes / (1024 * 1024), 2)} MB"


def _safe_zip_filename(filename: str) -> str:
    return PurePosixPath(str(filename or "").replace("\\", "/")).name


def _sqlite_user_text_size(
    db_path: Path,
    table_columns: dict[str, tuple[str, ...]],
    where_by_table: Optional[dict[str, str]] = None,
) -> int:
    """统计 SQLite 里的用户字段内容大小，不把空库页、索引、WAL/SHM 算进来。"""
    if not db_path.exists():
        return 0
    total = 0
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as conn:
            for table, columns in table_columns.items():
                table_row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()
                if table_row is None:
                    continue
                existing_columns = {
                    str(row[1])
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                usable_columns = [column for column in columns if column in existing_columns]
                if not usable_columns:
                    continue
                parts = [
                    f"LENGTH(CAST(COALESCE({column}, '') AS BLOB))"
                    for column in usable_columns
                ]
                where = ""
                if where_by_table and where_by_table.get(table):
                    where = f" WHERE {where_by_table[table]}"
                row = conn.execute(
                    f"SELECT COALESCE(SUM({' + '.join(parts)}), 0) FROM {table}{where}"
                ).fetchone()
                if row is not None:
                    total += int(row[0] or 0)
    except Exception as e:
        logger.warning("Failed to calculate sqlite user size for %s: %s", db_path, e)
    return total


def _calculate_data_size_bytes(data_type: str) -> int:
    """获取指定类型的可清理用户内容字节数。这个函数可能遍历目录，应放到后台线程调用。"""
    from deepcat.settings_store import get_settings_path, get_model_catalog_path

    app_dir = get_app_dir()
    total_bytes = 0

    try:
        if data_type == "clipboard":
            total_bytes += _sqlite_user_text_size(
                app_dir / "data" / "clipboard_history.db",
                {
                    "clipboard_records": (
                        "content",
                        "content_type",
                        "file_type",
                        "file_path",
                        "source_app",
                        "tags",
                        "error_info",
                        "metadata",
                    )
                },
            )
            img_dir = app_dir / "data" / "clipboard_images"
            if img_dir.exists():
                for f in img_dir.glob("**/*"):
                    if f.is_file():
                        total_bytes += f.stat().st_size
        elif data_type == "table_notes":
            total_bytes += _sqlite_user_text_size(
                app_dir / "data" / "table_notes.db",
                {
                    "table_tabs": ("name", "data", "group_name"),
                    "note_tabs": ("name", "html", "group_name"),
                },
            )
        elif data_type == "later_read":
            total_bytes += _sqlite_user_text_size(
                app_dir / "data" / "later_read.db",
                {"items": ("data",)},
            )
        elif data_type == "ai_chat_history":
            total_bytes += _sqlite_user_text_size(
                app_dir / "data" / "translation_history.db",
                {
                    "history_records": (
                        "source_text",
                        "result_text",
                        "prompt_text",
                        "title",
                        "model_name",
                        "source_lang",
                        "target_lang",
                    )
                },
            )
        elif data_type == "todo":
            total_bytes += _sqlite_user_text_size(
                app_dir / "data" / "todo.db",
                {"items": ("data",)},
            )
        elif data_type == "logs":
            log_dir = app_dir / "logs"
            if log_dir.exists():
                for f in log_dir.glob("**/*"):
                    if f.is_file():
                        total_bytes += f.stat().st_size
        elif data_type == "light_cleanup":
            total_bytes += estimate_light_cleanup_size(app_dir)
        elif data_type == "settings":
            p = get_settings_path()
            if p.exists():
                total_bytes += p.stat().st_size
        elif data_type == "model_catalog":
            p = get_model_catalog_path()
            if p.exists():
                total_bytes += p.stat().st_size
    except Exception as e:
        logger.warning(f"Failed to calculate size for {data_type}: {e}")
    return total_bytes


def _calculate_data_size_str(data_type: str) -> str:
    """获取指定类型的用户数据大小显示文本。"""
    return _format_size(_calculate_data_size_bytes(data_type))


class DataSizeWorker(QThread):
    sizes_ready = pyqtSignal(dict)

    def run(self) -> None:
        data_types = (
            "clipboard",
            "table_notes",
            "later_read",
            "ai_chat_history",
            "todo",
            "logs",
            "light_cleanup",
            "settings",
            "model_catalog",
        )
        raw_sizes = {
            data_type: _calculate_data_size_bytes(data_type)
            for data_type in data_types
        }
        sizes = {
            data_type: _format_size(size)
            for data_type, size in raw_sizes.items()
        }
        sizes["_raw_bytes"] = raw_sizes
        self.sizes_ready.emit(sizes)


class BackupTaskWorker(QThread):
    finished_signal = pyqtSignal(bool, str)
    progress_signal = pyqtSignal(str)
    progress_percent = pyqtSignal(int)
    list_loaded_signal = pyqtSignal(list)

    def __init__(self, action: str, params: dict[str, Any]):
        super().__init__()
        self.action = action
        self.params = params

    def run(self) -> None:
        try:
            self.progress_percent.emit(5)
            if self.action == "test_connect":
                self.progress_signal.emit("开始测试 WebDAV 连接...")
                self.progress_percent.emit(30)
                client = WebDAVClient(
                    self.params["server"],
                    self.params["user"],
                    self.params["password"]
                )
                self.progress_percent.emit(60)
                ok, msg = client.test_connection()
                self.progress_percent.emit(100)
                self.finished_signal.emit(ok, msg)

            elif self.action == "load_cloud_list":
                self.progress_signal.emit("正在从坚果云拉取备份列表...")
                self.progress_percent.emit(20)
                client = WebDAVClient(
                    self.params["server"],
                    self.params["user"],
                    self.params["password"],
                    self.params["backup_dir"]
                )
                self.progress_percent.emit(50)
                lst = client.list_files()
                self.progress_percent.emit(90)
                self.list_loaded_signal.emit(lst)
                self.progress_percent.emit(100)
                self.finished_signal.emit(True, "云端文件列表拉取完成")

            elif self.action == "delete_cloud_file":
                filename = _safe_zip_filename(self.params["filename"])
                if not filename.endswith(".zip"):
                    self.finished_signal.emit(False, "云端文件名非法")
                    return
                self.progress_signal.emit(f"正在删除云端备份文件: {filename} ...")
                self.progress_percent.emit(30)
                client = WebDAVClient(
                    self.params["server"],
                    self.params["user"],
                    self.params["password"],
                    self.params["backup_dir"]
                )
                self.progress_percent.emit(70)
                ok = client.delete_file(filename)
                self.progress_percent.emit(100)
                if ok:
                    self.finished_signal.emit(True, f"删除云端文件 {filename} 成功")
                else:
                    self.finished_signal.emit(False, f"删除云端文件 {filename} 失败")

            elif self.action == "manual_backup":
                local_path = Path(self.params["local_path"])
                options = self.params["options"]
                self.progress_signal.emit(f"本地备份文件准备打包: {local_path.name}")
                self.progress_percent.emit(10)

                # 1. 本地打包备份
                ok, msg = backup_data(local_path, options)
                if not ok:
                    self.progress_percent.emit(100)
                    self.finished_signal.emit(False, msg)
                    return
                self.progress_signal.emit("本地备份 ZIP 打包成功。")
                self.progress_percent.emit(50)

                # 2. 如果开启了云端同步，进行同步
                if self.params.get("webdav_enabled"):
                    self.progress_signal.emit("正在同步备份文件到坚果云云端...")
                    self.progress_percent.emit(60)
                    client = WebDAVClient(
                        self.params["server"],
                        self.params["user"],
                        self.params["password"],
                        self.params["backup_dir"]
                    )
                    up_ok = client.upload_file(local_path, local_path.name)
                    self.progress_percent.emit(85)
                    if up_ok:
                        self.progress_signal.emit("云端备份同步成功。")

                        # 清理多余的云端备份文件，实现轮转
                        try:
                            keep_count = self.params.get("webdav_keep_count", 5)
                            cloud_files = client.list_files()
                            if len(cloud_files) > keep_count:
                                self.progress_signal.emit(f"云端文件超出保留数量（{keep_count}份），开始清理旧备份...")
                                for extra_f in cloud_files[keep_count:]:
                                    client.delete_file(extra_f["name"])
                                    self.progress_signal.emit(f"已自动清理云端历史备份: {extra_f['name']}")
                        except Exception as e:
                            logger.error(f"Failed to rotate cloud backups: {e}")
                    else:
                        self.progress_signal.emit("同步到云端失败，但本地备份已成功保存。")
                        self.progress_percent.emit(100)
                        self.finished_signal.emit(False, f"本地备份已保存至: {local_path}，但同步到云端失败")
                        return
                self.progress_percent.emit(100)
                self.finished_signal.emit(True, f"备份成功保存至: {local_path}")

            elif self.action == "restore_cloud_file":
                filename = _safe_zip_filename(self.params["filename"])
                if not filename.endswith(".zip"):
                    self.finished_signal.emit(False, "云端文件名非法")
                    return
                temp_dir = Path(self.params["temp_dir"])
                self.progress_signal.emit(f"正在从云端下载备份文件: {filename} ...")
                self.progress_percent.emit(20)
                client = WebDAVClient(
                    self.params["server"],
                    self.params["user"],
                    self.params["password"],
                    self.params["backup_dir"]
                )
                self.progress_percent.emit(50)
                local_zip = temp_dir / filename
                ok = client.download_file(filename, local_zip)
                self.progress_percent.emit(90)
                if ok:
                    self.progress_signal.emit("云端备份文件下载成功")
                    self.progress_percent.emit(100)
                    self.finished_signal.emit(True, f"downloaded:{local_zip}")
                else:
                    self.progress_percent.emit(100)
                    self.finished_signal.emit(False, f"下载云端备份文件 {filename} 失败")

            elif self.action == "local_restore":
                filepath = Path(self.params["filepath"])
                self.progress_signal.emit(f"正在解压并覆盖还原数据...")
                self.progress_percent.emit(30)

                # 执行文件解压还原操作（已在主线程完成了数据库连接断开）
                ok, msg = restore_data(filepath)

                self.progress_percent.emit(80)
                # 清理临时文件
                if ok and self.params.get("delete_after_restore", False):
                    try:
                        if filepath.exists():
                            filepath.unlink()
                    except Exception:
                        pass

                self.progress_percent.emit(100)
                self.finished_signal.emit(ok, msg)

            elif self.action == "clear_data_files":
                # 纯磁盘重清理（大目录删除/遍历），不能在 GUI 线程执行
                from deepcat.core.data_manager import clear_data_disk_heavy

                self.progress_signal.emit("正在清理文件...")
                self.progress_percent.emit(40)
                ok, msg = clear_data_disk_heavy(
                    str(self.params.get("data_type", "")),
                    Path(str(self.params.get("app_dir", ""))) if self.params.get("app_dir") else None,
                )
                self.progress_percent.emit(100)
                self.finished_signal.emit(ok, msg)
        except Exception as e:
            logger.error(f"Worker execution failed: {e}")
            self.progress_percent.emit(100)
            self.finished_signal.emit(False, f"操作失败: {e}")


class DataManagementPage(QWidget):
    """数据管理配置与备份控制页面"""

    def __init__(self, main_window: Any) -> None:
        super().__init__()
        self.main_window = main_window
        self.worker: Optional[BackupTaskWorker] = None
        self.size_worker: Optional[DataSizeWorker] = None
        self.cloud_backups: list[dict[str, Any]] = []
        self._loading_config = False

        self._init_ui()
        self._load_config_to_ui()

    def _init_ui(self) -> None:
        # 使用 MainWindow 的原生 page 构造器创建一个整洁的框架
        # 我们把 main PageContent 塞进 ScrollArea 对应的 viewport 容器中
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # 1. 创建右上角“数据目录”动作按钮
        self.btn_data_dir = QPushButton("数据目录")
        self.btn_data_dir.setObjectName("BtnPrimary")
        self.btn_data_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_data_dir.setIcon(self.main_window._asset_icon("icon_folder_white.svg", self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)))
        self.btn_data_dir.setIconSize(QSize(22, 22))
        self.btn_data_dir.setMinimumSize(132, 42)
        self.btn_data_dir.setMaximumSize(132, 42)

        def _open_data_dir():
            import os
            import subprocess
            from deepcat.utils.paths import get_app_dir
            path = str(get_app_dir())
            try:
                if os.name == 'nt':
                    os.startfile(path)
                else:
                    subprocess.Popen(['xdg-open', path])
            except Exception as e:
                logger.error(f"Failed to open data dir: {e}")
                self._set_inline_status(f"打开数据目录失败：{e}", "error", auto_hide_ms=5200)

        self.btn_data_dir.clicked.connect(_open_data_dir)

        page = self.main_window._make_page("数据管理", action_widget=self.btn_data_dir)
        layout.addWidget(page)

        content_layout = page._content_layout
        content_layout.setSpacing(12)

        # -------------------------------------------------------------
        # 卡片一：手动导入与导出
        # -------------------------------------------------------------
        card1, card1_layout = self.main_window._card("")

        opts_widget = QWidget()
        opts_layout = QGridLayout(opts_widget)
        opts_layout.setContentsMargins(5, 5, 5, 5)
        opts_layout.setHorizontalSpacing(18)
        opts_layout.setVerticalSpacing(8)

        self.chk_clipboard = QCheckBox("复制记录")
        self.chk_table_notes = QCheckBox("表格记事")
        self.chk_later_read = QCheckBox("稍后阅读")
        self.chk_ai_chat_history = QCheckBox("AI对话记录")
        self.chk_todo = QCheckBox("休息待办")
        self.chk_settings = QCheckBox("设置数据")
        self.chk_model_catalog = QCheckBox("模型配置")

        # 默认全部勾选
        backup_checkboxes = (
            self.chk_clipboard,
            self.chk_table_notes,
            self.chk_later_read,
            self.chk_ai_chat_history,
            self.chk_todo,
            self.chk_settings,
            self.chk_model_catalog,
        )
        for index, chk in enumerate(backup_checkboxes):
            chk.setChecked(True)
            opts_layout.addWidget(chk, index // 4, index % 4)

        card1_layout.addWidget(opts_widget)

        btns_row = QWidget()
        btns_layout = QHBoxLayout(btns_row)
        btns_layout.setContentsMargins(5, 0, 5, 5)
        btns_layout.setSpacing(10)

        self.btn_export = QPushButton("立即备份到本地 (ZIP)")
        self.btn_export.setObjectName("BtnSmallPrimary")
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.clicked.connect(self._on_export_clicked)

        self.btn_import = QPushButton("导入本地备份并覆盖")
        self.btn_import.setObjectName("BtnSmallSecondary")
        self.btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_import.clicked.connect(self._on_import_clicked)

        btns_layout.addWidget(self.btn_export)
        btns_layout.addWidget(self.btn_import)
        btns_layout.addStretch(1)

        card1_layout.addWidget(btns_row)
        self._inline_status_label = QLabel("")
        self._inline_status_label.setObjectName("DataInlineStatus")
        self._inline_status_label.setVisible(False)
        self._inline_status_label.setWordWrap(False)
        self._inline_status_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._inline_status_label.setStyleSheet(
            "QLabel { color: #475569; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 6px 8px; font-size: 12px; }"
        )
        self._inline_status_token = 0
        card1_layout.addWidget(self._inline_status_label)
        content_layout.addWidget(card1)

        # -------------------------------------------------------------
        # 卡片二：定时自动备份
        # -------------------------------------------------------------
        card2, card2_layout = self.main_window._form_card("")

        self.chk_auto_backup = QCheckBox("启用定时自动备份")
        self.chk_auto_backup.clicked.connect(self._on_auto_backup_toggled)
        card2_layout.addRow("", self.chk_auto_backup)

        # 备份目录
        dir_row = QWidget()
        dir_layout = QHBoxLayout(dir_row)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.setSpacing(6)
        self.txt_backup_dir = QLineEdit()
        self.txt_backup_dir.setPlaceholderText("默认保存在软件目录 backups 文件夹中")
        self.txt_backup_dir.textChanged.connect(self._schedule_debounced_settings_save)
        self.txt_backup_dir.editingFinished.connect(self._save_config_from_ui)
        self.btn_browse_dir = QPushButton("浏览...")
        self.btn_browse_dir.setObjectName("BtnSmallSecondary")
        self.btn_browse_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse_dir.clicked.connect(self._on_browse_dir_clicked)
        dir_layout.addWidget(self.txt_backup_dir, 1)
        dir_layout.addWidget(self.btn_browse_dir)
        card2_layout.addRow("备份保存目录:", dir_row)

        # 备份频率与保留份数
        freq_row = QWidget()
        freq_layout = QHBoxLayout(freq_row)
        freq_layout.setContentsMargins(0, 0, 0, 0)
        freq_layout.setSpacing(10)

        self.cmb_freq = _create_modern_combo(tuple(label for label, _ in BACKUP_FREQUENCY_OPTIONS), 92)
        self.cmb_freq.setCurrentText(DEFAULT_BACKUP_FREQUENCY_LABEL)
        self.cmb_freq.currentIndexChanged.connect(self._save_config_from_ui)
        freq_layout.addWidget(self.cmb_freq)

        freq_layout.addWidget(QLabel("本地保留备份份数:"))
        self.cmb_keep_count = _create_modern_combo(("3", "5", "10", "20", "50"), 72)
        self.cmb_keep_count.setCurrentText("5")
        self.cmb_keep_count.currentIndexChanged.connect(self._save_config_from_ui)
        freq_layout.addWidget(self.cmb_keep_count)
        freq_layout.addStretch(1)

        card2_layout.addRow("频率与数量:", freq_row)
        content_layout.addWidget(card2)

        # -------------------------------------------------------------
        # 卡片三：坚果云 云端同步 (WebDAV)
        # -------------------------------------------------------------
        card3, card3_layout = self.main_window._form_card("")

        self.chk_webdav = QCheckBox("启用云端同步")
        self.chk_webdav.clicked.connect(self._on_webdav_toggled)
        card3_layout.addRow("", self.chk_webdav)

        self.txt_dav_server = QLineEdit()
        self.txt_dav_server.setPlaceholderText("https://dav.jianguoyun.com/dav/")
        self.txt_dav_server.textChanged.connect(self._schedule_debounced_settings_save)
        self.txt_dav_server.editingFinished.connect(self._save_config_from_ui)
        card3_layout.addRow("服务器地址:", self.txt_dav_server)

        self.txt_dav_user = QLineEdit()
        self.txt_dav_user.setPlaceholderText("坚果云账号 (通常为 Email)")
        self.txt_dav_user.textChanged.connect(self._schedule_debounced_settings_save)
        self.txt_dav_user.editingFinished.connect(self._save_config_from_ui)
        card3_layout.addRow("坚果云账号:", self.txt_dav_user)

        self.txt_dav_pass = QLineEdit()
        self.txt_dav_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_dav_pass.setPlaceholderText("坚果云应用密码 (非登录密码)")
        self.txt_dav_pass.textChanged.connect(self._schedule_debounced_settings_save)
        self.txt_dav_pass.editingFinished.connect(self._save_config_from_ui)
        card3_layout.addRow("应用密码:", self.txt_dav_pass)

        # 云备份目录与云保留数量
        cloud_settings_row = QWidget()
        cloud_settings_layout = QHBoxLayout(cloud_settings_row)
        cloud_settings_layout.setContentsMargins(0, 0, 0, 0)
        cloud_settings_layout.setSpacing(10)
        self.txt_dav_dir = QLineEdit()
        self.txt_dav_dir.setPlaceholderText("DeepCatBackup")
        self.txt_dav_dir.textChanged.connect(self._schedule_debounced_settings_save)
        self.txt_dav_dir.editingFinished.connect(self._save_config_from_ui)
        cloud_settings_layout.addWidget(self.txt_dav_dir, 1)

        cloud_settings_layout.addWidget(QLabel("云端保留份数:"))
        self.cmb_cloud_keep_count = _create_modern_combo(("3", "5", "10", "20"), 72)
        self.cmb_cloud_keep_count.setCurrentText("5")
        self.cmb_cloud_keep_count.currentIndexChanged.connect(self._save_config_from_ui)
        cloud_settings_layout.addWidget(self.cmb_cloud_keep_count)
        card3_layout.addRow("云端目录/数量:", cloud_settings_row)

        # WebDAV 操作按钮
        dav_btns = QWidget()
        dav_btns_layout = QHBoxLayout(dav_btns)
        dav_btns_layout.setContentsMargins(0, 0, 0, 0)
        dav_btns_layout.setSpacing(10)

        self.btn_dav_test = QPushButton("测试云连接")
        self.btn_dav_test.setObjectName("BtnSmallSecondary")
        self.btn_dav_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_dav_test.clicked.connect(self._on_dav_test_clicked)

        self.btn_dav_sync = QPushButton("立即同步当前到云端")
        self.btn_dav_sync.setObjectName("BtnSmallPrimary")
        self.btn_dav_sync.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_dav_sync.clicked.connect(self._on_dav_sync_clicked)

        self.btn_dav_refresh = QPushButton("刷新云备份列表")
        self.btn_dav_refresh.setObjectName("BtnSmallSecondary")
        self.btn_dav_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_dav_refresh.clicked.connect(self._on_dav_refresh_clicked)

        dav_btns_layout.addWidget(self.btn_dav_test)
        dav_btns_layout.addWidget(self.btn_dav_sync)
        dav_btns_layout.addWidget(self.btn_dav_refresh)
        dav_btns_layout.addStretch(1)
        card3_layout.addRow("", dav_btns)

        # 云备份列表表格
        self.tbl_cloud_files = QTableWidget()
        self.tbl_cloud_files.setColumnCount(4)
        self.tbl_cloud_files.setHorizontalHeaderLabels(["备份文件", "大小", "上传时间", "操作"])
        self.tbl_cloud_files.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl_cloud_files.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_cloud_files.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_cloud_files.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_cloud_files.setMinimumHeight(150)
        self.tbl_cloud_files.setMaximumHeight(200)
        self.tbl_cloud_files.setAlternatingRowColors(True)
        self.tbl_cloud_files.setStyleSheet("QTableWidget { font-size: 11px; }")
        card3_layout.addRow("云端历史备份:", self.tbl_cloud_files)
        content_layout.addWidget(card3)

        # -------------------------------------------------------------
        # 卡片四：数据清除（危险区）
        # -------------------------------------------------------------
        card4, card4_layout = self.main_window._card("")

        clear_row = QWidget()
        clear_grid = QFormLayout(clear_row)
        clear_grid.setContentsMargins(2, 5, 0, 5)
        clear_grid.setHorizontalSpacing(8)
        clear_grid.setVerticalSpacing(8)

        # 创建可动态测量并刷新大小的 QLabel 控件
        self.lbl_clear_clipboard = QLabel("清除复制记录:")
        self.lbl_clear_table_notes = QLabel("清除表格记事内容:")
        self.lbl_clear_later_read = QLabel("清除稍后阅读记录:")
        self.lbl_clear_ai_chat_history = QLabel("清除AI对话记录:")
        self.lbl_clear_todo = QLabel("清除休息待办记录:")
        self.lbl_clear_logs = QLabel("清除Log日志文件:")
        self.lbl_clear_settings = QLabel("恢复默认界面设置:")
        self.lbl_clear_model_catalog = QLabel("清除模型列表:")

        def make_danger_btn(text: str, data_type: str) -> QPushButton:
            btn = QPushButton(text)
            btn.setFixedWidth(110)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #f87171;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 5px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #ef4444;
                }
                QPushButton:pressed {
                    background-color: #dc2626;
                }
            """)
            btn.clicked.connect(lambda *_: self._on_clear_clicked(data_type))
            return btn

        clear_grid.addRow(self.lbl_clear_clipboard, make_danger_btn("清空复制记录", "clipboard"))
        clear_grid.addRow(self.lbl_clear_table_notes, make_danger_btn("清空表格记事", "table_notes"))
        clear_grid.addRow(self.lbl_clear_later_read, make_danger_btn("清空稍后阅读", "later_read"))
        clear_grid.addRow(self.lbl_clear_ai_chat_history, make_danger_btn("清空AI对话", "ai_chat_history"))
        clear_grid.addRow(self.lbl_clear_todo, make_danger_btn("清空休息待办", "todo"))
        clear_grid.addRow(self.lbl_clear_logs, make_danger_btn("清空日志", "logs"))
        clear_grid.addRow(self.lbl_clear_settings, make_danger_btn("重置界面设置", "settings"))
        clear_grid.addRow(self.lbl_clear_model_catalog, make_danger_btn("重置模型列表", "model_catalog"))

        overview = QWidget()
        overview.setMinimumWidth(210)
        overview.setMaximumWidth(230)
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(10, 5, 5, 5)
        overview_layout.setSpacing(7)

        overview_title = QLabel("数据概览")
        overview_title.setStyleSheet("font-weight:bold; color:#0f172a;")
        self.lbl_overview_total = QLabel("可清理内容总计: 计算中")
        self.lbl_overview_largest = QLabel("最大可清理项: 计算中")
        self.lbl_overview_light = QLabel("可轻量清理: 计算中")
        self.lbl_overview_backup = QLabel("上次备份: 读取中")
        recent_title = QLabel("最近清理记录")
        recent_title.setStyleSheet("font-weight:bold; color:#0f172a; margin-top:4px;")
        self.lbl_cleanup_record_1 = QLabel("暂无记录")
        self.lbl_cleanup_record_2 = QLabel("")
        self.lbl_cleanup_record_3 = QLabel("")
        for label in (
            self.lbl_overview_total,
            self.lbl_overview_largest,
            self.lbl_overview_light,
            self.lbl_overview_backup,
            self.lbl_cleanup_record_1,
            self.lbl_cleanup_record_2,
            self.lbl_cleanup_record_3,
        ):
            label.setStyleSheet("color:#475569; font-size:12px;")
            label.setWordWrap(False)

        self.btn_light_cleanup = QPushButton("一键轻量清理")
        self.btn_light_cleanup.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_light_cleanup.setFixedWidth(112)
        self.btn_light_cleanup.setStyleSheet("""
            QPushButton {
                background-color: #64748b;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #475569;
            }
            QPushButton:pressed {
                background-color: #334155;
            }
        """)
        self.btn_light_cleanup.clicked.connect(self._on_light_cleanup_clicked)

        self.btn_refresh_data_sizes = QPushButton("刷新")
        self.btn_refresh_data_sizes.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh_data_sizes.setFixedWidth(58)
        self.btn_refresh_data_sizes.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #334155;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #f8fafc;
                border-color: #94a3b8;
            }
            QPushButton:pressed {
                background-color: #e2e8f0;
            }
        """)
        self.btn_refresh_data_sizes.clicked.connect(self._on_refresh_all_data_clicked)

        overview_layout.addWidget(overview_title)
        overview_layout.addWidget(self.lbl_overview_total)
        overview_layout.addWidget(self.lbl_overview_largest)
        overview_layout.addWidget(self.lbl_overview_light)
        overview_layout.addWidget(self.lbl_overview_backup)
        overview_layout.addSpacing(4)
        cleanup_actions = QWidget()
        cleanup_actions_layout = QHBoxLayout(cleanup_actions)
        cleanup_actions_layout.setContentsMargins(0, 0, 0, 0)
        cleanup_actions_layout.setSpacing(6)
        cleanup_actions_layout.addWidget(self.btn_light_cleanup)
        cleanup_actions_layout.addWidget(self.btn_refresh_data_sizes)
        cleanup_actions_layout.addStretch(1)
        overview_layout.addWidget(cleanup_actions)
        overview_layout.addWidget(recent_title)
        overview_layout.addWidget(self.lbl_cleanup_record_1)
        overview_layout.addWidget(self.lbl_cleanup_record_2)
        overview_layout.addWidget(self.lbl_cleanup_record_3)
        overview_layout.addStretch(1)

        clear_host = QWidget()
        clear_host_layout = QHBoxLayout(clear_host)
        clear_host_layout.setContentsMargins(0, 0, 0, 0)
        clear_host_layout.setSpacing(10)
        clear_host_layout.addWidget(overview, 0, Qt.AlignmentFlag.AlignTop)
        clear_host_layout.addStretch(1)
        clear_host_layout.addWidget(clear_row, 0, Qt.AlignmentFlag.AlignTop)

        card4_layout.addWidget(clear_host)
        content_layout.addWidget(card4)

        # 调整所有圆角子布局，只对 self 操作以保留 AppPage 外层的 14, 12 margins
        self.main_window._compact_child_layouts(self)

    def log(self, msg: str) -> None:
        """向控制台日志追加消息"""
        logger.info(msg)

    def _prepare_floating_status_label(self, label: QLabel) -> Optional[QWidget]:
        parent = label.parentWidget()
        if parent is None:
            return None
        if bool(label.property("floatingStatusPrepared")):
            return parent
        y = 10
        layout = parent.layout()
        if layout is not None:
            index = layout.indexOf(label)
            spacing = max(4, int(layout.spacing() if layout.spacing() >= 0 else 6))
            if index > 0:
                for i in range(index - 1, -1, -1):
                    item = layout.itemAt(i)
                    widget = item.widget() if item is not None else None
                    if widget is not None and widget.isVisible():
                        y = int(widget.geometry().bottom()) + 1 + spacing
                        break
            layout.removeWidget(label)
        label.setParent(parent)
        label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        label.setProperty("floatingStatusPrepared", True)
        label.setProperty("floatingStatusY", max(8, y))
        return parent

    def _set_inline_status(self, text: str, tone: str = "info", *, auto_hide_ms: int = 0) -> None:
        label = getattr(self, "_inline_status_label", None)
        if not isinstance(label, QLabel):
            return
        parent = self._prepare_floating_status_label(label)
        if parent is None:
            return
        palette = {
            "info": ("#475569", "#f8fafc", "#e2e8f0"),
            "success": ("#166534", "#f0fdf4", "#bbf7d0"),
            "warning": ("#92400e", "#fffbeb", "#fde68a"),
            "error": ("#991b1b", "#fef2f2", "#fecaca"),
        }
        color, background, border = palette.get(str(tone or "info"), palette["info"])
        status_text = str(text or "").strip()
        if not status_text:
            label.hide()
            return
        self._inline_status_token = int(getattr(self, "_inline_status_token", 0)) + 1
        token = int(getattr(self, "_inline_status_token", 0))
        label.setText(status_text)
        label.setStyleSheet(
            "QLabel#DataInlineStatus {"
            f" color: {color}; background: {background}; border: 1px solid {border};"
            " border-radius: 8px; padding: 7px 10px; font-size: 12px;"
            "}"
        )
        max_width = max(120, int(parent.width()) - 24)
        label.setMinimumSize(0, 0)
        label.setMaximumSize(16777215, 16777215)
        label.setMaximumWidth(max_width)
        label.setWordWrap(False)
        label.adjustSize()
        hint = label.sizeHint()
        if int(hint.width()) > max_width:
            label.setWordWrap(True)
            label.setFixedWidth(max_width)
            label.adjustSize()
            width = max_width
            height = max(28, int(label.sizeHint().height()))
        else:
            width = max(80, int(hint.width()))
            height = max(28, int(hint.height()))
            label.setFixedSize(width, height)
        x = max(8, int((parent.width() - width) / 2))
        y = int(label.property("floatingStatusY") or 10)
        if y + height > int(parent.height()) - 8:
            y = 10
        label.setGeometry(x, y, width, height)
        label.raise_()
        label.show()
        if auto_hide_ms > 0:
            def hide() -> None:
                if int(getattr(self, "_inline_status_token", 0)) == token:
                    label.setText("")
                    label.setVisible(False)

            QTimer.singleShot(int(auto_hide_ms), hide)


    def _get_data_size_str(self, data_type: str) -> str:
        return _calculate_data_size_str(data_type)

    def _refresh_data_sizes(self) -> None:
        """后台测量各项数据容量，避免复制图片多时卡住设置页。"""
        try:
            self._apply_data_size_labels({})
            if self.size_worker is not None and self.size_worker.isRunning():
                return
            worker = DataSizeWorker()
            self.size_worker = worker
            worker.sizes_ready.connect(self._apply_data_size_labels)
            worker.finished.connect(lambda w=worker: self._on_size_worker_finished(w))
            worker.start()
        except Exception as e:
            logger.error(f"Failed to refresh data sizes: {e}")

    def _on_refresh_all_data_clicked(self) -> None:
        self._refresh_data_sizes()
        if not self.chk_webdav.isChecked():
            return
        if not (
            self.txt_dav_server.text().strip()
            and self.txt_dav_user.text().strip()
            and self.txt_dav_pass.text().strip()
        ):
            return
        self._on_dav_refresh_clicked()

    def _apply_data_size_labels(self, sizes: dict[str, Any]) -> None:
        fallback = "计算中"
        self.lbl_clear_clipboard.setText(f"清除复制记录（{sizes.get('clipboard', fallback)}）:")
        self.lbl_clear_table_notes.setText(f"清除表格记事内容（{sizes.get('table_notes', fallback)}）:")
        self.lbl_clear_later_read.setText(f"清除稍后阅读记录（{sizes.get('later_read', fallback)}）:")
        self.lbl_clear_ai_chat_history.setText(f"清除AI对话记录（{sizes.get('ai_chat_history', fallback)}）:")
        self.lbl_clear_todo.setText(f"清除休息待办记录（{sizes.get('todo', fallback)}）:")
        self.lbl_clear_logs.setText(f"清除Log日志文件（{sizes.get('logs', fallback)}）:")
        self.lbl_clear_settings.setText(f"恢复默认界面设置（{sizes.get('settings', fallback)}）:")
        self.lbl_clear_model_catalog.setText(f"清除模型列表（{sizes.get('model_catalog', fallback)}）:")
        self._apply_data_overview(sizes)

    def _last_backup_display_text(self) -> str:
        try:
            ui = dict(getattr(self.main_window._current, "ui", {}) or {})
            dm = dict(ui.get("data_management", {}) or {})
            last_backup_time = str(dm.get("last_backup_time", "") or "").strip()
            return last_backup_time or "暂无"
        except Exception:
            return "暂无"

    def _apply_data_overview(self, sizes: dict[str, Any]) -> None:
        raw_sizes = sizes.get("_raw_bytes")
        if not isinstance(raw_sizes, dict):
            self.lbl_overview_total.setText("可清理内容总计: 计算中")
            self.lbl_overview_largest.setText("最大可清理项: 计算中")
            self.lbl_overview_light.setText("可轻量清理: 计算中")
            self.lbl_overview_backup.setText(f"上次备份: {self._last_backup_display_text()}")
            self._refresh_cleanup_records()
            self.btn_light_cleanup.setEnabled(False)
            return

        total_bytes = sum(int(raw_sizes.get(data_type, 0) or 0) for data_type in USER_DATA_OVERVIEW_TYPES)
        self.lbl_overview_total.setText(f"可清理内容总计: {_format_size(total_bytes)}")

        largest_type = ""
        largest_bytes = 0
        for data_type in USER_DATA_OVERVIEW_TYPES:
            size = int(raw_sizes.get(data_type, 0) or 0)
            if size > largest_bytes:
                largest_type = data_type
                largest_bytes = size
        if largest_type:
            name = DATA_SIZE_LABELS.get(largest_type, largest_type)
            self.lbl_overview_largest.setText(f"最大可清理项: {name} {_format_size(largest_bytes)}")
        else:
            self.lbl_overview_largest.setText("最大可清理项: 暂无")

        light_bytes = int(raw_sizes.get("light_cleanup", 0) or 0)
        self.lbl_overview_light.setText(f"可轻量清理: {_format_size(light_bytes)}")
        self.lbl_overview_backup.setText(f"上次备份: {self._last_backup_display_text()}")
        self._refresh_cleanup_records()
        self.btn_light_cleanup.setEnabled(light_bytes > 0)

    def _cleanup_records(self) -> list[dict[str, str]]:
        try:
            ui = dict(getattr(self.main_window._current, "ui", {}) or {})
            dm = dict(ui.get("data_management", {}) or {})
            records = dm.get("cleanup_records", [])
            return [dict(record) for record in records if isinstance(record, dict)][:3]
        except Exception:
            return []

    def _refresh_cleanup_records(self) -> None:
        labels = (self.lbl_cleanup_record_1, self.lbl_cleanup_record_2, self.lbl_cleanup_record_3)
        records = self._cleanup_records()
        if not records:
            labels[0].setText("暂无记录")
            labels[1].setText("")
            labels[2].setText("")
            return
        for index, label in enumerate(labels):
            if index >= len(records):
                label.setText("")
                continue
            record = records[index]
            title = str(record.get("title", "") or "").strip()
            detail = str(record.get("detail", "") or "").strip()
            if title == "一键轻量清理" or detail.startswith("轻量清理已完成"):
                detail = "轻量清理已完成"
            created_at = str(record.get("created_at", "") or "").strip()
            prefix = f"{created_at[5:16]} " if len(created_at) >= 16 else ""
            label.setText(f"{prefix}{detail or '已完成'}")

    def _mark_backup_completed(self) -> None:
        backup_time = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            from deepcat.settings_store import update_settings

            def _mut(s: Any) -> Any:
                ui = dict(s.ui) if isinstance(s.ui, dict) else {}
                dm = dict(ui.get("data_management", {}) or {})
                dm["last_backup_time"] = backup_time
                ui["data_management"] = dm
                return dataclass_replace(s, ui=ui)

            self.main_window._current = update_settings(_mut)
            self.lbl_overview_backup.setText(f"上次备份: {backup_time}")
        except Exception as e:
            logger.warning("Failed to mark backup completed: %s", e)

    def _append_cleanup_record(self, title: str, detail: str = "") -> None:
        try:
            from deepcat.settings_store import update_settings

            created_at = time.strftime("%Y-%m-%d %H:%M:%S")

            def _mut(s: Any) -> Any:
                ui = dict(s.ui) if isinstance(s.ui, dict) else {}
                dm = dict(ui.get("data_management", {}) or {})
                records = [dict(record) for record in dm.get("cleanup_records", []) if isinstance(record, dict)]
                records.insert(
                    0,
                    {
                        "title": str(title or "清理记录")[:40],
                        "detail": str(detail or "")[:80],
                        "created_at": created_at,
                    },
                )
                dm["cleanup_records"] = records[:3]
                ui["data_management"] = dm
                return dataclass_replace(s, ui=ui)

            self.main_window._current = update_settings(_mut)
            self._refresh_cleanup_records()
        except Exception as e:
            logger.warning("Failed to append cleanup record: %s", e)

    def _on_size_worker_finished(self, worker: DataSizeWorker) -> None:
        if self.size_worker is worker:
            self.size_worker = None
        worker.deleteLater()

    def _load_config_to_ui(self) -> None:
        """从内存 settings.json 加载参数到 UI"""
        self._loading_config = True
        try:
            ui = dict(getattr(self.main_window._current, "ui", {}) or {})
            dm = dict(ui.get("data_management", {}) or {})

            # 卡片一选项（读取之前存储的备份选项）
            opts = dict(dm.get("backup_options", {}) or {})
            self.chk_clipboard.setChecked(opts.get("clipboard", True))
            self.chk_table_notes.setChecked(opts.get("table_notes", True))
            self.chk_later_read.setChecked(opts.get("later_read", True))
            self.chk_ai_chat_history.setChecked(opts.get("ai_chat_history", True))
            self.chk_todo.setChecked(opts.get("todo", True))
            self.chk_settings.setChecked(opts.get("settings", True))
            self.chk_model_catalog.setChecked(opts.get("model_catalog", True))

            # 卡片二自动备份
            self.chk_auto_backup.setChecked(dm.get("auto_backup_enabled", False))
            self.txt_backup_dir.setText(dm.get("auto_backup_dir", ""))

            interval_minutes = coerce_auto_backup_interval_minutes(
                dm.get("auto_backup_interval_minutes"),
                dm.get("auto_backup_interval_hours"),
            )
            freq_str = _backup_frequency_label(interval_minutes)
            idx = self.cmb_freq.findText(freq_str)
            if idx >= 0:
                self.cmb_freq.setCurrentIndex(idx)

            keep = dm.get("auto_backup_keep_count", 5)
            idx2 = self.cmb_keep_count.findText(str(keep))
            if idx2 >= 0:
                self.cmb_keep_count.setCurrentIndex(idx2)

            # 开启状态级联更新控件使能
            self._on_auto_backup_toggled(self.chk_auto_backup.isChecked())

            # 卡片三 WebDAV
            self.chk_webdav.setChecked(dm.get("webdav_enabled", False))
            self.txt_dav_server.setText(dm.get("webdav_server", "https://dav.jianguoyun.com/dav/"))
            self.txt_dav_user.setText(dm.get("webdav_username", ""))
            self.txt_dav_pass.setText(dm.get("webdav_password", ""))
            self.txt_dav_dir.setText(dm.get("webdav_backup_dir", "DeepCatBackup"))

            cloud_keep = dm.get("webdav_keep_count", 5)
            idx3 = self.cmb_cloud_keep_count.findText(str(cloud_keep))
            if idx3 >= 0:
                self.cmb_cloud_keep_count.setCurrentIndex(idx3)

            webdav_enabled = bool(dm.get("webdav_enabled", False))
            self.chk_webdav.setChecked(webdav_enabled)
            self._on_webdav_toggled(webdav_enabled)

            # 动态测量并刷新各项敏感数据在磁盘上的大小
            self._refresh_data_sizes()

            # 成功载入配置提示
            self.log("配置参数加载完成。")
            if dm.get("last_backup_time"):
                self.log(f"上一次备份时间: {dm.get('last_backup_time')}")

        except Exception as e:
            logger.error(f"Load config to UI failed: {e}")
            self.log(f"加载配置失败: {e}")
        finally:
            self._loading_config = False

    _SETTINGS_SAVE_DEBOUNCE_MS = 400

    def _schedule_debounced_settings_save(self) -> None:
        """输入类字段防抖保存：每个按键全量读写 settings.json 的开销太高。"""
        if bool(getattr(self, "_loading_config", False)):
            return
        timer = getattr(self, "_settings_save_debounce_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(self._SETTINGS_SAVE_DEBOUNCE_MS)
            timer.timeout.connect(self._save_config_from_ui)
            self._settings_save_debounce_timer = timer
        timer.start()

    def _save_config_from_ui(self) -> None:
        """将 UI 数据收集并写入 settings.json"""
        if bool(getattr(self, "_loading_config", False)):
            return
        if not hasattr(self.main_window, "_current"):
            return

        try:
            # 收集参数
            opts = {
                "clipboard": self.chk_clipboard.isChecked(),
                "table_notes": self.chk_table_notes.isChecked(),
                "later_read": self.chk_later_read.isChecked(),
                "ai_chat_history": self.chk_ai_chat_history.isChecked(),
                "todo": self.chk_todo.isChecked(),
                "settings": self.chk_settings.isChecked(),
                "model_catalog": self.chk_model_catalog.isChecked(),
            }

            interval_minutes = _backup_frequency_minutes(self.cmb_freq.currentText())

            dm_cfg = {
                "auto_backup_enabled": self.chk_auto_backup.isChecked(),
                "auto_backup_dir": self.txt_backup_dir.text().strip(),
                "auto_backup_interval_minutes": interval_minutes,
                "auto_backup_interval_hours": _legacy_backup_interval_hours(interval_minutes),
                "auto_backup_keep_count": int(self.cmb_keep_count.currentText()),

                "webdav_enabled": self.chk_webdav.isChecked(),
                "webdav_server": self.txt_dav_server.text().strip(),
                "webdav_username": self.txt_dav_user.text().strip(),
                "webdav_password": self.txt_dav_pass.text().strip(),
                "webdav_backup_dir": self.txt_dav_dir.text().strip(),
                "webdav_keep_count": int(self.cmb_cloud_keep_count.currentText()),

                "backup_options": opts,
            }

            # 使用 settings_store 的 update_settings 原子更新
            def _mut(s: Any) -> Any:
                ui = dict(s.ui) if isinstance(s.ui, dict) else {}
                current_dm = dict(ui.get("data_management", {}) or {})
                next_dm = dict(dm_cfg)
                next_dm["last_backup_time"] = str(current_dm.get("last_backup_time", "") or "")
                records = current_dm.get("cleanup_records", [])
                next_dm["cleanup_records"] = list(records)[:3] if isinstance(records, list) else []
                ui["data_management"] = next_dm
                return dataclass_replace(s, ui=ui)

            from deepcat.settings_store import update_settings
            self.main_window._current = update_settings(_mut)

        except Exception as e:
            logger.error(f"Save config failed: {e}")

    def _on_auto_backup_toggled(self, checked: bool) -> None:
        self.txt_backup_dir.setEnabled(checked)
        self.btn_browse_dir.setEnabled(checked)
        self.cmb_freq.setEnabled(checked)
        self.cmb_keep_count.setEnabled(checked)
        self._save_config_from_ui()

    def _on_webdav_toggled(self, checked: bool) -> None:
        self.txt_dav_server.setEnabled(checked)
        self.txt_dav_user.setEnabled(checked)
        self.txt_dav_pass.setEnabled(checked)
        self.txt_dav_dir.setEnabled(checked)
        self.cmb_cloud_keep_count.setEnabled(checked)
        self.btn_dav_test.setEnabled(checked)
        self.btn_dav_sync.setEnabled(checked)
        self.btn_dav_refresh.setEnabled(checked)
        self.tbl_cloud_files.setEnabled(checked)
        self._save_config_from_ui()

    def _on_browse_dir_clicked(self) -> None:
        current_dir = self.txt_backup_dir.text().strip() or str(get_app_dir())
        selected = QFileDialog.getExistingDirectory(self, "选择备份保存文件夹", current_dir)
        if selected:
            self.txt_backup_dir.setText(selected)
            self._save_config_from_ui()

    # -------------------------------------------------------------
    # 异步 QThread 通信
    # -------------------------------------------------------------
    def _restart_software(self) -> None:
        """安全地拉起软件的新实例，并强制退出当前实例，实现自动重启"""
        import sys
        import subprocess
        import os
        try:
            self.log("正在唤醒软件新实例以进行自动重启...")

            # 获取项目根目录
            root_dir = str(Path(__file__).resolve().parents[2])

            # 复制并配置环境变量，确保终端下 PYTHONPATH 包含项目根目录，从而解决找不到包的问题
            env = os.environ.copy()
            if "PYTHONPATH" in env:
                env["PYTHONPATH"] = root_dir + os.pathsep + env["PYTHONPATH"]
            else:
                env["PYTHONPATH"] = root_dir

            if getattr(sys, 'frozen', False):
                # 打包后的可执行 EXE 运行环境
                args = [sys.executable] + sys.argv[1:]
            else:
                # 源码 python 脚本运行环境
                args = [sys.executable] + sys.argv

            # 使用 Popen 静默拉起子进程，指定当前工作目录与配置好的环境变量
            subprocess.Popen(args, cwd=root_dir, env=env)
            self.log("新实例已成功拉起。")
        except Exception as e:
            logger.error(f"Auto-restart failed: {e}")
            self.log(f"自动重启失败: {e}，请手动重新运行程序。")
        finally:
            # 无论拉起成功与否，均需立即强制隔离退出当前进程，确保干净重载且无冲突
            os._exit(0)

    def _iter_ai_history_panels(self) -> list[Any]:
        panels: list[Any] = []
        seen: set[int] = set()
        main_window = self.main_window

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

    def _prepare_for_restore(self) -> bool:
        """在 Qt 线程停用数据写入，查询未结束时中止恢复。"""
        from deepcat.core.data_manager import _close_restore_sources, _SQLITE_BACKUP_TARGETS
        page = getattr(self.main_window, "_clipboard_history_page", None)
        monitor = getattr(page, "_monitor", None)
        timer = getattr(self.main_window, "_todo_timer", None)
        self._restore_resume_state = (bool(getattr(monitor, "running", False)), bool(timer and timer.isActive()))
        try:
            compact = getattr(self.main_window, "_compact_window_clipboard", None)
            controller = getattr(compact, "_query_controller", None)
            if controller is not None and not controller.cancel(wait_ms=2000):
                raise RuntimeError("紧凑复制记录查询尚未结束，请稍后重试")
            _close_restore_sources(self.main_window, {name for _, name in _SQLITE_BACKUP_TARGETS.values()})
            if timer is not None:
                timer.stop()
            popup = getattr(self.main_window, "_todo_popup", None)
            if popup is not None:
                popup.close()
                self.main_window._todo_popup = None
            self.log("数据库连接与监听器已暂停。")
            return True
        except Exception as error:
            self._resume_after_failed_restore()
            self._set_inline_status(f"暂时无法恢复：{error}", "error")
            return False

    def _resume_after_failed_restore(self) -> None:
        monitor_running, timer_running = getattr(self, "_restore_resume_state", (False, False))
        page = getattr(self.main_window, "_clipboard_history_page", None)
        if page is not None:
            page.resume_queries()
            monitor = getattr(page, "_monitor", None)
            if monitor_running and monitor is not None:
                monitor.start()
        timer = getattr(self.main_window, "_todo_timer", None)
        if timer_running and timer is not None:
            timer.start()

    def _start_worker(self, action: str, params: dict[str, Any], title: str = "数据管理") -> bool:
        if self.worker is not None and self.worker.isRunning():
            self._set_inline_status("当前有备份或网络任务正在运行，请等待其结束后再试。", "warning", auto_hide_ms=3000)
            return False

        self.log(f"发起任务: {action}")
        self._set_inline_status(f"正在进行：{title}...", "info")

        # 创建一个好看且实用的模态进度条对话框，防止操作冲突
        from PyQt6.QtWidgets import QProgressDialog
        self.progress_dialog = QProgressDialog(f"正在进行【{title}】操作，请稍候...", None, 0, 100, self)
        self.progress_dialog.setWindowTitle(title)
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setCancelButton(None)
        # 去除关闭按钮，确保只能等待完成
        self.progress_dialog.setWindowFlags(self.progress_dialog.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)
        self.progress_dialog.setValue(0)
        self.progress_dialog.show()

        self.worker = BackupTaskWorker(action, params)
        self.worker.progress_signal.connect(self.log)
        self.worker.progress_signal.connect(lambda msg: self._set_inline_status(str(msg), "info"))
        # 绑定进度条展示
        self.worker.progress_percent.connect(self.progress_dialog.setValue)
        # 同时也把当前详细日志作为进度条的 label 展示，方便用户感知
        self.worker.progress_signal.connect(self.progress_dialog.setLabelText)

        def on_finished(success: bool, msg: str):
            if hasattr(self, "progress_dialog") and self.progress_dialog is not None:
                self.progress_dialog.close()
                self.progress_dialog = None
            self._on_worker_finished(success, msg)

        self.worker.finished_signal.connect(on_finished)
        self.worker.list_loaded_signal.connect(self._on_cloud_list_loaded)
        self.worker.start()
        return True

    def _on_worker_finished(self, success: bool, msg: str) -> None:
        action = getattr(self.worker, "action", "")
        self.log(f"任务结束: {'成功' if success else '失败'} - {msg}")

        # 1. 成功下载云端备份包，交回主 GUI 线程继续触发“本地还原”工作线程以解压
        if success and msg.startswith("downloaded:"):
            filepath = Path(msg.replace("downloaded:", ""))
            self.log("云端文件下载完毕，正在主线程断开连接准备覆盖还原...")

            # 主线程断开占用
            if not self._prepare_for_restore():
                return

            # 启动工作线程执行 local_restore 动作
            self.log("启动异步本地覆盖还原任务...")
            params = {
                "filepath": str(filepath),
                "delete_after_restore": True # 解压完删掉临时 zip
            }
            if not self._start_worker("local_restore", params, title="还原云备份"):
                self._resume_after_failed_restore()

        # 2. 本地解包还原动作结束
        elif success and action == "local_restore":
            self.log("备份数据还原解压覆盖成功。")
            self._set_inline_status("备份数据已成功导入并覆盖，软件即将自动重启以应用更改。", "success")
            single_shot_scoped(1200, self, self._restart_software)

        # 2.5 后台磁盘清理（剪贴板图片目录 / 日志 / 轻量清理）
        elif success and action == "clear_data_files":
            params = getattr(self.worker, "params", {}) or {}
            self._append_cleanup_record(str(params.get("record_name") or "数据清理"), msg)
            self._set_inline_status(msg, "success", auto_hide_ms=3500)

        # 3. 其他常规反馈
        elif not success:
            if action == "local_restore":
                self._resume_after_failed_restore()
            self._set_inline_status(f"操作失败：{msg}", "error")
        else:
            if "测试成功" in msg or "连接成功" in msg:
                self._set_inline_status("WebDAV 连接测试通过，账号密码验证正确。", "success", auto_hide_ms=3500)
            elif "备份成功" in msg:
                if action == "manual_backup":
                    self._mark_backup_completed()
                self._set_inline_status("数据已成功备份并导出打包。", "success", auto_hide_ms=3500)
            else:
                self._set_inline_status(str(msg or "操作已完成"), "success", auto_hide_ms=3500)

        # 刷新 UI 状态
        self._load_config_to_ui()

    def _on_cloud_list_loaded(self, lst: list[dict[str, Any]]) -> None:
        self.cloud_backups = lst
        self.tbl_cloud_files.setRowCount(0)

        for idx, item in enumerate(lst):
            self.tbl_cloud_files.insertRow(idx)

            # 文件名
            self.tbl_cloud_files.setItem(idx, 0, QTableWidgetItem(item["name"]))

            # 大小
            size_kb = round(item["size"] / 1024, 1)
            self.tbl_cloud_files.setItem(idx, 1, QTableWidgetItem(f"{size_kb} KB"))

            # 上传时间
            self.tbl_cloud_files.setItem(idx, 2, QTableWidgetItem(item["mtime"]))

            # 操作按钮面板
            act_widget = QWidget()
            act_layout = QHBoxLayout(act_widget)
            act_layout.setContentsMargins(2, 2, 2, 2)
            act_layout.setSpacing(6)

            btn_restore = QPushButton("恢复")
            btn_restore.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_restore.setStyleSheet("QPushButton { font-size: 10px; background-color: #3b82f6; color: white; border-radius: 2px; padding: 2px 6px; }")
            btn_restore.clicked.connect(lambda *_, name=item["name"]: self._on_cloud_restore_clicked(name))

            btn_delete = QPushButton("删除")
            btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_delete.setStyleSheet("QPushButton { font-size: 10px; background-color: #ef4444; color: white; border-radius: 2px; padding: 2px 6px; }")
            btn_delete.clicked.connect(lambda *_, name=item["name"]: self._on_cloud_delete_clicked(name))

            act_layout.addWidget(btn_restore)
            act_layout.addWidget(btn_delete)

            self.tbl_cloud_files.setCellWidget(idx, 3, act_widget)

        count = len(lst)
        self.log(f"云端备份列表刷新完成，共找到 {count} 个 ZIP 备份文件。")
        self._set_inline_status(f"云备份列表已刷新，共找到 {count} 个备份文件。", "success", auto_hide_ms=3500)

    def _confirm_dangerous_action(
        self,
        *,
        title: str,
        summary: str,
        impact: object,
        level: str = "高风险",
        irreversible: bool = True,
    ) -> bool:
        if isinstance(impact, str):
            impact_items = [impact]
        else:
            try:
                impact_items = [str(item or "").strip() for item in list(impact or [])]
            except TypeError:
                impact_items = []
        impact_items = [item for item in impact_items if item]
        impact_text = "\n".join(f"- {item}" for item in impact_items) if impact_items else "- 当前数据"
        irreversible_text = "\n\n此操作不可撤销，请确认影响范围后再继续。" if irreversible else ""
        message = f"{summary}\n\n风险级别：{level}\n影响范围：\n{impact_text}{irreversible_text}"
        from deepcat.ui.main_window.compact import StyledMessageBox
        reply = StyledMessageBox.warning(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    # 动作响应事件
    # -------------------------------------------------------------
    def _on_export_clicked(self) -> None:
        # 收集备份项目，验证是否勾选
        opts = {
            "clipboard": self.chk_clipboard.isChecked(),
            "table_notes": self.chk_table_notes.isChecked(),
            "later_read": self.chk_later_read.isChecked(),
            "ai_chat_history": self.chk_ai_chat_history.isChecked(),
            "todo": self.chk_todo.isChecked(),
            "settings": self.chk_settings.isChecked(),
            "model_catalog": self.chk_model_catalog.isChecked(),
        }
        if not any(opts.values()):
            self._set_inline_status("请至少勾选一项要备份的数据内容。", "warning", auto_hide_ms=3000)
            return

        default_name = f"deepcat_backup_{time.strftime('%Y%m%d_%H%M%S')}.zip"
        path, _ = QFileDialog.getSaveFileName(self, "保存备份文件", default_name, "ZIP 压缩包 (*.zip)")
        if not path:
            return

        self._save_config_from_ui()

        # 组装任务参数并发起
        params = {
            "local_path": path,
            "options": opts,
            "webdav_enabled": False
        }
        self._start_worker("manual_backup", params, title="备份到本地")

    def _on_import_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择导入备份文件", "", "ZIP 压缩包 (*.zip)")
        if not path:
            return

        if not self._confirm_dangerous_action(
            title="恢复本地备份",
            summary="导入备份会覆盖当前对应数据和设置，确定继续吗？",
            impact=[
                f"备份文件：{Path(path).name}",
                "会覆盖备份包中包含的本地数据、设置或模型配置",
                "恢复完成后需要重启软件才能应用更改",
            ],
            level="高风险",
        ):
            return

        self.log(f"开始导入本地备份文件: {path}")

        # 1. 主线程同步断开数据库连接与监听
        if not self._prepare_for_restore():
            return

        # 2. 启动异步导入还原任务
        params = {
            "filepath": path,
            "delete_after_restore": False
        }
        if not self._start_worker("local_restore", params, title="导入备份"):
            self._resume_after_failed_restore()

    def _on_dav_test_clicked(self) -> None:
        self._save_config_from_ui()
        params = {
            "server": self.txt_dav_server.text().strip(),
            "user": self.txt_dav_user.text().strip(),
            "password": self.txt_dav_pass.text().strip()
        }
        if not params["server"] or not params["user"] or not params["password"]:
            self._set_inline_status("请完整填写 WebDAV 服务器地址、账号以及密码。", "warning", auto_hide_ms=3000)
            return

        self._start_worker("test_connect", params, title="测试云连接")

    def _on_dav_sync_clicked(self) -> None:
        # 勾选项
        opts = {
            "clipboard": self.chk_clipboard.isChecked(),
            "table_notes": self.chk_table_notes.isChecked(),
            "later_read": self.chk_later_read.isChecked(),
            "ai_chat_history": self.chk_ai_chat_history.isChecked(),
            "todo": self.chk_todo.isChecked(),
            "settings": self.chk_settings.isChecked(),
            "model_catalog": self.chk_model_catalog.isChecked(),
        }
        if not any(opts.values()):
            self._set_inline_status("请至少勾选一项要备份的数据内容。", "warning", auto_hide_ms=3000)
            return

        self._save_config_from_ui()

        # 备份保存在自动备份文件夹（没有则保存在应用根目录/backups/）
        backup_dir = self.txt_backup_dir.text().strip()
        if not backup_dir:
            backup_dir = str(get_app_dir() / "backups")
        Path(backup_dir).mkdir(parents=True, exist_ok=True)

        filename = f"deepcat_backup_{time.strftime('%Y%m%d_%H%M%S')}.zip"
        local_zip = Path(backup_dir) / filename

        params = {
            "local_path": str(local_zip),
            "options": opts,
            "webdav_enabled": True,
            "server": self.txt_dav_server.text().strip(),
            "user": self.txt_dav_user.text().strip(),
            "password": self.txt_dav_pass.text().strip(),
            "backup_dir": self.txt_dav_dir.text().strip() or "DeepCatBackup",
            "webdav_keep_count": int(self.cmb_cloud_keep_count.currentText())
        }

        if not params["server"] or not params["user"] or not params["password"]:
            self._set_inline_status("请完整填写云端同步卡片中的服务器地址、账号和应用密码。", "warning", auto_hide_ms=3000)
            return

        self._start_worker("manual_backup", params, title="云端同步备份")

    def _on_dav_refresh_clicked(self) -> None:
        self._save_config_from_ui()
        params = {
            "server": self.txt_dav_server.text().strip(),
            "user": self.txt_dav_user.text().strip(),
            "password": self.txt_dav_pass.text().strip(),
            "backup_dir": self.txt_dav_dir.text().strip() or "DeepCatBackup"
        }
        if not params["server"] or not params["user"] or not params["password"]:
            self._set_inline_status("请完整填写服务器地址、账号和应用密码。", "warning", auto_hide_ms=3000)
            return

        self._start_worker("load_cloud_list", params, title="拉取备份列表")

    def _on_cloud_restore_clicked(self, filename: str) -> None:
        if not self._confirm_dangerous_action(
            title="从云端恢复",
            summary="将下载云端备份并覆盖当前数据，确定继续吗？",
            impact=[
                f"云端备份文件：{filename}",
                "会覆盖备份包中包含的本地数据、设置或模型配置",
                "恢复完成后需要重启软件才能应用更改",
            ],
            level="高风险",
        ):
            return

        self._save_config_from_ui()

        # 临时下载目录在 data/temp
        temp_dir = get_app_dir() / "data" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        params = {
            "filename": filename,
            "temp_dir": str(temp_dir),
            "server": self.txt_dav_server.text().strip(),
            "user": self.txt_dav_user.text().strip(),
            "password": self.txt_dav_pass.text().strip(),
            "backup_dir": self.txt_dav_dir.text().strip() or "DeepCatBackup"
        }
        self._start_worker("restore_cloud_file", params, title="下载云备份")

    def _on_cloud_delete_clicked(self, filename: str) -> None:
        if not self._confirm_dangerous_action(
            title="删除云端备份",
            summary="确定要永久删除这个云端备份文件吗？",
            impact=[
                f"云端备份文件：{filename}",
                "只删除云端备份文件，不会删除本机当前数据",
            ],
            level="中风险",
        ):
            return

        self._save_config_from_ui()
        params = {
            "filename": filename,
            "server": self.txt_dav_server.text().strip(),
            "user": self.txt_dav_user.text().strip(),
            "password": self.txt_dav_pass.text().strip(),
            "backup_dir": self.txt_dav_dir.text().strip() or "DeepCatBackup"
        }
        self._start_worker("delete_cloud_file", params, title="删除云备份")

    # -------------------------------------------------------------
    # 敏感数据清除
    # -------------------------------------------------------------
    def _on_light_cleanup_clicked(self) -> None:
        if not self._confirm_dangerous_action(
            title="轻量清理",
            summary="将清理软件日志和临时文件，确定继续吗？",
            impact=[
                "清理范围：日志文件、data/temp 临时文件",
                "不会删除复制记录、AI对话、待办、模型配置或界面设置",
            ],
            level="低风险",
            irreversible=False,
        ):
            return

        # 日志与临时文件清理涉及大目录遍历，放到工作线程执行
        self._start_worker(
            "clear_data_files",
            {"data_type": "light_cleanup", "record_name": "一键轻量清理"},
            title="一键轻量清理",
        )

    def _on_clear_clicked(self, data_type: str) -> None:
        type_names = {
            "clipboard": "复制记录及剪贴板图片",
            "table_notes": "记事本表格与笔记数据",
            "later_read": "稍后阅读条目及设置",
            "ai_chat_history": "AI对话记录",
            "todo": "休息待办记录",
            "logs": "Log日志文件",
            "settings": "默认界面设置",
            "model_catalog": "模型列表",
        }
        name = type_names.get(data_type, "该项数据")
        if data_type in {"settings", "model_catalog"}:
            summary = f"确定要将“{name}”恢复为默认值吗？"
            impact = [
                f"恢复默认：{name}",
                "当前对应配置会被默认配置覆盖",
                "操作完成后软件将自动重启",
            ]
        else:
            summary = f"确定要彻底清空“{name}”吗？"
            impact = [
                f"清空数据：{name}",
                "本机对应数据会被物理清除",
                "建议先确认已有可用备份",
            ]
        if not self._confirm_dangerous_action(
            title="高危操作确认",
            summary=summary,
            impact=impact,
            level="高风险",
        ):
            return

        self.log(f"开始清空: {name} ...")
        if data_type == "clipboard":
            # 主线程只清数据库与界面；图片目录可达 1GB，删除交由工作线程执行
            ok, msg = clear_data_class(data_type, self.main_window, clear_disk_heavy=False)
            if not ok:
                self._set_inline_status(f"清除失败：{msg}", "error")
                self.log(f"清除失败: {msg}")
                return
            if not self._start_worker(
                "clear_data_files",
                {"data_type": "clipboard", "record_name": name},
                title="清理剪贴板图片",
            ):
                # 任务排队失败（有其他任务在跑）时兜底同步清理，保证数据被真正清除
                ok, msg = clear_data_disk_heavy(data_type)
                if ok:
                    self._append_cleanup_record(name, msg)
                    self._set_inline_status(f"{name}已成功清除并重新加载。", "success", auto_hide_ms=3500)
                    self._load_config_to_ui()
                else:
                    self._set_inline_status(f"清除失败：{msg}", "error")
            return
        if data_type == "logs":
            # 日志目录遍历与删除放到工作线程
            self._start_worker(
                "clear_data_files",
                {"data_type": "logs", "record_name": name},
                title="清理日志文件",
            )
            return
        ok, msg = clear_data_class(data_type, self.main_window)
        if ok:
            self._append_cleanup_record(name, msg)
            if data_type in {"settings", "model_catalog"}:
                self._set_inline_status(f"{name}已重置为默认值，软件即将自动重启以生效。", "success")
                single_shot_scoped(1200, self, self._restart_software)
            else:
                self._set_inline_status(f"{name}已成功清除并重新加载。", "success", auto_hide_ms=3500)
                if data_type == "logs":
                    self._refresh_data_sizes()
                else:
                    self.log(f"数据清除成功: {msg}")
                    self._load_config_to_ui()
        else:
            self._set_inline_status(f"清除失败：{msg}", "error")
            self.log(f"清除失败: {msg}")
