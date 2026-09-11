from __future__ import annotations

import json
import os
import re
import shutil
import stat
import threading
import time
import ctypes
import html
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from deepcat.ui.main_window.window import MainWindow
from PyQt6.QtCore import Qt, QThread, QTimer, QSize, QEvent, QPoint, pyqtSignal, QUrl, QObject
from PyQt6.QtGui import QIcon, QColor, QPixmap, QDesktopServices, QBrush
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QProgressBar,
    QFrame,
    QCheckBox,
    QLineEdit,
)
from deepcat.ui.settings_dialog import SettingsDialog
from deepcat.ui.post_capture_actions import ModernPopupComboBox, OcrTranslationWorker
from deepcat.drive_cleaner import (
    AVOID,
    CONFIRM_REQUIRED,
    CONTENTS,
    SAFE,
    SELF,
    CleanupReport,
    CleanupItemResult,
    ScanReport,
    SoftwareEntry,
    SoftwareUninstallItemResult,
    SoftwareUninstallReport,
    cleanup_items,
    cleanup_report_to_markdown,
    enumerate_drives,
    enumerate_installed_software,
    enrich_software_entry,
    reset_software_caches,
    export_scan_report,
    format_bytes,
    move_path_to_recycle_bin,
    now_iso,
    request_llm_advice,
    scan_drives,
    software_entry_force_paths,
    software_entry_should_display,
    software_uninstall_report_to_text,
    uninstall_software,
)
from deepcat.utils.paths import get_app_dir, get_files_dir

from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND, MAIN_WINDOW_BACKGROUND_COLORREF
from deepcat.ui.main_window.helpers import _safe_copy
from deepcat.ui.main_window.notes import _NoFocusTableDelegate


class _DriveCleanerScanWorker(QThread):
    progress = pyqtSignal(str)
    item_found = pyqtSignal(str, object)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        roots: list[str],
        *,
        min_candidate_size_mb: int,
        min_large_file_size_mb: int,
        min_duplicate_size_mb: int,
        max_depth: int,
        scan_modes: Optional[list[str]] = None,
        confirm_duplicate_content_hash: bool = True,
    ) -> None:
        super().__init__()
        self._roots = list(roots)
        self._min_candidate_size_mb = int(min_candidate_size_mb)
        self._min_large_file_size_mb = int(min_large_file_size_mb)
        self._min_duplicate_size_mb = int(min_duplicate_size_mb)
        self._max_depth = int(max_depth)
        self._scan_modes = list(scan_modes or [])
        self._confirm_duplicate_content_hash = bool(confirm_duplicate_content_hash)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            stream_item_found = lambda kind, item: self.item_found.emit(str(kind), _safe_copy(item))
            report = scan_drives(
                self._roots,
                min_candidate_size_mb=self._min_candidate_size_mb,
                min_large_file_size_mb=self._min_large_file_size_mb,
                min_duplicate_size_mb=self._min_duplicate_size_mb,
                max_depth=self._max_depth,
                scan_modes=self._scan_modes or None,
                confirm_duplicate_content_hash=self._confirm_duplicate_content_hash,
                cancel=self._cancel.is_set,
                progress=lambda msg: self.progress.emit(str(msg)),
                item_found=stream_item_found,
            )
            self.finished_ok.emit(report)
        except Exception as exc:
            self.failed.emit(str(exc) or repr(exc))


class _DriveCleanerCleanupWorker(QThread):
    progress = pyqtSignal(int, int, str, int)
    detail_progress = pyqtSignal(int, int, str, int, int, str)
    item_failed = pyqtSignal(str, str, str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        report: ScanReport,
        selected_ids: list[str],
        *,
        dry_run: bool,
        allow_confirm_required: bool = False,
        safe_age_hours: int = 0,
        delete_mode: str = "recycle",
    ) -> None:
        super().__init__()
        self._report = report
        self._selected_ids = list(selected_ids)
        self._dry_run = bool(dry_run)
        self._allow_confirm_required = bool(allow_confirm_required)
        self._safe_age_hours = int(safe_age_hours)
        self._delete_mode = "permanent" if str(delete_mode).lower() == "permanent" else "recycle"
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            candidates = [candidate for result in self._report.drives for candidate in result.candidates]

            def on_progress(processed: int, total: int, path: str, released: int, result: CleanupItemResult) -> None:
                self.progress.emit(int(processed), int(total), str(path or ""), int(released or 0))
                if str(result.status) not in {"Success", "Partial", "DryRun"} or int(result.error_count or 0) > 0:
                    self.item_failed.emit(str(result.path or path or ""), str(result.mode or "清理"), str(result.first_error or result.status or "未知错误"))

            cleanup_report = cleanup_items(
                candidates,
                self._selected_ids,
                dry_run=self._dry_run,
                confirmed_by_user=True,
                allow_confirm_required=self._allow_confirm_required,
                safe_age_hours=self._safe_age_hours,
                cancel=self._cancel.is_set,
                progress=on_progress,
                detail_progress=lambda done, total, candidate_path, detail_done, detail_total, detail_path: self.detail_progress.emit(
                    int(done),
                    int(total),
                    str(candidate_path or ""),
                    int(detail_done),
                    int(detail_total),
                    str(detail_path or ""),
                ),
                delete_mode=self._delete_mode,
            )
            self.finished_ok.emit(cleanup_report)
        except Exception as exc:
            self.failed.emit(str(exc) or repr(exc))


class _DriveCleanerFileCleanupWorker(QThread):
    progress = pyqtSignal(int, int, str, int)
    item_failed = pyqtSignal(str, str, str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, items: list[tuple[str, str]], *, dry_run: bool, delete_mode: str = "recycle") -> None:
        super().__init__()
        self._items = [(str(item_id), str(path)) for item_id, path in items]
        self._dry_run = bool(dry_run)
        self._delete_mode = "permanent" if str(delete_mode).lower() == "permanent" else "recycle"
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            started = now_iso()
            results: list[CleanupItemResult] = []
            total = len(self._items)
            released_total = 0
            for item_id, path_text in self._items:
                if self._cancel.is_set():
                    break
                path = Path(path_text)
                before = 0
                try:
                    if path.exists() and path.is_file() and not path.is_symlink():
                        before = int(path.stat().st_size)
                    elif not path.exists():
                        results.append(CleanupItemResult(item_id, path_text, "file", 0, 0, 0, "Missing", 1, "文件不存在", self._delete_mode))
                        self.item_failed.emit(path_text, "删除文件", "文件不存在")
                        self.progress.emit(len(results), total, path_text, released_total)
                        continue
                    else:
                        results.append(CleanupItemResult(item_id, path_text, "file", 0, 0, 0, "Rejected", 1, "只允许清理普通文件", self._delete_mode))
                        self.item_failed.emit(path_text, "删除文件", "只允许清理普通文件")
                        self.progress.emit(len(results), total, path_text, released_total)
                        continue
                    if self._dry_run:
                        results.append(CleanupItemResult(item_id, path_text, "file", before, before, 0, "DryRun", 0, "", self._delete_mode))
                        self.progress.emit(len(results), total, path_text, released_total)
                        continue
                    if self._delete_mode == "recycle":
                        ok, recycle_error = move_path_to_recycle_bin(path)
                        if not ok:
                            result = CleanupItemResult(item_id, path_text, "file", before, before, 0, "Failed", 1, recycle_error or "移入回收站失败", self._delete_mode)
                            results.append(result)
                            self.item_failed.emit(path_text, "移入回收站", result.first_error)
                            self.progress.emit(len(results), total, path_text, released_total)
                            continue
                    else:
                        try:
                            path.unlink(missing_ok=True)
                        except PermissionError:
                            os.chmod(str(path), stat.S_IWRITE)
                            path.unlink(missing_ok=True)
                    after = int(path.stat().st_size) if path.exists() else 0
                    freed = max(0, before - after)
                    released_total += freed
                    result = CleanupItemResult(item_id, path_text, "file", before, after, freed, "Success" if after == 0 else "Partial", 0, "", self._delete_mode)
                    results.append(result)
                    if result.status != "Success":
                        self.item_failed.emit(path_text, "删除文件", result.status)
                    self.progress.emit(len(results), total, path_text, released_total)
                except Exception as exc:
                    results.append(CleanupItemResult(item_id, path_text, "file", before, before, 0, "Failed", 1, str(exc), self._delete_mode))
                    self.item_failed.emit(path_text, "删除文件", str(exc))
                    self.progress.emit(len(results), total, path_text, released_total)
            self.finished_ok.emit(CleanupReport("1.0", started, now_iso(), self._dry_run, results))
        except Exception as exc:
            self.failed.emit(str(exc) or repr(exc))


class _DriveCleanerMoveWorker(QThread):
    progress = pyqtSignal(int, int, str, int)
    item_failed = pyqtSignal(str, str, str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, paths: list[str], target_dir: str, *, conflict_strategy: str = "skip") -> None:
        super().__init__()
        self._paths = [str(path) for path in paths if str(path).strip()]
        self._target_dir = str(target_dir)
        self._conflict_strategy = "rename" if str(conflict_strategy).lower() == "rename" else "skip"
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def _target_path(self, src: Path) -> Path:
        dst = Path(self._target_dir) / src.name
        if self._conflict_strategy != "rename" or not dst.exists():
            return dst
        stem = dst.stem
        suffix = dst.suffix
        parent = dst.parent
        for index in range(1, 10000):
            candidate = parent / f"{stem} ({index}){suffix}"
            if not candidate.exists():
                return candidate
        return dst

    def run(self) -> None:
        try:
            started = now_iso()
            results: list[CleanupItemResult] = []
            total = len(self._paths)
            released_total = 0
            target_root = Path(self._target_dir)
            if not target_root.exists() or not target_root.is_dir():
                self.failed.emit("目标目录不存在或不可写。")
                return
            for path_text in self._paths:
                if self._cancel.is_set():
                    break
                src = Path(path_text)
                before = 0
                try:
                    if not src.exists():
                        result = CleanupItemResult(path_text, path_text, "move", 0, 0, 0, "Missing", 1, "路径不存在", "move")
                        results.append(result)
                        self.item_failed.emit(path_text, "移动文件", result.first_error)
                        self.progress.emit(len(results), total, path_text, released_total)
                        continue
                    if not src.is_file() or src.is_symlink():
                        result = CleanupItemResult(path_text, path_text, "move", 0, 0, 0, "Rejected", 1, "只允许移动普通文件", "move")
                        results.append(result)
                        self.item_failed.emit(path_text, "移动文件", result.first_error)
                        self.progress.emit(len(results), total, path_text, released_total)
                        continue
                    before = int(src.stat().st_size)
                    dst = self._target_path(src)
                    if dst.exists() and self._conflict_strategy == "skip":
                        result = CleanupItemResult(path_text, path_text, "move", before, before, 0, "Failed", 1, f"同名冲突：{dst}", "move")
                        results.append(result)
                        self.item_failed.emit(path_text, "移动文件", result.first_error)
                        self.progress.emit(len(results), total, path_text, released_total)
                        continue
                    shutil.move(str(src), str(dst))
                    after = int(src.stat().st_size) if src.exists() else 0
                    freed = max(0, before - after)
                    released_total += freed
                    result = CleanupItemResult(path_text, str(dst), "move", before, after, freed, "Success", 0, "", "move")
                    results.append(result)
                    self.progress.emit(len(results), total, path_text, released_total)
                except PermissionError as exc:
                    result = CleanupItemResult(path_text, path_text, "move", before, before, 0, "Failed", 1, f"权限不足：{exc}", "move")
                    results.append(result)
                    self.item_failed.emit(path_text, "移动文件", result.first_error)
                    self.progress.emit(len(results), total, path_text, released_total)
                except Exception as exc:
                    result = CleanupItemResult(path_text, path_text, "move", before, before, 0, "Failed", 1, str(exc), "move")
                    results.append(result)
                    self.item_failed.emit(path_text, "移动文件", result.first_error)
                    self.progress.emit(len(results), total, path_text, released_total)
            self.finished_ok.emit(CleanupReport("1.0", started, now_iso(), False, results))
        except Exception as exc:
            self.failed.emit(str(exc) or repr(exc))


class _SoftwareUninstallScanWorker(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object)
    entry_enriched = pyqtSignal(int, object)  # (index, enriched_entry)
    enrich_done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            self.progress.emit("正在读取 Windows 已安装软件列表...")
            if self._cancel.is_set():
                self.finished_ok.emit([])
                return
            # 第一阶段：仅读注册表，秒级完成
            entries = enumerate_installed_software(fast=True)
            self.finished_ok.emit(entries)
            if self._cancel.is_set():
                return
            # 第二阶段：后台逐条补全磁盘目录和大小
            self.progress.emit("正在补全安装目录与大小信息...")
            reset_software_caches()
            for index, entry in enumerate(entries):
                if self._cancel.is_set():
                    break
                try:
                    enriched = enrich_software_entry(entry)
                except Exception:
                    continue
                if (enriched.install_location != entry.install_location
                        or enriched.data_location != entry.data_location
                        or enriched.size_bytes != entry.size_bytes):
                    self.entry_enriched.emit(index, enriched)
            self.enrich_done.emit()
        except Exception as exc:
            self.failed.emit(str(exc) or repr(exc))


class _SoftwareUninstallWorker(QThread):
    progress = pyqtSignal(int, int, str, int)
    item_failed = pyqtSignal(str, str, str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, entries: list[SoftwareEntry], selected_ids: list[str], *, force: bool) -> None:
        super().__init__()
        self._entries = list(entries)
        self._selected_ids = list(selected_ids)
        self._force = bool(force)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            if self._cancel.is_set():
                self.finished_ok.emit(SoftwareUninstallReport("1.0", now_iso(), now_iso(), "force" if self._force else "standard", []))
                return
            total = max(1, len(self._selected_ids))
            if self._force:
                self.progress.emit(0, total, "正在准备强力卸载影响范围", 0)
            else:
                self.progress.emit(0, total, "正在启动卸载器", 0)

            def on_progress(processed: int, total: int, entry: SoftwareEntry, result: SoftwareUninstallItemResult) -> None:
                if not self._force and str(result.status) == "Started":
                    self.progress.emit(max(0, int(processed) - 1), int(total), "正在等待卸载向导完成，完成后可返回查看残留候选", 0)
                elif self._force:
                    self.progress.emit(max(0, int(processed) - 1), int(total), "正在清理残留目录与卸载登记信息", 0)
                current = entry.install_location or entry.name
                self.progress.emit(int(processed), int(total), str(current or entry.name), 0)
                if str(result.status) not in {"Started", "Success", "Partial"}:
                    self.item_failed.emit(str(current or entry.name), "强力卸载" if self._force else "标准卸载", str(result.message or result.status or "未知错误"))

            report = uninstall_software(
                self._entries,
                self._selected_ids,
                force=self._force,
                cancel=self._cancel.is_set,
                progress=on_progress,
            )
            self.finished_ok.emit(report)
        except Exception as exc:
            self.failed.emit(str(exc) or repr(exc))


class _DriveCleanerAdvisorWorker(QThread):
    advised = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, report: ScanReport, model_config: dict[str, Any]) -> None:
        super().__init__()
        self._report = report
        self._model_config = dict(model_config or {})

    def run(self) -> None:
        try:
            advice = request_llm_advice(self._report, self._model_config)
            self.advised.emit(str(advice))
        except Exception as exc:
            self.failed.emit(str(exc) or repr(exc))


class _DriveCleanerTableItem(QTableWidgetItem):
    def __init__(self, text: str = "", *, sort_value: object = None, path: str = "") -> None:
        super().__init__(str(text or ""))
        self._sort_value = sort_value
        self._path = str(path or "")
        if self._path:
            self.setData(Qt.ItemDataRole.UserRole, self._path)
            self.setToolTip("点击打开路径")

    def __lt__(self, other) -> bool:
        left = getattr(self, "_sort_value", None)
        right = getattr(other, "_sort_value", None)
        if left is not None and right is not None:
            try:
                return left < right
            except TypeError:
                return str(left).lower() < str(right).lower()
        return self.text().lower() < other.text().lower()


