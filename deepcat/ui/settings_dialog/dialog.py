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
    list_codex_client_process_names,
    check_client_window_loaded,
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
from deepcat.ui.settings_dialog.general_settings import GeneralSettingsMixin
from deepcat.ui.settings_dialog.presentation import SettingsPresentationMixin
from deepcat.ui.settings_dialog.translator_settings import TranslatorSettingsMixin


class SettingsDialog(
    SettingsPresentationMixin,
    TranslatorSettingsMixin,
    GeneralSettingsMixin,
    QDialog,
):
    _INLINE_ACTION_BUTTON_HEIGHT = 22
    _MODEL_ID_INLINE_BUTTON_SIZE = 20
    _MODEL_ID_INLINE_BUTTON_GAP = 2
    _MODEL_ID_INLINE_BUTTON_RIGHT_MARGIN = 6
    _MODEL_ID_INLINE_TEXT_GAP = 6
    _MODEL_ID_COMBO_TEXT_RIGHT_PADDING = (
        _MODEL_ID_INLINE_BUTTON_RIGHT_MARGIN
        + _MODEL_ID_INLINE_BUTTON_SIZE * 2
        + _MODEL_ID_INLINE_BUTTON_GAP
        + _MODEL_ID_INLINE_TEXT_GAP
    )
    _TRANSLATOR_FORM_LABEL_WIDTH = 64
    _MODEL_ROLE_LABEL_WIDTH = _TRANSLATOR_FORM_LABEL_WIDTH
    _MODEL_ROLE_COMBO_WIDTH = 210
    _MODEL_ID_COMBO_MIN_WIDTH = 190
    _MODEL_ID_COMBO_MAX_WIDTH = 280
    _TRANSLATOR_FOOTER_BUTTON_STYLE = """
        QPushButton#TranslatorFooterButton {
            background-color: #1e293b;
            color: #ffffff;
            border: none;
            border-radius: 4px;
            padding: 0px 10px;
            font-size: 11px;
            font-weight: 700;
        }
        QPushButton#TranslatorFooterButton:hover {
            background-color: #334155;
            color: #ffffff;
        }
        QPushButton#TranslatorFooterButton:pressed {
            background-color: #0f172a;
            color: #ffffff;
        }
        QPushButton#TranslatorFooterButton:disabled {
            background-color: #cbd5e1;
            color: rgba(255, 255, 255, 0.82);
        }
    """








    def __init__(
        self,
        parent: QWidget,
        initial: AppSettings,
        on_hotkey_changed: Optional[Callable[[str], tuple[bool, str]]] = None,
        on_scroll_hotkey_changed: Optional[Callable[[str], tuple[bool, str]]] = None,
        on_selection_translate_changed: Optional[Callable[[bool], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self._apply_caption_color()
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setModal(False)
        self.setMinimumWidth(520)
        from pathlib import Path
        arrow_url = str((Path(__file__).resolve().parent.parent / "assets" / "icon_combo_arrow.svg").as_posix())
        checkbox_check_icon_url = str((Path(__file__).resolve().parent.parent / "assets" / "icon_checkbox_check.svg").as_posix())
        self.setStyleSheet(f"""
            QDialog {{
                background: #f8fafc;
            }}
            QLabel {{
                color: #111827;
                font-size: 13px;
            }}
            QLineEdit, QKeySequenceEdit, QComboBox {{
                min-height: 28px;
                max-height: 32px;
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 2px 10px;
                color: #111827;
                font-size: 13px;
            }}
            QLineEdit:hover, QKeySequenceEdit:hover, QComboBox:hover {{
                border: 1px solid #cbd5e1;
            }}
            QLineEdit:focus, QKeySequenceEdit:focus, QComboBox:focus {{
                border: 1px solid #cbd5e1;
                background: white;
            }}
            QLineEdit[missingRequired="true"], QComboBox[missingRequired="true"] {{
                border: 1px solid #ef4444;
                background: #fff7f7;
            }}
            QLineEdit[missingRequired="true"]:focus, QComboBox[missingRequired="true"]:focus {{
                border: 1px solid #dc2626;
                background: #fffafa;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 28px;
            }}
            QComboBox::down-arrow {{
                image: url("{arrow_url}");
                width: 12px;
                height: 12px;
            }}
            QGroupBox {{
                background: #ffffff;
                border: 1px solid #f1f5f9;
                border-radius: 12px;
                margin-top: 16px;
                padding: 16px 14px 10px 14px;
                font-weight: 800;
                font-size: 13px;
                color: #111827;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 16px;
                padding: 0 6px;
                background: transparent;
                color: #475569;
                font-weight: 700;
            }}
            QTabWidget::pane {{
                border: 1px solid #f1f5f9;
                border-radius: 12px;
                background: #ffffff;
            }}
            QTabBar::tab {{
                background: transparent;
                color: #6b7280;
                border: none;
                padding: 6px 16px;
                font-size: 13px;
                font-weight: 600;
                border-radius: 6px;
                margin-right: 4px;
                margin-bottom: 4px;
            }}
            QTabBar::tab:hover {{
                background: #f1f5f9;
                color: #374151;
            }}
            QTabBar::tab:selected {{
                background: rgba(30, 41, 59, 0.08);
                color: #1e293b;
                font-weight: 700;
            }}
            QPushButton {{
                background: #1e293b;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 700;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: #334155; }}
            QPushButton:pressed {{ background: #0f172a; }}
            QPushButton:disabled {{ background: #cbd5e1; color: rgba(255,255,255,0.80); }}
            QCheckBox {{
                color: #111827;
                font-size: 13px;
            }}
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border-radius: 4px;
                border: 1px solid #cbd5e1;
                background: white;
            }}
            QCheckBox::indicator:hover {{
                border-color: #94a3b8;
            }}
            QCheckBox::indicator:checked {{
                border-color: #1e293b;
                background: #1e293b;
                image: url("{checkbox_check_icon_url}");
            }}
        """)

        self._initial = initial
        self._current = initial
        self._on_hotkey_changed = on_hotkey_changed
        self._on_scroll_hotkey_changed = on_scroll_hotkey_changed
        self._on_selection_translate_changed = on_selection_translate_changed
        self._translator_loading = False
        self._translator = normalize_translator_settings((getattr(initial, "ui", {}) or {}).get("translator"))
        self._annotation_style = normalize_annotation_style((getattr(initial, "ui", {}) or {}).get("annotation_style"))
        self._annotation_color_buttons: dict[str, QPushButton] = {}
        self._translator_test_worker: Optional[TranslatorConnectionTestWorker] = None
        self._proxy_test_worker: Optional[ProxyConnectionTestWorker] = None
        self._update_check_worker: Optional[UpdateCheckWorker] = None
        self._update_download_worker: Optional[UpdateDownloadWorker] = None
        self._update_download_info = None

        self._autostart = QCheckBox("开机启动")
        self._autostart.setChecked(bool(is_autostart_enabled()))
        self._notifications = QCheckBox("提示通知")
        self._notifications.setChecked(bool(getattr(initial, "notifications_enabled", False)))
        self._auto_snap_enabled = QCheckBox("自动吸附")
        initial_ui = dict(getattr(initial, "ui", {}) or {})
        self._auto_snap_enabled.setChecked(bool(initial_ui.get("auto_snap_enabled", True)))
        self._save_button_mode = ModernPopupComboBox()
        self._save_button_mode.addItems(["自动保存", "手动保存"])
        save_button_mode = str((getattr(initial, "ui", {}) or {}).get("save_button_mode", "auto")).strip().lower()
        if save_button_mode == "manual":
            self._save_button_mode.setCurrentText("手动保存")
        else:
            self._save_button_mode.setCurrentText("自动保存")
        self._previous_capture_action = ModernPopupComboBox()
        self._previous_capture_action.addItems(["置顶前图", "保存前图", "拼接前图", "暂存前图"])
        self._previous_capture_action.setToolTip("框选后点击鼠标确认截图，再次点击鼠标连续截图。")
        previous_action = str((getattr(initial, "ui", {}) or {}).get("previous_capture_action", "pin")).strip().lower()
        if previous_action == "pin":
            self._previous_capture_action.setCurrentText("置顶前图")
        elif previous_action == "save":
            self._previous_capture_action.setCurrentText("保存前图")
        elif previous_action == "stitch":
            self._previous_capture_action.setCurrentText("拼接前图")
        elif previous_action == "stash":
            self._previous_capture_action.setCurrentText("暂存前图")
        else:
            self._previous_capture_action.setCurrentText("置顶前图")
        self._post_capture_button_style = ModernPopupComboBox()
        self._post_capture_button_style.addItems(["图标按钮", "文字按钮"])
        button_style = str(initial_ui.get("post_capture_button_style", "icon")).strip().lower()
        self._post_capture_button_style.setCurrentText("文字按钮" if button_style == "text" else "图标按钮")
        for combo in (self._save_button_mode, self._previous_capture_action, self._post_capture_button_style):
            combo.setProperty("matchPopupWidthToParent", True)
            combo.setFixedWidth(120)
        row_flags = QWidget()
        row_flags_layout = QHBoxLayout(row_flags)
        row_flags_layout.setContentsMargins(0, 0, 0, 0)
        row_flags_layout.setSpacing(12)
        row_flags_layout.addWidget(self._autostart)
        row_flags_layout.addWidget(self._notifications)
        row_flags_layout.addWidget(self._auto_snap_enabled)
        row_flags_layout.addStretch(1)

        file_dir = str(initial.image_output_dir or initial.pdf_output_dir)
        self._img_dir = QLineEdit(file_dir)
        self._pdf_dir = self._img_dir
        self._img_browse = QPushButton("浏览")

        self._hotkey = QKeySequenceEdit()
        self._hotkey.setKeySequence(QKeySequence(pynput_to_qt(initial.hotkey)))
        self._scroll_hotkey = QKeySequenceEdit()
        self._scroll_hotkey.setKeySequence(QKeySequence(pynput_to_qt(str(initial_ui.get("scroll_hotkey", "<ctrl>+<f1>") or "<ctrl>+<f1>"))))

        form = QFormLayout()
        form.addRow("", row_flags)
        form.addRow("点击保存按钮", self._save_button_mode)
        form.addRow("文件保存路径", self._row_dir(self._img_dir, self._img_browse))
        form.addRow("框选截屏快捷键", self._hotkey)
        form.addRow("滚动截屏快捷键", self._scroll_hotkey)
        continuous_capture_label = QLabel("连续截图")
        continuous_capture_label.setToolTip("框选后点击鼠标确认截图，再次点击鼠标连续截图。")
        form.addRow(continuous_capture_label, self._previous_capture_action)
        form.addRow("按钮组样式", self._post_capture_button_style)

        annotation_style_group = self._build_annotation_style_group()

        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        general_layout.setContentsMargins(12, 12, 12, 12)
        general_layout.addLayout(form)
        self._general_status_label = self._make_settings_status_label()
        general_layout.addWidget(self._general_status_label)
        general_layout.addWidget(annotation_style_group)

        tabs = QTabWidget()
        tabs.addTab(general_tab, "基本设置")
        tabs.addTab(self._build_translator_tab(), "模型管理")
        tabs.addTab(self._build_about_tab(), "关于")

        self._selection_translate_switch = QCheckBox("划词开关")
        self._selection_translate_switch.setChecked(bool(self._translator.get("selection_translate_enabled", False)))
        self._selection_translate_switch.setToolTip("勾选后启用划词功能")
        self._selection_popup_switch = QCheckBox("划词浮窗")
        self._selection_popup_switch.setChecked(bool(self._translator.get("selection_popup_enabled", False)))
        self._selection_popup_switch.setToolTip("勾选后在划词旁边弹出功能浮窗，不勾选可以通过快捷键实现相关功能")
        self._ocr_translate_switch = QCheckBox("识别翻译")
        self._ocr_translate_switch.setChecked(bool(self._translator.get("ocr_translate_enabled", False)))
        self._ocr_translate_switch.setToolTip("识别内容非中文字符占比大于50%时，自动翻译。")
        self._ocr_translate_switch.setVisible(False)
        self._auto_copy_switch = QCheckBox("自动复制")
        self._auto_copy_switch.setChecked(bool(self._translator.get("auto_copy_answers", False)))
        self._auto_copy_switch.setToolTip("勾选后，自动复制翻译、回答、回复、优化等回答内容。")
        self._local_translation_service_switch = QCheckBox("本地API")
        self._local_translation_service_switch.setChecked(bool(self._translator.get("local_translation_service_enabled", False)))
        self._local_translation_service_switch.setToolTip("勾选后启动 DeepCat 本地 API 服务，提供翻译和问答的 OpenAI 兼容接口。")

        self._local_translation_firewall_btn = self._create_local_translation_firewall_button()

        self._codex_config_switch = QCheckBox("配置Codex")
        self._codex_config_switch.setChecked(bool(self._translator.get("codex_config_enabled", False)))
        self._codex_config_switch.setToolTip("勾选后将自动配置本地API接口到Codex")

        self._prompt_settings_btn = QPushButton("提示词")
        self._style_translator_footer_button(self._prompt_settings_btn, 70)
        self._prompt_settings_btn.setStyleSheet(self._prompt_settings_btn.styleSheet() + " QPushButton::menu-indicator { image: none; width: 0px; }")
        self._prompt_settings_btn.installEventFilter(self)

        self._prompt_settings_btn.clicked.connect(self._show_prompt_settings_popup)

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        row_btn = QHBoxLayout()
        row_btn.setContentsMargins(4, 0, 4, 2)
        row_btn.setSpacing(10)
        row_btn.addWidget(self._selection_translate_switch)
        row_btn.addWidget(self._selection_popup_switch)
        row_btn.addWidget(self._ocr_translate_switch)
        row_btn.addWidget(self._auto_copy_switch)
        row_btn.addWidget(self._local_translation_service_switch)
        row_btn.addWidget(self._local_translation_firewall_btn)
        row_btn.addWidget(self._codex_config_switch)
        row_btn.addStretch(1)
        row_btn.addWidget(self._prompt_settings_btn)
        row_btn.addWidget(btn_close)
        self._translator_footer_widgets = [
            self._selection_translate_switch,
            self._selection_popup_switch,
            self._auto_copy_switch,
            self._local_translation_service_switch,
            self._local_translation_firewall_btn,
            self._codex_config_switch,
            self._prompt_settings_btn,
        ]

        def update_translator_footer(idx: int) -> None:
            visible = tabs.tabText(idx) == "模型管理"
            self._translator_footer_active = bool(visible)
            for widget in self._translator_footer_widgets:
                widget.setVisible(bool(visible))
            self._refresh_local_translation_firewall_button()

        update_translator_footer(tabs.currentIndex())
        tabs.currentChanged.connect(update_translator_footer)

        root = QVBoxLayout()
        root.setContentsMargins(8, 8, 8, 6)
        root.setSpacing(6)
        root.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        root.addWidget(tabs)
        root.addLayout(row_btn)
        self.setLayout(root)
        self._restore_window_geometry()
        self._compact_window_height()

        self._img_browse.clicked.connect(lambda: self._browse_dir(self._img_dir))
        self._img_dir.editingFinished.connect(self._apply_dirs)
        self._autostart.stateChanged.connect(self._apply_autostart)
        self._notifications.stateChanged.connect(self._apply_notifications)
        self._app_log_switch.stateChanged.connect(self._apply_logging_settings)
        self._crash_log_switch.stateChanged.connect(self._apply_logging_settings)
        self._check_update_btn.clicked.connect(lambda: self._check_for_updates(manual=True))
        self._auto_update_switch.stateChanged.connect(self._apply_auto_update)
        self._auto_snap_enabled.stateChanged.connect(self._apply_auto_snap_enabled)
        self._save_button_mode.currentIndexChanged.connect(self._apply_save_button_mode)
        self._previous_capture_action.currentIndexChanged.connect(self._apply_previous_capture_action)
        self._post_capture_button_style.currentIndexChanged.connect(self._apply_post_capture_button_style)
        self._hotkey.keySequenceChanged.connect(self._apply_hotkey)
        self._scroll_hotkey.keySequenceChanged.connect(self._apply_scroll_hotkey)
        self._translator_model.activated.connect(lambda idx: self._on_model_combo_activated(self._translator_model, idx, "translate"))
        self._qa_model.activated.connect(lambda idx: self._on_model_combo_activated(self._qa_model, idx, "qa"))
        self._translator_model.deleteRequested.connect(lambda model_name: self._delete_translator_model(model_name, "translate"))
        self._qa_model.deleteRequested.connect(lambda model_name: self._delete_translator_model(model_name, "qa"))
        self._translator_model.batchDeleteRequested.connect(lambda model_names: self._delete_translator_models(model_names, "translate"))
        self._qa_model.batchDeleteRequested.connect(lambda model_names: self._delete_translator_models(model_names, "qa"))
        if self._translator_model.lineEdit() is not None:
            self._translator_model.lineEdit().editingFinished.connect(lambda: self._on_model_edit_finished(self._translator_model, "translate"))
        if self._qa_model.lineEdit() is not None:
            self._qa_model.lineEdit().editingFinished.connect(lambda: self._on_model_edit_finished(self._qa_model, "qa"))
        for field in (self._translator_api_url, self._translator_model_name, self._translator_model_note, self._translator_api_key, self._translator_proxy_url):
            field.textEdited.connect(self._mark_translator_settings_dirty)
        self._translator_api_url.editingFinished.connect(self._apply_translator_settings)
        self._translator_model_name.editingFinished.connect(self._apply_translator_settings)
        self._translator_model_note.editingFinished.connect(self._apply_translator_settings)
        self._translator_provider.activated.connect(lambda *_: self._on_provider_selected_for_new_model())
        self._translator_api_key.editingFinished.connect(self._apply_translator_settings)
        self._translator_use_proxy.stateChanged.connect(lambda *_: self._mark_translator_settings_dirty())
        self._translator_use_proxy.stateChanged.connect(self._apply_translator_settings)
        self._translator_proxy_url.editingFinished.connect(self._apply_translator_settings)
        self._fill_codex_config_switch.stateChanged.connect(self._apply_current_model_codex_config_enabled)
        self._translator_test_btn.clicked.connect(self._test_translator_connection)
        self._selection_translate_switch.stateChanged.connect(self._apply_selection_translate_enabled)
        self._selection_popup_switch.stateChanged.connect(self._apply_selection_popup_enabled)
        self._ocr_translate_switch.stateChanged.connect(self._apply_ocr_translate_enabled)
        self._auto_copy_switch.stateChanged.connect(self._apply_auto_copy_answers)
        self._local_translation_service_switch.stateChanged.connect(self._apply_local_translation_service_enabled)
        self._codex_config_switch.stateChanged.connect(self._apply_codex_config_enabled)


    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()










































































    def eventFilter(self, obj, event) -> bool:
        try:
            SettingsDialog._handle_prompt_settings_popup_hover_event(self, obj, event)
        except Exception:
            pass
        if (
            hasattr(self, "_translator_provider")
            and self._translator_provider is not None
            and self._translator_provider.view() is not None
            and obj is self._translator_provider.view().viewport()
        ):
            view = self._translator_provider.view()
            try:
                pos = event.position().toPoint()
            except Exception:
                pos = event.pos() if hasattr(event, "pos") else None
            if event.type() == QEvent.Type.Leave:
                view.viewport().unsetCursor()
            elif pos is not None and event.type() in {QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease}:
                index = view.indexAt(pos)
                in_action = False
                if index.isValid():
                    action_rect = ProviderSwitchHintDelegate.action_rect(view.visualRect(index), view.fontMetrics())
                    in_action = action_rect.contains(pos)
                if event.type() == QEvent.Type.MouseMove:
                    if in_action:
                        view.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
                    else:
                        view.viewport().unsetCursor()
                elif event.type() == QEvent.Type.MouseButtonRelease:
                    try:
                        is_left = event.button() == Qt.MouseButton.LeftButton
                    except Exception:
                        is_left = True
                    if is_left and index.isValid():
                        provider = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
                        self._translator_provider.hidePopup()
                        if in_action:
                            self._move_current_model_to_provider(provider)
                        else:
                            self._select_provider_for_new_model(provider)
                        return True
        if hasattr(self, "_translator_api_key") and obj is self._translator_api_key and event.type() == QEvent.Type.Resize:
            self._position_translator_api_key_eye()
        if hasattr(self, "_translator_proxy_url") and obj is self._translator_proxy_url and event.type() == QEvent.Type.Resize:
            self._position_translator_proxy_test_btn()
        if hasattr(self, "_translator_api_url") and obj is self._translator_api_url and event.type() == QEvent.Type.Resize:
            self._position_translator_api_url_role_label()
            self._position_translator_get_models_btn()
        if hasattr(self, "_translator_provider") and obj is self._translator_provider and event.type() == QEvent.Type.Resize:
            self._position_translator_provider_btn()
            self._position_translator_provider_hover_hint()
        if hasattr(self, "_translator_provider") and obj is self._translator_provider and event.type() in {QEvent.Type.Enter, QEvent.Type.HoverEnter}:
            if hasattr(self, "_translator_provider_hover_hint") and self._translator_provider_hover_hint is not None:
                self._position_translator_provider_hover_hint()
                self._translator_provider_hover_hint.setVisible(True)
                self._translator_provider_hover_hint.raise_()
        if hasattr(self, "_translator_provider") and obj is self._translator_provider and event.type() in {QEvent.Type.Leave, QEvent.Type.HoverLeave}:
            if hasattr(self, "_translator_provider_hover_hint") and self._translator_provider_hover_hint is not None:
                self._translator_provider_hover_hint.setVisible(False)
        if hasattr(self, "_translator_model_name") and self._translator_model_name is not None and obj is self._translator_model_name and event.type() == QEvent.Type.Resize:
            self._position_translator_model_name_btns()
        return super().eventFilter(obj, event)

    def _restore_window_geometry(self) -> None:
        restored = False
        try:
            g = str((getattr(self._current, "ui", {}) or {}).get("settings_dialog_geometry_b64", "") or "")
            if g:
                restored = bool(self.restoreGeometry(QByteArray(decode_qbytearray(g))))
                self._compact_window_height()
        except Exception:
            pass
        self._ensure_window_on_screen(restored=bool(restored))

    def _persist_window_geometry(self) -> None:
        try:
            self._ensure_window_on_screen(restored=True)
            self._current = update_ui_settings(settings_dialog_geometry_b64=encode_qbytearray(bytes(self.saveGeometry())))
        except Exception:
            pass

    def accept(self) -> None:
        self._persist_window_geometry()
        super().accept()

    def reject(self) -> None:
        self._persist_window_geometry()
        super().reject()

    def closeEvent(self, event) -> None:
        self._persist_window_geometry()
        super().closeEvent(event)
