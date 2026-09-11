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


def _settings_dialog_class():
    from deepcat.ui.settings_dialog.dialog import SettingsDialog

    return SettingsDialog


class SettingsPresentationMixin:
    @staticmethod
    def _style_translator_footer_button(button: QPushButton, min_width: int = 0) -> None:
        button.setObjectName("TranslatorFooterButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFlat(False)
        button.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if int(min_width) > 0:
            button.setFixedWidth(int(min_width))
        button.setStyleSheet(_settings_dialog_class()._TRANSLATOR_FOOTER_BUTTON_STYLE)
        button.setFixedHeight(_settings_dialog_class()._INLINE_ACTION_BUTTON_HEIGHT)

    @staticmethod
    def _show_feedback_message(
        parent: QWidget,
        *,
        success: object,
        title: str,
        summary: str,
        detail: str = "",
    ) -> None:
        is_loading = (success == "loading")
        is_success = bool(success) if not is_loading else False
        title_text = str(title or ("启动中" if is_loading else ("操作成功" if is_success else "操作失败"))).strip()
        summary_text = str(summary or ("请稍候..." if is_loading else ("操作已完成。" if is_success else "操作失败。"))).strip()
        detail_text = str(detail or "").strip()
        message = f"{title_text}：{summary_text}"
        if detail_text:
            message = f"{message}\n{detail_text}"
        if parent is None:
            return
        if not isinstance(parent, QWidget):
            if is_loading:
                return
            if is_success:
                QMessageBox.information(None, title_text, message)
            else:
                QMessageBox.warning(None, title_text, message)
            return
        try:
            label = getattr(parent, "_inline_feedback_label", None)
            if label is None:
                label = QLabel(parent)
                label.setObjectName("InlineFeedbackLabel")
                label.setWordWrap(True)
                label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                setattr(parent, "_inline_feedback_label", label)
            token = int(getattr(parent, "_inline_feedback_token", 0)) + 1
            setattr(parent, "_inline_feedback_token", token)

            if is_loading:
                color = "#1d4ed8"      # 蓝色文本
                background = "#eff6ff" # 蓝色背景
                border = "#bfdbfe"     # 蓝色边框
            elif is_success:
                color = "#166534"
                background = "#f0fdf4"
                border = "#bbf7d0"
            else:
                color = "#991b1b"
                background = "#fef2f2"
                border = "#fecaca"

            label.setText(message)
            label.setStyleSheet(
                f"QLabel#InlineFeedbackLabel {{ color: {color}; background: {background}; border: 1px solid {border}; border-radius: 8px; padding: 8px 10px; font-size: 12px; }}"
            )
            max_width = max(260, min(520, int(parent.width()) - 32))
            label.setMaximumWidth(max_width)
            label.adjustSize()
            width = min(max_width, max(260, label.sizeHint().width()))
            height = label.sizeHint().height()
            x = max(12, (int(parent.width()) - width) // 2)
            y = max(12, int(parent.height()) - height - 18)
            label.setGeometry(x, y, width, height)
            label.raise_()
            label.show()

            def hide() -> None:
                if int(getattr(parent, "_inline_feedback_token", 0)) == token:
                    label.hide()

            if not is_loading:
                QTimer.singleShot(3600, hide)
        except Exception:
            logger.exception("显示内联反馈失败")

    def _make_settings_status_label(self) -> QLabel:
        label = QLabel("")
        label.setObjectName("SettingsInlineStatus")
        label.setVisible(False)
        label.setWordWrap(False)
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        return label

    def _prepare_settings_status_label(self, label: QLabel) -> Optional[QWidget]:
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

    def _show_settings_status(self, label: Optional[QLabel], text: str, tone: str = "info", *, auto_hide_ms: int = 3200) -> None:
        if label is None:
            return
        try:
            message = str(text or "").strip()
            if not message:
                label.hide()
                return
            parent = self._prepare_settings_status_label(label)
            if parent is None:
                return
            palette = {
                "success": ("#166534", "#f0fdf4", "#bbf7d0"),
                "warning": ("#92400e", "#fffbeb", "#fde68a"),
                "error": ("#991b1b", "#fef2f2", "#fecaca"),
                "info": ("#1e3a8a", "#eff6ff", "#bfdbfe"),
            }
            color, background, border = palette.get(str(tone or "info"), palette["info"])
            label.setText(message)
            label.setStyleSheet(
                "QLabel#SettingsInlineStatus {"
                f" color: {color}; background: {background}; border: 1px solid {border};"
                " border-radius: 8px; padding: 7px 10px; font-size: 12px; font-weight: 600;"
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
            token = int(getattr(label, "_status_token", 0)) + 1
            setattr(label, "_status_token", token)
            if int(auto_hide_ms or 0) > 0:
                def hide() -> None:
                    if int(getattr(label, "_status_token", 0)) == token:
                        label.hide()

                QTimer.singleShot(int(auto_hide_ms), hide)
        except Exception:
            logger.exception("显示设置页状态失败")

    def _show_general_status(self, text: str, tone: str = "info", *, auto_hide_ms: int = 3200) -> None:
        self._show_settings_status(getattr(self, "_general_status_label", None), text, tone, auto_hide_ms=auto_hide_ms)

    def _show_update_status(self, text: str, tone: str = "info", *, auto_hide_ms: int = 3600) -> None:
        self._show_settings_status(getattr(self, "_update_status_label", None), text, tone, auto_hide_ms=auto_hide_ms)

    def _apply_caption_color(self) -> None:
        try:
            if os.name != "nt":
                return
            hwnd = int(self.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(0x00FDF9F5)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def _build_annotation_style_group(self) -> QGroupBox:
        group = QGroupBox("标注样式")
        grid = QGridLayout(group)
        grid.setContentsMargins(0, 0, 6, 2)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)
        control_w = 46
        control_h = 22
        items = [
            ("selection_border_color", "线框"),
            ("arrow_color", "箭头"),
            ("pen_color", "画笔"),
            ("marker_color", "记号"),
            ("number_color", "序号"),
            ("text_color", "文本"),
            ("rect_color", "框选"),
        ]
        for i, (key, label) in enumerate(items):
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(3)
            cell_layout.addWidget(QLabel(label))
            btn = QPushButton()
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedSize(control_w, control_h)
            self._annotation_color_buttons[key] = btn
            self._refresh_color_button(key)
            btn.clicked.connect(lambda _=False, k=key: self._choose_annotation_color(k))
            cell_layout.addWidget(btn)
            grid.addWidget(cell, 0, i)

        self._line_style = ModernPopupComboBox()
        self._line_style.setObjectName("AnnotationLineStyle")
        self._line_style.setProperty("matchPopupWidthToParent", True)
        self._line_style.addItems(["虚线", "实线"])
        self._line_style.setCurrentText("实线" if str(self._annotation_style.get("line_style")) == "solid" else "虚线")
        self._line_style.setStyleSheet(
            "QComboBox#AnnotationLineStyle { padding: 0px 14px 0px 6px; min-height: 0px; }"
            "QComboBox#AnnotationLineStyle::drop-down { width: 14px; border: none; }"
        )
        style_cell = QWidget()
        style_layout = QHBoxLayout(style_cell)
        style_layout.setContentsMargins(0, 0, 0, 0)
        style_layout.setSpacing(3)
        style_layout.addWidget(QLabel("线型"))
        self._line_style.setFixedSize(120, control_h)
        style_layout.addWidget(self._line_style)
        grid.addWidget(style_cell, 0, 7)
        self._line_style.currentIndexChanged.connect(self._apply_annotation_style)
        return group

    def _refresh_color_button(self, key: str) -> None:
        btn = self._annotation_color_buttons.get(str(key))
        if btn is None:
            return
        color = str(self._annotation_style.get(str(key), "#E53935") or "#E53935")
        qcolor = QColor(color)
        btn.setText("")
        btn.setToolTip(color)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {qcolor.name() if qcolor.isValid() else '#E53935'}; border: 1px solid #B8B8B8;"
            " border-radius: 5px; padding: 0px; }"
        )

    def _choose_annotation_color(self, key: str) -> None:
        current = QColor(str(self._annotation_style.get(str(key), "#E53935") or "#E53935"))
        dialog = QColorDialog(current if current.isValid() else QColor("#E53935"), self)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        dialog.setWindowTitle("选择颜色")
        self._localize_color_dialog(dialog)
        QTimer.singleShot(0, lambda dlg=dialog: self._localize_color_dialog(dlg))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color = dialog.selectedColor()
        if not color.isValid():
            return
        self._annotation_style[str(key)] = color.name(QColor.NameFormat.HexRgb).upper()
        self._refresh_color_button(str(key))
        self._apply_annotation_style()

    def _localize_color_dialog(self, dialog: QColorDialog) -> None:
        text_map = {
            "Basic colors": "基础颜色",
            "Custom colors": "自定义颜色",
            "Pick Screen Color": "吸取屏幕颜色",
            "Add to Custom Colors": "添加到自定义颜色",
            "Hue:": "色相:",
            "Sat:": "饱和度:",
            "Val:": "亮度:",
            "Red:": "红色:",
            "Green:": "绿色:",
            "Blue:": "蓝色:",
            "Alpha channel:": "透明度:",
            "HTML:": "色值:",
        }
        try:
            for label in dialog.findChildren(QLabel):
                raw = str(label.text() or "")
                key = raw.replace("&", "").strip()
                if key in text_map:
                    label.setText(text_map[key])
            for button in dialog.findChildren(QPushButton):
                raw = str(button.text() or "")
                key = raw.replace("&", "").strip()
                if key in text_map:
                    button.setText(text_map[key])
            box = dialog.findChild(QDialogButtonBox)
            if box is not None:
                box.setCenterButtons(False)
                box.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
                ok = box.button(QDialogButtonBox.StandardButton.Ok)
                cancel = box.button(QDialogButtonBox.StandardButton.Cancel)
                if ok is not None:
                    ok.setText("确定")
                if cancel is not None:
                    cancel.setText("取消")

                layout = box.layout()
                if layout is not None and ok is not None and cancel is not None:
                    idx_ok = layout.indexOf(ok)
                    idx_cancel = layout.indexOf(cancel)
                    if idx_ok != -1 and idx_cancel != -1 and idx_ok < idx_cancel:
                        layout.removeWidget(cancel)
                        layout.insertWidget(idx_ok, cancel)
        except Exception:
            pass

    def _apply_annotation_style(self) -> None:
        self._annotation_style["line_style"] = "solid" if str(self._line_style.currentText()) == "实线" else "dash"
        self._annotation_style = normalize_annotation_style(self._annotation_style)
        for key in list(self._annotation_color_buttons.keys()):
            self._refresh_color_button(key)
        self._current = update_ui_settings(annotation_style=dict(self._annotation_style))
        self._show_general_status("标注样式已保存。", "success", auto_hide_ms=1800)

    def _open_log_dir_from_about(self) -> None:
        try:
            path = get_log_dir()
            path.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise RuntimeError(str(path))
        except Exception as exc:
            self._show_update_status(f"无法打开日志目录：{exc}", "error", auto_hide_ms=4200)

    def _open_feedback_url_from_about(self) -> None:
        url = QUrl("https://github.com/pipabcc/deepcat/issues/new")
        if not QDesktopServices.openUrl(url):
            self._show_update_status("无法打开反馈/问题上报页面。", "error", auto_hide_ms=4200)

    def _build_about_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        title = QLabel("DeepCat")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: rgba(20,32,45,0.92);")
        desc = QLabel("多功能截图工具：框选截屏、长页面自动滚动截屏、OCR 识别、标注、马赛克、贴图、录屏、翻译和划词功能。")
        desc.setWordWrap(True)

        info_group = QGroupBox("软件信息")
        info_form = QFormLayout(info_group)
        repo = QLabel('<a href="https://github.com/pipabcc/deepcat">https://github.com/pipabcc/deepcat</a>')
        repo.setOpenExternalLinks(True)
        repo.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        repo_row = QWidget()
        repo_layout = QHBoxLayout(repo_row)
        repo_layout.setContentsMargins(0, 0, 0, 0)
        repo_layout.setSpacing(8)
        feedback_btn = QPushButton("反馈/问题上报")
        feedback_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        feedback_btn.setFixedWidth(112)
        feedback_btn.clicked.connect(self._open_feedback_url_from_about)
        repo_layout.addWidget(repo)
        repo_layout.addStretch(1)
        repo_layout.addWidget(feedback_btn)
        license_label = QLabel("GPL-3.0-or-later")
        updater_settings = normalize_updater_settings((getattr(self._current, "ui", {}) or {}).get("updater"))
        version_row = QWidget()
        version_layout = QHBoxLayout(version_row)
        version_layout.setContentsMargins(0, 0, 0, 0)
        version_layout.setSpacing(8)
        version_layout.addWidget(QLabel(str(__version__)))
        self._check_update_btn = QPushButton("检查更新")
        self._check_update_btn.setFixedWidth(86)
        self._auto_update_switch = QCheckBox("自动更新")
        self._auto_update_switch.setChecked(bool(updater_settings.get("auto_update_enabled", False)))
        version_layout.addWidget(self._check_update_btn)
        version_layout.addWidget(self._auto_update_switch)
        version_layout.addStretch(1)
        info_form.addRow("软件名称:", QLabel("DeepCat（deepcat.exe）"))
        info_form.addRow("版本号:", version_row)
        info_form.addRow("适用平台:", QLabel("Windows 10 / 11"))
        info_form.addRow("开源协议:", license_label)
        info_form.addRow("GitHub:", repo_row)

        self._update_status_label = self._make_settings_status_label()

        log_settings = normalize_log_settings((getattr(self._current, "ui", {}) or {}).get("logging"))
        log_group = QGroupBox("日志")
        log_layout = QHBoxLayout(log_group)
        log_layout.setContentsMargins(10, 10, 10, 10)
        log_layout.setSpacing(18)
        open_log_btn = QPushButton("打开日志目录")
        open_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_log_btn.setFixedWidth(100)
        open_log_btn.clicked.connect(self._open_log_dir_from_about)
        self._app_log_switch = QCheckBox("app.log")
        self._app_log_switch.setChecked(bool(log_settings.get("app_log_enabled", False)))
        self._app_log_switch.setToolTip("记录应用日志")
        self._crash_log_switch = QCheckBox("crash.log")
        self._crash_log_switch.setChecked(bool(log_settings.get("crash_log_enabled", False)))
        self._crash_log_switch.setToolTip("记录崩溃日志")
        log_layout.addWidget(self._app_log_switch)
        log_layout.addWidget(self._crash_log_switch)
        log_layout.addWidget(open_log_btn)
        log_layout.addStretch(1)

        privacy_group = QGroupBox("隐私声明")
        privacy_layout = QVBoxLayout(privacy_group)
        privacy_layout.setContentsMargins(10, 10, 10, 10)
        privacy_tips = QLabel("欢迎使用 DeepCat。我们严格保护用户隐私，您的任何截图、标注和设置数据均仅保存在本地设备中，我们不会收集或上传您的任何信息。")
        privacy_tips.setWordWrap(True)
        privacy_tips.setStyleSheet("color: rgba(0,0,0,0.58);")
        privacy_layout.addWidget(privacy_tips)

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(info_group)
        layout.addWidget(self._update_status_label)
        layout.addWidget(log_group)
        layout.addWidget(privacy_group)
        return tab