class _DriveCleanerWindow(QDialog):
    _RESULT_ROLE_PATH = Qt.ItemDataRole.UserRole.value + 21
    _RESULT_ROLE_SIZE = Qt.ItemDataRole.UserRole.value + 22
    _RESULT_ROLE_RISK = Qt.ItemDataRole.UserRole.value + 23
    _RESULT_ROLE_MTIME = Qt.ItemDataRole.UserRole.value + 24
    _RESULT_ROLE_TYPE = Qt.ItemDataRole.UserRole.value + 25

    MODES = [
        ("cleanup", "常规清理", "扫描临时文件、缓存、日志等可清理候选", "icon_nav_disk.svg"),
        ("space", "空间分析", "按目录查看磁盘空间占用", "icon_nav_data.svg"),
        ("large_files", "扫大文件", "查找超过阈值的大文件", "icon_nav_search_file.svg"),
        ("duplicates", "重复文件", "按大小和哈希识别重复文件", "icon_nav_clipboard.svg"),
        ("uninstall", "软件卸载", "列出用户软件和系统软件，支持标准卸载与强力卸载", "icon_nav_uninstall.svg"),
    ]

    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(None)
        self._main_window = parent
        self._scan_worker: Optional[_DriveCleanerScanWorker] = None
        self._cleanup_worker: Optional[_DriveCleanerCleanupWorker] = None
        self._advisor_worker: Optional[OcrTranslationWorker] = None
        self._model_probe_worker: Optional[OcrTranslationWorker] = None
        self._reports: dict[str, ScanReport] = {}
        self._software_entries: list[SoftwareEntry] = []
        self._software_list_loaded = False
        self._current_mode = "cleanup"
        self._drive_checkboxes: dict[str, QCheckBox] = {}
        self._custom_scan_folder = ""
        self._scan_started_at = 0.0
        self._scan_cancel_requested = False
        self._scan_stage = ""
        self._active_scan_mode = ""
        self._active_execution_mode = ""
        self._mode_status_texts: dict[str, str] = {mode: "" for mode, *_ in self.MODES}
        self._execution_cancel_requested = False
        self._execution_operation = ""
        self._execution_failures: list[dict[str, str]] = []
        self._pending_result_refresh_modes: set[str] = set()
        self._last_failure_report = ""
        self._next_delete_mode = "recycle"
        self._cleanup_records: list[dict[str, Any]] = []
        self._pending_cleanup_record: Optional[dict[str, Any]] = None
        self._last_cleanup_remove_ids: set[str] = set()
        self._duplicate_cleanup_group_paths: dict[str, set[str]] = {}
        self._duplicate_keep_paths: dict[str, str] = {}
        self._duplicate_manual_groups: set[str] = set()
        self._duplicate_expanded_groups: set[str] = set()
        self._large_ignore: dict[str, list[dict[str, str]]] = {"paths": [], "dirs": [], "extensions": [], "keywords": []}
        self._large_filter_custom_days = 0
        self._large_move_target = ""
        self._advisor_expanded = False
        self.setWindowTitle("磁盘清理优化工具")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setMinimumSize(1080, 720)
        self.resize(1108, 760)
        self.setWindowIcon(parent._asset_icon("icon_nav_cleaner.svg", parent.windowIcon()))
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(500)
        self._elapsed_timer.timeout.connect(self._update_scan_elapsed_status)
        self._load_large_ignore_rules()
        self._build_ui()
        self._apply_style()

    def _apply_caption_color(self) -> None:
        try:
            if os.name != "nt":
                return
            hwnd = int(self.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()

    def _asset_icon(self, name: str, fallback: Optional[QIcon] = None) -> QIcon:
        return self._main_window._asset_icon(name, fallback)

    def _apply_caption_color_to_window(self, window: QWidget) -> None:
        try:
            if hasattr(self._main_window, "_apply_caption_color_to_window"):
                self._main_window._apply_caption_color_to_window(window)
                return
            if os.name != "nt":
                return
            hwnd = int(window.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def _apply_dialog_caption_color(self, dialog: QDialog) -> None:
        self._apply_caption_color_to_window(dialog)
        QTimer.singleShot(0, lambda d=dialog: self._apply_caption_color_to_window(d))

    def _drive_get_existing_directory(self, title: str, start_dir: str) -> str:
        dialog = QFileDialog(self, title, start_dir)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self._apply_dialog_caption_color(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        files = dialog.selectedFiles()
        return str(files[0]) if files else ""

    def _drive_get_save_file_name(self, title: str, start_path: str, name_filter: str) -> tuple[str, str]:
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            title,
            start_path,
            name_filter,
        )
        return str(path or ""), str(selected_filter or "")

    def _drive_get_int(self, title: str, label: str, value: int, minimum: int, maximum: int, step: int = 1) -> tuple[int, bool]:
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setInputMode(QInputDialog.InputMode.IntInput)
        dialog.setIntRange(int(minimum), int(maximum))
        dialog.setIntStep(int(step))
        dialog.setIntValue(int(value))
        self._apply_dialog_caption_color(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return int(value), False
        return int(dialog.intValue()), True

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("DriveCleanerSidebar")
        sidebar.setFixedWidth(92)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(10, 14, 10, 12)
        side_layout.setSpacing(8)
        self._mode_buttons: dict[str, QToolButton] = {}
        for mode, title, tooltip, icon_name in self.MODES:
            btn = QToolButton()
            btn.setObjectName("DriveCleanerModeButton")
            btn.setText(title)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setIcon(self._asset_icon(icon_name))
            btn.setIconSize(QSize(24, 24))
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tooltip)
            btn.clicked.connect(lambda _=False, m=mode: self._switch_mode(m))
            self._mode_buttons[mode] = btn
            side_layout.addWidget(btn)
        side_layout.addStretch(1)
        root.addWidget(sidebar)

        body = QWidget()
        body.setObjectName("DriveCleanerBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 18)
        body_layout.setSpacing(12)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        self._title_icon = QLabel()
        self._title_icon.setFixedSize(34, 34)
        self._title_label = QLabel("常规清理")
        self._title_label.setObjectName("DriveCleanerTitle")
        self._subtitle_label = QLabel("扫描临时文件、缓存、日志等可清理候选")
        self._subtitle_label.setObjectName("DriveCleanerSubtitle")
        title_box = QWidget()
        title_layout = QVBoxLayout(title_box)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(2)
        title_layout.addWidget(self._title_label)
        title_layout.addWidget(self._subtitle_label)
        header_layout.addWidget(self._title_icon)
        header_layout.addWidget(title_box, 1)
        body_layout.addWidget(header)

        controls = QWidget()
        controls_layout = QGridLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setHorizontalSpacing(10)
        controls_layout.setVerticalSpacing(8)
        self._drive_selector_row = QWidget()
        drive_layout = QHBoxLayout(self._drive_selector_row)
        drive_layout.setContentsMargins(0, 0, 0, 0)
        drive_layout.setSpacing(8)
        self._refresh_drive_list()
        self._folder_scan_btn = QPushButton("选择文件夹")
        self._folder_scan_btn.setObjectName("DriveCleanerSecondary")
        self._folder_scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_scan_btn.clicked.connect(self._choose_scan_folder)
        self._clear_folder_scan_btn = QPushButton("清除文件夹")
        self._clear_folder_scan_btn.setObjectName("DriveCleanerSecondary")
        self._clear_folder_scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_folder_scan_btn.clicked.connect(self._clear_scan_folder)
        self._clear_folder_scan_btn.setVisible(False)
        self._folder_scan_label = QLabel("")
        self._folder_scan_label.setObjectName("DriveCleanerFolderScope")
        self._folder_scan_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        drive_layout.addWidget(self._folder_scan_btn)
        drive_layout.addWidget(self._clear_folder_scan_btn)
        drive_layout.addWidget(self._folder_scan_label)
        self._drive_selector_label = QLabel("选择范围:")
        controls_layout.addWidget(self._drive_selector_label, 0, 0)
        controls_layout.addWidget(self._drive_selector_row, 0, 1, 1, 7)

        self._min_candidate = QSpinBox()
        self._min_candidate.setRange(1, 10240)
        self._min_candidate.setValue(20)
        self._min_candidate.setSuffix(" MB")
        self._large_file = QSpinBox()
        self._large_file.setRange(10, 102400)
        self._large_file.setValue(100)
        self._large_file.setSuffix(" MB")
        self._duplicate_file = QSpinBox()
        self._duplicate_file.setRange(1, 10240)
        self._duplicate_file.setValue(10)
        self._duplicate_file.setSuffix(" MB")
        self._scan_depth = QSpinBox()
        self._scan_depth.setRange(2, 20)
        self._scan_depth.setValue(7)
        for widget in (self._min_candidate, self._large_file, self._duplicate_file, self._scan_depth):
            widget.setFixedWidth(104)
            widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._min_candidate_label = QLabel("候选阈值:")
        self._large_file_label = QLabel("大文件:")
        self._duplicate_file_label = QLabel("重复文件:")
        self._scan_depth_label = QLabel("深度:")
        self._duplicate_hash_confirm = QCheckBox("按内容哈希确认")
        self._duplicate_hash_confirm.setObjectName("DriveSelectorCheckBox")
        self._duplicate_hash_confirm.setChecked(True)
        self._duplicate_hash_confirm.setToolTip("建议开启：按大小分组、快速指纹预筛，再用完整内容哈希确认重复。关闭后仅按快速指纹预筛，需要人工确认。")
        self._duplicate_keep_rule_label = QLabel("保留规则:")
        self._duplicate_keep_rule = ModernPopupComboBox()
        self._duplicate_keep_rule.setObjectName("DriveCleanerCombo")
        self._prepare_drive_combo(self._duplicate_keep_rule)
        self._duplicate_keep_rule.addItem("保留最新", "newest")
        self._duplicate_keep_rule.addItem("保留最旧", "oldest")
        self._duplicate_keep_rule.addItem("保留路径最短", "shortest_path")
        self._duplicate_keep_rule.addItem("手动选择", "manual")
        self._duplicate_keep_rule.currentIndexChanged.connect(lambda *_: self._on_duplicate_keep_rule_changed())
        self._large_unused_label = QLabel("未使用:")
        self._large_unused_filter = ModernPopupComboBox()
        self._large_unused_filter.setObjectName("DriveCleanerCombo")
        self._prepare_drive_combo(self._large_unused_filter)
        self._large_unused_filter.addItem("全部", 0)
        self._large_unused_filter.addItem("30 天未访问", 30)
        self._large_unused_filter.addItem("90 天未访问", 90)
        self._large_unused_filter.addItem("180 天未访问", 180)
        self._large_unused_filter.addItem("自定义天数...", -1)
        self._large_unused_filter.currentIndexChanged.connect(lambda *_: self._on_large_filter_changed())
        self._large_type_label = QLabel("扩展名分组:")
        self._large_type_filter = ModernPopupComboBox()
        self._large_type_filter.setObjectName("DriveCleanerCombo")
        self._prepare_drive_combo(self._large_type_filter)
        for label in ("全部", "安装包", "压缩包", "视频", "视频缓存", "镜像", "日志", "其它"):
            self._large_type_filter.addItem(label, "" if label == "全部" else label)
        self._large_type_filter.currentIndexChanged.connect(lambda *_: self._on_large_filter_changed())
        self._large_path_filter = QLineEdit()
        self._large_path_filter.setObjectName("DriveCleanerSearch")
        self._large_path_filter.setPlaceholderText("筛选路径关键词...")
        self._large_path_filter.textChanged.connect(lambda *_: self._filter_large_files_table())
        SettingsDialog._install_custom_text_context_menus(self, self._large_path_filter)
        self._result_risk_label = QLabel("风险:")
        self._result_risk_filter = ModernPopupComboBox()
        self._result_risk_filter.setObjectName("DriveCleanerCombo")
        self._prepare_drive_combo(self._result_risk_filter)
        for label, value in (("全部", ""), ("安全", "安全"), ("需确认", "需确认"), ("高风险", "高风险")):
            self._result_risk_filter.addItem(label, value)
        self._result_risk_filter.currentIndexChanged.connect(lambda *_: self._apply_result_filters())
        self._result_type_label = QLabel("类型:")
        self._result_type_filter = ModernPopupComboBox()
        self._result_type_filter.setObjectName("DriveCleanerCombo")
        self._prepare_drive_combo(self._result_type_filter)
        self._result_type_filter.addItem("全部", "")
        self._result_type_filter.currentIndexChanged.connect(lambda *_: self._apply_result_filters())
        self._result_path_filter = QLineEdit()
        self._result_path_filter.setObjectName("DriveCleanerSearch")
        self._result_path_filter.setPlaceholderText("按路径关键词筛选...")
        self._result_path_filter.textChanged.connect(lambda *_: self._apply_result_filters())
        SettingsDialog._install_custom_text_context_menus(self, self._result_path_filter)
        self._result_min_size_label = QLabel("大小:")
        self._result_min_size = QSpinBox()
        self._result_min_size.setRange(0, 1024000)
        self._result_min_size.setSuffix(" MB+")
        self._result_min_size.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._result_min_size.setFixedWidth(92)
        self._result_min_size.valueChanged.connect(lambda *_: self._apply_result_filters())
        self._result_max_size = QSpinBox()
        self._result_max_size.setRange(0, 1024000)
        self._result_max_size.setSuffix(" MB-")
        self._result_max_size.setSpecialValueText("不限")
        self._result_max_size.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._result_max_size.setFixedWidth(92)
        self._result_max_size.valueChanged.connect(lambda *_: self._apply_result_filters())
        self._result_mtime_label = QLabel("修改:")
        self._result_mtime_filter = ModernPopupComboBox()
        self._result_mtime_filter.setObjectName("DriveCleanerCombo")
        self._prepare_drive_combo(self._result_mtime_filter)
        for label, value in (("全部", ""), ("7 天内", "within_7"), ("30 天内", "within_30"), ("90 天内", "within_90"), ("90 天以前", "older_90"), ("180 天以前", "older_180")):
            self._result_mtime_filter.addItem(label, value)
        self._result_mtime_filter.currentIndexChanged.connect(lambda *_: self._apply_result_filters())
        self._filter_confirm_btn = QPushButton("只看需确认")
        self._filter_safe_btn = QPushButton("只看安全")
        self._filter_clear_btn = QPushButton("清除筛选")
        for btn in (self._filter_confirm_btn, self._filter_safe_btn, self._filter_clear_btn):
            btn.setObjectName("DriveCleanerSecondary")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._filter_confirm_btn.clicked.connect(lambda: self._set_quick_result_filter("需确认"))
        self._filter_safe_btn.clicked.connect(lambda: self._set_quick_result_filter("安全"))
        self._filter_clear_btn.clicked.connect(self._clear_result_filters)
        self._software_search = QLineEdit()
        self._software_search.setObjectName("DriveCleanerSearch")
        self._software_search.setPlaceholderText("搜索软件名称、发布者、安装目录或数据目录...")
        self._software_search.setClearButtonEnabled(True)
        self._software_search.textChanged.connect(lambda *_: self._on_software_search_changed())
        SettingsDialog._install_custom_text_context_menus(self, self._software_search)
        self._controls_layout = controls_layout
        controls_layout.addWidget(self._min_candidate_label, 1, 0)
        controls_layout.addWidget(self._min_candidate, 1, 1)
        controls_layout.addWidget(self._large_file_label, 1, 2)
        controls_layout.addWidget(self._large_file, 1, 3)
        controls_layout.addWidget(self._duplicate_file_label, 1, 4)
        controls_layout.addWidget(self._duplicate_file, 1, 5)
        controls_layout.addWidget(self._scan_depth_label, 1, 6)
        controls_layout.addWidget(self._scan_depth, 1, 7)
        body_layout.addWidget(controls)

        self._stack = QStackedWidget()
        self._tables: dict[str, QTableWidget] = {}
        for mode, *_ in self.MODES:
            table = self._make_table_for_mode(mode)
            self._tables[mode] = table
            self._stack.addWidget(table)
        body_layout.addWidget(self._stack, 1)

        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        self._scan_btn = QPushButton("扫描")
        self._scan_btn.setObjectName("DriveCleanerPrimary")
        self._select_all_btn = QPushButton("全选")
        self._select_all_btn.setObjectName("DriveCleanerSecondary")
        self._dry_run_btn = QPushButton("预演清理")
        self._dry_run_btn.setObjectName("DriveCleanerSecondary")
        self._clean_btn = QPushButton("执行清理")
        self._clean_btn.setObjectName("DriveCleanerDanger")
        self._large_move_btn = QPushButton("移动到...")
        self._large_move_btn.setObjectName("DriveCleanerSecondary")
        self._large_ignore_btn = QPushButton("管理忽略规则")
        self._large_ignore_btn.setObjectName("DriveCleanerSecondary")
        self._export_btn = QPushButton("导出报告")
        self._export_btn.setObjectName("DriveCleanerSecondary")
        self._copy_failures_btn = QPushButton("复制失败报告")
        self._copy_failures_btn.setObjectName("DriveCleanerSecondary")
        self._copy_failures_btn.setVisible(False)
        self._open_recycle_bin_btn = QPushButton("打开回收站")
        self._open_recycle_bin_btn.setObjectName("DriveCleanerSecondary")
        self._open_recycle_bin_btn.setVisible(False)
        self._progress = QProgressBar()
        self._progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress.setFixedHeight(30)
        self._progress.setMinimumWidth(100)
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._status_label = QLabel("就绪")
        self._status_label.setObjectName("DriveCleanerStatus")
        self._status_label.setWordWrap(True)
        self._status_label.setMinimumWidth(180)
        self._status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        for btn in (self._scan_btn, self._select_all_btn, self._dry_run_btn, self._clean_btn, self._large_move_btn, self._large_ignore_btn, self._export_btn, self._copy_failures_btn, self._open_recycle_bin_btn):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dry_run_btn.setEnabled(False)
        self._clean_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        action_layout.addWidget(self._scan_btn)
        action_layout.addWidget(self._select_all_btn)
        action_layout.addWidget(self._dry_run_btn)
        action_layout.addWidget(self._clean_btn)
        action_layout.addWidget(self._large_move_btn)
        action_layout.addWidget(self._large_ignore_btn)
        action_layout.addWidget(self._export_btn)
        action_layout.addWidget(self._copy_failures_btn)
        action_layout.addWidget(self._open_recycle_bin_btn)
        action_layout.addWidget(self._progress, 0, Qt.AlignmentFlag.AlignVCenter)
        action_layout.addWidget(self._status_label, 1)
        body_layout.addWidget(action_row)

        lower = QWidget()
        lower_layout = QHBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(10)
        self._log = QTextEdit()
        self._log.setObjectName("DriveCleanerLog")
        self._log.setReadOnly(True)
        self._log.setAcceptRichText(False)
        self._log.setPlaceholderText("操作日志...")
        self._log.document().setMaximumBlockCount(600)

        self._advisor_slot = QFrame()
        self._advisor_slot.setObjectName("DriveCleanerAdvisorSlot")
        self._advisor_slot.setMinimumHeight(126)
        self._advisor_slot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._advisor_slot_layout = QVBoxLayout(self._advisor_slot)
        self._advisor_slot_layout.setContentsMargins(0, 0, 0, 0)
        self._advisor_slot_layout.setSpacing(0)
        self._advisor_card = QFrame()
        self._advisor_card.setObjectName("DriveCleanerAdvisorCard")
        self._advisor_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        advisor_card_layout = QVBoxLayout(self._advisor_card)
        advisor_card_layout.setContentsMargins(0, 0, 0, 0)
        advisor_card_layout.setSpacing(0)
        advisor_header = QFrame()
        advisor_header.setObjectName("DriveCleanerAdvisorHeader")
        advisor_header_layout = QHBoxLayout(advisor_header)
        advisor_header_layout.setContentsMargins(10, 8, 8, 6)
        advisor_header_layout.setSpacing(6)
        advisor_title = QLabel("AI 分析")
        advisor_title.setObjectName("DriveCleanerAdvisorTitle")
        self._advisor_expand_btn = QToolButton()
        self._advisor_expand_btn.setObjectName("DriveCleanerAdvisorExpand")
        self._advisor_expand_btn.setIcon(self._asset_icon("icon_selection_search.svg"))
        self._advisor_expand_btn.setIconSize(QSize(15, 15))
        self._advisor_expand_btn.setCheckable(True)
        self._advisor_expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._advisor_expand_btn.setToolTip("展开 AI 分析窗口")
        self._advisor_expand_btn.clicked.connect(lambda _=False: self._toggle_advisor_expanded())
        advisor_header_layout.addWidget(advisor_title)
        advisor_header_layout.addStretch(1)
        advisor_header_layout.addWidget(self._advisor_expand_btn)
        self._advisor = QTextEdit()
        self._advisor.setObjectName("DriveCleanerAdvisor")
        self._advisor.setReadOnly(True)
        self._advisor.setAcceptRichText(True)
        self._advisor.setPlaceholderText("大模型扫描清理对话...")
        advisor_card_layout.addWidget(advisor_header)
        advisor_card_layout.addWidget(self._advisor, 1)
        self._advisor_slot_layout.addWidget(self._advisor_card)
        self._advisor_float_panel = QFrame(self)
        self._advisor_float_panel.setObjectName("DriveCleanerAdvisorFloat")
        self._advisor_float_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._advisor_float_layout = QVBoxLayout(self._advisor_float_panel)
        self._advisor_float_layout.setContentsMargins(0, 0, 0, 0)
        self._advisor_float_layout.setSpacing(0)
        self._advisor_float_card = QFrame()
        self._advisor_float_card.setObjectName("DriveCleanerAdvisorCard")
        self._advisor_float_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        advisor_float_card_layout = QVBoxLayout(self._advisor_float_card)
        advisor_float_card_layout.setContentsMargins(0, 0, 0, 0)
        advisor_float_card_layout.setSpacing(0)
        advisor_float_header = QFrame()
        advisor_float_header.setObjectName("DriveCleanerAdvisorHeader")
        advisor_float_header_layout = QHBoxLayout(advisor_float_header)
        advisor_float_header_layout.setContentsMargins(10, 8, 8, 6)
        advisor_float_header_layout.setSpacing(6)
        advisor_float_title = QLabel("AI 分析")
        advisor_float_title.setObjectName("DriveCleanerAdvisorTitle")
        self._advisor_float_expand_btn = QToolButton()
        self._advisor_float_expand_btn.setObjectName("DriveCleanerAdvisorExpand")
        self._advisor_float_expand_btn.setIcon(self._asset_icon("icon_selection_search.svg"))
        self._advisor_float_expand_btn.setIconSize(QSize(15, 15))
        self._advisor_float_expand_btn.setCheckable(True)
        self._advisor_float_expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._advisor_float_expand_btn.setToolTip("收起 AI 分析窗口")
        self._advisor_float_expand_btn.clicked.connect(lambda _=False: self._collapse_advisor_panel())
        advisor_float_header_layout.addWidget(advisor_float_title)
        advisor_float_header_layout.addStretch(1)
        advisor_float_header_layout.addWidget(self._advisor_float_expand_btn)
        self._advisor_float_text = QTextEdit()
        self._advisor_float_text.setObjectName("DriveCleanerAdvisor")
        self._advisor_float_text.setReadOnly(True)
        self._advisor_float_text.setAcceptRichText(True)
        self._advisor_float_text.setPlaceholderText("大模型扫描清理对话...")
        advisor_float_card_layout.addWidget(advisor_float_header)
        advisor_float_card_layout.addWidget(self._advisor_float_text, 1)
        self._advisor_float_layout.addWidget(self._advisor_float_card)
        advisor_shadow = QGraphicsDropShadowEffect(self._advisor_float_panel)
        advisor_shadow.setBlurRadius(26)
        advisor_shadow.setOffset(0, 8)
        advisor_shadow.setColor(QColor(15, 23, 42, 46))
        self._advisor_float_panel.setGraphicsEffect(advisor_shadow)
        self._advisor_float_panel.hide()
        lower_layout.addWidget(self._log, 1)
        lower_layout.addWidget(self._advisor_slot, 1)
        body_layout.addWidget(lower)

        root.addWidget(body, 1)
        self._scan_btn.clicked.connect(self._start_scan)
        self._select_all_btn.clicked.connect(self._select_current_table_all)
        self._dry_run_btn.clicked.connect(self._run_mode_secondary_action)
        self._clean_btn.clicked.connect(self._run_mode_danger_action)
        self._large_move_btn.clicked.connect(self._move_selected_large_files)
        self._large_ignore_btn.clicked.connect(self._show_large_ignore_manager)
        self._export_btn.clicked.connect(self._export_report)
        self._copy_failures_btn.clicked.connect(self._copy_failure_report)
        self._open_recycle_bin_btn.clicked.connect(self._open_recycle_bin)
        self._result_refresh_timer = QTimer(self)
        self._result_refresh_timer.setSingleShot(True)
        self._result_refresh_timer.timeout.connect(self._flush_result_refresh)
        self._switch_mode("cleanup")
        SettingsDialog._install_custom_text_context_menus(self, self)

    def _prepare_drive_combo(self, combo: QComboBox) -> None:
        combo.setMaxVisibleItems(8)
        combo.setMinimumWidth(150)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        combo.setProperty("matchPopupWidthToParent", True)
        combo.setProperty("activeIndicator", "background")
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        view = combo.view()
        if view is not None:
            view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def _close_drive_combo_popup(self, widget: QWidget) -> None:
        if not isinstance(widget, QComboBox):
            if isinstance(widget, QLineEdit):
                widget.clearFocus()
            return
        try:
            widget.hidePopup()
        except RuntimeError:
            pass
        try:
            view = widget.view()
            if view is not None:
                view.hide()
                popup_window = view.window()
                if popup_window is not None and popup_window is not widget:
                    popup_window.hide()
        except RuntimeError:
            pass

    def _adopt_drive_control_widget(self, widget: QWidget, parent: Optional[QWidget]) -> None:
        if parent is None or widget.parent() is not None:
            return
        widget.setParent(parent)

    def _make_table_for_mode(self, mode: str) -> QTableWidget:
        headers = {
            "cleanup": ["勾选", "风险", "项目", "大小", "修改时间", "路径", "说明"],
            "space": ["风险", "目录", "大小", "修改时间", "路径", "说明"],
            "large_files": ["勾选", "风险", "文件名", "大小", "目录", "未使用", "修改时间", "类型说明", "操作"],
            "duplicates": ["勾选", "风险", "重复组", "预计释放", "数量", "修改时间", "保留规则 / 文件"],
            "uninstall": ["勾选", "类型", "名称", "大小", "版本", "安装目录", "数据目录", "说明", "操作"],
        }[mode]
        table = QTableWidget(0, len(headers))
        table.setObjectName("DriveCleanerTable")
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setItemDelegate(_NoFocusTableDelegate(table))
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.setWordWrap(False)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        for index in range(len(headers)):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        if mode != "space":
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._apply_table_column_widths(table, mode)
        self._prepare_path_click_table(table)
        table.itemChanged.connect(lambda *_: self._refresh_actions())
        return table

    def _prepare_path_click_table(self, table: QTableWidget) -> None:
        table.setMouseTracking(True)
        table.setProperty("DriveCleanerPathTable", True)
        viewport = table.viewport()
        viewport.setMouseTracking(True)
        if not bool(viewport.property("DriveCleanerPathFilterInstalled")):
            viewport.installEventFilter(self)
            viewport.setProperty("DriveCleanerPathFilterInstalled", True)
        table.itemClicked.connect(self._open_path_from_table_item)

    def _apply_table_column_widths(self, table: QTableWidget, mode: str) -> None:
        widths = {
            "cleanup": [52, 78, 120, 110, 150, 620, 860],
            "space": [78, 220, 120, 150, 620, 860],
            "large_files": [52, 78, 220, 110, 420, 130, 150, 320, 460],
            "duplicates": [52, 78, 300, 140, 80, 150, 760],
            "uninstall": [52, 78, 240, 110, 120, 420, 420, 360, 300],
        }.get(mode, [])
        for index, width in enumerate(widths):
            if index < table.columnCount():
                table.setColumnWidth(index, int(width))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        table = self._table_for_viewport(obj)
        if table is not None:
            if event.type() == QEvent.Type.Leave:
                table.viewport().unsetCursor()
            elif event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                item = table.itemAt(pos)
                if self._item_opens_path(item):
                    table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
                else:
                    table.viewport().unsetCursor()
        return super().eventFilter(obj, event)

    def _table_for_viewport(self, obj: QObject) -> Optional[QTableWidget]:
        for table in getattr(self, "_tables", {}).values():
            if obj is table.viewport():
                return table
        parent_obj = obj.parent() if obj is not None else None
        while parent_obj is not None:
            if isinstance(parent_obj, QTableWidget) and bool(parent_obj.property("DriveCleanerPathTable")):
                return parent_obj
            parent_obj = parent_obj.parent()
        return None

    def _refresh_drive_list(self, *, preserve_selection: bool = False) -> None:
        selected_roots = set(self._selected_roots()) if preserve_selection and self._drive_checkboxes else set()
        self._drive_checkboxes = {}
        layout = self._drive_selector_row.layout()
        preserved_widgets = [
            widget
            for widget in (
                getattr(self, "_folder_scan_btn", None),
                getattr(self, "_clear_folder_scan_btn", None),
                getattr(self, "_folder_scan_label", None),
            )
            if widget is not None
        ]
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if widget in preserved_widgets:
                    widget.setParent(self._drive_selector_row)
                else:
                    widget.deleteLater()
        for drive in enumerate_drives():
            checkbox = QCheckBox(f"{drive.drive} ({format_bytes(drive.free_bytes)} 可用)")
            checkbox.setObjectName("DriveSelectorCheckBox")
            default_checked = self._norm_drive_root(drive.root) == self._norm_drive_root(self._system_drive_root())
            checkbox.setChecked((drive.root in selected_roots) if preserve_selection else default_checked)
            checkbox.setEnabled(str(drive.drive_type).lower() not in {"network", "cdrom"})
            checkbox.setToolTip(f"{drive.root}  总空间 {format_bytes(drive.total_bytes)}")
            self._drive_checkboxes[drive.root] = checkbox
            layout.addWidget(checkbox)
        if not self._drive_checkboxes:
            layout.addWidget(QLabel("未发现可扫描盘符"))
        for widget in preserved_widgets:
            stretch = 1 if widget is getattr(self, "_folder_scan_label", None) else 0
            layout.addWidget(widget, stretch)

    def _selected_roots(self) -> list[str]:
        if self._custom_scan_folder:
            return [self._custom_scan_folder]
        return [root for root, box in self._drive_checkboxes.items() if box.isEnabled() and box.isChecked()]

    def _system_drive_root(self) -> str:
        system_drive = os.environ.get("SystemDrive", "C:").strip() or "C:"
        if len(system_drive) == 2 and system_drive[1] == ":":
            return system_drive + "\\"
        return system_drive

    def _norm_drive_root(self, root: str) -> str:
        text = str(root or "").strip().replace("/", "\\")
        if len(text) == 2 and text[1] == ":":
            text += "\\"
        return text.rstrip("\\").lower()

    def _choose_scan_folder(self) -> None:
        start_dir = self._custom_scan_folder or self._system_drive_root()
        folder = self._drive_get_existing_directory("选择精细扫描文件夹", start_dir)
        if not folder:
            return
        path = str(Path(folder))
        self._custom_scan_folder = path
        for checkbox in self._drive_checkboxes.values():
            checkbox.setChecked(False)
        self._folder_scan_label.setText(f"文件夹: {path}")
        self._folder_scan_label.setToolTip(path)
        self._clear_folder_scan_btn.setVisible(True)
        self._append_log(f"已选择精细扫描文件夹: {path}")

    def _clear_scan_folder(self) -> None:
        self._custom_scan_folder = ""
        self._folder_scan_label.clear()
        self._folder_scan_label.setToolTip("")
        self._clear_folder_scan_btn.setVisible(False)
        self._append_log("已清除精细扫描文件夹，恢复按盘符扫描。")

    def _switch_mode(self, mode: str) -> None:
        self._current_mode = str(mode)
        index = [m[0] for m in self.MODES].index(self._current_mode)
        self._stack.setCurrentIndex(index)
        title, subtitle, icon_name = self.MODES[index][1], self.MODES[index][2], self.MODES[index][3]
        self._title_label.setText(title)
        self._subtitle_label.setText(subtitle)
        pix = self._asset_icon(icon_name).pixmap(34, 34)
        self._title_icon.setPixmap(pix)
        for key, btn in self._mode_buttons.items():
            btn.setChecked(key == mode)
        show_drive_selector = mode != "uninstall"
        self._drive_selector_label.setVisible(show_drive_selector)
        self._drive_selector_row.setVisible(show_drive_selector)
        show_candidate = mode == "cleanup"
        show_large = mode == "large_files"
        show_duplicate = mode == "duplicates"
        show_depth = mode in {"space", "duplicates"}
        show_software = mode == "uninstall"
        self._min_candidate_label.setVisible(show_candidate)
        self._min_candidate.setVisible(show_candidate)
        self._large_file_label.setVisible(show_large)
        self._large_file.setVisible(show_large)
        self._duplicate_file_label.setVisible(show_duplicate)
        self._duplicate_file.setVisible(show_duplicate)
        self._scan_depth_label.setVisible(show_depth)
        self._scan_depth_label.setText("展示深度:" if mode == "space" else "深度:")
        self._scan_depth.setVisible(show_depth)
        self._layout_mode_controls()
        cleanable = mode in {"cleanup", "large_files", "duplicates"}
        uninstallable = mode == "uninstall"
        self._select_all_btn.setVisible(cleanable)
        self._dry_run_btn.setVisible(cleanable or uninstallable)
        self._clean_btn.setVisible(cleanable or uninstallable)
        self._export_btn.setVisible(not uninstallable)
        self._dry_run_btn.setText("标准卸载" if uninstallable else "预演清理")
        self._clean_btn.setText("强力卸载" if uninstallable else "执行清理")
        if self._scan_worker is None or not self._scan_worker.isRunning():
            self._scan_btn.setText("刷新列表" if uninstallable else "扫描")
        if not uninstallable:
            self._refresh_result_type_options(mode)
        self._apply_result_filters()
        self._update_select_all_text()
        self._refresh_actions()
        self._refresh_mode_status()

    def _layout_mode_controls(self) -> None:
        controls_widget = self._controls_layout.parentWidget()
        if controls_widget is not None:
            controls_widget.setUpdatesEnabled(False)
        pairs = [
            (self._min_candidate_label, self._min_candidate),
            (self._large_file_label, self._large_file),
            (self._duplicate_file_label, self._duplicate_file),
            (self._scan_depth_label, self._scan_depth),
        ]
        legacy_filter_labels = (
            self._duplicate_keep_rule_label,
            self._large_unused_label,
            self._large_type_label,
            self._result_risk_label,
            self._result_type_label,
            self._result_min_size_label,
            self._result_mtime_label,
        )
        filter_widgets = (
            self._duplicate_hash_confirm,
            self._duplicate_keep_rule,
            self._large_unused_filter,
            self._large_type_filter,
            self._large_path_filter,
            self._result_risk_filter,
            self._result_type_filter,
            self._result_path_filter,
            self._result_min_size,
            self._result_max_size,
            self._result_mtime_filter,
            self._filter_confirm_btn,
            self._filter_safe_btn,
            self._filter_clear_btn,
            self._software_search,
        )
        try:
            for label, widget in pairs:
                self._adopt_drive_control_widget(label, controls_widget)
                self._adopt_drive_control_widget(widget, controls_widget)
            for widget in (*legacy_filter_labels, *filter_widgets):
                self._adopt_drive_control_widget(widget, controls_widget)
            for widget in (*legacy_filter_labels, *filter_widgets):
                self._close_drive_combo_popup(widget)
                self._controls_layout.removeWidget(widget)
            for label in legacy_filter_labels:
                label.setVisible(False)
            for widget in filter_widgets:
                widget.setVisible(False)
            if self._current_mode == "uninstall":
                self._controls_layout.addWidget(self._software_search, 1, 0, 1, 8)
                self._software_search.setVisible(True)
                return
            col = 0
            if self._current_mode == "duplicates":
                self._controls_layout.addWidget(self._duplicate_hash_confirm, 1, 0, 1, 2)
                self._duplicate_hash_confirm.setVisible(True)
                self._controls_layout.addWidget(self._duplicate_file_label, 1, 2)
                self._controls_layout.addWidget(self._duplicate_file, 1, 3)
                self._controls_layout.addWidget(self._scan_depth_label, 1, 4)
                self._controls_layout.addWidget(self._scan_depth, 1, 5)
                self._controls_layout.addWidget(self._duplicate_keep_rule_label, 1, 6)
                self._controls_layout.addWidget(self._duplicate_keep_rule, 1, 7)
                for widget in (
                    self._duplicate_file_label,
                    self._duplicate_file,
                    self._scan_depth_label,
                    self._scan_depth,
                    self._duplicate_keep_rule_label,
                    self._duplicate_keep_rule,
                ):
                    widget.setVisible(True)
                return
            for label, widget in pairs:
                self._close_drive_combo_popup(widget)
                self._controls_layout.removeWidget(label)
                self._controls_layout.removeWidget(widget)
                if (not label.isHidden()) and (not widget.isHidden()):
                    self._controls_layout.addWidget(label, 1, col)
                    self._controls_layout.addWidget(widget, 1, col + 1)
                    col += 2
        finally:
            if controls_widget is not None:
                controls_widget.setUpdatesEnabled(True)
                controls_widget.update()

    def _append_log(self, text: str) -> None:
        self._log.append(f"{time.strftime('%H:%M:%S')}  {text}")

    def _set_advisor_plain_text(self, text: str) -> None:
        self._advisor.setPlainText(str(text or "").strip())
        self._sync_advisor_float_text()

    def _set_advisor_markdown(self, text: str) -> None:
        content = str(text or "").strip()
        if not content:
            self._set_advisor_plain_text("模型未返回有效建议。")
            return
        self._advisor.setMarkdown(content)
        self._sync_advisor_float_text()

    def _sync_advisor_float_text(self) -> None:
        floating = getattr(self, "_advisor_float_text", None)
        source = getattr(self, "_advisor", None)
        if floating is None or source is None:
            return
        try:
            if source.document().isEmpty():
                floating.setPlainText("")
            else:
                floating.setHtml(source.toHtml())
            source_bar = source.verticalScrollBar()
            floating_bar = floating.verticalScrollBar()
            if source_bar is not None and floating_bar is not None:
                floating_bar.setValue(min(int(source_bar.value()), int(floating_bar.maximum())))
        except Exception:
            pass

    def _toggle_advisor_expanded(self) -> None:
        if bool(getattr(self, "_advisor_expanded", False)):
            self._collapse_advisor_panel()
        else:
            self._expand_advisor_panel()

    def _expand_advisor_panel(self) -> None:
        if bool(getattr(self, "_advisor_expanded", False)):
            self._position_advisor_floating_panel()
            return
        self._sync_advisor_float_text()
        self._advisor_expanded = True
        self._advisor_expand_btn.setChecked(True)
        self._advisor_float_expand_btn.setChecked(True)
        self._advisor_expand_btn.setToolTip("收起 AI 分析窗口")
        self._advisor_float_panel.show()
        self._position_advisor_floating_panel()
        self._advisor_float_panel.raise_()
        QTimer.singleShot(0, self._position_advisor_floating_panel)

    def _collapse_advisor_panel(self) -> None:
        if not bool(getattr(self, "_advisor_expanded", False)):
            return
        self._advisor_float_panel.hide()
        self._advisor_expanded = False
        self._advisor_expand_btn.setChecked(False)
        self._advisor_float_expand_btn.setChecked(False)
        self._advisor_expand_btn.setToolTip("展开 AI 分析窗口")

    def _position_advisor_floating_panel(self) -> None:
        if not bool(getattr(self, "_advisor_expanded", False)):
            return
        slot = getattr(self, "_advisor_slot", None)
        panel = getattr(self, "_advisor_float_panel", None)
        if slot is None or panel is None:
            return
        slot_pos = slot.mapTo(self, QPoint(0, 0))
        right = min(max(0, self.width() - 18), slot_pos.x() + slot.width())
        bottom = min(max(0, self.height() - 18), slot_pos.y() + slot.height())
        available_width = max(260, self.width() - 24)
        width = min(max(380, slot.width()), available_width)
        available_height = max(200, bottom - 20)
        desired_height = max(340, slot.height() + 220)
        height = min(desired_height, available_height)
        x = max(12, right - width)
        y = max(12, bottom - height)
        panel.setGeometry(x, y, width, height)
        panel.raise_()

    def _elapsed_text(self) -> str:
        if self._scan_started_at <= 0:
            return "0秒"
        elapsed = max(0, int(time.monotonic() - self._scan_started_at))
        minutes, seconds = divmod(elapsed, 60)
        if minutes:
            return f"{minutes}分{seconds:02d}秒"
        return f"{seconds}秒"

    def _set_mode_status(self, mode: str, text: str) -> None:
        mode_key = str(mode or self._current_mode or "cleanup")
        status = str(text or "").strip()
        self._mode_status_texts[mode_key] = status
        if mode_key == self._current_mode:
            self._status_label.setText(status or "就绪")

    def _refresh_mode_status(self) -> None:
        self._status_label.setText(self._mode_status_texts.get(self._current_mode, "") or "就绪")

    def _schedule_result_refresh(self, mode: str) -> None:
        self._pending_result_refresh_modes.add(str(mode or self._current_mode or "cleanup"))
        if not self._result_refresh_timer.isActive():
            self._result_refresh_timer.start(120)

    def _flush_result_refresh(self) -> None:
        modes = set(self._pending_result_refresh_modes)
        self._pending_result_refresh_modes.clear()
        if not modes:
            return
        for mode in modes:
            self._refresh_result_type_options(mode)
        if self._current_mode in modes:
            self._apply_result_filters()
            self._update_select_all_text()
            self._refresh_actions()

    def _mode_title(self, mode: str) -> str:
        for item_mode, title, *_ in self.MODES:
            if item_mode == mode:
                return title
        return str(mode or "")

    def _root_contains_system_scope(self, root: str) -> bool:
        value = str(root or "").strip()
        if not value:
            return False
        normalized = str(Path(value)).replace("/", "\\").rstrip("\\").lower()
        system_drive = self._norm_drive_root(self._system_drive_root())
        if self._norm_drive_root(value) == system_drive:
            return True
        system_root = str(Path(os.environ.get("SystemRoot", r"C:\Windows"))).replace("/", "\\").rstrip("\\").lower()
        drive = Path(value).drive
        sensitive_roots = [
            system_root,
            f"{drive}\\program files".lower() if drive else "",
            f"{drive}\\program files (x86)".lower() if drive else "",
            f"{drive}\\programdata".lower() if drive else "",
            f"{drive}\\windows".lower() if drive else "",
        ]
        return any(path and (normalized == path or normalized.startswith(path + "\\")) for path in sensitive_roots)

    def _path_requires_confirmation(self, path: str) -> bool:
        value = str(path or "").strip()
        if not value:
            return True
        normalized = str(Path(value)).replace("/", "\\").rstrip("\\").lower()
        drive = Path(value).drive
        user_profile = str(Path(os.environ.get("USERPROFILE", str(Path.home())))).replace("/", "\\").rstrip("\\").lower()
        sensitive_roots = [
            str(Path(os.environ.get("SystemRoot", r"C:\Windows"))).replace("/", "\\").rstrip("\\").lower(),
            f"{drive}\\program files".lower() if drive else "",
            f"{drive}\\program files (x86)".lower() if drive else "",
            f"{drive}\\programdata".lower() if drive else "",
            f"{drive}\\system volume information".lower() if drive else "",
            f"{user_profile}\\desktop",
            f"{user_profile}\\documents",
            f"{user_profile}\\downloads",
        ]
        return any(root and (normalized == root or normalized.startswith(root + "\\")) for root in sensitive_roots)

    def _large_file_requires_confirmation(self, path: str) -> bool:
        normalized = str(path or "").replace("/", "\\").lower()
        safe_parts = ("\\temp\\", "\\tmp\\", "\\cache\\", "\\caches\\")
        return not any(part in normalized for part in safe_parts)

    def _scan_file_type_text(self, mode: str) -> str:
        return {
            "cleanup": "缓存、临时文件、日志、回收站候选等",
            "space": "目录体积",
            "large_files": f"超过 {self._large_file.value()} MB 的大文件",
            "duplicates": f"超过 {self._duplicate_file.value()} MB 且可比对的普通文件；{'完整内容哈希确认' if self._duplicate_hash_confirm.isChecked() else '仅快速指纹预筛选'}",
            "uninstall": "已安装软件、安装目录、卸载命令与残留候选",
        }.get(mode, "当前模式可扫描项目")

    def _scan_threshold_text(self, mode: str) -> str:
        if mode == "uninstall":
            return "阈值: 不适用；扫描深度: 不适用"
        if mode == "space":
            return f"展示深度 {self._scan_depth.value()} 层；统计所有可访问文件；结果上限 5000 项"
        if mode == "large_files":
            return f"大文件 {self._large_file.value()} MB；全深度扫描；结果上限 1000 项"
        return (
            f"阈值: 候选 {self._min_candidate.value()} MB；"
            f"大文件 {self._large_file.value()} MB；"
            f"重复文件 {self._duplicate_file.value()} MB；"
            f"扫描深度 {self._scan_depth.value()} 层"
        )

    def _scan_scope_summary(self, mode: str, roots: list[str]) -> str:
        scope_text = "系统已安装软件列表" if mode == "uninstall" else "、".join(roots)
        includes_system = mode == "uninstall" or any(self._root_contains_system_scope(root) for root in roots)
        system_text = "包含系统目录，结果会标记高风险项" if includes_system else "不包含系统目录"
        return (
            f"本次扫描范围：扫描模式 {self._mode_title(mode)}；"
            f"扫描范围 {scope_text or '未选择'}；"
            f"文件类型 {self._scan_file_type_text(mode)}；"
            f"{system_text}；{self._scan_threshold_text(mode)}。"
        )

    def _scan_stage_text(self, mode: str, progress_message: str = "") -> str:
        text = str(progress_message or "")
        if mode == "cleanup":
            if "整理" in text or "发现" in text:
                return "正在整理可清理候选"
            if "临时" in text or "扫描" in text:
                return "正在分析临时文件"
            return "正在统计缓存"
        if mode == "space":
            if "整理" in text:
                return "正在整理目录列表"
            if "统计目录" in text or "正在分析目录" in text:
                return "正在统计目录体积"
            return "正在分析大目录"
        if mode == "large_files":
            if "发现" in text or "整理" in text:
                return "正在整理大文件列表"
            return "正在分析大文件"
        if mode == "duplicates":
            if "发现重复" in text or "整理" in text:
                return "正在整理重复文件组"
            if "哈希" in text or "比对" in text:
                return "正在比对重复文件"
            return "正在收集候选文件"
        if mode == "uninstall":
            if "补全" in text or "安装目录" in text or "大小信息" in text:
                return "正在补全安装目录与大小信息"
            return "正在读取软件列表"
        return "扫描中"

    def _update_scan_stage(self, mode: str, progress_message: str = "") -> None:
        if self._scan_cancel_requested:
            self._scan_stage = "正在取消，请稍候"
        else:
            self._scan_stage = self._scan_stage_text(mode, progress_message)
        self._set_mode_status(mode, f"{self._scan_stage} · 用时 {self._elapsed_text()}")

    def _on_scan_progress(self, mode: str, message: str) -> None:
        text = str(message or "")
        self._append_log(text)
        self._update_scan_stage(mode, text)

    def _scan_confirmation_count(self, mode: str, report: ScanReport) -> int:
        if mode == "cleanup":
            return sum(
                1
                for result in report.drives
                for candidate in result.candidates
                if str(candidate.risk) != SAFE or self._path_requires_confirmation(candidate.path)
            )
        if mode == "space":
            return sum(
                1
                for result in report.drives
                for entry in result.large_dirs
                if self._path_requires_confirmation(entry.path)
            )
        if mode == "large_files":
            return sum(
                1
                for result in report.drives
                for entry in result.large_files
                if self._large_file_requires_confirmation(entry.path)
            )
        if mode == "duplicates":
            return sum(1 for result in report.drives for group in result.duplicate_groups if len(group.paths) > 1)
        return 0

    def _scan_completion_summary(self, mode: str, report: ScanReport, *, cancelled: bool) -> str:
        prefix = "已取消" if cancelled else "扫描完成"
        confirm_count = self._scan_confirmation_count(mode, report)
        if mode == "cleanup":
            count = sum(len(result.candidates) for result in report.drives)
            size = sum(candidate.size_bytes for result in report.drives for candidate in result.candidates if candidate.risk != AVOID)
            detail = f"发现 {count} 项，预计可释放 {format_bytes(size)}，其中 {confirm_count} 项需确认"
        elif mode == "space":
            displayed = sum(len(result.large_dirs) for result in report.drives)
            total_dirs = sum(int(getattr(result, "space_total_dirs", 0) or 0) for result in report.drives)
            if total_dirs <= 0:
                total_dirs = displayed
            total_size = sum(int(getattr(result, "space_total_size_bytes", 0) or 0) for result in report.drives)
            scanned_files = sum(int(getattr(result, "space_scanned_files", 0) or 0) for result in report.drives)
            truncated = any(bool(getattr(result, "space_result_truncated", False)) for result in report.drives)
            max_size = max((entry.size_bytes for result in report.drives for entry in result.large_dirs), default=0)
            display_text = f"当前显示占用最大的 {displayed} 个" if truncated or displayed < total_dirs else f"当前显示 {displayed} 个"
            file_text = f"，扫描文件约 {scanned_files} 个" if scanned_files else ""
            detail = (
                f"共扫描 {total_dirs} 个目录，{display_text}；"
                f"可访问文件总大小 {format_bytes(total_size)}，最大目录占用 {format_bytes(max_size)}，需确认目录 {confirm_count} 个{file_text}"
            )
        elif mode == "large_files":
            count = sum(len(result.large_files) for result in report.drives)
            size = sum(entry.size_bytes for result in report.drives for entry in result.large_files)
            detail = f"发现 {count} 个大文件，合计占用 {format_bytes(size)}，其中 {confirm_count} 个需确认"
        else:
            count = sum(len(result.duplicate_groups) for result in report.drives)
            size = sum(group.size_bytes * max(0, len(group.paths) - 1) for result in report.drives for group in result.duplicate_groups)
            detail = f"发现 {count} 组重复文件，理论可释放 {format_bytes(size)}，其中 {confirm_count} 组需确认"
        return f"{prefix}，用时 {self._elapsed_text()}。\n{detail}。"

    def _update_scan_elapsed_status(self) -> None:
        if self._scan_worker is not None and self._scan_worker.isRunning():
            if self._scan_cancel_requested:
                self._scan_stage = "正在取消，请稍候"
            mode = self._active_scan_mode or self._current_mode
            stage = self._scan_stage or self._scan_stage_text(mode)
            self._set_mode_status(mode, f"{stage} · 用时 {self._elapsed_text()}")

    def _report_stats(self, mode: str, report: ScanReport) -> str:
        if mode == "cleanup":
            count = sum(len(result.candidates) for result in report.drives)
            size = sum(candidate.size_bytes for result in report.drives for candidate in result.candidates if candidate.risk != AVOID)
            return f"候选 {count} 项，占用约 {format_bytes(size)}"
        if mode == "space":
            displayed = sum(len(result.large_dirs) for result in report.drives)
            total_dirs = sum(int(getattr(result, "space_total_dirs", 0) or 0) for result in report.drives)
            if total_dirs <= 0:
                total_dirs = displayed
            total_size = sum(int(getattr(result, "space_total_size_bytes", 0) or 0) for result in report.drives)
            return f"目录 {displayed}/{total_dirs} 项，总大小 {format_bytes(total_size)}"
        if mode == "large_files":
            count = sum(len(result.large_files) for result in report.drives)
            size = sum(entry.size_bytes for result in report.drives for entry in result.large_files)
            return f"大文件 {count} 个，合计 {format_bytes(size)}"
        count = sum(len(result.duplicate_groups) for result in report.drives)
        size = sum(group.size_bytes * max(0, len(group.paths) - 1) for result in report.drives for group in result.duplicate_groups)
        return f"重复组 {count} 组，理论可释放 {format_bytes(size)}"

    def _set_busy(self, busy: bool) -> None:
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("取消扫描" if busy else ("刷新列表" if self._current_mode == "uninstall" else "扫描"))
        if busy:
            self._progress.setRange(0, 0)
        self._progress.setVisible(busy)
        if not busy:
            self._elapsed_timer.stop()
        self._refresh_actions()

    def _set_execution_busy(self, busy: bool, *, operation: str = "", total: int = 0) -> None:
        if busy:
            self._execution_cancel_requested = False
            self._execution_operation = str(operation or "执行")
            self._active_execution_mode = self._current_mode
            self._execution_failures = []
            self._last_failure_report = ""
            self._copy_failures_btn.setVisible(False)
            self._open_recycle_bin_btn.setVisible(False)
            self._scan_btn.setEnabled(True)
            self._scan_btn.setText("取消执行")
            self._progress.setVisible(True)
            self._progress.setRange(0, max(1, int(total or 1)))
            self._progress.setValue(0)
        else:
            self._progress.setVisible(False)
            self._progress.setRange(0, 0)
            self._scan_btn.setText("刷新列表" if self._current_mode == "uninstall" else "扫描")
        self._refresh_actions()

    def _delete_mode_label(self, delete_mode: str) -> str:
        return {
            "recycle": "回收站",
            "permanent": "永久删除",
            "uninstall": "卸载",
            "move": "移动",
        }.get(str(delete_mode or ""), str(delete_mode or "未知"))

    def _begin_cleanup_record(
        self,
        *,
        mode: str,
        item_count: int,
        estimated_size: int,
        paths: list[str],
        delete_mode: str,
    ) -> None:
        self._pending_cleanup_record = {
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": str(mode or ""),
            "item_count": int(item_count or 0),
            "estimated_size": int(estimated_size or 0),
            "actual_size": 0,
            "paths": [str(path) for path in paths],
            "delete_mode": self._delete_mode_label(delete_mode),
            "failures": [],
            "status": "执行中",
        }

    def _finish_cleanup_record(self, *, actual_size: int, failures: list[dict[str, str]], status: str) -> None:
        record = self._pending_cleanup_record
        if record is None:
            return
        record["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        record["actual_size"] = int(actual_size or 0)
        record["failures"] = [dict(row) for row in failures]
        record["status"] = str(status or "")
        self._cleanup_records.insert(0, record)
        self._cleanup_records = self._cleanup_records[:20]
        self._pending_cleanup_record = None

    def _show_cleanup_records(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("最近清理记录")
        self._apply_dialog_caption_color(dialog)
        dialog.setMinimumSize(680, 460)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        text = QTextEdit()
        text.setObjectName("DriveCleanerRecordsText")
        text.setReadOnly(True)
        text.setAcceptRichText(False)
        text.setStyleSheet(
            "QTextEdit#DriveCleanerRecordsText {"
            " background: #ffffff;"
            " border: none;"
            " border-radius: 8px;"
            " padding: 8px;"
            " color: #111827;"
            "}"
        )
        if not self._cleanup_records:
            text.setPlainText("暂无清理记录。")
        else:
            sections: list[str] = []
            for index, record in enumerate(self._cleanup_records, start=1):
                failures = list(record.get("failures", []) or [])
                paths = list(record.get("paths", []) or [])
                lines = [
                    f"#{index} {record.get('mode', '')}",
                    f"清理时间：{record.get('started_at', '')}",
                    f"完成时间：{record.get('finished_at', '未完成')}",
                    f"状态：{record.get('status', '')}",
                    f"项目数量：{record.get('item_count', 0)}",
                    f"预计释放：{format_bytes(int(record.get('estimated_size', 0) or 0))}",
                    f"实际释放：{format_bytes(int(record.get('actual_size', 0) or 0))}",
                    f"失败项数：{len(failures)}",
                    f"删除方式：{record.get('delete_mode', '')}",
                    "路径预览：",
                ]
                if paths:
                    lines.extend(f"- {path}" for path in paths[:12])
                    if len(paths) > 12:
                        lines.append(f"... 还有 {len(paths) - 12} 项")
                else:
                    lines.append("- 无")
                lines.append(f"失败项（{len(failures)}）：")
                if failures:
                    lines.extend(
                        f"- [{row.get('category', '未知错误')}] {row.get('operation', '')}: {row.get('path', '')}；{row.get('reason', '')}"
                        for row in failures
                    )
                else:
                    lines.append("- 无")
                sections.append("\n".join(lines))
            text.setPlainText("\n\n".join(sections))
        layout.addWidget(text, 1)
        SettingsDialog._install_custom_text_context_menus(dialog, dialog)
        dialog.exec()

    def _open_recycle_bin(self) -> None:
        if not QDesktopServices.openUrl(QUrl("shell:RecycleBinFolder")):
            self._show_resource_status("打开回收站失败，请从系统桌面打开。", tone="error", auto_hide_ms=4200)

    def _cancel_execution(self) -> None:
        if self._cleanup_worker is None or not self._cleanup_worker.isRunning():
            return
        self._execution_cancel_requested = True
        self._cleanup_worker.cancel()
        mode = self._active_execution_mode or self._current_mode
        self._set_mode_status(mode, "正在取消执行，请稍候...")
        self._append_log("正在取消当前执行任务，请稍候...")

    def _short_path_text(self, path: str, limit: int = 88) -> str:
        text = str(path or "").strip()
        if len(text) <= limit:
            return text
        return "..." + text[-max(12, limit - 3):]

    def _on_execution_progress(self, processed: int, total: int, path: str, released: int) -> None:
        total = max(1, int(total or 1))
        processed = max(0, min(int(processed or 0), total))
        self._progress.setVisible(True)
        self._progress.setRange(0, total)
        self._progress.setValue(processed)
        operation = self._execution_operation or "执行"
        path_text = self._short_path_text(path)
        released_text = format_bytes(int(released or 0))
        mode = self._active_execution_mode or self._current_mode
        if self._execution_cancel_requested:
            self._set_mode_status(mode, f"正在取消执行，已处理 {processed}/{total}，已释放 {released_text}")
        else:
            self._set_mode_status(mode, f"正在{operation} {processed}/{total}：{path_text} · 已释放 {released_text}")

    def _on_execution_detail_progress(
        self,
        processed: int,
        total: int,
        candidate_path: str,
        detail_processed: int,
        detail_total: int,
        detail_path: str,
    ) -> None:
        mode = self._active_execution_mode or self._current_mode
        operation = self._execution_operation or "清理"
        candidate_text = self._short_path_text(candidate_path, 66)
        detail_text = self._short_path_text(detail_path, 58)
        self._set_mode_status(
            mode,
            f"正在{operation} {processed + 1}/{max(1, total)}：{candidate_text} · 内部 {detail_processed}/{max(1, detail_total)}：{detail_text}",
        )

    def _failure_reason_category(self, reason: str) -> str:
        text = str(reason or "")
        lower = text.lower()
        if "permission" in lower or "access denied" in lower or "拒绝访问" in text or "权限" in text:
            return "权限不足"
        if ("uninstall" in lower and ("not found" in lower or "不存在" in text or "missing" in lower)) or "卸载器不存在" in text:
            return "卸载器不存在"
        if "used by another process" in lower or "being used" in lower or "占用" in text:
            return "文件被占用"
        if "错误码 32" in text:
            return "文件被占用"
        if "错误码 120" in text or "访问被拒绝" in text:
            return "权限不足"
        if "错误码 124" in text or "无法处理该路径" in text or "特殊目录结构" in text:
            return "路径无效或正在变化"
        if "进程" in text and ("占用" in text or "正在运行" in text):
            return "进程占用"
        if "注册表" in text and ("失败" in text or "权限" in text or "拦截" in text):
            return "注册表访问失败"
        if "not found" in lower or "不存在" in text or "missing" in lower:
            return "路径不存在"
        if "directory not empty" in lower or "非空" in text:
            return "非空目录"
        if "protected" in lower or "受保护" in text or "系统" in text:
            return "系统保护"
        return "未知错误"

    def _on_execution_item_failed(self, path: str, operation: str, reason: str) -> None:
        category = self._failure_reason_category(reason)
        row = {
            "path": str(path or ""),
            "operation": str(operation or self._execution_operation or "执行"),
            "reason": str(reason or category),
            "category": category,
        }
        self._execution_failures.append(row)
        self._append_log(f"失败项: [{category}] {row['operation']}  {row['path']}  {row['reason']}")

    def _show_resource_status(self, text: str, *, tone: str = "info", auto_hide_ms: int = 3200) -> None:
        label = getattr(self, "_drive_toast_label", None)
        if label is None:
            label = QLabel(self)
            label.setObjectName("DriveCleanerToast")
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            label.setStyleSheet(
                "QLabel#DriveCleanerToast {"
                " background: rgba(15, 23, 42, 232); color: white;"
                " border-radius: 9px; padding: 8px 14px; font-size: 12px; font-weight: 800;"
                "}"
            )
            self._drive_toast_label = label
        label.setText(str(text or ""))
        label.adjustSize()
        x = max(12, int((self.width() - label.width()) / 2))
        y = max(12, self.height() - label.height() - 24)
        label.move(x, y)
        label.show()
        label.raise_()
        if int(auto_hide_ms or 0) > 0:
            token = int(label.property("toastToken") or 0) + 1
            label.setProperty("toastToken", token)
            QTimer.singleShot(
                int(auto_hide_ms),
                lambda target=label, current=token: target.hide()
                if int(target.property("toastToken") or 0) == current
                else None,
            )

    def _cleanup_operation_title(self, report: CleanupReport, *, mode: str = "") -> str:
        if report.dry_run:
            return "预演"
        mode_key = str(mode or self._current_mode)
        if mode_key == "large_files":
            return "删除大文件"
        if mode_key == "duplicates":
            return "清理重复文件"
        return "清理"

    def _summarize_cleanup_result(self, report: CleanupReport, *, mode: str = "") -> str:
        freed = sum(int(item.freed_size or 0) for item in report.items)
        success_count = sum(1 for item in report.items if item.status in {"Success", "Partial", "DryRun"})
        failed_count = len(report.items) - success_count
        delete_modes = {str(getattr(item, "delete_mode", "") or "") for item in report.items}
        recycle_text = "，已移入回收站，可前往回收站恢复" if (not report.dry_run and "recycle" in delete_modes) else ""
        irreversible_text = "，不可撤销" if (not report.dry_run and "permanent" in delete_modes and "recycle" not in delete_modes) else ""
        if self._execution_cancel_requested:
            return f"已取消，已处理 {len(report.items)} 项，已释放 {format_bytes(freed)}{recycle_text}{irreversible_text}。"
        if report.dry_run:
            return f"预演完成，已检查 {len(report.items)} 项，{failed_count} 项失败。"
        if any(str(getattr(item, "mode", "") or "") == "move" for item in report.items):
            target = self._large_move_target or "目标目录"
            if failed_count:
                return f"已移动 {success_count} 个文件到 {target}，{failed_count} 项失败，释放源位置 {format_bytes(freed)}。"
            return f"已移动 {success_count} 个文件到 {target}，释放源位置 {format_bytes(freed)}。"
        mode_key = str(mode or self._current_mode)
        if mode_key == "large_files":
            subject = f"已删除 {success_count} 个大文件"
        elif mode_key == "duplicates":
            group_count = len(self._last_cleanup_remove_ids) or success_count
            subject = f"已清理 {group_count} 组重复文件"
        else:
            subject = f"已清理 {success_count} 项"
        if failed_count:
            return f"{subject}，{failed_count} 项失败，释放 {format_bytes(freed)}{recycle_text}{irreversible_text}。"
        return f"{subject}，释放 {format_bytes(freed)}{recycle_text}{irreversible_text}。"

    def _summarize_uninstall_result(self, report: SoftwareUninstallReport) -> str:
        success_count = sum(1 for item in report.items if item.status in {"Started", "Success", "Partial"})
        failed_count = len(report.items) - success_count
        if self._execution_cancel_requested:
            return f"已取消，已处理 {len(report.items)} 项。"
        action = "强力卸载" if report.mode == "force" else "标准卸载"
        irreversible_text = "，不可撤销" if report.mode == "force" else ""
        if failed_count:
            return f"{action}已处理 {success_count} 项，{failed_count} 项失败{irreversible_text}。"
        return f"{action}已处理 {success_count} 项{irreversible_text}。"

    def _append_failure_report_to_log(self) -> None:
        if not self._execution_failures:
            return
        self._append_log("失败项列表：")
        for row in self._execution_failures:
            self._append_log(
                f"- [{row.get('category', '未知错误')}] {row.get('operation', '')}: "
                f"{row.get('path', '')}；原因：{row.get('reason', '')}"
            )

    def _build_failure_report(
        self,
        *,
        mode: str,
        started_at: str,
        success_count: int,
        failed_count: int,
        released_size: int = 0,
    ) -> str:
        lines = [
            "磁盘清理失败报告",
            f"执行时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"模式: {mode}",
            f"开始时间: {started_at}",
            f"成功数: {success_count}",
            f"失败数: {failed_count}",
            f"释放空间: {format_bytes(int(released_size or 0))}",
            "",
            "失败明细:",
        ]
        if not self._execution_failures:
            lines.append("- 无")
        for row in self._execution_failures:
            lines.append(
                f"- 路径: {row.get('path', '')}\n"
                f"  操作: {row.get('operation', '')}\n"
                f"  归类: {row.get('category', '')}\n"
                f"  原因: {row.get('reason', '')}"
            )
        return "\n".join(lines).rstrip()

    def _copy_failure_report(self) -> None:
        if not self._last_failure_report:
            self._show_resource_status("当前没有可复制的失败报告。", tone="info")
            return
        try:
            QApplication.clipboard().setText(self._last_failure_report)
        except Exception as exc:
            self._show_resource_status(f"复制失败报告失败：{exc}", tone="error", auto_hide_ms=5200)
            return
        self._show_resource_status("失败报告已复制。", tone="success", auto_hide_ms=1800)

    def _start_scan(self) -> None:
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._stop_scan()
            return
        if self._cleanup_worker is not None and self._cleanup_worker.isRunning():
            self._cancel_execution()
            return
        if self._current_mode == "uninstall":
            self._start_software_scan()
            return
        mode = self._current_mode
        roots = self._selected_roots()
        if not roots:
            self._show_resource_status("请至少选择一个可扫描盘符。", tone="warning")
            return
        self._scan_cancel_requested = False
        self._active_scan_mode = mode
        self._scan_started_at = time.monotonic()
        self._elapsed_timer.start()
        self._tables[mode].setRowCount(0)
        self._log.clear()
        self._set_advisor_plain_text("")
        self._copy_failures_btn.setVisible(False)
        self._last_failure_report = ""
        summary = self._scan_scope_summary(mode, roots)
        self._append_log(summary)
        self._append_log(f"开始{self._title_label.text()}，范围: {', '.join(roots)}")
        self._start_model_probe()
        self._scan_stage = self._scan_stage_text(mode)
        self._set_mode_status(mode, f"{self._scan_stage} · 用时 0秒")
        self._set_busy(True)
        worker = _DriveCleanerScanWorker(
            roots,
            min_candidate_size_mb=int(self._min_candidate.value()),
            min_large_file_size_mb=int(self._large_file.value()),
            min_duplicate_size_mb=int(self._duplicate_file.value()),
            max_depth=int(self._scan_depth.value()),
            scan_modes=[mode],
            confirm_duplicate_content_hash=bool(self._duplicate_hash_confirm.isChecked()),
        )
        self._scan_worker = worker
        worker.progress.connect(lambda msg, mode=mode: self._on_scan_progress(mode, str(msg)))
        worker.item_found.connect(lambda kind, item, mode=mode: self._on_stream_item(mode, kind, item))
        worker.finished_ok.connect(lambda report, mode=mode: self._on_scan_finished(mode, report))
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _start_software_scan(self) -> None:
        self._scan_cancel_requested = False
        self._active_scan_mode = "uninstall"
        self._scan_started_at = time.monotonic()
        self._elapsed_timer.start()
        self._tables["uninstall"].setRowCount(0)
        self._software_entries = []
        self._software_list_loaded = False
        self._log.clear()
        self._set_advisor_plain_text("")
        self._copy_failures_btn.setVisible(False)
        self._last_failure_report = ""
        self._append_log(self._scan_scope_summary("uninstall", []))
        self._append_log("开始扫描已安装软件列表")
        self._start_model_probe()
        self._scan_stage = self._scan_stage_text("uninstall")
        self._set_mode_status("uninstall", f"{self._scan_stage} · 用时 0秒")
        self._set_busy(True)
        worker = _SoftwareUninstallScanWorker()
        self._scan_worker = worker
        worker.progress.connect(lambda msg: self._on_scan_progress("uninstall", str(msg)))
        worker.finished_ok.connect(self._on_software_scan_finished)
        worker.entry_enriched.connect(self._on_software_entry_enriched)
        worker.enrich_done.connect(self._on_software_enrich_done)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _stop_scan(self) -> None:
        if self._scan_worker is not None and self._scan_worker.isRunning():
            mode = self._active_scan_mode or self._current_mode
            self._scan_cancel_requested = True
            self._scan_worker.cancel()
            self._scan_stage = "正在取消，请稍候"
            self._set_mode_status(mode, f"{self._scan_stage} · 用时 {self._elapsed_text()}")
            self._append_log("正在取消扫描，请稍候...")

    def _on_scan_finished(self, mode: str, report: object) -> None:
        self._scan_worker = None
        self._set_busy(False)
        if not isinstance(report, ScanReport):
            self._set_mode_status(mode, "扫描结果异常")
            self._active_scan_mode = ""
            return
        self._reports[mode] = report
        self._populate_table(mode, report)
        summary = self._scan_completion_summary(mode, report, cancelled=self._scan_cancel_requested)
        self._set_mode_status(mode, summary)
        self._append_log(summary.replace("\n", " "))
        self._append_log(report.advisor_summary)
        self._start_llm_advice(report)
        self._export_btn.setEnabled(True)
        self._active_scan_mode = ""

    def _on_software_scan_finished(self, entries: object) -> None:
        """Phase 1 完成：快速渲染表格，后台继续补全。"""
        if not isinstance(entries, list):
            self._scan_worker = None
            self._set_busy(False)
            self._set_mode_status("uninstall", "扫描结果异常")
            self._active_scan_mode = ""
            return
        self._software_list_loaded = True
        self._software_entries = [entry for entry in entries if isinstance(entry, SoftwareEntry)]
        self._populate_software_table(self._software_entries)
        user_count = sum(1 for entry in self._software_entries if not entry.is_system)
        system_count = sum(1 for entry in self._software_entries if entry.is_system)
        self._scan_stage = "正在补全安装目录与大小信息"
        self._set_mode_status(
            "uninstall",
            f"列表加载完成，用时 {self._elapsed_text()}，用户软件 {user_count} 个，系统软件 {system_count} 个，正在补全详情...",
        )
        self._append_log(f"发现用户软件 {user_count} 个，系统软件 {system_count} 个。正在后台补全安装目录与大小信息...")
        self._start_software_llm_advice(self._software_entries)
        self._export_btn.setEnabled(False)
        # 注意：worker 仍在运行第二阶段补全，不要清理 _scan_worker

    def _on_software_entry_enriched(self, index: int, enriched_entry: object) -> None:
        """后台补全回调：更新单行的安装目录、数据目录、大小。"""
        if not isinstance(enriched_entry, SoftwareEntry):
            return
        if 0 <= index < len(self._software_entries):
            self._software_entries[index] = enriched_entry
        table = self._tables.get("uninstall")
        if table is None:
            return
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == enriched_entry.id:
                table.setItem(row, 3, self._size_item(enriched_entry.size_bytes))
                table.setItem(row, 5, self._multi_path_item(enriched_entry.install_location) if enriched_entry.install_location else self._text_item("未发现安装目录"))
                table.setItem(row, 6, self._multi_path_item(enriched_entry.data_location) if enriched_entry.data_location else self._text_item("未发现数据目录"))
                self._set_software_action_widget(table, row, enriched_entry)
                break

    def _on_software_enrich_done(self) -> None:
        """后台补全全部完成。"""
        self._scan_worker = None
        self._set_busy(False)
        before_count = len(self._software_entries)
        self._software_entries = [entry for entry in self._software_entries if software_entry_should_display(entry)]
        if len(self._software_entries) != before_count:
            self._populate_software_table(self._software_entries)
        user_count = sum(1 for entry in self._software_entries if not entry.is_system)
        system_count = sum(1 for entry in self._software_entries if entry.is_system)
        prefix = "已取消" if self._scan_cancel_requested else "扫描完成"
        summary = f"{prefix}，用时 {self._elapsed_text()}。用户软件 {user_count} 个，系统软件 {system_count} 个。"
        self._set_mode_status("uninstall", summary)
        self._append_log(summary)
        self._append_log("目录与大小信息补全完成。")
        self._active_scan_mode = ""

    def _on_scan_failed(self, message: str) -> None:
        mode = self._active_scan_mode or self._current_mode
        self._scan_worker = None
        self._set_busy(False)
        self._set_mode_status(mode, f"扫描失败，用时 {self._elapsed_text()}")
        self._scan_stage = ""
        self._active_scan_mode = ""
        self._append_log(f"扫描失败: {message}")

    def _risk_label(self, risk: str) -> str:
        return {
            SAFE: "安全",
            CONFIRM_REQUIRED: "需确认",
            AVOID: "不建议",
        }.get(str(risk or ""), str(risk or ""))

    def _candidate_selectable(self, candidate: object) -> bool:
        risk = str(getattr(candidate, "risk", ""))
        mode = str(getattr(candidate, "cleanup_mode", ""))
        if risk == SAFE:
            return bool(getattr(candidate, "allowed_by_cleaner", False))
        if risk == CONFIRM_REQUIRED:
            return mode in {CONTENTS, SELF} and str(getattr(candidate, "kind", "")) not in {"backup"}
        return False

    def _text_item(self, text: str, *, sort_value: object = None) -> QTableWidgetItem:
        return _DriveCleanerTableItem(str(text or ""), sort_value=sort_value)

    def _size_item(self, size: int) -> QTableWidgetItem:
        return _DriveCleanerTableItem(format_bytes(int(size or 0)), sort_value=int(size or 0))

    def _path_item(self, path: str, text: Optional[str] = None) -> QTableWidgetItem:
        value = str(path or "")
        item = _DriveCleanerTableItem(str(text if text is not None else value), sort_value=value.lower(), path=value)
        item.setForeground(QBrush(QColor("#2563eb")))
        item.setToolTip("点击打开路径")
        return item

    def _multi_path_item(self, paths_text: str) -> QTableWidgetItem:
        text = str(paths_text or "")
        first_path = self._first_path_segment(text)
        return self._path_item(first_path, text) if first_path else self._text_item(text)

    def _first_path_segment(self, paths_text: str) -> str:
        for part in str(paths_text or "").split(" | "):
            value = part.strip()
            if value:
                return value
        return ""

    def _item_opens_path(self, item: Optional[QTableWidgetItem]) -> bool:
        if item is None:
            return False
        if item.column() == 0 and bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return False
        return bool(str(getattr(item, "_path", "") or "").strip())

    def _open_path_from_table_item(self, item: QTableWidgetItem) -> None:
        if not self._item_opens_path(item):
            return
        path = str(getattr(item, "_path", "") or "").strip()
        target = Path(path)
        folder = target if target.is_dir() else target.parent
        if not str(folder):
            return
        if not folder.exists():
            self._append_log(f"路径不存在: {path}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _timestamp_from_text(self, text: str) -> float:
        value = str(text or "").strip()
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(value).timestamp()
        except Exception:
            try:
                return time.mktime(time.strptime(value[:19], "%Y-%m-%d %H:%M:%S"))
            except Exception:
                return 0.0

    def _format_timestamp_text(self, value: object) -> str:
        if isinstance(value, (int, float)):
            timestamp = float(value or 0)
        else:
            timestamp = self._timestamp_from_text(str(value or ""))
        if timestamp <= 0:
            return "未知"
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
        except Exception:
            return "未知"

    def _last_write_text(self, item: object) -> str:
        return self._format_timestamp_text(str(getattr(item, "last_write_time", "") or ""))

    def _set_result_row_meta(
        self,
        table: QTableWidget,
        row: int,
        *,
        path: str,
        size: int,
        risk: str,
        mtime: float,
        type_label: str,
    ) -> None:
        item = table.item(row, 0)
        if item is None:
            item = QTableWidgetItem("")
            table.setItem(row, 0, item)
        item.setData(self._RESULT_ROLE_PATH, str(path or ""))
        item.setData(self._RESULT_ROLE_SIZE, int(size or 0))
        item.setData(self._RESULT_ROLE_RISK, str(risk or ""))
        item.setData(self._RESULT_ROLE_MTIME, float(mtime or 0.0))
        item.setData(self._RESULT_ROLE_TYPE, str(type_label or ""))

    def _result_row_meta(self, table: QTableWidget, row: int) -> dict[str, object]:
        item = table.item(row, 0)
        if item is None:
            return {"path": "", "size": 0, "risk": "", "mtime": 0.0, "type": ""}
        return {
            "path": str(item.data(self._RESULT_ROLE_PATH) or ""),
            "size": int(item.data(self._RESULT_ROLE_SIZE) or 0),
            "risk": str(item.data(self._RESULT_ROLE_RISK) or ""),
            "mtime": float(item.data(self._RESULT_ROLE_MTIME) or 0.0),
            "type": str(item.data(self._RESULT_ROLE_TYPE) or ""),
        }

    def _duplicate_group_mtime(self, group: object) -> float:
        latest = 0.0
        for path in list(getattr(group, "paths", []) or []):
            try:
                latest = max(latest, float(Path(str(path)).stat().st_mtime))
            except OSError:
                continue
        return latest

    def _duplicate_group_type_label(self, group: object) -> str:
        suffixes = {Path(str(path)).suffix.lower() for path in list(getattr(group, "paths", []) or []) if Path(str(path)).suffix}
        if not suffixes:
            return "重复文件"
        if len(suffixes) == 1:
            suffix = next(iter(suffixes))
            if suffix in {".exe", ".msi", ".dmg", ".pkg"}:
                return "安装包"
            if suffix in {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}:
                return "压缩包"
            if suffix in {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".ts", ".m4v"}:
                return "视频"
            if suffix == ".log":
                return "日志"
            return suffix
        return "多类型重复"

    def _refresh_result_type_options(self, mode: Optional[str] = None) -> None:
        if not hasattr(self, "_result_type_filter"):
            return
        table = self._tables.get(mode or self._current_mode)
        if table is None:
            return
        current = str(self._result_type_filter.currentData() or "")
        values: list[str] = []
        for row in range(table.rowCount()):
            type_label = str(self._result_row_meta(table, row).get("type", "") or "").strip()
            if type_label and type_label not in values:
                values.append(type_label)
        self._result_type_filter.blockSignals(True)
        self._result_type_filter.clear()
        self._result_type_filter.addItem("全部", "")
        for value in sorted(values, key=lambda item: item.lower()):
            self._result_type_filter.addItem(value, value)
        index = self._result_type_filter.findData(current)
        self._result_type_filter.setCurrentIndex(index if index >= 0 else 0)
        self._result_type_filter.blockSignals(False)

    def _set_candidate_row(self, table: QTableWidget, row: int, candidate: object) -> None:
        check_item = QTableWidgetItem("")
        check_item.setData(Qt.ItemDataRole.UserRole, getattr(candidate, "id", ""))
        check_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if self._candidate_selectable(candidate):
            check_item.setFlags(check_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check_item.setCheckState(Qt.CheckState.Unchecked)
        else:
            check_item.setFlags(check_item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        table.setItem(row, 0, check_item)
        risk_label = self._candidate_risk_info(candidate)[0]
        type_label = str(getattr(candidate, "kind", "") or "")
        size = int(getattr(candidate, "size_bytes", 0) or 0)
        path = str(getattr(candidate, "path", "") or "")
        table.setItem(row, 1, self._text_item(risk_label))
        table.setItem(row, 2, self._text_item(type_label))
        mtime = self._timestamp_from_text(str(getattr(candidate, "last_write_time", "") or ""))
        table.setItem(row, 3, self._size_item(size))
        table.setItem(row, 4, self._text_item(self._format_timestamp_text(mtime), sort_value=mtime))
        table.setItem(row, 5, self._path_item(path))
        reason = str(getattr(candidate, "reason", "") or "命中本地清理规则")
        rule = str(getattr(candidate, "source_rule", "") or "")
        detail = f"为什么建议清理：{reason}" + (f"\n规则来源：{rule}" if rule else "")
        item = self._text_item(detail)
        item.setToolTip(detail)
        table.setItem(row, 6, item)
        self._set_result_row_meta(
            table,
            row,
            path=path,
            size=size,
            risk=risk_label,
            mtime=mtime,
            type_label=type_label,
        )

    def _set_space_dir_row(self, table: QTableWidget, row: int, entry: object) -> None:
        path = str(getattr(entry, "path", "") or "")
        risk_label = self._path_risk_info(path)[0]
        size = int(getattr(entry, "size_bytes", 0) or 0)
        mtime = self._timestamp_from_text(str(getattr(entry, "last_write_time", "") or ""))
        description = str(getattr(entry, "type_description", "") or "").strip()
        is_estimated = any(token in description for token in ("估算", "部分统计"))
        size_text = f"约 {format_bytes(size)}" if is_estimated else format_bytes(size)
        reason = description or "为什么展示：该目录占用空间较大。空间分析仅展示体积；删除前请进入路径确认内容。"
        table.setItem(row, 0, self._text_item(risk_label))
        table.setItem(row, 1, self._path_item(path, Path(path).name or path))
        table.setItem(row, 2, self._text_item(size_text, sort_value=size))
        table.setItem(row, 3, self._text_item(self._format_timestamp_text(mtime), sort_value=mtime))
        table.setItem(row, 4, self._path_item(path))
        reason_item = self._text_item(reason)
        reason_item.setToolTip(reason)
        table.setItem(row, 5, reason_item)
        self._set_result_row_meta(
            table,
            row,
            path=path,
            size=size,
            risk=risk_label,
            mtime=mtime,
            type_label="目录",
        )

    def _large_ignore_file_path(self) -> Path:
        return Path(get_app_dir()) / "drive_cleaner_large_ignore.json"

    def _load_large_ignore_rules(self) -> None:
        path = self._large_ignore_file_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            data = {}
        self._large_ignore = {
            "paths": [row for row in list(data.get("paths", []) or []) if isinstance(row, dict)],
            "dirs": [row for row in list(data.get("dirs", []) or []) if isinstance(row, dict)],
            "extensions": [row for row in list(data.get("extensions", []) or []) if isinstance(row, dict)],
            "keywords": [row for row in list(data.get("keywords", []) or []) if isinstance(row, dict)],
        }

    def _save_large_ignore_rules(self) -> None:
        try:
            path = self._large_ignore_file_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._large_ignore, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self._show_resource_status(f"保存忽略列表失败：{exc}", tone="error", auto_hide_ms=5200)

    def _refresh_current_drive_table(self) -> None:
        if self._current_mode == "large_files":
            self._filter_large_files_table()
            return
        report = self._reports.get(self._current_mode)
        if report is not None and self._current_mode in {"cleanup", "space", "duplicates"}:
            self._populate_table(self._current_mode, report)

    def _large_ignore_values(self, key: str) -> list[str]:
        return [str(row.get("value", "") or "") for row in list(self._large_ignore.get(key, []) or []) if str(row.get("value", "") or "").strip()]

    def _is_drive_path_ignored(self, path: str) -> bool:
        normalized = self._normalized_risk_path(path)
        suffix = Path(path).suffix.lower()
        if normalized in {self._normalized_risk_path(value) for value in self._large_ignore_values("paths")}:
            return True
        for directory in self._large_ignore_values("dirs"):
            root = self._normalized_risk_path(directory)
            if root and (normalized == root or normalized.startswith(root + "\\")):
                return True
        if suffix and suffix in {value.lower() for value in self._large_ignore_values("extensions")}:
            return True
        name = Path(str(path or "")).name.lower()
        return any(keyword.lower() in name for keyword in self._large_ignore_values("keywords"))

    def _is_large_file_ignored(self, path: str) -> bool:
        return self._is_drive_path_ignored(path)

    def _is_duplicate_group_ignored(self, group: object) -> bool:
        paths = [str(path) for path in list(getattr(group, "paths", []) or [])]
        return bool(paths) and any(self._is_drive_path_ignored(path) for path in paths)

    def _add_large_ignore_rule(self, key: str, value: str, source: str = "大文件") -> None:
        text = str(value or "").strip()
        if not text:
            return
        if key == "extensions" and not text.startswith("."):
            text = "." + text
        values = {str(row.get("value", "") or "").lower() for row in self._large_ignore.get(key, [])}
        if text.lower() not in values:
            self._large_ignore.setdefault(key, []).append({
                "value": text,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": source,
            })
            self._save_large_ignore_rules()
        self._refresh_current_drive_table()
        self._show_resource_status("已加入忽略规则。", tone="success", auto_hide_ms=1800)

    def _large_access_timestamp(self, entry: object) -> float:
        text = str(getattr(entry, "last_access_time", "") or getattr(entry, "last_write_time", "") or "")
        try:
            return datetime.fromisoformat(text).timestamp()
        except Exception:
            try:
                return Path(str(getattr(entry, "path", ""))).stat().st_mtime
            except OSError:
                return 0.0

    def _large_unused_days(self, entry: object) -> int:
        timestamp = self._large_access_timestamp(entry)
        if timestamp <= 0:
            return 0
        return max(0, int((time.time() - timestamp) // 86400))

    def _large_access_text(self, entry: object) -> str:
        days = self._large_unused_days(entry)
        basis = str(getattr(entry, "access_time_basis", "") or "修改时间估算")
        label = "按访问时间" if "访问" in basis else "按修改时间估算"
        return f"{days} 天\n{label}"

    def _set_large_file_row(self, table: QTableWidget, row: int, entry: object) -> None:
        path = str(getattr(entry, "path", "") or "")
        check_item = QTableWidgetItem("")
        check_item.setData(Qt.ItemDataRole.UserRole, path)
        check_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        check_item.setFlags(check_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        check_item.setCheckState(Qt.CheckState.Unchecked)
        table.setItem(row, 0, check_item)
        risk_label = self._path_risk_info(path)[0]
        table.setItem(row, 1, self._text_item(risk_label))
        table.setItem(row, 2, self._path_item(path, Path(path).name))
        size = int(getattr(entry, "size_bytes", 0) or 0)
        table.setItem(row, 3, self._size_item(size))
        table.setItem(row, 4, self._path_item(path, str(Path(path).parent)))
        table.setItem(row, 5, self._text_item(self._large_access_text(entry), sort_value=self._large_unused_days(entry)))
        mtime = self._timestamp_from_text(str(getattr(entry, "last_write_time", "") or ""))
        table.setItem(row, 6, self._text_item(self._format_timestamp_text(mtime), sort_value=mtime))
        type_label = str(getattr(entry, "type_label", "") or "其它")
        type_desc = str(getattr(entry, "type_description", "") or "大文件可能是用户资料或程序数据，处理前请确认内容。")
        why_text = f"为什么建议清理：文件超过大文件阈值。\n类型说明：{type_desc}"
        type_item = self._text_item(f"{type_label}\n{why_text}", sort_value=type_label)
        type_item.setToolTip(why_text)
        table.setItem(row, 7, type_item)
        actions = QWidget()
        layout = QHBoxLayout(actions)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for text, callback in (
            ("打开", lambda p=path: self._open_duplicate_location(p)),
            ("预览", lambda p=path: self._preview_duplicate_file(p)),
            ("忽略文件", lambda p=path: self._add_large_ignore_rule("paths", p)),
            ("忽略目录", lambda p=path: self._add_large_ignore_rule("dirs", str(Path(p).parent))),
        ):
            btn = QPushButton(text)
            btn.setObjectName("DriveCleanerSecondary")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, cb=callback: cb())
            layout.addWidget(btn)
        table.setCellWidget(row, 8, actions)
        self._set_result_row_meta(
            table,
            row,
            path=path,
            size=size,
            risk=risk_label,
            mtime=mtime,
            type_label=type_label,
        )

    def _software_uninstall_command(self, entry: SoftwareEntry) -> str:
        return str(entry.uninstall_command or entry.quiet_uninstall_command or "").strip()

    def _software_command_path(self, entry: SoftwareEntry) -> str:
        command = os.path.expandvars(self._software_uninstall_command(entry)).strip()
        if not command or command.lower().startswith("msiexec"):
            return ""
        quoted = re.match(r'^"([^"]+)"', command)
        if quoted:
            return re.sub(r",\s*-?\d+\s*$", "", quoted.group(1)).strip()
        match = re.match(r"^([A-Za-z]:\\.*?\\[^\\/:*?\"<>|]+\.(?:exe|msi|bat|cmd|com))(?:[, ]|$)", command, flags=re.IGNORECASE)
        return re.sub(r",\s*-?\d+\s*$", "", match.group(1)).strip() if match else ""

    def _first_existing_software_path(self, text: str) -> str:
        for part in str(text or "").split(" | "):
            path = os.path.expandvars(part.strip())
            if not path:
                continue
            try:
                if Path(path).exists():
                    return path
            except OSError:
                continue
        return ""

    def _open_software_path(self, path: str, missing_message: str) -> None:
        target = str(path or "").strip()
        if not target:
            self._show_resource_status(missing_message, tone="warning")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(target)):
            self._show_resource_status(f"打开失败：{target}", tone="error", auto_hide_ms=4200)

    def _open_software_install_dir(self, entry: SoftwareEntry) -> None:
        path = self._first_existing_software_path(entry.install_location)
        self._open_software_path(path, "安装目录不存在或未识别。")

    def _open_software_uninstall_dir(self, entry: SoftwareEntry) -> None:
        command_path = self._software_command_path(entry)
        folder = ""
        if command_path:
            try:
                target = Path(command_path)
                if target.exists():
                    folder = str(target.parent if target.is_file() else target)
            except OSError:
                folder = ""
        self._open_software_path(folder, "卸载器路径不存在或无法从命令中识别。")

    def _copy_software_uninstall_command(self, entry: SoftwareEntry) -> None:
        command = self._software_uninstall_command(entry)
        if not command:
            self._show_resource_status("未找到系统卸载命令，可尝试强力卸载。", tone="warning", auto_hide_ms=4200)
            return
        QApplication.clipboard().setText(command)
        self._show_resource_status("卸载命令已复制。", tone="success", auto_hide_ms=1800)

    def _set_software_action_button(self, text: str, enabled: bool, tooltip: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("DriveCleanerSecondary")
        button.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        button.setEnabled(bool(enabled))
        button.setToolTip(tooltip)
        if enabled:
            button.clicked.connect(lambda _=False: callback())
        return button

    def _set_software_action_widget(self, table: QTableWidget, row: int, entry: SoftwareEntry) -> None:
        install_dir = self._first_existing_software_path(entry.install_location)
        uninstall_path = self._software_command_path(entry)
        uninstall_dir_exists = False
        if uninstall_path:
            try:
                uninstall_dir_exists = Path(uninstall_path).exists()
            except OSError:
                uninstall_dir_exists = False
        command = self._software_uninstall_command(entry)
        actions = QWidget()
        layout = QHBoxLayout(actions)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._set_software_action_button(
            "安装目录",
            bool(install_dir),
            "打开软件安装目录" if install_dir else "安装目录不存在或未识别",
            lambda e=entry: self._open_software_install_dir(e),
        ))
        layout.addWidget(self._set_software_action_button(
            "卸载器",
            bool(uninstall_dir_exists),
            "打开卸载器所在目录" if uninstall_dir_exists else "卸载器路径不存在或无法识别",
            lambda e=entry: self._open_software_uninstall_dir(e),
        ))
        layout.addWidget(self._set_software_action_button(
            "复制命令",
            bool(command),
            "复制系统卸载命令" if command else "未找到系统卸载命令，可尝试强力卸载",
            lambda e=entry: self._copy_software_uninstall_command(e),
        ))
        table.setCellWidget(row, 8, actions)

    def _set_software_row(self, table: QTableWidget, row: int, entry: SoftwareEntry) -> None:
        check_item = QTableWidgetItem("")
        check_item.setData(Qt.ItemDataRole.UserRole, entry.id)
        check_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if not entry.is_system:
            check_item.setFlags(check_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check_item.setCheckState(Qt.CheckState.Unchecked)
        else:
            check_item.setFlags(check_item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            check_item.setToolTip("系统软件不可勾选")
        table.setItem(row, 0, check_item)
        table.setItem(row, 1, self._text_item(entry.software_type))
        table.setItem(row, 2, self._text_item(entry.name))
        table.setItem(row, 3, self._size_item(entry.size_bytes))
        table.setItem(row, 4, self._text_item(entry.version))
        table.setItem(row, 5, self._multi_path_item(entry.install_location) if entry.install_location else self._text_item("未发现安装目录"))
        table.setItem(row, 6, self._multi_path_item(entry.data_location) if entry.data_location else self._text_item("未发现数据目录"))
        table.setItem(row, 7, self._text_item(entry.reason))
        self._set_software_action_widget(table, row, entry)

    def _populate_software_table(self, entries: list[SoftwareEntry]) -> None:
        table = self._tables["uninstall"]
        sorting = table.isSortingEnabled()
        table.setSortingEnabled(False)
        table.blockSignals(True)
        try:
            table.setRowCount(len(entries))
            for row, entry in enumerate(entries):
                self._set_software_row(table, row, entry)
        finally:
            table.blockSignals(False)
            table.setSortingEnabled(sorting)
        self._filter_software_table()
        self._refresh_actions()

    def _on_software_search_changed(self) -> None:
        self._filter_software_table()
        self._start_software_scan_from_search_if_needed()

    def _start_software_scan_from_search_if_needed(self) -> None:
        if self._current_mode != "uninstall":
            return
        if not hasattr(self, "_software_search") or not self._software_search.text().strip():
            return
        if self._software_list_loaded:
            return
        table = self._tables.get("uninstall")
        if table is not None and table.rowCount() > 0:
            return
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        if self._cleanup_worker is not None and self._cleanup_worker.isRunning():
            return
        self._start_software_scan()

    def _duplicate_group_id(self, group: object) -> str:
        paths = list(getattr(group, "paths", []) or [])
        return str(getattr(group, "hash", "") or "|".join(paths))

    def _duplicate_rule_value(self) -> str:
        if not hasattr(self, "_duplicate_keep_rule"):
            return "newest"
        return str(self._duplicate_keep_rule.currentData() or "newest")

    def _duplicate_path_mtime(self, path: str) -> float:
        try:
            return float(Path(path).stat().st_mtime)
        except OSError:
            return 0.0

    def _duplicate_default_keep_path(self, paths: list[str], rule: Optional[str] = None) -> str:
        valid = [str(path) for path in paths if str(path).strip()]
        if not valid:
            return ""
        value = str(rule or self._duplicate_rule_value())
        if value == "oldest":
            return min(valid, key=lambda path: (self._duplicate_path_mtime(path), len(path), path.lower()))
        if value == "shortest_path":
            return min(valid, key=lambda path: (len(path), path.lower()))
        return max(valid, key=lambda path: (self._duplicate_path_mtime(path), -len(path), path.lower()))

    def _duplicate_keep_path(self, group: object) -> str:
        group_id = self._duplicate_group_id(group)
        paths = [str(path) for path in list(getattr(group, "paths", []) or [])]
        keep = self._duplicate_keep_paths.get(group_id, "")
        if keep in paths:
            return keep
        keep = self._duplicate_default_keep_path(paths)
        if keep:
            self._duplicate_keep_paths[group_id] = keep
        return keep

    def _duplicate_group_files_text(self, group: object, *, expanded: bool) -> str:
        paths = [str(path) for path in list(getattr(group, "paths", []) or [])]
        keep = self._duplicate_keep_path(group)
        lines: list[str] = []
        visible = paths if expanded else paths[:3]
        for path in visible:
            status = "保留" if path == keep else "待清理副本"
            exists = Path(path).exists()
            if not exists:
                status = "不可访问"
            mtime = ""
            try:
                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(Path(path).stat().st_mtime))
            except OSError:
                mtime = "未知时间"
            parent = str(Path(path).parent)
            lines.append(f"[{status}] {Path(path).name} · {format_bytes(int(getattr(group, 'size_bytes', 0) or 0))} · {mtime}\n{parent}")
        if not expanded and len(paths) > len(visible):
            lines.append(f"... 还有 {len(paths) - len(visible)} 个文件，点击展开查看")
        return "\n".join(lines)

    def _duplicate_rule_label(self, group: object) -> str:
        group_id = self._duplicate_group_id(group)
        if group_id in self._duplicate_manual_groups:
            return "手动选择"
        return self._duplicate_keep_rule.currentText() if hasattr(self, "_duplicate_keep_rule") else "保留最新"

    def _on_duplicate_keep_rule_changed(self) -> None:
        if not hasattr(self, "_tables"):
            return
        if str(self._duplicate_keep_rule.currentData() or "") != "manual":
            self._duplicate_manual_groups.clear()
            self._duplicate_keep_paths.clear()
        report = self._reports.get("duplicates")
        if report is not None:
            self._populate_table("duplicates", report)

    def _visible_duplicate_groups(self) -> list[object]:
        report = self._reports.get("duplicates")
        if report is None:
            return []
        return [
            group
            for result in report.drives
            for group in result.duplicate_groups
            if not self._is_duplicate_group_ignored(group)
        ]

    def _visible_duplicate_group_ids(self) -> list[str]:
        groups = self._visible_duplicate_groups()
        if groups:
            return [self._duplicate_group_id(group) for group in groups]
        table = self._tables.get("duplicates")
        if table is None:
            return []
        ids: list[str] = []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            group_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""
            if group_id:
                ids.append(group_id)
        return ids

    def _all_duplicate_groups_expanded(self) -> bool:
        group_ids = self._visible_duplicate_group_ids()
        return bool(group_ids) and all(group_id in self._duplicate_expanded_groups for group_id in group_ids)

    def _toggle_all_duplicate_groups_expanded(self) -> None:
        group_ids = self._visible_duplicate_group_ids()
        if not group_ids:
            return
        if all(group_id in self._duplicate_expanded_groups for group_id in group_ids):
            self._duplicate_expanded_groups.difference_update(group_ids)
        else:
            self._duplicate_expanded_groups.update(group_ids)
        report = self._reports.get("duplicates")
        if report is not None:
            self._populate_table("duplicates", report)

    def _toggle_duplicate_group_expanded(self, group_id: str) -> None:
        if group_id in self._duplicate_expanded_groups:
            self._duplicate_expanded_groups.remove(group_id)
        else:
            self._duplicate_expanded_groups.add(group_id)
        report = self._reports.get("duplicates")
        if report is not None:
            self._populate_table("duplicates", report)

    def _set_duplicate_keep_path(self, group_id: str, path: str) -> None:
        self._duplicate_keep_paths[str(group_id)] = str(path or "")
        self._duplicate_manual_groups.add(str(group_id))
        if hasattr(self, "_duplicate_keep_rule"):
            manual_index = self._duplicate_keep_rule.findData("manual")
            if manual_index >= 0:
                self._duplicate_keep_rule.blockSignals(True)
                self._duplicate_keep_rule.setCurrentIndex(manual_index)
                self._duplicate_keep_rule.blockSignals(False)
        report = self._reports.get("duplicates")
        if report is not None:
            self._populate_table("duplicates", report)
        self._show_resource_status("已设为保留文件。", tone="success", auto_hide_ms=1600)

    def _open_duplicate_location(self, path: str) -> None:
        target = Path(path)
        folder = target if target.is_dir() else target.parent
        if not folder.exists():
            self._show_resource_status("打开位置失败：路径不存在。", tone="error", auto_hide_ms=4200)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _preview_duplicate_file(self, path: str) -> None:
        target = Path(path)
        if not target.exists():
            self._show_resource_status("无法预览：路径不存在。", tone="error", auto_hide_ms=4200)
            return
        try:
            size = int(target.stat().st_size)
        except OSError as exc:
            self._show_resource_status(f"无法预览：权限不足或文件被占用：{exc}", tone="error", auto_hide_ms=5200)
            return
        suffix = target.suffix.lower()
        image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
        if suffix in image_exts:
            if size > 20 * 1024 * 1024:
                self._show_resource_status("无法预览：图片过大。", tone="warning", auto_hide_ms=4200)
                return
            dialog = QDialog(self)
            dialog.setWindowTitle("文件预览")
            self._apply_dialog_caption_color(dialog)
            dialog.setMinimumSize(520, 420)
            layout = QVBoxLayout(dialog)
            image_label = QLabel()
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pix = QPixmap(str(target))
            if pix.isNull():
                self._show_resource_status("无法预览：格式不支持或文件损坏。", tone="warning", auto_hide_ms=4200)
                return
            image_label.setPixmap(pix.scaled(QSize(480, 320), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            info = QLabel(f"{target.name}\n{format_bytes(size)}\n{target.parent}")
            info.setWordWrap(True)
            layout.addWidget(image_label, 1)
            layout.addWidget(info)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.exec()
            return
        if suffix in {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".toml", ".ini", ".log", ".doc", ".docx", ".pdf"}:
            self._show_resource_status(f"文档预览：{target.name}，{format_bytes(size)}。", tone="info", auto_hide_ms=4200)
            return
        self._open_duplicate_location(str(target))

    def _show_duplicate_group_dialog(self, group: object) -> None:
        paths = [str(path) for path in list(getattr(group, "paths", []) or [])]
        group_id = self._duplicate_group_id(group)
        keep = self._duplicate_keep_path(group)
        dialog = QDialog(self)
        dialog.setWindowTitle("重复文件组")
        self._apply_dialog_caption_color(dialog)
        dialog.setMinimumSize(760, 460)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        header = QLabel(
            f"重复组大小：{format_bytes(int(getattr(group, 'size_bytes', 0) or 0))}  "
            f"文件数量：{len(paths)}  预计可释放：{format_bytes(int(getattr(group, 'size_bytes', 0) or 0) * max(0, len(paths) - 1))}\n"
            f"当前保留规则：{self._duplicate_rule_label(group)}  "
            f"风险状态：{'已按内容哈希确认' if bool(getattr(group, 'content_hash_confirmed', True)) else '仅按大小和快速指纹预筛选，需确认'}"
        )
        header.setWordWrap(True)
        layout.addWidget(header)
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["状态", "文件名", "大小 / 修改时间", "所在目录", "操作"])
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        detail_header = table.horizontalHeader()
        for index in range(table.columnCount()):
            detail_header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        for index, width in enumerate((88, 190, 150, 360, 250)):
            table.setColumnWidth(index, width)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._prepare_path_click_table(table)
        table.setRowCount(len(paths))
        for row, path in enumerate(paths):
            status = "保留" if path == keep else ("不可访问" if not Path(path).exists() else "待清理")
            table.setItem(row, 0, self._text_item(status))
            table.setItem(row, 1, self._path_item(path, Path(path).name))
            try:
                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(Path(path).stat().st_mtime))
            except OSError:
                mtime = "未知时间"
            table.setItem(row, 2, self._text_item(f"{format_bytes(int(getattr(group, 'size_bytes', 0) or 0))}\n{mtime}"))
            table.setItem(row, 3, self._path_item(path, str(Path(path).parent)))
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(0, 0, 0, 0)
            action_layout.setSpacing(4)
            keep_btn = QPushButton("设为保留")
            preview_btn = QPushButton("预览")
            open_btn = QPushButton("打开位置")
            for btn in (keep_btn, preview_btn, open_btn):
                btn.setObjectName("DriveCleanerSecondary")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                action_layout.addWidget(btn)
            keep_btn.clicked.connect(lambda _=False, gid=group_id, p=path, d=dialog: (self._set_duplicate_keep_path(gid, p), d.accept()))
            preview_btn.clicked.connect(lambda _=False, p=path: self._preview_duplicate_file(p))
            open_btn.clicked.connect(lambda _=False, p=path: self._open_duplicate_location(p))
            table.setCellWidget(row, 4, actions)
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText("关闭")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _show_all_duplicate_groups_dialog(self, fallback_group: Optional[object] = None) -> None:
        groups = self._visible_duplicate_groups()
        if not groups and fallback_group is not None:
            groups = [fallback_group]
        if not groups:
            self._show_resource_status("当前没有可查看的重复文件详情。", tone="info", auto_hide_ms=2200)
            return
        total_files = sum(len(list(getattr(group, "paths", []) or [])) for group in groups)
        total_reclaim = sum(
            int(getattr(group, "size_bytes", 0) or 0) * max(0, len(list(getattr(group, "paths", []) or [])) - 1)
            for group in groups
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("重复文件详情")
        self._apply_dialog_caption_color(dialog)
        dialog.setMinimumSize(920, 560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        header = QLabel(
            f"重复组：{len(groups)}  文件数量：{total_files}  理论可释放：{format_bytes(total_reclaim)}\n"
            "详情列出当前结果里的所有重复组。可在每组中设定保留文件，其余文件将作为待清理副本。"
        )
        header.setWordWrap(True)
        layout.addWidget(header)
        table = QTableWidget(0, 6)
        table.setObjectName("DriveCleanerTable")
        table.setHorizontalHeaderLabels(["重复组", "状态", "文件名", "大小 / 修改时间", "所在目录", "操作"])
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setItemDelegate(_NoFocusTableDelegate(table))
        self._prepare_path_click_table(table)
        header_view = table.horizontalHeader()
        for index in range(table.columnCount()):
            header_view.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        for index, width in enumerate((180, 88, 220, 150, 360, 250)):
            table.setColumnWidth(index, width)
        rows: list[tuple[int, object, str]] = []
        for group_index, group in enumerate(groups, start=1):
            for path in [str(value) for value in list(getattr(group, "paths", []) or [])]:
                rows.append((group_index, group, path))
        table.setRowCount(len(rows))
        for row, (group_index, group, path) in enumerate(rows):
            group_id = self._duplicate_group_id(group)
            paths = [str(value) for value in list(getattr(group, "paths", []) or [])]
            keep = self._duplicate_keep_path(group)
            size = int(getattr(group, "size_bytes", 0) or 0)
            reclaim = size * max(0, len(paths) - 1)
            status = "保留" if path == keep else ("不可访问" if not Path(path).exists() else "待清理")
            group_text = f"#{group_index}  {format_bytes(size)} x {len(paths)}\n可释放 {format_bytes(reclaim)}"
            table.setItem(row, 0, self._text_item(group_text, sort_value=group_index))
            table.setItem(row, 1, self._text_item(status))
            table.setItem(row, 2, self._path_item(path, Path(path).name))
            try:
                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(Path(path).stat().st_mtime))
            except OSError:
                mtime = "未知时间"
            table.setItem(row, 3, self._text_item(f"{format_bytes(size)}\n{mtime}"))
            table.setItem(row, 4, self._path_item(path, str(Path(path).parent)))
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(0, 0, 0, 0)
            action_layout.setSpacing(4)
            keep_btn = QPushButton("设为保留")
            preview_btn = QPushButton("预览")
            open_btn = QPushButton("打开位置")
            for btn in (keep_btn, preview_btn, open_btn):
                btn.setObjectName("DriveCleanerSecondary")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                action_layout.addWidget(btn)
            keep_btn.clicked.connect(lambda _=False, gid=group_id, p=path, d=dialog: (self._set_duplicate_keep_path(gid, p), d.accept()))
            preview_btn.clicked.connect(lambda _=False, p=path: self._preview_duplicate_file(p))
            open_btn.clicked.connect(lambda _=False, p=path: self._open_duplicate_location(p))
            table.setCellWidget(row, 5, actions)
            table.setRowHeight(row, 58)
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText("关闭")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _set_duplicate_row(self, table: QTableWidget, row: int, group: object) -> None:
        paths = list(getattr(group, "paths", []) or [])
        group_id = self._duplicate_group_id(group)
        is_preview = str(getattr(group, "hash", "") or "").startswith("preview:")
        check_item = QTableWidgetItem("")
        check_item.setData(Qt.ItemDataRole.UserRole, group_id)
        check_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if len(paths) > 1 and not is_preview:
            check_item.setFlags(check_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check_item.setCheckState(Qt.CheckState.Unchecked)
        else:
            check_item.setFlags(check_item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        table.setItem(row, 0, check_item)
        confirmed = bool(getattr(group, "content_hash_confirmed", True))
        table.setItem(row, 1, self._text_item("扫描中" if is_preview else ("需确认" if confirmed else "高风险")))
        expand_btn = QPushButton("全部收起" if self._all_duplicate_groups_expanded() else "全部展开")
        expand_btn.setObjectName("DriveCleanerSecondary")
        expand_btn.setMinimumWidth(72)
        expand_btn.clicked.connect(lambda _=False: self._toggle_all_duplicate_groups_expanded())
        group_widget = QWidget()
        group_layout = QVBoxLayout(group_widget)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(6)
        group_actions = QWidget()
        group_actions_layout = QHBoxLayout(group_actions)
        group_actions_layout.setContentsMargins(0, 0, 0, 0)
        group_actions_layout.setSpacing(6)
        group_label = QLabel(f"{format_bytes(int(getattr(group, 'size_bytes', 0) or 0))} x {len(paths)}")
        group_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        group_label.setToolTip("点击详情可查看并设定保留文件")
        detail_btn = QPushButton("详情")
        detail_btn.setObjectName("DriveCleanerSecondary")
        detail_btn.setMinimumWidth(60)
        detail_btn.clicked.connect(lambda _=False, g=group: self._show_all_duplicate_groups_dialog(g))
        group_actions_layout.addWidget(expand_btn)
        group_actions_layout.addWidget(group_label, 1)
        group_actions_layout.addWidget(detail_btn)
        group_layout.addWidget(group_actions)
        expanded = group_id in self._duplicate_expanded_groups
        if expanded:
            file_summary = QLabel(self._duplicate_group_files_text(group, expanded=True))
            file_summary.setObjectName("DriveDuplicateExpandedSummary")
            file_summary.setWordWrap(True)
            file_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            file_summary.setStyleSheet("QLabel#DriveDuplicateExpandedSummary { color: #475569; font-size: 12px; line-height: 1.25; }")
            group_layout.addWidget(file_summary)
        table.setCellWidget(row, 2, group_widget)
        mtime = self._duplicate_group_mtime(group)
        reclaim = int(getattr(group, "size_bytes", 0) or 0) * max(0, len(paths) - 1)
        table.setItem(row, 3, self._size_item(reclaim))
        table.setItem(row, 4, self._text_item(str(len(paths)), sort_value=len(paths)))
        table.setItem(row, 5, self._text_item(self._format_timestamp_text(mtime), sort_value=mtime))
        status = (
            "扫描中预览，正在继续比对"
            if is_preview
            else ("已按内容哈希确认" if confirmed else "仅按大小预筛选，需确认")
        )
        why = (
            "为什么展示：这些文件大小相同，正在继续做快速指纹/内容哈希确认。"
            if is_preview
            else (
                "为什么建议清理：这些文件内容哈希相同，可保留一份并清理副本。"
                if confirmed
                else "为什么建议清理：这些文件大小和快速指纹相同，但未做完整内容哈希，清理前必须人工确认。"
            )
        )
        errors = list(getattr(group, "hash_errors", []) or [])
        error_text = "\n哈希失败：" + "；".join(str(path) for path in errors[:3]) if errors else ""
        keep = self._duplicate_keep_path(group)
        duplicate_summary = f"保留 1 个，待清理副本 {max(0, len(paths) - 1)} 个"
        info = (
            f"{status}\n"
            f"{why}\n"
            f"{duplicate_summary}\n"
            f"当前保留：{Path(keep).name if keep else '未选择'}\n"
            f"规则：{self._duplicate_rule_label(group)}\n"
            f"{self._duplicate_group_files_text(group, expanded=expanded)}"
            f"{error_text}"
        )
        item = self._text_item(info)
        item.setToolTip(info)
        table.setItem(row, 6, item)
        table.setRowHeight(row, max(128, 70 + len(paths) * 44) if expanded else 78)
        self._set_result_row_meta(
            table,
            row,
            path="\n".join(paths),
            size=reclaim,
            risk="需确认" if confirmed else "高风险",
            mtime=mtime,
            type_label=self._duplicate_group_type_label(group),
        )

    def _set_duplicate_paths_widget(self, table: QTableWidget, row: int, col: int, paths: list[str]) -> None:
        if not paths:
            return
        label = QLabel()
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(
            '<span style="color:#2563eb;">'
            + '</span><span style="color:#dc2626;font-weight:700;"> | </span><span style="color:#2563eb;">'.join(html.escape(str(path)) for path in paths)
            + "</span>"
        )
        label.setToolTip("点击打开路径")
        label.setStyleSheet("QLabel { background: transparent; padding-left: 4px; }")
        table.setCellWidget(row, col, label)

    def _stream_preview_limit(self, mode: str) -> int:
        return {
            "cleanup": 250,
            "space": 80,
            "large_files": 250,
            "duplicates": 120,
        }.get(str(mode or ""), 120)

    def _find_result_row_by_path(self, table: QTableWidget, path: str) -> int:
        target = str(path or "")
        if not target:
            return -1
        for row in range(table.rowCount()):
            if str(self._result_row_meta(table, row).get("path", "") or "") == target:
                return row
        return -1

    def _find_result_row_by_id(self, table: QTableWidget, item_id: str) -> int:
        target = str(item_id or "")
        if not target:
            return -1
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and str(item.data(Qt.ItemDataRole.UserRole) or "") == target:
                return row
        return -1

    def _stream_row_for_path_item(self, table: QTableWidget, mode: str, path: str) -> Optional[int]:
        row = self._find_result_row_by_path(table, path)
        if row >= 0:
            return row
        if table.rowCount() >= self._stream_preview_limit(mode):
            return None
        row = table.rowCount()
        table.insertRow(row)
        return row

    def _on_stream_item(self, mode: str, kind: str, item: object) -> None:
        table = self._tables.get(mode)
        if table is None:
            return
        sorting = table.isSortingEnabled()
        table.setSortingEnabled(False)
        table.blockSignals(True)
        try:
            if mode == "cleanup" and kind == "candidate" and hasattr(item, "path"):
                path = str(getattr(item, "path", "") or "")
                if self._is_drive_path_ignored(path):
                    return
                row = self._stream_row_for_path_item(table, mode, path)
                if row is None:
                    return
                self._set_candidate_row(table, row, item)
                self._append_log(f"发现清理候选: {getattr(item, 'path', '')}")
                self._update_scan_stage(mode, "发现清理候选")
            elif mode == "space" and kind in {"large_dir", "large_dir_preview"} and hasattr(item, "path"):
                path = str(getattr(item, "path", "") or "")
                if self._is_drive_path_ignored(path):
                    return
                row = self._stream_row_for_path_item(table, mode, path)
                if row is None:
                    return
                self._set_space_dir_row(table, row, item)
                if kind == "large_dir":
                    self._append_log(
                        f"目录占用: {format_bytes(int(getattr(item, 'size_bytes', 0)))}  {getattr(item, 'path', '')}"
                    )
                self._update_scan_stage(mode, "正在分析目录")
            elif mode == "large_files" and kind == "large_file" and hasattr(item, "path"):
                path = str(getattr(item, "path", "") or "")
                if self._is_large_file_ignored(path):
                    return
                row = self._stream_row_for_path_item(table, mode, path)
                if row is None:
                    return
                self._set_large_file_row(table, row, item)
                self._append_log(
                    f"发现大文件: {format_bytes(int(getattr(item, 'size_bytes', 0)))}  {getattr(item, 'path', '')}"
                )
                self._update_scan_stage(mode, "发现大文件")
            elif mode == "duplicates" and kind in {"duplicate_group", "duplicate_group_preview"} and hasattr(item, "paths"):
                if self._is_duplicate_group_ignored(item):
                    return
                row = self._find_result_row_by_id(table, self._duplicate_group_id(item))
                if row < 0:
                    if table.rowCount() >= self._stream_preview_limit(mode):
                        return
                    row = table.rowCount()
                    table.insertRow(row)
                self._set_duplicate_row(table, row, item)
                if kind == "duplicate_group":
                    self._append_log(
                        f"发现重复文件组: {format_bytes(int(getattr(item, 'size_bytes', 0)))} x {len(list(getattr(item, 'paths', []) or []))}"
                    )
                    self._update_scan_stage(mode, "发现重复文件组")
                else:
                    self._update_scan_stage(mode, "正在收集重复候选")
        finally:
            table.blockSignals(False)
            table.setSortingEnabled(sorting)
        self._schedule_result_refresh(mode)

    def _populate_table(self, mode: str, report: ScanReport) -> None:
        table = self._tables[mode]
        sorting = table.isSortingEnabled()
        table.setSortingEnabled(False)
        table.blockSignals(True)
        try:
            if mode == "cleanup":
                rows = [c for r in report.drives for c in r.candidates if not self._is_drive_path_ignored(str(getattr(c, "path", "") or ""))]
                table.setRowCount(len(rows))
                for row, candidate in enumerate(rows):
                    self._set_candidate_row(table, row, candidate)
            elif mode == "space":
                rows = [e for r in report.drives for e in r.large_dirs if not self._is_drive_path_ignored(str(getattr(e, "path", "") or ""))]
                table.setRowCount(len(rows))
                for row, entry in enumerate(rows):
                    self._set_space_dir_row(table, row, entry)
            elif mode == "large_files":
                rows = [e for r in report.drives for e in r.large_files if not self._is_large_file_ignored(e.path)]
                table.setRowCount(len(rows))
                for row, entry in enumerate(rows):
                    self._set_large_file_row(table, row, entry)
            else:
                rows = [g for r in report.drives for g in r.duplicate_groups if not self._is_duplicate_group_ignored(g)]
                table.setRowCount(len(rows))
                for row, group in enumerate(rows):
                    self._set_duplicate_row(table, row, group)
        finally:
            table.blockSignals(False)
            table.setSortingEnabled(sorting)
        self._refresh_result_type_options(mode)
        self._apply_result_filters()
        self._update_select_all_text()
        self._refresh_actions()

    def _selected_ids(self) -> list[str]:
        table = self._tables[self._current_mode]
        selected: list[str] = []
        for row in range(table.rowCount()):
            if table.isRowHidden(row):
                continue
            item = table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                selected.append(str(item.data(Qt.ItemDataRole.UserRole) or ""))
        return [x for x in selected if x]

    def _large_entry_by_path(self, path: str) -> Optional[object]:
        report = self._reports.get("large_files")
        if report is None:
            return None
        for result in report.drives:
            for entry in result.large_files:
                if str(getattr(entry, "path", "") or "") == str(path or ""):
                    return entry
        return None

    def _on_large_filter_changed(self) -> None:
        if self._large_unused_filter.currentData() == -1:
            value, ok = self._drive_get_int("自定义未使用天数", "显示多少天未访问的大文件:", max(1, self._large_filter_custom_days or 365), 1, 3650, 1)
            if ok:
                self._large_filter_custom_days = int(value)
            else:
                self._large_unused_filter.blockSignals(True)
                self._large_unused_filter.setCurrentIndex(0)
                self._large_unused_filter.blockSignals(False)
        self._filter_large_files_table()

    def _large_unused_filter_days(self) -> int:
        data = self._large_unused_filter.currentData()
        if data == -1:
            return int(self._large_filter_custom_days or 0)
        return int(data or 0)

    def _filter_large_files_table(self) -> None:
        self._apply_result_filters()

    def _set_quick_result_filter(self, risk: str) -> None:
        if not hasattr(self, "_result_risk_filter"):
            return
        index = self._result_risk_filter.findData(str(risk or ""))
        self._result_risk_filter.setCurrentIndex(index if index >= 0 else 0)
        self._apply_result_filters()

    def _clear_result_filters(self) -> None:
        for combo in (self._result_risk_filter, self._result_type_filter, self._result_mtime_filter):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        for spin in (self._result_min_size, self._result_max_size):
            spin.blockSignals(True)
            spin.setValue(0)
            spin.blockSignals(False)
        self._result_path_filter.blockSignals(True)
        self._result_path_filter.clear()
        self._result_path_filter.blockSignals(False)
        if hasattr(self, "_large_path_filter"):
            self._large_path_filter.blockSignals(True)
            self._large_path_filter.clear()
            self._large_path_filter.blockSignals(False)
        if hasattr(self, "_large_type_filter"):
            self._large_type_filter.blockSignals(True)
            self._large_type_filter.setCurrentIndex(0)
            self._large_type_filter.blockSignals(False)
        if hasattr(self, "_large_unused_filter"):
            self._large_unused_filter.blockSignals(True)
            self._large_unused_filter.setCurrentIndex(0)
            self._large_unused_filter.blockSignals(False)
        self._apply_result_filters()

    def _mtime_filter_matches(self, timestamp: float, filter_value: str) -> bool:
        if not filter_value:
            return True
        if timestamp <= 0:
            return False
        now = time.time()
        parts = str(filter_value).split("_", 1)
        if len(parts) != 2:
            return True
        mode, days_text = parts
        try:
            seconds = int(days_text) * 86400
        except ValueError:
            return True
        age = now - float(timestamp)
        if mode == "within":
            return age <= seconds
        if mode == "older":
            return age >= seconds
        return True

    def _row_matches_result_filters(self, table: QTableWidget, row: int) -> bool:
        meta = self._result_row_meta(table, row)
        path = str(meta.get("path", "") or "")
        risk = str(meta.get("risk", "") or "")
        type_label = str(meta.get("type", "") or "")
        size = int(meta.get("size", 0) or 0)
        timestamp = float(meta.get("mtime", 0.0) or 0.0)
        risk_filter = str(self._result_risk_filter.currentData() or "") if hasattr(self, "_result_risk_filter") else ""
        type_filter = str(self._result_type_filter.currentData() or "") if hasattr(self, "_result_type_filter") else ""
        path_query = self._result_path_filter.text().strip().lower() if hasattr(self, "_result_path_filter") else ""
        min_size = int(self._result_min_size.value() or 0) * 1024 * 1024 if hasattr(self, "_result_min_size") else 0
        max_size = int(self._result_max_size.value() or 0) * 1024 * 1024 if hasattr(self, "_result_max_size") else 0
        mtime_filter = str(self._result_mtime_filter.currentData() or "") if hasattr(self, "_result_mtime_filter") else ""
        if risk_filter and risk_filter != risk:
            return False
        if type_filter and type_filter != type_label:
            return False
        if path_query and path_query not in path.lower():
            return False
        if min_size and size < min_size:
            return False
        if max_size and size > max_size:
            return False
        if not self._mtime_filter_matches(timestamp, mtime_filter):
            return False
        return True

    def _apply_result_filters(self) -> None:
        table = self._tables.get(self._current_mode)
        if table is None:
            return
        for row in range(table.rowCount()):
            meta = self._result_row_meta(table, row)
            path = str(meta.get("path", "") or "")
            hidden = False
            # 大文件忽略规则
            if self._current_mode == "large_files":
                if self._is_large_file_ignored(path):
                    hidden = True
            # 通用结果筛选器（风险、类型、路径、大小、修改时间）
            if not hidden and not self._row_matches_result_filters(table, row):
                hidden = True
            table.setRowHidden(row, hidden)
        self._refresh_actions()

    def _show_large_ignore_manager(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("管理磁盘清理忽略规则")
        self._apply_dialog_caption_color(dialog)
        dialog.setMinimumSize(680, 460)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        summary = QLabel("命中忽略规则的项目不会出现在常规清理、大文件和重复文件结果里。")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        add_row = QWidget()
        add_layout = QHBoxLayout(add_row)
        add_layout.setContentsMargins(0, 0, 0, 0)
        rule_type = ModernPopupComboBox()
        rule_type.setObjectName("DriveCleanerCombo")
        self._prepare_drive_combo(rule_type)
        rule_type.addItem("精确路径", "paths")
        rule_type.addItem("目录路径", "dirs")
        rule_type.addItem("扩展名", "extensions")
        rule_type.addItem("文件名关键词", "keywords")
        rule_input = QLineEdit()
        rule_input.setPlaceholderText("输入路径、目录、.log 或文件名关键词")
        add_btn = QPushButton("添加")
        add_btn.setObjectName("DriveCleanerSecondary")
        add_layout.addWidget(rule_type)
        add_layout.addWidget(rule_input, 1)
        add_layout.addWidget(add_btn)
        layout.addWidget(add_row)
        list_widget = QListWidget()
        rows: list[tuple[str, int]] = []
        labels = {"paths": "精确路径", "dirs": "目录路径", "extensions": "扩展名", "keywords": "文件名关键词"}
        for key in ("paths", "dirs", "extensions", "keywords"):
            for index, row in enumerate(self._large_ignore.get(key, []) or []):
                item = QListWidgetItem(f"[{labels.get(key, key)}] {row.get('value', '')}  · {row.get('created_at', '')}")
                list_widget.addItem(item)
                rows.append((key, index))
        if not rows:
            list_widget.addItem("暂无忽略项。")
        layout.addWidget(list_widget, 1)
        buttons_row = QWidget()
        buttons_layout = QHBoxLayout(buttons_row)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        remove_btn = QPushButton("移除选中")
        clear_btn = QPushButton("清空")
        close_btn = QPushButton("关闭")
        for btn in (remove_btn, clear_btn, close_btn):
            btn.setObjectName("DriveCleanerSecondary")
            buttons_layout.addWidget(btn)
        layout.addWidget(buttons_row)

        def remove_selected() -> None:
            row = list_widget.currentRow()
            if row < 0 or row >= len(rows):
                return
            key, index = rows[row]
            try:
                del self._large_ignore[key][index]
            except Exception:
                return
            self._save_large_ignore_rules()
            dialog.accept()
            self._show_large_ignore_manager()
            self._refresh_current_drive_table()

        def add_rule() -> None:
            key = str(rule_type.currentData() or "paths")
            value = rule_input.text().strip()
            if not value:
                self._show_resource_status("请先输入忽略规则。", tone="warning", auto_hide_ms=2400)
                return
            self._add_large_ignore_rule(key, value, source="手动添加")
            dialog.accept()
            self._show_large_ignore_manager()

        def clear_all() -> None:
            self._large_ignore = {"paths": [], "dirs": [], "extensions": [], "keywords": []}
            self._save_large_ignore_rules()
            dialog.accept()
            self._refresh_current_drive_table()
            self._show_resource_status("忽略规则已清空。", tone="success", auto_hide_ms=1800)

        add_btn.clicked.connect(add_rule)
        rule_input.returnPressed.connect(add_rule)
        remove_btn.clicked.connect(remove_selected)
        clear_btn.clicked.connect(clear_all)
        close_btn.clicked.connect(dialog.accept)
        dialog.exec()

    def _filter_software_table(self) -> None:
        table = self._tables.get("uninstall")
        if table is None:
            return
        query = self._software_search.text().strip().lower() if hasattr(self, "_software_search") else ""
        for row in range(table.rowCount()):
            haystack = " ".join(
                table.item(row, col).text()
                for col in range(table.columnCount())
                if table.item(row, col) is not None
            ).lower()
            table.setRowHidden(row, bool(query and query not in haystack))
        self._refresh_actions()

    def _select_current_table_all(self) -> None:
        table = self._tables[self._current_mode]
        # 收集所有可见可勾选项，按风险分类
        visible_safe_items: list[QTableWidgetItem] = []
        visible_other_items: list[QTableWidgetItem] = []
        for row in range(table.rowCount()):
            if table.isRowHidden(row):
                continue
            item = table.item(row, 0)
            if item is None or not bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
            risk = str(item.data(self._RESULT_ROLE_RISK) or "")
            if risk == "安全":
                visible_safe_items.append(item)
            else:
                visible_other_items.append(item)
        # 判断当前是否所有可见安全项都已勾选 → 执行取消；否则执行全选
        should_uncheck = bool(visible_safe_items) and all(
            item.checkState() == Qt.CheckState.Checked for item in visible_safe_items
        )
        table.blockSignals(True)
        checked_count = 0
        unchecked_count = 0
        for row in range(table.rowCount()):
            if table.isRowHidden(row):
                continue
            item = table.item(row, 0)
            if item is None or not bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
            if should_uncheck:
                # 取消全选：取消所有可见勾选项
                if item.checkState() == Qt.CheckState.Checked:
                    unchecked_count += 1
                self._set_table_item_check_state(table, item, Qt.CheckState.Unchecked)
            else:
                # 全选：仅勾选安全项，跳过需确认和高风险项
                risk = str(item.data(self._RESULT_ROLE_RISK) or "")
                if risk == "安全":
                    if item.checkState() != Qt.CheckState.Checked:
                        checked_count += 1
                    self._set_table_item_check_state(table, item, Qt.CheckState.Checked)
                # 非安全项保持原样，不勾选
        table.blockSignals(False)
        # 强制刷新视口：blockSignals 拦住了 itemChanged 信号，需手动触发重绘
        table.viewport().update()
        self._update_select_all_text()
        self._refresh_actions()
        # 状态提示
        if should_uncheck:
            self._show_resource_status(f"已取消全部勾选（{unchecked_count} 项）", tone="info")
        elif checked_count > 0:
            skipped = len(visible_other_items)
            if skipped > 0:
                self._show_resource_status(
                    f"已勾选 {checked_count} 个安全项，跳过 {skipped} 个需确认/高风险项", tone="success"
                )
            else:
                self._show_resource_status(f"已勾选 {checked_count} 个安全项", tone="success")
        elif visible_other_items:
            self._show_resource_status(
                f"当前视图无安全项，{len(visible_other_items)} 个需确认/高风险项未勾选", tone="warning", auto_hide_ms=4000
            )
        else:
            self._show_resource_status("当前视图没有可勾选项", tone="info")

    @staticmethod
    def _set_table_item_check_state(
        table: QTableWidget,
        item: QTableWidgetItem,
        state: Qt.CheckState,
    ) -> None:
        """通过模型写入枚举状态，确保 Qt 委托能够正确绘制复选标记。"""
        index = table.indexFromItem(item)
        if not index.isValid():
            return
        table.model().setData(index, state, Qt.ItemDataRole.CheckStateRole)

    def _update_select_all_text(self) -> None:
        table = self._tables.get(self._current_mode)
        if table is None:
            self._select_all_btn.setText("全选")
            return
        # 只收集可见行中的安全项（与 _select_current_table_all 的判断逻辑一致）
        visible_safe_items = [
            table.item(row, 0)
            for row in range(table.rowCount())
            if not table.isRowHidden(row)
            and table.item(row, 0) is not None
            and bool(table.item(row, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
            and str(table.item(row, 0).data(self._RESULT_ROLE_RISK) or "") == "安全"
        ]
        if visible_safe_items and all(item.checkState() == Qt.CheckState.Checked for item in visible_safe_items):
            self._select_all_btn.setText("取消")
        else:
            self._select_all_btn.setText("全选")

    def _refresh_actions(self) -> None:
        busy = self._scan_worker is not None and self._scan_worker.isRunning()
        cleanup_busy = self._cleanup_worker is not None and self._cleanup_worker.isRunning()
        selected = bool(self._selected_ids())
        self._update_select_all_text()
        can_clean = self._current_mode in {"cleanup", "large_files", "duplicates"}
        can_uninstall = self._current_mode == "uninstall"
        self._dry_run_btn.setEnabled((not busy) and (not cleanup_busy) and can_clean and selected)
        self._clean_btn.setEnabled((not busy) and (not cleanup_busy) and can_clean and selected)
        large_mode = self._current_mode == "large_files"
        ignore_manageable = self._current_mode in {"cleanup", "space", "large_files", "duplicates"}
        self._large_move_btn.setVisible(large_mode)
        self._large_ignore_btn.setVisible(ignore_manageable)
        self._large_move_btn.setEnabled((not busy) and (not cleanup_busy) and large_mode and selected)
        self._large_ignore_btn.setEnabled((not busy) and (not cleanup_busy) and ignore_manageable)
        if can_uninstall:
            self._dry_run_btn.setEnabled((not busy) and (not cleanup_busy) and selected)
            self._clean_btn.setEnabled((not busy) and (not cleanup_busy) and selected)

    def _run_mode_secondary_action(self) -> None:
        if self._current_mode == "uninstall":
            self._run_software_uninstall(force=False)
        else:
            self._run_cleanup(dry_run=True)

    def _run_mode_danger_action(self) -> None:
        if self._current_mode == "uninstall":
            self._run_software_uninstall(force=True)
        else:
            self._run_cleanup(dry_run=False)

    def _run_cleanup(self, *, dry_run: bool) -> None:
        report = self._reports.get(self._current_mode)
        if report is None:
            return
        selected_ids = self._selected_ids()
        if not selected_ids:
            return
        if self._current_mode == "large_files":
            self._run_file_cleanup(selected_ids, dry_run=dry_run, title="大文件清理")
            return
        if self._current_mode == "duplicates":
            groups = self._selected_duplicate_groups(selected_ids)
            delete_paths = [
                (path, path)
                for group in groups
                for path in list(getattr(group, "paths", []) or [])
                if path != self._duplicate_keep_path(group)
            ]
            if not delete_paths:
                self._show_resource_status("选中的重复组没有可清理的副本。", tone="warning")
                return
            if not dry_run:
                self._next_delete_mode = "recycle"
                duplicate_size = sum(
                    int(getattr(group, "size_bytes", 0) or 0) * max(0, len(list(getattr(group, "paths", []) or [])) - 1)
                    for group in groups
                )
                edited_paths = self._confirm_file_cleanup(
                    [path for _, path in delete_paths],
                    title="重复文件清理",
                    detail=(
                        f"重复组数量：{len(groups)}；将删除副本数量：{len(delete_paths)}；"
                        f"已通过内容哈希确认：{'是' if all(bool(getattr(group, 'content_hash_confirmed', True)) for group in groups) else '否'}；"
                        f"存在需确认组：{'是' if any(not bool(getattr(group, 'content_hash_confirmed', True)) for group in groups) else '否'}。"
                    ),
                    estimated_size=duplicate_size,
                    contains_confirm_required=any(not bool(getattr(group, "content_hash_confirmed", True)) for group in groups),
                    irreversible_text="否，默认移入回收站；勾选永久删除后不可撤销。",
                    danger_level="高",
                )
                if edited_paths is None:
                    return
                keep = set(edited_paths)
                delete_paths = [(item_id, path) for item_id, path in delete_paths if path in keep]
                if not delete_paths:
                    self._show_resource_status("确认列表中没有保留任何待清理路径。", tone="warning")
                    return
            self._last_cleanup_remove_ids = set(selected_ids) if not dry_run else set()
            self._duplicate_cleanup_group_paths = {
                self._duplicate_group_id(group): {
                    path
                    for path in list(getattr(group, "paths", []) or [])
                    if path != self._duplicate_keep_path(group) and any(path == delete_path for _, delete_path in delete_paths)
                }
                for group in groups
            } if not dry_run else {}
            if not dry_run:
                self._begin_cleanup_record(
                    mode="重复文件清理",
                    item_count=len(delete_paths),
                    estimated_size=sum(self._path_size_if_file(path) for _, path in delete_paths),
                    paths=[path for _, path in delete_paths],
                    delete_mode=self._next_delete_mode,
                )
            self._start_file_cleanup_worker(delete_paths, dry_run=dry_run)
            return
        allow_confirm_required = self._selected_has_confirm_required(selected_ids)
        if not dry_run:
            self._next_delete_mode = "recycle"
            confirmed_ids = self._confirm_cleanup(selected_ids, require_confirm_required=allow_confirm_required)
            if confirmed_ids is None:
                return
            selected_ids = confirmed_ids
            if not selected_ids:
                self._show_resource_status("确认列表中没有保留任何待清理路径。", tone="warning")
                return
            allow_confirm_required = self._selected_has_confirm_required(selected_ids)
            selected_candidates = self._selected_candidates(selected_ids)
            self._begin_cleanup_record(
                mode="常规清理",
                item_count=len(selected_ids),
                estimated_size=sum(int(getattr(candidate, "size_bytes", 0) or 0) for candidate in selected_candidates),
                paths=[str(getattr(candidate, "path", "") or "") for candidate in selected_candidates],
                delete_mode=self._next_delete_mode,
            )
        self._last_cleanup_remove_ids = set(selected_ids) if not dry_run else set()
        worker = _DriveCleanerCleanupWorker(
            report,
            selected_ids,
            dry_run=bool(dry_run),
            allow_confirm_required=allow_confirm_required,
            safe_age_hours=0,
            delete_mode=self._next_delete_mode,
        )
        self._cleanup_worker = worker
        self._set_execution_busy(True, operation="预演" if dry_run else "清理", total=len(selected_ids))
        self._set_mode_status(self._active_execution_mode or self._current_mode, "正在预演清理..." if dry_run else "正在执行清理...")
        worker.progress.connect(self._on_execution_progress)
        worker.detail_progress.connect(self._on_execution_detail_progress)
        worker.item_failed.connect(self._on_execution_item_failed)
        worker.finished_ok.connect(self._on_cleanup_finished)
        worker.failed.connect(self._on_cleanup_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _run_file_cleanup(self, selected_paths: list[str], *, dry_run: bool, title: str) -> None:
        paths = [path for path in selected_paths if path]
        if not paths:
            return
        if not dry_run:
            self._next_delete_mode = "recycle"
            edited_paths = self._confirm_file_cleanup(
                paths,
                title=title,
                detail="将删除勾选的普通文件。",
                estimated_size=self._selected_large_file_size(paths),
                contains_confirm_required=True,
                irreversible_text="否，默认移入回收站；勾选永久删除后不可撤销。",
                danger_level="高",
            )
            if edited_paths is None:
                return
            paths = [path for path in paths if path in set(edited_paths)]
            if not paths:
                self._show_resource_status("确认列表中没有保留任何待清理路径。", tone="warning")
                return
            self._begin_cleanup_record(
                mode=title,
                item_count=len(paths),
                estimated_size=self._selected_large_file_size(paths),
                paths=paths,
                delete_mode=self._next_delete_mode,
            )
        self._last_cleanup_remove_ids = set(paths) if not dry_run else set()
        self._duplicate_cleanup_group_paths = {}
        self._start_file_cleanup_worker([(path, path) for path in paths], dry_run=dry_run)

    def _move_selected_large_files(self) -> None:
        if self._current_mode != "large_files":
            return
        paths = self._selected_ids()
        if not paths:
            self._show_resource_status("请选择要移动的大文件。", tone="warning")
            return
        target_dir = self._drive_get_existing_directory("选择移动目标目录", str(Path.home()))
        if not target_dir:
            return
        strategy = self._confirm_large_move(paths, target_dir)
        if not strategy:
            return
        self._large_move_target = target_dir
        total_size = sum(self._path_size_if_file(path) for path in paths)
        self._begin_cleanup_record(
            mode="移动大文件",
            item_count=len(paths),
            estimated_size=total_size,
            paths=paths,
            delete_mode="move",
        )
        worker = _DriveCleanerMoveWorker(paths, target_dir, conflict_strategy=strategy)
        self._cleanup_worker = worker
        self._set_execution_busy(True, operation="移动大文件", total=len(paths))
        self._set_mode_status(self._active_execution_mode or self._current_mode, f"正在移动大文件到 {target_dir}...")
        worker.progress.connect(self._on_execution_progress)
        worker.item_failed.connect(self._on_execution_item_failed)
        worker.finished_ok.connect(self._on_cleanup_finished)
        worker.failed.connect(self._on_cleanup_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _confirm_large_move(self, paths: list[str], target_dir: str) -> str:
        dialog = QDialog(self)
        dialog.setWindowTitle("确认移动大文件")
        self._apply_dialog_caption_color(dialog)
        dialog.setMinimumSize(560, 360)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        total_size = sum(self._path_size_if_file(path) for path in paths)
        summary = QLabel(
            f"文件数量：{len(paths)}\n"
            f"总大小：{format_bytes(total_size)}\n"
            f"目标目录：{target_dir}\n"
            "是否覆盖同名文件：否\n"
            "失败处理策略：默认跳过同名文件，也可以选择自动重命名。"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        strategy_box = ModernPopupComboBox()
        strategy_box.setObjectName("DriveCleanerCombo")
        self._prepare_drive_combo(strategy_box)
        strategy_box.addItem("同名文件：跳过", "skip")
        strategy_box.addItem("同名文件：自动重命名", "rename")
        layout.addWidget(strategy_box)
        preview = QTextEdit()
        preview.setReadOnly(True)
        preview.setAcceptRichText(False)
        lines = [f"- {path}" for path in paths[:10]]
        if len(paths) > 10:
            lines.append(f"... 还有 {len(paths) - 10} 项")
        preview.setPlainText("\n".join(lines))
        layout.addWidget(preview, 1)
        confirm = QCheckBox("我已确认移动目标目录和冲突处理策略")
        confirm.setObjectName("DriveConfirmCheckBox")
        layout.addWidget(confirm)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("开始移动")
            ok_btn.setEnabled(False)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText("取消")
        confirm.stateChanged.connect(lambda *_: ok_btn.setEnabled(confirm.isChecked()) if ok_btn is not None else None)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not confirm.isChecked():
            return ""
        return str(strategy_box.currentData() or "skip")

    def _selected_software_entries(self, selected_ids: list[str]) -> list[SoftwareEntry]:
        by_id = {entry.id: entry for entry in self._software_entries}
        return [by_id[item_id] for item_id in selected_ids if item_id in by_id and not by_id[item_id].is_system]

    def _run_software_uninstall(self, *, force: bool) -> None:
        selected_ids = self._selected_ids()
        selected_entries = self._selected_software_entries(selected_ids)
        if not selected_entries:
            self._show_resource_status("请选择可卸载的用户软件。", tone="warning")
            return
        edited_paths_by_id = self._confirm_software_uninstall(selected_entries, force=force)
        if edited_paths_by_id is None:
            return
        worker_entries = self._software_entries
        if force:
            worker_entries = self._software_entries_with_edited_force_paths(edited_paths_by_id)
        self._begin_cleanup_record(
            mode="强力卸载" if force else "标准卸载",
            item_count=len(selected_entries),
            estimated_size=sum(int(entry.size_bytes or 0) for entry in selected_entries),
            paths=[
                path
                for entry in selected_entries
                for path in ([entry.name] + self._software_entry_paths(entry))
                if str(path).strip()
            ],
            delete_mode="uninstall",
        )
        worker = _SoftwareUninstallWorker(worker_entries, [entry.id for entry in selected_entries], force=bool(force))
        self._cleanup_worker = worker
        self._set_execution_busy(True, operation="强力卸载" if force else "标准卸载", total=len(selected_entries))
        self._set_mode_status(self._active_execution_mode or self._current_mode, "正在执行强力卸载..." if force else "正在启动标准卸载...")
        worker.progress.connect(self._on_execution_progress)
        worker.item_failed.connect(self._on_execution_item_failed)
        worker.finished_ok.connect(self._on_software_uninstall_finished)
        worker.failed.connect(self._on_cleanup_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _confirm_software_uninstall(self, entries: list[SoftwareEntry], *, force: bool) -> Optional[dict[str, list[str]]]:
        missing_uninstall_count = sum(1 for entry in entries if not (entry.uninstall_command or entry.quiet_uninstall_command))
        scope_counts = self._software_uninstall_scope_counts(entries)
        preview_rows: list[tuple[str, str, str]] = []
        for entry in entries:
            entry_title = f"{entry.name} {entry.version}".strip()
            if force:
                for path in self._software_entry_paths(entry):
                    label, reason = self._path_risk_info(path)
                    default_note = "；高风险项默认不处理" if self._risk_level_rank(label) >= 4 or self._path_is_protected_for_direct_delete(path) else ""
                    preview_rows.append((f"{entry_title} | 目录: {path}", label, reason + default_note))
                if entry.registry_root or entry.registry_path:
                    preview_rows.append((f"{entry_title} | 注册表: {entry.registry_root}\\{entry.registry_path}", "高风险", "卸载登记项，不进入回收站"))
                if not self._software_entry_paths(entry):
                    preview_rows.append((entry_title, "高风险", "未发现可确认目录，仅能尝试清理卸载登记信息"))
            elif entry.uninstall_command or entry.quiet_uninstall_command:
                command = self._software_uninstall_command(entry)
                command_path = self._software_command_path(entry)
                command_reason = "调用软件自带卸载程序"
                command_exists = False
                if command_path:
                    try:
                        command_exists = Path(command_path).exists()
                    except OSError:
                        command_exists = False
                    command_reason += "；卸载器存在" if command_exists else "；卸载器路径失效"
                preview_rows.append((f"{entry_title} | 卸载命令: {command}", "低风险" if not command_path or command_exists else "高风险", command_reason))
                preview_rows.append((f"{entry_title} | 安装目录: {entry.install_location or '未发现'}", "需确认", "卸载前展示安装位置，标准卸载不会直接删除该目录"))
                for path in self._software_entry_paths(entry)[:2]:
                    label, reason = self._path_risk_info(path)
                    preview_rows.append((f"{entry_title} | 路径预览: {path}", label, reason))
            else:
                preview_rows.append((entry_title, "高风险", "未找到标准卸载命令"))
        contains_confirm_required = force or missing_uninstall_count > 0
        operation = "强力卸载" if force else "标准卸载"
        if force:
            detail = (
                "强力卸载会处理残留目录、配置/缓存目录，并尝试清理卸载登记信息；"
                f"影响范围：注册表项 {scope_counts['registry_items']} 项，安装目录 {scope_counts['install_dirs']} 个，"
                f"快捷方式 {scope_counts['shortcuts']} 个，开机启动项 {scope_counts['startup_items']} 个，"
                f"缓存/配置目录 {scope_counts['data_dirs']} 个。高风险路径默认不处理；普通残留路径优先移入回收站，注册表项属于不可恢复项目。"
            )
        else:
            detail = (
                "标准卸载只启动系统或软件自带卸载程序；卸载向导结束后会扫描残留候选，"
                "残留目录、快捷方式、配置/缓存和注册表项默认只提示，不会自动清理。"
            )
        accepted, _permanent = self._show_drive_danger_confirm(
            title=f"确认{operation}",
            operation=operation,
            action_text=operation,
            item_count=len(entries),
            estimated_size=sum(int(entry.size_bytes or 0) for entry in entries),
            preview_rows=preview_rows,
            detail=detail,
            danger_level="高风险" if force else ("中风险" if missing_uninstall_count == 0 else "高风险"),
            contains_confirm_required=contains_confirm_required,
            irreversible_text="是，强力卸载会清理注册表等不可恢复项目；普通残留路径优先移入回收站。" if force else "否，标准卸载由原厂卸载程序处理；残留候选仅提示。",
            notes=self._special_operation_notes(
                operation=operation,
                paths=[row[0] for row in preview_rows],
                force_uninstall=force,
                standard_uninstall=not force,
            ),
            require_second_confirm=force or missing_uninstall_count > 0,
        )
        if not accepted:
            return None
        if not force:
            return {entry.id: [] for entry in entries}
        return {
            entry.id: self._software_default_force_paths(entry)
            for entry in entries
        }

    def _software_entries_with_edited_force_paths(self, edited_paths_by_id: dict[str, list[str]]) -> list[SoftwareEntry]:
        output: list[SoftwareEntry] = []
        for entry in self._software_entries:
            if entry.id not in edited_paths_by_id:
                output.append(entry)
                continue
            paths = edited_paths_by_id.get(entry.id, [])
            output.append(
                SoftwareEntry(
                    id=entry.id,
                    name=entry.name,
                    version=entry.version,
                    publisher=entry.publisher,
                    size_bytes=entry.size_bytes,
                    install_location=" | ".join(paths),
                    data_location="",
                    uninstall_command=entry.uninstall_command,
                    quiet_uninstall_command=entry.quiet_uninstall_command,
                    registry_root=entry.registry_root,
                    registry_path=entry.registry_path,
                    registry_view=entry.registry_view,
                    software_type=entry.software_type,
                    is_system=entry.is_system,
                    reason=entry.reason,
                )
            )
        return output

    def _start_file_cleanup_worker(self, items: list[tuple[str, str]], *, dry_run: bool) -> None:
        worker = _DriveCleanerFileCleanupWorker(items, dry_run=bool(dry_run), delete_mode=self._next_delete_mode)
        self._cleanup_worker = worker
        operation = "预演" if dry_run else ("清理重复文件" if self._current_mode == "duplicates" else "删除大文件")
        self._set_execution_busy(True, operation=operation, total=len(items))
        self._set_mode_status(self._active_execution_mode or self._current_mode, "正在预演清理..." if dry_run else "正在执行清理...")
        worker.progress.connect(self._on_execution_progress)
        worker.item_failed.connect(self._on_execution_item_failed)
        worker.finished_ok.connect(self._on_cleanup_finished)
        worker.failed.connect(self._on_cleanup_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _selected_large_file_size(self, paths: list[str]) -> int:
        report = self._reports.get("large_files")
        if report is None:
            return 0
        selected = {str(path) for path in paths}
        return sum(
            int(getattr(entry, "size_bytes", 0) or 0)
            for result in report.drives
            for entry in result.large_files
            if str(getattr(entry, "path", "")) in selected
        )

    def _path_size_if_file(self, path: str) -> int:
        try:
            target = Path(path)
            if target.exists() and target.is_file() and not target.is_symlink():
                return int(target.stat().st_size)
        except OSError:
            return 0
        return 0

    def _danger_operation_summary_text(
        self,
        *,
        operation: str,
        danger_level: str,
        item_count: int,
        estimated_size: int,
        preview_count: int,
        contains_confirm_required: bool,
        irreversible_text: str,
    ) -> str:
        size_text = format_bytes(int(estimated_size or 0)) if int(estimated_size or 0) > 0 else "未知"
        confirm_text = "是" if contains_confirm_required else "否"
        return (
            f"危险等级：{danger_level}\n"
            f"操作类型：{operation}\n"
            f"将处理：{int(item_count or 0)} 项\n"
            f"预计释放大小：{size_text}\n"
            f"路径预览：{int(preview_count or 0)} 条\n"
            f"包含“需确认”项目：{confirm_text}\n"
            f"是否不可恢复：{irreversible_text}"
        )

    def _normalized_risk_path(self, path: str) -> str:
        return str(path or "").strip().replace("/", "\\").rstrip("\\").lower()

    def _path_risk_info(self, path: str) -> tuple[str, str]:
        normalized = self._normalized_risk_path(path)
        if not normalized:
            return ("需确认", "路径为空或无法识别")
        if self._path_is_protected_for_direct_delete(path):
            return ("高风险", "配置、数据库或用户配置类文件默认保护")
        protected_runtime_tokens = (
            "\\microsoft\\windows\\webcache",
            "\\windows defender\\quarantine",
            "\\windows defender\\definition updates\\backup",
        )
        if any(token in normalized for token in protected_runtime_tokens):
            return ("高风险", "系统占用或安全防护目录，普通清理默认跳过")
        if "\\user data\\" in normalized and any(token in normalized for token in ("\\cache", "\\service worker")):
            return ("需确认", "浏览器运行时缓存；建议关闭浏览器后再清理")
        drive = Path(path).drive
        system_drive = self._norm_drive_root(self._system_drive_root())
        system_root = self._normalized_risk_path(os.environ.get("SystemRoot", r"C:\Windows"))
        program_data = self._normalized_risk_path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        program_roots = [
            f"{drive}\\program files".lower() if drive else "",
            f"{drive}\\program files (x86)".lower() if drive else "",
        ]
        if normalized == system_drive or normalized == system_root or normalized.startswith(system_root + "\\"):
            return ("高风险", "系统目录或系统盘根目录")
        if program_data and (normalized == program_data or normalized.startswith(program_data + "\\")):
            return ("高风险", "ProgramData 系统级应用数据")
        if any(root and (normalized == root or normalized.startswith(root + "\\")) for root in program_roots):
            return ("高风险", "程序安装目录")
        user_profile = self._normalized_risk_path(os.environ.get("USERPROFILE", str(Path.home())))
        user_dirs = [
            f"{user_profile}\\desktop",
            f"{user_profile}\\documents",
            f"{user_profile}\\downloads",
            f"{user_profile}\\pictures",
            f"{user_profile}\\videos",
            f"{user_profile}\\music",
        ]
        if any(root and (normalized == root or normalized.startswith(root + "\\")) for root in user_dirs):
            return ("需确认", "用户文档或主动保存目录")
        if any(part in normalized for part in ("\\temp\\", "\\tmp\\", "\\cache\\", "\\caches\\")):
            return ("安全", "缓存或临时目录")
        return ("需确认", "普通路径，执行前请确认内容")

    def _path_is_protected_for_direct_delete(self, path: str) -> bool:
        normalized = self._normalized_risk_path(path)
        suffix = Path(str(path or "")).suffix.lower()
        if suffix in {".db", ".sqlite", ".sqlite3", ".json", ".yaml", ".yml", ".toml", ".ini"}:
            return True
        protected_tokens = ("\\user data\\default\\preferences", "\\user data\\default\\secure preferences", "\\config\\", "\\settings\\")
        return any(token in normalized for token in protected_tokens)

    def _candidate_can_permanent_delete(self, candidate: object) -> bool:
        if self._path_is_protected_for_direct_delete(str(getattr(candidate, "path", "") or "")):
            return False
        risk_label, _ = self._candidate_risk_info(candidate)
        text = f"{getattr(candidate, 'kind', '')} {getattr(candidate, 'source_rule', '')} {getattr(candidate, 'reason', '')} {getattr(candidate, 'path', '')}".lower()
        return risk_label in {"安全", "低风险"} and any(token in text for token in ("temp", "tmp", "cache", "缓存", "临时"))

    def _candidate_risk_info(self, candidate: object) -> tuple[str, str]:
        risk = str(getattr(candidate, "risk", "") or "")
        path_label, path_reason = self._path_risk_info(str(getattr(candidate, "path", "") or ""))
        if risk == AVOID:
            return ("高风险", "本地规则不建议处理")
        if risk == CONFIRM_REQUIRED:
            return ("需确认", str(getattr(candidate, "reason", "") or path_reason))
        if path_label in {"高风险", "中高风险"}:
            return (path_label, path_reason)
        return ("安全", str(getattr(candidate, "reason", "") or "普通缓存/临时项"))

    def _risk_level_rank(self, label: str) -> int:
        text = str(label or "")
        if "高风险" in text or text == "高":
            return 4
        if "中高" in text:
            return 3
        if "需确认" in text or "中风险" in text or text == "中":
            return 2
        if "低风险" in text or "安全" in text or text == "低":
            return 1
        return 2

    def _merge_danger_level(self, levels: list[str], fallback: str = "中风险") -> str:
        rank = max([self._risk_level_rank(level) for level in levels] or [self._risk_level_rank(fallback)])
        if rank >= 4:
            return "高风险"
        if rank == 3:
            return "中高风险"
        if rank == 2:
            return "中风险"
        return "低风险"

    def _special_operation_notes(self, *, operation: str, paths: list[str], force_uninstall: bool = False, standard_uninstall: bool = False) -> list[str]:
        notes: list[str] = []
        text = "\n".join(paths).lower().replace("/", "\\")
        if "$recycle.bin" in text or "回收站" in operation:
            notes.append("清理回收站：回收站内容将永久删除，无法从软件内恢复。")
        if "browser_cache" in text or "\\chrome\\" in text or "\\edge\\" in text or "\\firefox\\" in text or "浏览器缓存" in operation:
            notes.append("浏览器缓存：可能需要重新登录部分网站或重新加载资源。")
        if "\\downloads" in text or "下载目录" in operation:
            notes.append("下载目录：可能包含用户主动保存的安装包、文档或压缩包。")
        if standard_uninstall:
            notes.append("标准卸载：调用系统或软件自带卸载程序，风险较低；卸载过程由原厂向导控制。")
        if force_uninstall:
            notes.append("强力卸载：会额外处理残留目录、配置和卸载注册表项，可能误删关联数据。")
        return notes

    def _preview_rows_from_paths(self, paths: list[str]) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for path in paths:
            label, reason = self._path_risk_info(path)
            rows.append((str(path), label, reason))
        return rows

    def _show_drive_danger_confirm(
        self,
        *,
        title: str,
        operation: str,
        action_text: str,
        item_count: int,
        estimated_size: int,
        preview_rows: list[tuple[str, str, str]],
        detail: str,
        danger_level: str,
        contains_confirm_required: bool,
        irreversible_text: str,
        notes: Optional[list[str]] = None,
        require_second_confirm: bool = False,
        show_permanent_option: bool = False,
        allow_permanent_delete: bool = False,
    ) -> tuple[bool, bool]:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        self._apply_dialog_caption_color(dialog)
        dialog.setMinimumSize(620, 430)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        is_irreversible = "是" in str(irreversible_text)
        if is_irreversible and ("不会进入回收站" in str(irreversible_text) or "永久" in str(irreversible_text)):
            extra_irreversible = "\n此操作不会进入回收站。"
        elif is_irreversible:
            extra_irreversible = "\n此操作包含不可恢复项目。"
        else:
            extra_irreversible = ""
        summary = QLabel(
            self._danger_operation_summary_text(
                operation=operation,
                danger_level=danger_level,
                item_count=item_count,
                estimated_size=estimated_size,
                preview_count=len(preview_rows),
                contains_confirm_required=contains_confirm_required,
                irreversible_text=irreversible_text,
            )
            + extra_irreversible
            + (f"\n{detail}" if detail else "")
        )
        summary.setWordWrap(True)
        root.addWidget(summary)

        preview_box = QTextEdit()
        preview_box.setReadOnly(True)
        preview_box.setAcceptRichText(False)
        preview_limit = 10
        preview_lines = [
            f"- [{risk}] {path}" + (f"  · {reason}" if reason else "")
            for path, risk, reason in preview_rows[:preview_limit]
        ]
        remaining = max(0, len(preview_rows) - preview_limit)
        if remaining:
            preview_lines.append(f"... 还有 {remaining} 项未显示")
        preview_box.setPlainText("\n".join(preview_lines) if preview_lines else "无路径预览")
        root.addWidget(preview_box, 1)

        all_notes = list(notes or [])
        if all_notes:
            note_label = QLabel("\n".join(f"• {note}" for note in all_notes))
            note_label.setWordWrap(True)
            root.addWidget(note_label)

        permanent_check = None
        if show_permanent_option:
            permanent_check = QCheckBox("永久删除（不进入回收站，不可撤销）")
            permanent_check.setObjectName("DriveConfirmCheckBox")
            permanent_check.setChecked(False)
            permanent_check.setEnabled(bool(allow_permanent_delete))
            if not allow_permanent_delete:
                permanent_check.setToolTip("包含配置、数据库、系统/程序目录或非低风险项目，默认不允许直接永久删除。")
            root.addWidget(permanent_check)

        confirm = QCheckBox(f"我已确认本次“{operation}”的影响范围")
        confirm.setObjectName("DriveConfirmCheckBox")
        root.addWidget(confirm)
        second_confirm = None
        if require_second_confirm or contains_confirm_required or is_irreversible or show_permanent_option or self._risk_level_rank(danger_level) >= 4:
            second_confirm = QCheckBox("我已了解风险，并确认继续执行")
            second_confirm.setObjectName("DriveConfirmCheckBox")
            root.addWidget(second_confirm)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText(action_text)
            ok_btn.setEnabled(False)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText("取消")

        def refresh_ok() -> None:
            if ok_btn is None:
                return
            second_ok = True if second_confirm is None else second_confirm.isChecked()
            ok_btn.setEnabled(confirm.isChecked() and second_ok)

        confirm.stateChanged.connect(lambda *_: refresh_ok())
        if second_confirm is not None:
            second_confirm.stateChanged.connect(lambda *_: refresh_ok())
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        root.addWidget(buttons)
        SettingsDialog._install_custom_text_context_menus(dialog, dialog)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted and confirm.isChecked() and (second_confirm is None or second_confirm.isChecked())
        permanent = bool(permanent_check is not None and permanent_check.isEnabled() and permanent_check.isChecked())
        return accepted, permanent

    def _confirm_file_cleanup(
        self,
        paths: list[str],
        *,
        title: str,
        detail: str,
        estimated_size: int,
        contains_confirm_required: bool,
        irreversible_text: str,
        danger_level: str,
    ) -> Optional[list[str]]:
        preview_rows = self._preview_rows_from_paths(paths)
        merged_level = self._merge_danger_level([risk for _, risk, _ in preview_rows], danger_level)
        allow_permanent = not any(self._path_is_protected_for_direct_delete(path) for path in paths)
        accepted, permanent = self._show_drive_danger_confirm(
            title=title,
            operation=title,
            action_text="执行清理",
            item_count=len(paths),
            estimated_size=estimated_size,
            preview_rows=preview_rows,
            detail=detail,
            danger_level=merged_level,
            contains_confirm_required=contains_confirm_required or any(self._risk_level_rank(risk) >= 2 for _, risk, _ in preview_rows),
            irreversible_text=irreversible_text,
            notes=self._special_operation_notes(operation=title, paths=paths),
            require_second_confirm=True,
            show_permanent_option=True,
            allow_permanent_delete=allow_permanent,
        )
        self._next_delete_mode = "permanent" if permanent else "recycle"
        return list(paths) if accepted else None

    def _selected_candidates(self, selected_ids: list[str]) -> list[object]:
        report = self._reports.get("cleanup")
        if report is None:
            return []
        candidates = {c.id: c for r in report.drives for c in r.candidates}
        return [candidates[item_id] for item_id in selected_ids if item_id in candidates]

    def _selected_has_confirm_required(self, selected_ids: list[str]) -> bool:
        return any(str(getattr(candidate, "risk", "")) == CONFIRM_REQUIRED for candidate in self._selected_candidates(selected_ids))

    def _selected_duplicate_groups(self, selected_ids: list[str]) -> list[object]:
        report = self._reports.get("duplicates")
        if report is None:
            return []
        selected = set(selected_ids)
        return [
            group
            for result in report.drives
            for group in result.duplicate_groups
            if str(getattr(group, "hash", "")) in selected
        ]

    def _confirm_cleanup(self, selected_ids: list[str], *, require_confirm_required: bool) -> Optional[list[str]]:
        selected = self._selected_candidates(selected_ids)
        if not selected:
            return None
        total_size = sum(candidate.size_bytes for candidate in selected)
        safe_count = sum(1 for candidate in selected if str(candidate.risk) == SAFE)
        confirm_count = sum(1 for candidate in selected if str(candidate.risk) == CONFIRM_REQUIRED)
        preview_rows = [
            (
                str(getattr(candidate, "path", "") or ""),
                self._candidate_risk_info(candidate)[0],
                self._candidate_risk_info(candidate)[1],
            )
            for candidate in selected
        ]
        danger_level = self._merge_danger_level([risk for _, risk, _ in preview_rows], "中风险" if confirm_count else "低风险")
        allow_permanent = all(self._candidate_can_permanent_delete(candidate) for candidate in selected)
        accepted, permanent = self._show_drive_danger_confirm(
            title="确认执行清理",
            operation="常规清理",
            action_text="执行清理",
            item_count=len(selected),
            estimated_size=total_size,
            preview_rows=preview_rows,
            detail=f"其中安全 {safe_count} 项，需确认 {confirm_count} 项。建议先关闭浏览器、IDE、包管理器等关联程序。",
            danger_level=danger_level,
            contains_confirm_required=confirm_count > 0 or require_confirm_required,
            irreversible_text="否，默认移入回收站；勾选永久删除后不可撤销。",
            notes=self._special_operation_notes(
                operation="常规清理",
                paths=[str(getattr(candidate, "path", "") or "") for candidate in selected],
            ),
            require_second_confirm=confirm_count > 0 or require_confirm_required or self._risk_level_rank(danger_level) >= 4,
            show_permanent_option=True,
            allow_permanent_delete=allow_permanent,
        )
        if not accepted:
            return None
        self._next_delete_mode = "permanent" if permanent else "recycle"
        return [str(getattr(candidate, "id", "")) for candidate in selected]

    def _software_entry_paths(self, entry: SoftwareEntry) -> list[str]:
        return software_entry_force_paths(entry)

    def _software_default_force_paths(self, entry: SoftwareEntry) -> list[str]:
        paths: list[str] = []
        for path in self._software_entry_paths(entry):
            risk, _reason = self._path_risk_info(path)
            if self._risk_level_rank(risk) >= 4 or self._path_is_protected_for_direct_delete(path):
                continue
            paths.append(path)
        return paths

    def _software_shortcut_search_roots(self, *, startup_only: bool = False) -> list[Path]:
        roots: list[Path] = []
        if startup_only:
            candidates = [
                os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"),
                os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"),
            ]
        else:
            candidates = [
                os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
                os.path.join(os.environ.get("PUBLIC", ""), "Desktop"),
                os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
                os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
            ]
        for candidate in candidates:
            try:
                path = Path(os.path.expandvars(candidate))
                if path.exists() and path.is_dir():
                    roots.append(path)
            except OSError:
                continue
        return roots

    def _software_shortcut_candidates(self, entry: SoftwareEntry, *, startup_only: bool = False, limit: int = 20) -> list[str]:
        name_key = re.sub(r"\s+", " ", str(entry.name or "").lower()).strip()
        if not name_key:
            return []
        matches: list[str] = []
        for root in self._software_shortcut_search_roots(startup_only=startup_only):
            try:
                for dirpath, _dirnames, filenames in os.walk(root):
                    for filename in filenames:
                        if not filename.lower().endswith(".lnk"):
                            continue
                        stem = Path(filename).stem.lower()
                        if name_key in stem or stem in name_key:
                            matches.append(str(Path(dirpath) / filename))
                            if len(matches) >= limit:
                                return matches
            except OSError:
                continue
        return matches

    def _estimate_existing_path_size(self, path_text: str, *, max_files: int = 2000) -> int:
        try:
            path = Path(os.path.expandvars(str(path_text or "").strip()))
            if not path.exists() or path.is_symlink():
                return 0
            if path.is_file():
                return max(0, int(path.stat().st_size))
            total = 0
            seen = 0
            for dirpath, _dirnames, filenames in os.walk(path):
                for filename in filenames:
                    if seen >= max_files:
                        return total
                    seen += 1
                    try:
                        total += int((Path(dirpath) / filename).stat().st_size)
                    except OSError:
                        continue
            return total
        except OSError:
            return 0

    def _software_residual_candidates(self, entries: list[SoftwareEntry]) -> list[dict[str, object]]:
        residuals: list[dict[str, object]] = []
        for entry in entries:
            entry_title = f"{entry.name} {entry.version}".strip()
            for path in self._software_entry_paths(entry):
                try:
                    target = Path(os.path.expandvars(path))
                    if not target.exists():
                        continue
                except OSError:
                    continue
                category = "配置/缓存目录" if path == entry.data_location or any(token in self._normalized_risk_path(path) for token in ("\\appdata\\", "\\cache\\", "\\config\\")) else "残留目录"
                risk, reason = self._path_risk_info(path)
                residuals.append({
                    "software": entry_title,
                    "category": category,
                    "path": path,
                    "risk": risk,
                    "reason": reason,
                    "size": self._estimate_existing_path_size(path),
                })
            for shortcut in self._software_shortcut_candidates(entry):
                residuals.append({
                    "software": entry_title,
                    "category": "快捷方式",
                    "path": shortcut,
                    "risk": "低风险",
                    "reason": "开始菜单或桌面快捷方式",
                    "size": self._estimate_existing_path_size(shortcut),
                })
            if entry.registry_root or entry.registry_path:
                residuals.append({
                    "software": entry_title,
                    "category": "注册表项",
                    "path": f"{entry.registry_root}\\{entry.registry_path}".strip("\\"),
                    "risk": "高风险",
                    "reason": "卸载登记项，不进入回收站，需单独确认",
                    "size": 0,
                })
        return residuals

    def _software_uninstall_scope_counts(self, entries: list[SoftwareEntry]) -> dict[str, int]:
        install_dirs = 0
        data_dirs = 0
        registry_items = 0
        shortcuts = 0
        startup_items = 0
        high_risk = 0
        for entry in entries:
            install_dirs += len([path for path in str(entry.install_location or "").split(" | ") if path.strip()])
            data_dirs += len([path for path in str(entry.data_location or "").split(" | ") if path.strip()])
            registry_items += 1 if (entry.registry_root or entry.registry_path) else 0
            shortcuts += len(self._software_shortcut_candidates(entry))
            startup_items += len(self._software_shortcut_candidates(entry, startup_only=True))
            high_risk += sum(1 for path in self._software_entry_paths(entry) if self._risk_level_rank(self._path_risk_info(path)[0]) >= 4)
        return {
            "install_dirs": install_dirs,
            "data_dirs": data_dirs,
            "registry_items": registry_items,
            "shortcuts": shortcuts,
            "startup_items": startup_items,
            "high_risk": high_risk,
        }

    def _append_standard_residual_summary(self, entries: list[SoftwareEntry]) -> None:
        residuals = self._software_residual_candidates(entries)
        if not residuals:
            self._append_log("标准卸载后未发现可展示的残留候选。")
            self._set_mode_status("uninstall", "标准卸载完成，未发现明显残留候选。")
            self._show_resource_status("标准卸载完成，未发现明显残留候选。", tone="success", auto_hide_ms=3600)
            return
        total_size = sum(int(item.get("size", 0) or 0) for item in residuals)
        category_counts: dict[str, int] = {}
        confirm_count = 0
        for item in residuals:
            category = str(item.get("category", "其它") or "其它")
            category_counts[category] = category_counts.get(category, 0) + 1
            if self._risk_level_rank(str(item.get("risk", ""))) >= 2:
                confirm_count += 1
        summary = (
            f"发现残留候选 {len(residuals)} 项，预计 {format_bytes(total_size)}，"
            f"其中 {confirm_count} 项需确认；已默认保留，未自动清理。"
        )
        self._set_mode_status("uninstall", summary)
        self._append_log(summary)
        self._append_log("残留分类：" + "；".join(f"{key} {value} 项" for key, value in category_counts.items()))
        self._append_log("残留候选路径预览：")
        for item in residuals[:12]:
            self._append_log(
                f"- [{item.get('risk', '')}] {item.get('category', '')}: {item.get('path', '')}"
                f" · {format_bytes(int(item.get('size', 0) or 0))} · {item.get('reason', '')}"
            )
        if len(residuals) > 12:
            self._append_log(f"... 还有 {len(residuals) - 12} 项残留候选未显示")
        self._show_resource_status(summary, tone="warning" if confirm_count else "info", auto_hide_ms=5200)

    def _paths_kept_after_edit(self, original_paths: list[str], edited_text: str) -> list[str]:
        text = str(edited_text or "")
        kept: list[str] = []
        for path in original_paths:
            value = str(path or "").strip()
            if value and value in text and value not in kept:
                kept.append(value)
        return kept

    def _on_cleanup_finished(self, report: object) -> None:
        mode = self._active_execution_mode or self._current_mode
        self._cleanup_worker = None
        if isinstance(report, CleanupReport):
            self._append_log(cleanup_report_to_markdown(report))
            freed = sum(item.freed_size for item in report.items)
            ok_count = sum(1 for item in report.items if item.status in {"Success", "Partial"})
            failed_count = sum(1 for item in report.items if item.status not in {"Success", "Partial", "DryRun"})
            if not report.dry_run:
                self._remove_cleaned_candidates(report, mode=mode)
                self._refresh_drive_list(preserve_selection=True)
            summary = self._summarize_cleanup_result(report, mode=mode)
            self._set_execution_busy(False)
            self._set_mode_status(mode, summary)
            known_failure_paths = {row.get("path", "") for row in self._execution_failures}
            for item in report.items:
                if item.status not in {"Success", "Partial", "DryRun"} and item.path not in known_failure_paths:
                    self._on_execution_item_failed(item.path, item.mode, item.first_error or item.status)
            delete_modes = {str(getattr(item, "delete_mode", "") or "") for item in report.items}
            if (not report.dry_run) and "recycle" in delete_modes and ok_count > 0:
                self._open_recycle_bin_btn.setVisible(True)
            record_status = "已取消" if self._execution_cancel_requested else ("部分完成" if self._execution_failures or failed_count else "完成")
            self._finish_cleanup_record(actual_size=freed, failures=self._execution_failures, status=record_status)
            if self._execution_failures or failed_count:
                self._append_failure_report_to_log()
                self._last_failure_report = self._build_failure_report(
                    mode=self._cleanup_operation_title(report, mode=mode),
                    started_at=str(report.started_at),
                    success_count=ok_count if not report.dry_run else len(report.items) - failed_count,
                    failed_count=max(failed_count, len(self._execution_failures)),
                    released_size=freed,
                )
                self._copy_failures_btn.setVisible(True)
            self._show_resource_status(
                summary,
                tone="warning" if self._execution_failures or failed_count else "success",
                auto_hide_ms=4200,
            )
            self._execution_cancel_requested = False
            self._active_execution_mode = ""
            self._refresh_actions()

    def _on_software_uninstall_finished(self, report: object) -> None:
        self._cleanup_worker = None
        if not isinstance(report, SoftwareUninstallReport):
            self._set_execution_busy(False)
            self._set_mode_status("uninstall", "卸载结果异常")
            self._active_execution_mode = ""
            self._refresh_actions()
            return
        self._append_log(software_uninstall_report_to_text(report))
        selected_entry_by_id = {entry.id: entry for entry in self._software_entries}
        handled_standard_entries = [
            selected_entry_by_id[item.id]
            for item in report.items
            if report.mode == "standard" and item.status in {"Started", "Success", "Partial"} and item.id in selected_entry_by_id
        ]
        success_ids = {
            item.id
            for item in report.items
            if report.mode == "force" and item.status in {"Success", "Partial"}
        }
        if success_ids:
            self._software_entries = [entry for entry in self._software_entries if entry.id not in success_ids]
            self._populate_software_table(self._software_entries)
        handled = sum(1 for item in report.items if item.status in {"Started", "Success", "Partial"})
        failed_count = len(report.items) - handled
        summary = self._summarize_uninstall_result(report)
        self._set_execution_busy(False)
        self._set_mode_status("uninstall", summary)
        known_failure_paths = {row.get("path", "") for row in self._execution_failures}
        for item in report.items:
            marker = item.name
            if item.status not in {"Started", "Success", "Partial"} and marker not in known_failure_paths:
                self._on_execution_item_failed(marker, "强力卸载" if report.mode == "force" else "标准卸载", item.message or item.status)
        record_status = "已取消" if self._execution_cancel_requested else ("部分完成" if self._execution_failures or failed_count else "完成")
        self._finish_cleanup_record(actual_size=0, failures=self._execution_failures, status=record_status)
        if self._execution_failures or failed_count:
            self._append_failure_report_to_log()
            self._last_failure_report = self._build_failure_report(
                mode="强力卸载" if report.mode == "force" else "标准卸载",
                started_at=str(report.started_at),
                success_count=handled,
                failed_count=max(failed_count, len(self._execution_failures)),
                released_size=0,
            )
            self._copy_failures_btn.setVisible(True)
        self._show_resource_status(
            summary,
            tone="warning" if self._execution_failures or failed_count else "success",
            auto_hide_ms=4200,
        )
        if handled_standard_entries and not self._execution_cancel_requested:
            self._set_mode_status("uninstall", "正在扫描标准卸载后的残留候选...")
            self._append_log("正在扫描标准卸载后的残留候选...")
            self._append_standard_residual_summary(handled_standard_entries)
        self._execution_cancel_requested = False
        self._active_execution_mode = ""
        self._refresh_actions()

    def _on_cleanup_failed(self, message: str) -> None:
        mode = self._active_execution_mode or self._current_mode
        self._cleanup_worker = None
        self._set_execution_busy(False)
        self._append_log(f"清理失败: {message}")
        self._set_mode_status(mode, "清理失败")
        self._show_resource_status(f"清理失败：{message}", tone="error", auto_hide_ms=5200)
        self._finish_cleanup_record(actual_size=0, failures=self._execution_failures, status="失败")
        self._execution_cancel_requested = False
        self._active_execution_mode = ""
        self._refresh_actions()

    def _remove_cleaned_candidates(self, cleanup_report: CleanupReport, *, mode: str = "") -> None:
        mode = str(mode or self._current_mode)
        successful_item_ids = {
            item.id
            for item in cleanup_report.items
            if item.status == "Success" or (item.status == "Partial" and int(item.freed_size or 0) > 0)
        }
        if mode == "duplicates":
            successful_paths = {
                item.path
                for item in cleanup_report.items
                if item.status == "Success" or (item.status == "Partial" and int(item.freed_size or 0) > 0)
            }
            remove_ids = {
                group_id
                for group_id, paths in self._duplicate_cleanup_group_paths.items()
                if paths & successful_paths
            }
        else:
            remove_ids = successful_item_ids
        if not remove_ids:
            self._last_cleanup_remove_ids = set()
            self._duplicate_cleanup_group_paths = {}
            return
        table = self._tables.get(mode)
        if table is not None:
            sorting = table.isSortingEnabled()
            table.setSortingEnabled(False)
            table.blockSignals(True)
            try:
                for row in range(table.rowCount() - 1, -1, -1):
                    item = table.item(row, 0)
                    if item is not None and str(item.data(Qt.ItemDataRole.UserRole) or "") in remove_ids:
                        table.removeRow(row)
            finally:
                table.blockSignals(False)
                table.setSortingEnabled(sorting)
        report = self._reports.get(mode)
        if report is not None:
            if mode == "cleanup":
                for result in report.drives:
                    result.candidates = [candidate for candidate in result.candidates if candidate.id not in remove_ids]
            elif mode == "large_files":
                for result in report.drives:
                    result.large_files = [entry for entry in result.large_files if entry.path not in remove_ids]
            elif mode == "duplicates":
                for result in report.drives:
                    result.duplicate_groups = [group for group in result.duplicate_groups if group.hash not in remove_ids]
        self._last_cleanup_remove_ids = set()
        self._duplicate_cleanup_group_paths = {}

    def _start_llm_advice(self, report: ScanReport) -> None:
        runtime = self._main_window._drive_cleaner_qa_runtime_config()
        if not runtime["ok"]:
            self._set_advisor_plain_text(f"{runtime['error']}，已使用本地规则建议。\n\n{report.advisor_summary}")
            return
        model_name = str(runtime["model_name"])
        self._set_advisor_plain_text(f"正在使用问答模型「{model_name}」生成扫描清理建议...")
        prompt = self._build_advice_prompt(report)
        worker = OcrTranslationWorker(
            prompt,
            "自动检测",
            "中文",
            runtime["cfg"],
            use_proxy=bool(runtime["use_proxy"]),
            proxy_url=str(runtime["proxy_url"]),
            task="qa",
            prompt=prompt,
            messages=[
                {
                    "role": "system",
                    "content": "你是 Windows 磁盘清理建议助手。只基于扫描元数据解释风险、排序和下一步建议。不要输出命令，不要新增路径，不要要求直接删除 Avoid 项。不要输出 Markdown 源码标记，如 ###、**、表格、代码块。",
                },
                {"role": "user", "content": prompt},
            ],
            enable_conversation_append=False,
        )
        self._advisor_worker = worker
        worker.translation_finished.connect(lambda text, ok, err, r=report, m=model_name: self._on_llm_advice_finished(text, ok, err, r, m))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _start_software_llm_advice(self, entries: list[SoftwareEntry]) -> None:
        runtime = self._main_window._drive_cleaner_qa_runtime_config()
        local_summary = self._software_local_summary(entries)
        if not runtime["ok"]:
            self._set_advisor_plain_text(f"{runtime['error']}，已使用本地规则建议。\n\n{local_summary}")
            return
        model_name = str(runtime["model_name"])
        self._set_advisor_plain_text(f"正在使用问答模型「{model_name}」生成软件卸载建议...")
        prompt = self._build_software_advice_prompt(entries)
        worker = OcrTranslationWorker(
            prompt,
            "自动检测",
            "中文",
            runtime["cfg"],
            use_proxy=bool(runtime["use_proxy"]),
            proxy_url=str(runtime["proxy_url"]),
            task="qa",
            prompt=prompt,
            messages=[
                {
                    "role": "system",
                    "content": "你是 Windows 软件卸载建议助手。只基于已安装软件列表解释风险和处理顺序。不要输出命令，不要建议卸载系统软件，不要输出 Markdown 源码标记。",
                },
                {"role": "user", "content": prompt},
            ],
            enable_conversation_append=False,
        )
        self._advisor_worker = worker
        worker.translation_finished.connect(lambda text, ok, err, m=model_name, s=local_summary: self._on_software_llm_advice_finished(text, ok, err, m, s))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _start_model_probe(self) -> None:
        runtime = self._main_window._drive_cleaner_qa_runtime_config()
        if not runtime["ok"]:
            self._set_advisor_plain_text(f"{runtime['error']}，扫描将继续使用本地规则。")
            self._append_log(str(runtime["error"]))
            return
        model_name = str(runtime["model_name"])
        self._set_advisor_plain_text(f"正在连接模型管理中的问答模型「{model_name}」...")
        probe = OcrTranslationWorker(
            "请只回复 OK，用于磁盘清理工具连接问答模型预热。",
            "自动检测",
            "中文",
            runtime["cfg"],
            use_proxy=bool(runtime["use_proxy"]),
            proxy_url=str(runtime["proxy_url"]),
            task="qa",
            prompt="请只回复 OK，用于磁盘清理工具连接问答模型预热。",
            messages=[{"role": "user", "content": "请只回复 OK，用于磁盘清理工具连接问答模型预热。"}],
            enable_conversation_append=False,
        )
        self._model_probe_worker = probe
        probe.translation_finished.connect(lambda text, ok, err, m=model_name: self._on_model_probe_finished(text, ok, err, m))
        probe.finished.connect(probe.deleteLater)
        probe.start()

    def _on_model_probe_finished(self, text: str, ok: bool, error: str, model_name: str) -> None:
        self._model_probe_worker = None
        if ok:
            self._append_log(f"问答模型连接成功: {model_name}")
            current = self._advisor.toPlainText().strip()
            if "正在连接模型管理中的问答模型" in current:
                self._set_advisor_plain_text(f"问答模型「{model_name}」连接成功，等待扫描结果生成建议。")
        else:
            message = str(error or "未知错误")
            self._append_log(f"问答模型连接失败: {message}")
            self._set_advisor_plain_text(f"问答模型「{model_name}」连接失败，扫描将继续使用本地规则。\n\n{message}")

    def _on_llm_advice_finished(self, text: str, ok: bool, error: str, report: ScanReport, model_name: str) -> None:
        self._advisor_worker = None
        if ok:
            self._set_advisor_markdown(str(text or "").strip() or "模型未返回有效建议。")
        else:
            self._set_advisor_plain_text(f"问答模型「{model_name}」生成建议失败，已使用本地规则建议。\n\n{error or '未知错误'}\n\n{report.advisor_summary}")

    def _on_software_llm_advice_finished(self, text: str, ok: bool, error: str, model_name: str, local_summary: str) -> None:
        self._advisor_worker = None
        if ok:
            self._set_advisor_markdown(str(text or "").strip() or "模型未返回有效建议。")
        else:
            self._set_advisor_plain_text(f"问答模型「{model_name}」生成建议失败，已使用本地规则建议。\n\n{error or '未知错误'}\n\n{local_summary}")

    def _software_local_summary(self, entries: list[SoftwareEntry]) -> str:
        user_count = sum(1 for entry in entries if not entry.is_system)
        system_count = sum(1 for entry in entries if entry.is_system)
        sized = sorted((entry for entry in entries if entry.size_bytes > 0), key=lambda entry: entry.size_bytes, reverse=True)[:5]
        lines = [f"发现用户软件 {user_count} 个，系统软件 {system_count} 个。系统软件不可勾选。"]
        if sized:
            lines.append("体积靠前的软件：" + "；".join(f"{entry.name} {format_bytes(entry.size_bytes)}" for entry in sized))
        lines.append("建议优先使用标准卸载；只有确认安装目录和数据目录无保留价值时，再使用强力卸载。")
        return "\n".join(lines)

    def _build_advice_prompt(self, report: ScanReport) -> str:
        lines = [
            "请分析下面的磁盘扫描结果，输出中文建议：",
            "",
            f"本地规则摘要：{report.advisor_summary}",
            "",
            "盘符概览：",
        ]
        for result in report.drives:
            d = result.drive
            lines.append(f"- {d.drive}: 总 {format_bytes(d.total_bytes)}，已用 {format_bytes(d.used_bytes)}，可用 {format_bytes(d.free_bytes)}")
        candidates = [c for r in report.drives for c in r.candidates[:20]]
        if candidates:
            lines.extend(["", "清理候选："])
            for c in candidates[:30]:
                lines.append(f"- [{c.risk}] {format_bytes(c.size_bytes)} {c.kind} {c.path}；原因：{c.reason}")
        large_dirs = [e for r in report.drives for e in r.large_dirs[:10]]
        if large_dirs:
            lines.extend(["", "目录占用："])
            for e in large_dirs[:15]:
                lines.append(f"- {format_bytes(e.size_bytes)} {e.path}")
        large_files = [e for r in report.drives for e in r.large_files[:10]]
        if large_files:
            lines.extend(["", "大文件："])
            for e in large_files[:15]:
                lines.append(f"- {format_bytes(e.size_bytes)} {e.path}")
        duplicates = [g for r in report.drives for g in r.duplicate_groups[:10]]
        if duplicates:
            lines.extend(["", "重复文件组："])
            for g in duplicates[:15]:
                lines.append(f"- {format_bytes(g.size_bytes)} x {len(g.paths)}，示例：{g.paths[0] if g.paths else ''}")
        lines.extend(["", "请按“可安全处理 / 需要确认 / 不建议处理”三组给出建议。"])
        lines.append("输出要求：用清晰自然的中文段落和短列表，不要使用 Markdown 源码标记。")
        return "\n".join(lines)

    def _build_software_advice_prompt(self, entries: list[SoftwareEntry]) -> str:
        user_entries = [entry for entry in entries if not entry.is_system]
        system_entries = [entry for entry in entries if entry.is_system]
        sized = sorted(user_entries, key=lambda entry: entry.size_bytes, reverse=True)
        lines = [
            "请分析下面的 Windows 已安装软件列表，输出中文卸载建议：",
            "",
            f"用户软件数量：{len(user_entries)}",
            f"系统软件数量：{len(system_entries)}（系统软件不可勾选、不可建议卸载）",
            "",
            "用户软件示例：",
        ]
        for entry in sized[:40]:
            lines.append(
                f"- {entry.name}；版本：{entry.version or '未知'}；发布者：{entry.publisher or '未知'}；"
                f"大小：{format_bytes(entry.size_bytes)}；安装目录：{entry.install_location or '未知'}；数据目录：{entry.data_location or '未发现'}"
            )
        lines.extend(["", "请按“可优先标准卸载 / 需谨慎确认 / 不建议处理”三组给出建议。不要输出命令，不要建议卸载系统软件。"])
        return "\n".join(lines)

    def _export_report(self) -> None:
        report = self._reports.get(self._current_mode)
        if report is None:
            return
        default_name = f"drive_cleaner_{self._current_mode}_{time.strftime('%Y%m%d_%H%M%S')}.md"
        path, _ = self._drive_get_save_file_name("导出扫描报告", str(get_files_dir() / default_name), "Markdown (*.md);;JSON (*.json)")
        if not path:
            return
        export_scan_report(report, path)
        self._append_log(f"报告已导出: {path}")

    def _apply_style(self) -> None:
        combo_arrow_url = str((Path(__file__).resolve().parent.parent / "assets" / "icon_combo_arrow.svg").as_posix())
        checkbox_check_icon_url = str((Path(__file__).resolve().parent.parent / "assets" / "icon_checkbox_check_slate.svg").as_posix())
        self.setStyleSheet("""
            QDialog { background: __MAIN_BACKGROUND__; }
            QWidget { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 13px; color: #111827; }
            QWidget#DriveCleanerSidebar { background: __MAIN_BACKGROUND__; border-right: 1px solid #f4faff; }
            QWidget#DriveCleanerBody { background: __MAIN_BACKGROUND__; }
            QToolButton#DriveCleanerModeButton {
                min-height: 66px; border: none; border-radius: 10px; background: transparent; color: #475569; font-weight: 700;
            }
            QToolButton#DriveCleanerModeButton:hover { background: #f1f5f9; color: #111827; }
            QToolButton#DriveCleanerModeButton:checked { background: #e5edf8; color: #1e3a5f; }
            QLabel#DriveCleanerTitle { font-size: 24px; font-weight: 900; color: #0f172a; }
            QLabel#DriveCleanerSubtitle, QLabel#DriveCleanerStatus { color: #64748b; font-size: 12px; font-weight: 600; }
            QLineEdit, QComboBox, QSpinBox { min-height: 30px; border: 1px solid #dbe3ee; border-radius: 7px; background: white; padding: 2px 8px; }
            QComboBox#DriveCleanerCombo {
                min-height: 30px;
                max-height: 34px;
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 2px 30px 2px 10px;
                color: #111827;
                font-size: 13px;
                font-weight: 600;
            }
            QComboBox#DriveCleanerCombo:hover {
                border: 1px solid #cbd5e1;
                background: #ffffff;
            }
            QComboBox#DriveCleanerCombo:focus {
                border: 1px solid #cbd5e1;
                background: #ffffff;
            }
            QComboBox#DriveCleanerCombo::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                border: none;
                width: 28px;
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
                background: transparent;
            }
            QComboBox#DriveCleanerCombo::down-arrow {
                image: url("__COMBO_ARROW__");
                width: 12px;
                height: 12px;
                margin-right: 8px;
            }
            QComboBox#DriveCleanerCombo QAbstractItemView {
                background-color: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 8px;
                padding: 5px;
                outline: none;
                selection-background-color: #f3f4f6;
                selection-color: #111827;
            }
            QComboBox#DriveCleanerCombo QAbstractItemView::item {
                min-height: 30px;
                padding: 4px 12px;
                border-radius: 6px;
                color: #111827;
                font-size: 13px;
                font-weight: 600;
            }
            QComboBox#DriveCleanerCombo QAbstractItemView::item:hover,
            QComboBox#DriveCleanerCombo QAbstractItemView::item:selected {
                background-color: #f3f4f6;
                color: #111827;
            }
            QComboBox#DriveCleanerCombo QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 4px 2px 4px 2px;
            }
            QComboBox#DriveCleanerCombo QScrollBar::handle:vertical {
                background: #cbd5e1;
                border-radius: 4px;
                min-height: 24px;
            }
            QComboBox#DriveCleanerCombo QScrollBar::add-line:vertical,
            QComboBox#DriveCleanerCombo QScrollBar::sub-line:vertical {
                height: 0px;
                border: none;
                background: transparent;
            }
            QLineEdit#DriveCleanerSearch { min-height: 34px; padding-left: 12px; }
            QLineEdit#DriveCleanerSearch:hover, QLineEdit#DriveCleanerSearch:focus { border-color: #94a3b8; background: #ffffff; }
            QPushButton { min-height: 30px; border-radius: 7px; padding: 4px 14px; font-weight: 800; border: 1px solid #dbe3ee; background: white; color: #111827; }
            QPushButton#DriveCleanerPrimary { background: #64748b; color: white; border: none; }
            QPushButton#DriveCleanerDanger { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
            QPushButton:hover { background: #eef2f7; }
            QTableWidget#DriveCleanerTable { background: white; border: 1px solid #e5e7eb; border-radius: 8px; gridline-color: #edf2f7; alternate-background-color: #f7f8fa; }
            QTableWidget#DriveCleanerTable::item:hover { background: rgba(226, 232, 240, 0.42); }
            QTableWidget#DriveCleanerTable::item:selected { background: rgba(219, 234, 254, 0.52); color: #111827; }
            QTableWidget#DriveCleanerTable QHeaderView::section { background: #f8fafc; color: #475569; border: none; border-bottom: 1px solid #e5e7eb; padding: 6px; font-weight: 800; }
            QTextEdit#DriveCleanerLog { background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 8px; min-height: 108px; }
            QFrame#DriveCleanerAdvisorSlot { background: transparent; border: none; }
            QFrame#DriveCleanerAdvisorFloat { background: transparent; border: none; }
            QFrame#DriveCleanerAdvisorCard { background: white; border: 1px solid #e5e7eb; border-radius: 8px; }
            QFrame#DriveCleanerAdvisorHeader { background: transparent; border: none; border-bottom: 1px solid #eef2f7; }
            QLabel#DriveCleanerAdvisorTitle { color: #334155; font-size: 12px; font-weight: 800; }
            QToolButton#DriveCleanerAdvisorExpand {
                min-width: 26px;
                max-width: 26px;
                min-height: 24px;
                max-height: 24px;
                border: none;
                border-radius: 6px;
                background: transparent;
                padding: 2px;
            }
            QToolButton#DriveCleanerAdvisorExpand:hover,
            QToolButton#DriveCleanerAdvisorExpand:checked {
                background: #eef2f7;
            }
            QTextEdit#DriveCleanerAdvisor { background: white; border: none; border-radius: 0px; padding: 8px; min-height: 76px; }
            QProgressBar { min-height: 30px; max-height: 30px; max-width: 100px; text-align: center; background: #e5e7eb; border: none; border-radius: 6px; }
            QProgressBar::chunk { background: #64748b; border-radius: 6px; }
            QCheckBox#DriveSelectorCheckBox, QCheckBox#DriveSelectorCheckBox:checked, QCheckBox#DriveSelectorCheckBox:hover {
                background: transparent;
                padding: 0 2px;
            }
            QCheckBox#DriveSelectorCheckBox::indicator,
            QCheckBox#DriveConfirmCheckBox::indicator {
                width: 14px;
                height: 14px;
                border-radius: 4px;
                border: 1px solid #94a3b8;
                background: transparent;
            }
            QCheckBox#DriveSelectorCheckBox::indicator:hover,
            QCheckBox#DriveConfirmCheckBox::indicator:hover {
                border-color: #64748b;
                background: rgba(241, 245, 249, 0.62);
            }
            QCheckBox#DriveSelectorCheckBox::indicator:checked,
            QCheckBox#DriveConfirmCheckBox::indicator:checked {
                border-color: #64748b;
                background: transparent;
                image: url('__CHECKBOX_ICON__');
            }
            QTableWidget#DriveCleanerTable::indicator {
                width: 14px;
                height: 14px;
                border-radius: 4px;
                border: 1px solid #94a3b8;
                background: transparent;
            }
            QTableWidget#DriveCleanerTable::indicator:hover {
                border-color: #64748b;
                background: rgba(241, 245, 249, 0.62);
            }
            QTableWidget#DriveCleanerTable::indicator:checked {
                border-color: #64748b;
                background: transparent;
                image: url('__CHECKBOX_ICON__');
            }
            QCheckBox#DriveConfirmCheckBox, QCheckBox#DriveConfirmCheckBox:checked, QCheckBox#DriveConfirmCheckBox:hover {
                background: transparent;
            }
        """.replace("__CHECKBOX_ICON__", checkbox_check_icon_url).replace("__COMBO_ARROW__", combo_arrow_url).replace("__MAIN_BACKGROUND__", MAIN_WINDOW_BACKGROUND))

    def closeEvent(self, event) -> None:
        self._collapse_advisor_panel()
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._scan_worker.cancel()
            self._append_log("窗口关闭，正在停止当前扫描...")
        if self._cleanup_worker is not None and self._cleanup_worker.isRunning():
            self._cleanup_worker.cancel()
            self._append_log("窗口关闭，正在停止当前清理...")
        for worker in (self._model_probe_worker, self._advisor_worker):
            try:
                if worker is not None and worker.isRunning():
                    worker.requestInterruption()
            except Exception:
                pass
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_advisor_floating_panel()
