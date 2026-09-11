from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path
from types import FunctionType
from typing import Callable, Optional
from PyQt6.QtCore import QByteArray, QEvent, QRect, Qt, QTimer, QPoint, QSize, QUrl
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QProgressBar,
)
from deepcat import __version__
from deepcat.input.hotkey_format import pynput_to_qt, qt_to_pynput
from deepcat.utils.codex_config import (
    apply_codex_config,
    has_codex_client_process,
    launch_codex_client,
    restart_codex_client,
    stop_codex_client,
)
from deepcat.utils.claude_code_config import apply_claude_code_config, restore_claude_code_config
from deepcat.settings_store import (
    AppSettings,
    DEEPLX_INFO_ADDRESS,
    DEFAULT_EXPLAIN_PROMPT,
    DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
    DEFAULT_AI_SEARCH_PROMPT,
    DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
    DEFAULT_REPLY_PROMPT,
    DEFAULT_REPLY_PROMPT_BUTTON_NAME,
    DEFAULT_SUMMARY_PROMPT,
    DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
    default_translator_settings,
    decode_qbytearray,
    encode_qbytearray,
    infer_translator_model_type,
    infer_translator_model_provider,
    normalize_annotation_style,
    normalize_log_settings,
    normalize_output_dir,
    normalize_translator_provider,
    normalize_translator_settings,
    normalize_updater_settings,
    update_settings,
    update_settings_fields,
    update_ui_settings,
    validate_output_dir,
)
from deepcat.utils.autostart import is_autostart_enabled, set_autostart
from deepcat.utils.logger import get_log_dir
from deepcat.ui.post_capture_actions import (
    ModernPopupComboBox,
    OcrGenericMenuPopup,
    PROMPT_ACTION_POPUP_WIDTH,
    _translator_model_search_text,
)

from deepcat.ui.settings_dialog._shared import (
    logger,
    CLAUDE_CODE_CURRENT_MODEL_CHECK_COLOR,
    CLAUDE_CODE_PROVIDER_NAME,
    CODEX_CURRENT_MODEL_CHECK_COLOR,
    FREE_TRANSLATOR_TYPES,
    PROTECTED_TRANSLATOR_MODEL_NAMES,
    QA_EXCLUDED_TRANSLATOR_MODEL_NAMES,
    TRANSLATOR_MODEL_CODEX_BOUND_ROLE,
    TRANSLATOR_MODEL_HIGHLIGHT_COLOR_ROLE,
    TRANSLATOR_MODEL_SEARCH_ROLE,
)
from deepcat.ui.settings_dialog.delegates import ProviderSwitchHintDelegate
from deepcat.ui.settings_dialog.workers import FetchModelsWorker, ModelDownloadWorker, ProxyConnectionTestWorker, TranslatorConnectionTestWorker, UpdateCheckWorker, UpdateDownloadWorker
from deepcat.ui.thread_utils import request_thread_cancel
from deepcat.ui.settings_dialog.field_widgets import ApiKeyVisibilityButton, ProxyTestButton, TranslatorModelComboBox, _FetchedModelIdPopup


from deepcat.ui.module_compat import DynamicModuleAttribute

update_ui_settings = DynamicModuleAttribute("deepcat.ui.settings_dialog.dialog", "update_ui_settings")


class GeneralSettingsMixin:
    def _row_dir(self, edit: QLineEdit, btn: QPushButton) -> QWidget:
        w = QWidget()
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        h.addWidget(btn)
        w.setLayout(h)
        return w

    def _browse_dir(self, target: QLineEdit) -> None:
        init = target.text().strip()
        if init and Path(init).exists():
            start_dir = init
        else:
            start_dir = str(Path.home())
        p = QFileDialog.getExistingDirectory(self, "选择保存目录", start_dir)
        if not p:
            return
        target.setText(p)
        self._apply_dirs()

    def _apply_dirs(self) -> None:
        file_dir = normalize_output_dir(self._img_dir.text().strip())
        ok, msg = validate_output_dir(file_dir or "")
        if not ok:
            self._show_general_status(f"文件保存路径无效：{msg}", "error", auto_hide_ms=5200)
            self._img_dir.setText(str(self._current.image_output_dir))
            return
        self._current = update_settings_fields(image_output_dir=str(file_dir), pdf_output_dir=str(file_dir))
        self._show_general_status("文件保存路径已保存。", "success", auto_hide_ms=1800)

    def _apply_autostart(self) -> None:
        enabled = bool(self._autostart.isChecked())
        err = set_autostart(enabled)
        if err is not None:
            self._show_general_status(f"开机启动设置失败：{err}", "error", auto_hide_ms=5200)
            self._autostart.setChecked(bool(is_autostart_enabled()))
            return
        self._current = update_settings_fields(autostart=bool(enabled))
        self._show_general_status("开机启动已开启。" if enabled else "开机启动已关闭。", "success", auto_hide_ms=1800)

    def _apply_notifications(self) -> None:
        self._current = update_settings_fields(notifications_enabled=bool(self._notifications.isChecked()))
        self._show_general_status("提示通知已开启。" if self._notifications.isChecked() else "提示通知已关闭。", "success", auto_hide_ms=1800)

    def _apply_logging_settings(self) -> None:
        self._current = update_ui_settings(
            logging=normalize_log_settings(
                {
                    "app_log_enabled": bool(self._app_log_switch.isChecked()),
                    "crash_log_enabled": bool(self._crash_log_switch.isChecked()),
                }
            )
        )
        try:
            from deepcat.utils.crash_reporter import sync_crash_reporter_with_settings

            sync_crash_reporter_with_settings()
        except Exception:
            pass
        self._show_update_status("日志设置已保存。", "success", auto_hide_ms=1800)

    def _apply_auto_update(self) -> None:
        self._save_updater_settings(auto_update_enabled=bool(self._auto_update_switch.isChecked()))
        self._show_update_status("自动更新已开启。" if self._auto_update_switch.isChecked() else "自动更新已关闭。", "success", auto_hide_ms=1800)

    def _save_updater_settings(self, **updates: object) -> None:
        def _mut(s: AppSettings) -> AppSettings:
            ui = dict(s.ui)
            updater = normalize_updater_settings(ui.get("updater"))
            for key, value in updates.items():
                updater[str(key)] = value
            ui["updater"] = normalize_updater_settings(updater)
            return AppSettings(
                version=int(s.version),
                autostart=bool(s.autostart),
                auto_save=bool(getattr(s, "auto_save", True)),
                image_output_dir=str(s.image_output_dir),
                pdf_output_dir=str(s.pdf_output_dir),
                hotkey=str(s.hotkey),
                ui=ui,
                notifications_enabled=bool(getattr(s, "notifications_enabled", False)),
            )

        self._current = update_settings(_mut)

    def _set_update_controls_busy(self, busy: bool, text: str = "检查更新") -> None:
        try:
            self._check_update_btn.setEnabled(not bool(busy))
            self._check_update_btn.setText(str(text or "检查更新"))
        except Exception:
            pass

    def _check_for_updates(self, manual: bool = False) -> None:
        worker = getattr(self, "_update_check_worker", None)
        if worker is not None and worker.isRunning():
            if manual:
                self._show_update_status("正在检查更新，请稍候。", "info", auto_hide_ms=1800)
            return
        self._save_updater_settings(last_check_at=time.strftime("%Y-%m-%d"))
        self._set_update_controls_busy(True, "检查中...")
        if manual:
            self._show_update_status("正在检查更新...", "info", auto_hide_ms=0)
        worker = UpdateCheckWorker(str(__version__))
        self._update_check_worker = worker
        worker.checked.connect(lambda info, error, m=bool(manual): self._on_update_checked(info, error, m))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_update_checked(self, info: object, error: str, manual: bool) -> None:
        self._update_check_worker = None
        self._set_update_controls_busy(False)
        if error:
            if manual:
                self._show_update_status(f"无法检查更新：{error}", "error", auto_hide_ms=5200)
            return
        if info is None:
            if manual:
                self._show_update_status("没有收到更新信息。", "error", auto_hide_ms=5200)
            return
        if not bool(getattr(info, "available", False)):
            if manual:
                self._show_update_status(f"当前已是最新版本（{__version__}）。", "success")
            return
        latest_version = str(getattr(info, "latest_version", "") or getattr(info, "tag_name", "") or "")
        if not str(getattr(info, "download_url", "") or "").strip():
            if manual:
                self._show_update_status(f"发现新版本 {latest_version}，但 Release 中没有可下载的 ZIP 包。", "warning", auto_hide_ms=5200)
            return
        if manual:
            ret = QMessageBox.question(
                self,
                "发现新版本",
                f"发现新版本 {latest_version}，是否现在下载更新包？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        self._download_update(info, manual=manual)

    def _download_update(self, info: object, manual: bool) -> None:
        worker = getattr(self, "_update_download_worker", None)
        if worker is not None and worker.isRunning():
            if manual:
                self._show_update_status("正在下载更新包，请稍候。", "info", auto_hide_ms=1800)
            return
        self._update_download_info = info
        self._update_download_manual = bool(manual)
        self._set_update_controls_busy(True, "下载中...")
        self._show_update_status("正在下载更新包...", "info", auto_hide_ms=0)
        worker = UpdateDownloadWorker(info)
        self._update_download_worker = worker
        worker.progress.connect(self._on_update_download_progress)
        worker.downloaded.connect(self._on_update_downloaded)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_update_download_progress(self, done: int, total: int) -> None:
        if int(total or 0) > 0:
            percent = int(max(0, min(100, int(done) * 100 / int(total))))
            self._set_update_controls_busy(True, f"下载 {percent}%")
            self._show_update_status(f"正在下载更新包：{percent}%", "info", auto_hide_ms=0)
        else:
            self._set_update_controls_busy(True, "下载中...")
            self._show_update_status("正在下载更新包...", "info", auto_hide_ms=0)

    def _on_update_downloaded(self, path: object, error: str) -> None:
        self._update_download_worker = None
        self._set_update_controls_busy(False)
        manual = bool(getattr(self, "_update_download_manual", False))
        info = getattr(self, "_update_download_info", None)
        if error:
            if manual:
                self._show_update_status(f"无法下载更新包：{error}", "error", auto_hide_ms=5200)
            return
        if path is None:
            if manual:
                self._show_update_status("更新包下载失败。", "error", auto_hide_ms=5200)
            return
        latest_version = str(getattr(info, "latest_version", "") or "")
        if latest_version:
            self._save_updater_settings(last_downloaded_version=latest_version)
        self._show_update_status("更新包已下载完成，等待安装确认。", "success", auto_hide_ms=2200)
        self._confirm_and_install_update(Path(str(path)), latest_version)

    def _confirm_and_install_update(self, zip_path: Path, latest_version: str) -> None:
        version_text = f" {latest_version}" if latest_version else ""
        ret = QMessageBox.question(
            self,
            "安装更新",
            f"更新包已下载完成。是否现在关闭旧版并安装新版{version_text}？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            from deepcat.updater import current_app_dir, launch_installer

            info = getattr(self, "_update_download_info", None)
            launch_installer(
                zip_path,
                os.getpid(),
                current_app_dir(),
                "deepcat.exe",
                asset_digest=str(getattr(info, "asset_digest", "") or ""),
            )
        except Exception as exc:
            self._show_update_status(f"无法启动更新安装器：{exc}", "error", auto_hide_ms=5200)
            return
        try:
            parent = self.parent()
            if parent is not None and hasattr(parent, "_allow_quit"):
                setattr(parent, "_allow_quit", True)
                parent.close()
        except Exception:
            pass
        try:
            self.accept()
        except Exception:
            pass
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _apply_auto_snap_enabled(self) -> None:
        self._current = update_ui_settings(auto_snap_enabled=bool(self._auto_snap_enabled.isChecked()))
        self._show_general_status("自动吸附已开启。" if self._auto_snap_enabled.isChecked() else "自动吸附已关闭。", "success", auto_hide_ms=1800)

    def _apply_save_button_mode(self) -> None:
        text = str(self._save_button_mode.currentText())
        if text.startswith("手动"):
            mode = "manual"
        else:
            mode = "auto"
        self._current = update_ui_settings(save_button_mode=mode)
        self._show_general_status(f"保存按钮模式已设为：{text}。", "success", auto_hide_ms=1800)

    def _apply_previous_capture_action(self) -> None:
        text = str(self._previous_capture_action.currentText())
        if text == "置顶前图":
            action = "pin"
        elif text == "保存前图":
            action = "save"
        elif text == "拼接前图":
            action = "stitch"
        elif text == "暂存前图":
            action = "stash"
        else:
            action = "pin"
        self._current = update_ui_settings(previous_capture_action=action)
        self._show_general_status(f"连续截图策略已设为：{text}。", "success", auto_hide_ms=1800)

    def _apply_post_capture_button_style(self) -> None:
        text = str(self._post_capture_button_style.currentText())
        self._current = update_ui_settings(post_capture_button_style="text" if text.startswith("文字") else "icon")
        self._show_general_status(f"按钮组样式已设为：{text}。", "success", auto_hide_ms=1800)

    def _apply_hotkey(self) -> None:
        qt_seq = self._hotkey.keySequence().toString()
        new_hotkey = qt_to_pynput(qt_seq)
        if not str(qt_seq).strip():
            self._show_general_status("框选截屏快捷键不能为空。", "warning", auto_hide_ms=3200)
            return
        if new_hotkey is None:
            new_hotkey = str(qt_seq).strip().lower()
        if new_hotkey == str(self._current.hotkey):
            return
        if self._on_hotkey_changed is not None:
            result = self._on_hotkey_changed(str(new_hotkey))
            if isinstance(result, tuple):
                ok, msg = result
                if not ok:
                    self._show_general_status(f"框选截屏快捷键保存失败：{msg or new_hotkey}", "error", auto_hide_ms=5200)
                    return
        self._current = update_settings_fields(hotkey=str(new_hotkey))
        self._show_general_status(f"框选截屏快捷键已保存：{qt_seq}", "success")

    def _apply_scroll_hotkey(self) -> None:
        qt_seq = self._scroll_hotkey.keySequence().toString()
        new_hotkey = qt_to_pynput(qt_seq)
        if not str(qt_seq).strip():
            self._show_general_status("滚动截屏快捷键不能为空。", "warning", auto_hide_ms=3200)
            return
        if new_hotkey is None:
            new_hotkey = str(qt_seq).strip().lower()
        ui = dict(getattr(self._current, "ui", {}) or {})
        if str(new_hotkey) == str(ui.get("scroll_hotkey", "<ctrl>+<f1>")):
            return
        if self._on_scroll_hotkey_changed is not None:
            result = self._on_scroll_hotkey_changed(str(new_hotkey))
            if isinstance(result, tuple):
                ok, msg = result
                if not ok:
                    self._show_general_status(f"滚动截屏快捷键保存失败：{msg or new_hotkey}", "error", auto_hide_ms=5200)
                    return
        self._current = update_ui_settings(scroll_hotkey=str(new_hotkey))
        self._show_general_status(f"滚动截屏快捷键已保存：{qt_seq}", "success")

    def _on_history_clicked(self) -> None:
        """打开翻译历史记录页面（委托主窗口统一打开同一个页面）。"""
        parent = self.parent()
        if parent is not None and hasattr(parent, "_on_history_clicked"):
            try:
                parent._on_history_clicked()
                return
            except Exception:
                pass
