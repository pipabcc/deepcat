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
    QButtonGroup,
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
    normalize_network_probe_settings,
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
from deepcat.ui.settings_dialog.model_config_logic import (
    model_config_identity,
    models_by_provider,
    normalize_api_url,
    same_model_config_scope,
    unique_model_note_name,
)

TranslatorConnectionTestWorker = DynamicModuleAttribute(
    "deepcat.ui.settings_dialog.dialog", "TranslatorConnectionTestWorker"
)
apply_claude_code_config = DynamicModuleAttribute(
    "deepcat.ui.settings_dialog.dialog", "apply_claude_code_config"
)
apply_codex_config = DynamicModuleAttribute("deepcat.ui.settings_dialog.dialog", "apply_codex_config")
has_codex_client_process = DynamicModuleAttribute(
    "deepcat.ui.settings_dialog.dialog", "has_codex_client_process"
)
list_codex_client_process_names = DynamicModuleAttribute(
    "deepcat.ui.settings_dialog.dialog", "list_codex_client_process_names"
)
check_client_window_loaded = DynamicModuleAttribute(
    "deepcat.ui.settings_dialog.dialog", "check_client_window_loaded"
)
launch_codex_client = DynamicModuleAttribute("deepcat.ui.settings_dialog.dialog", "launch_codex_client")
restore_claude_code_config = DynamicModuleAttribute(
    "deepcat.ui.settings_dialog.dialog", "restore_claude_code_config"
)
stop_codex_client = DynamicModuleAttribute("deepcat.ui.settings_dialog.dialog", "stop_codex_client")


def _settings_dialog_class():
    from deepcat.ui.settings_dialog.dialog import SettingsDialog

    return SettingsDialog


class TranslatorSettingsMixin:
    def _build_translator_tab(self) -> QWidget:
        tab = QWidget()
        self._translator_tab = tab
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        model_group = QGroupBox()
        model_layout = QHBoxLayout(model_group)
        model_layout.setContentsMargins(8, 10, 8, 8)
        model_layout.setSpacing(16)
        self._translator_model = TranslatorModelComboBox()
        self._qa_model = TranslatorModelComboBox()
        for combo in (self._translator_model, self._qa_model):
            combo.setEditable(False)
            combo.setFixedWidth(self._MODEL_ROLE_COMBO_WIDTH)
            combo.setMinimumContentsLength(16)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._translator_model.setProperty("purpose", "translate")
        self._qa_model.setProperty("purpose", "qa")

        current_model = str(self._translator.get("translate_model", self._translator.get("current_model", "gemini-3.5-flash-thinking")))
        qa_model = _settings_dialog_class()._initial_translator_detail_model(self._translator)
        if current_model == "自定义模型":
            current_model = "gemini-3.5-flash-thinking"
        if qa_model == "自定义模型":
            qa_model = "gemini-3.5-flash-thinking"

        detail_model = qa_model
        self._translator_current_model = detail_model
        self._translator_current_role = "qa"

        left_model = QWidget()
        left_model_layout = QHBoxLayout(left_model)
        left_model_layout.setContentsMargins(2, 0, 0, 0)
        left_model_layout.setSpacing(6)
        left_label = QLabel("翻译模型:")
        left_label.setFixedWidth(self._MODEL_ROLE_LABEL_WIDTH)
        left_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        left_model_layout.addWidget(left_label)
        left_model_layout.addWidget(self._translator_model, 0)
        left_model_layout.addStretch(1)

        right_model = QWidget()
        right_model_layout = QHBoxLayout(right_model)
        right_model_layout.setContentsMargins(0, 0, 0, 0)
        right_model_layout.setSpacing(4)
        right_label = QLabel("问答模型:")
        right_label.setFixedWidth(self._MODEL_ROLE_LABEL_WIDTH)
        right_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        right_model_layout.addWidget(right_label)
        right_model_layout.addWidget(self._qa_model, 0)
        right_model_layout.addStretch(1)

        model_layout.addWidget(left_model, 1)
        model_layout.addWidget(right_model, 1)
        layout.addWidget(model_group)

        config_group = QGroupBox()
        config_form = QFormLayout(config_group)
        config_form.setContentsMargins(8, 10, 8, 8)
        config_form.setHorizontalSpacing(4)

        def form_label(text: str) -> QLabel:
            label = QLabel(text)
            label.setFixedWidth(self._TRANSLATOR_FORM_LABEL_WIDTH)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return label

        provider_row_widget = QWidget()
        provider_row_layout = QHBoxLayout(provider_row_widget)
        provider_row_layout.setContentsMargins(0, 0, 0, 0)
        provider_row_layout.setSpacing(4)

        self._translator_provider = ModernPopupComboBox()
        self._translator_provider.setObjectName("TranslatorProviderCombo")
        self._translator_provider.setEditable(False)
        self._translator_provider.setItemDelegate(ProviderSwitchHintDelegate(self._translator_provider))
        self._translator_provider.view().setMouseTracking(True)
        self._translator_provider.view().viewport().setMouseTracking(True)
        self._translator_provider.view().viewport().installEventFilter(self)
        self._translator_provider.installEventFilter(self)
        self._translator_provider.setMouseTracking(True)
        self._translator_provider.setStyleSheet(
            "QComboBox#TranslatorProviderCombo { padding-right: 152px; }"
            "QComboBox#TranslatorProviderCombo::drop-down { width: 28px; border: none; }"
        )
        _settings_dialog_class()._refresh_translator_providers(self)

        self._translator_provider_hover_hint = QLabel("选择分组添加模型", self._translator_provider)
        self._translator_provider_hover_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._translator_provider_hover_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._translator_provider_hover_hint.setStyleSheet("background: transparent; border: none; color: #94a3b8; font-size: 12px;")
        self._translator_provider_hover_hint.setVisible(False)

        inline_btn_style = (
            "QPushButton { background: transparent; border: none; border-radius: 0px; padding: 0px; margin: 0px; color: #1e293b; }"
            "QPushButton:hover { color: #334155; background: transparent; border: none; }"
            "QPushButton:pressed { color: #0f172a; background: transparent; border: none; }"
        )
        edit_icon = QIcon(str(Path(__file__).resolve().parent.parent / "assets" / "icon_todo_edit.svg"))

        self._edit_provider_btn = QPushButton(self._translator_provider)
        self._edit_provider_btn.setToolTip("重命名分组")
        self._edit_provider_btn.setFixedSize(20, 20)
        self._edit_provider_btn.setIcon(edit_icon)
        self._edit_provider_btn.setIconSize(QSize(16, 16))
        self._edit_provider_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_provider_btn.setStyleSheet(inline_btn_style)
        self._edit_provider_btn.clicked.connect(self._on_edit_provider_clicked)

        self._add_provider_btn = QPushButton("+", self._translator_provider)
        self._add_provider_btn.setToolTip("添加分组")
        self._add_provider_btn.setFixedSize(20, 20)
        self._add_provider_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_provider_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 0px; padding: 0px; margin: 0px; font-size: 18px; font-weight: bold; color: #1e293b; }"
            "QPushButton:hover { color: #334155; background: transparent; border: none; }"
            "QPushButton:pressed { color: #0f172a; background: transparent; border: none; }"
        )
        self._add_provider_btn.clicked.connect(self._on_add_provider_clicked)

        provider_row_layout.addWidget(self._translator_provider, 1)

        self._translator_api_url = QLineEdit()
        self._translator_api_url.setTextMargins(0, 0, 34, 0)
        self._translator_api_url.installEventFilter(self)
        self._translator_api_url.setPlaceholderText("输入新的 API 地址")

        self._translator_api_url_role_label = QLabel(self._translator_api_url)
        self._translator_api_url_role_label.setStyleSheet("background: transparent; border: none; color: #94a3b8; font-size: 12px; font-weight: normal;")
        self._translator_api_url_role_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._translator_api_url_role_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._translator_api_url_role_label.setVisible(False)

        self._fetching_models = False

        self._new_api_model_btn = QToolButton(self._translator_api_url)
        self._new_api_model_btn.setText("+")
        self._new_api_model_btn.setToolTip("新建一条模型配置，不修改已有配置。")
        self._new_api_model_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_api_model_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; border-radius: 0px; padding: 0px; margin: 0px; font-size: 18px; font-weight: bold; color: #1e293b; }"
            "QToolButton:hover { background: rgba(15, 23, 42, 0.06); color: #334155; }"
            "QToolButton:pressed { background: rgba(15, 23, 42, 0.12); color: #0f172a; }"
            "QToolButton:disabled { background: transparent; }"
        )
        self._new_api_model_btn.clicked.connect(self._on_new_api_model_clicked)

        model_row_widget = QWidget()
        model_row_layout = QHBoxLayout(model_row_widget)
        model_row_layout.setContentsMargins(0, 0, 0, 0)
        model_row_layout.setSpacing(4)

        self._translator_model_name = QLineEdit()
        self._translator_model_name.setObjectName("TranslatorModelNameEdit")
        self._translator_model_name.setMinimumWidth(self._MODEL_ID_COMBO_MIN_WIDTH)
        self._translator_model_name.setMaximumWidth(self._MODEL_ID_COMBO_MAX_WIDTH)
        self._translator_model_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._translator_model_name.setTextMargins(0, 0, 34, 0)
        self._translator_model_name.setPlaceholderText("填写接口模型 ID")
        self._translator_model_name.setToolTip("模型 ID 会写入 model_name，用于请求 API；点击右侧按钮可从当前 API 返回的模型列表中选择。")
        self._translator_model_name.installEventFilter(self)
        self._translator_model_name.setStyleSheet(
            "QLineEdit#TranslatorModelNameEdit { color: #111827; }"
        )

        self._model_list_plus_btn = QPushButton()
        self._model_list_plus_btn.setVisible(False)
        self._fetched_models_list = []
        self._fetched_models_base_url = ""
        self._model_id_selection_creates_config = False

        self._get_models_btn = QToolButton(self._translator_model_name)
        self._get_models_btn.setText("")
        self._get_models_btn.setToolTip("从当前 API 地址拉取模型 ID 列表")
        self._get_models_btn.setIcon(QIcon(str(Path(__file__).resolve().parent.parent / "assets" / "icon_action_refresh.svg")))
        self._get_models_btn.setIconSize(QSize(16, 16))
        self._get_models_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._get_models_btn.setStyleSheet(
            "QToolButton { background: transparent; border: none; border-radius: 0px; padding: 0px; margin: 0px; }"
            "QToolButton:hover { background: rgba(15, 23, 42, 0.06); }"
            "QToolButton:pressed { background: rgba(15, 23, 42, 0.12); }"
            "QToolButton:disabled { background: transparent; }"
        )
        self._get_models_btn.clicked.connect(self._on_get_models_clicked)

        self._translator_model_note = QLineEdit()
        self._translator_model_note.setMinimumWidth(self._MODEL_ID_COMBO_MIN_WIDTH)
        self._translator_model_note.setMaximumWidth(self._MODEL_ID_COMBO_MAX_WIDTH)
        self._translator_model_note.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._translator_model_note.setPlaceholderText("界面显示名称")
        self._translator_model_note.setToolTip("显示名称/模型别名只用于界面列表；不会作为 API 模型 ID 请求。")

        self._translator_save_status = QLabel("")
        self._translator_save_status.setObjectName("TranslatorSaveStatus")
        self._translator_save_status.setFixedWidth(96)
        self._translator_save_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._translator_save_status.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 700;")
        self._translator_save_status.setToolTip("当前模型配置保存状态")
        self._translator_save_status.setVisible(False)
        self._translator_save_status_token = 0

        note_label = QLabel("显示名称:")
        note_label.setFixedWidth(self._TRANSLATOR_FORM_LABEL_WIDTH)
        note_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        model_row_layout.addWidget(self._translator_model_name, 1)
        model_row_layout.addWidget(note_label)
        model_row_layout.addWidget(self._translator_model_note, 1)

        self._fill_codex_config_switch = QCheckBox("填入Codex")
        self._fill_codex_config_switch.setChecked(False)
        self._fill_codex_config_switch.setToolTip("勾选后将当前模型 ID、API 地址和 API 密钥写入 Codex 配置")
        self._fill_codex_config_switch.hide()

        self._translator_test_btn = QPushButton("模型测试")
        _settings_dialog_class()._style_translator_footer_button(self._translator_test_btn, 70)
        self._translator_test_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._translator_testing = False

        self._translator_api_key = QLineEdit()
        self._translator_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._translator_api_key.setTextMargins(0, 0, 34, 0)
        self._translator_api_key.installEventFilter(self)
        self._translator_api_key_eye = ApiKeyVisibilityButton(self._translator_api_key)
        self._translator_api_key_eye.clicked.connect(self._toggle_translator_api_key_visibility)
        self._translator_api_key.setPlaceholderText("API 密钥")

        self._translator_use_proxy = QCheckBox("使用代理")
        self._translator_use_proxy.setToolTip("勾选后仅当前模型配置使用代理，不影响其他模型。")

        self._download_mt15_btn = QPushButton("下载MT1.5")
        self._download_mt15_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #1e90ff; border: none; padding: 0px; text-decoration: underline; font-weight: normal; font-size: 13px; }"
            "QPushButton:hover { color: #00bfff; }"
            "QPushButton:disabled { color: #a1a1a1; text-decoration: none; }"
        )
        self._download_mt15_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_mt15_btn.clicked.connect(self._on_download_mt15_clicked)

        self._download_mt2_btn = QPushButton("下载MT2")
        self._download_mt2_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #1e90ff; border: none; padding: 0px; text-decoration: underline; font-weight: normal; font-size: 13px; }"
            "QPushButton:hover { color: #00bfff; }"
            "QPushButton:disabled { color: #a1a1a1; text-decoration: none; }"
        )
        self._download_mt2_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_mt2_btn.clicked.connect(self._on_download_mt2_clicked)

        self._download_progress = QProgressBar()
        self._download_progress.setRange(0, 100)
        self._download_progress.setValue(0)
        self._download_progress.setFixedWidth(80)
        self._download_progress.setFixedHeight(14)
        self._download_progress.setTextVisible(True)
        self._download_progress.setStyleSheet(
            "QProgressBar { border: 1px solid #cbd5e1; border-radius: 4px; background: white; text-align: center; font-size: 10px; color: #1f2937; }"
            "QProgressBar::chunk { background-color: #10b981; border-radius: 3px; }"
        )
        self._download_progress.setVisible(False)
        self._active_download_worker = None

        self._download_cancel_btn = QPushButton("取消")
        self._download_cancel_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #ef4444; border: none; padding: 0px; text-decoration: underline; font-weight: normal; font-size: 13px; }"
            "QPushButton:hover { color: #f87171; }"
        )
        self._download_cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_cancel_btn.setVisible(False)
        self._download_cancel_btn.clicked.connect(self._on_download_cancel_clicked)

        proxy_test_row = QWidget()
        proxy_test_layout = QHBoxLayout(proxy_test_row)
        proxy_test_layout.setContentsMargins(0, 0, 0, 0)
        proxy_test_layout.setSpacing(8)
        proxy_test_layout.addWidget(self._translator_use_proxy)
        proxy_test_layout.addWidget(self._download_mt15_btn)
        proxy_test_layout.addWidget(self._download_mt2_btn)
        proxy_test_layout.addWidget(self._download_progress)
        proxy_test_layout.addWidget(self._download_cancel_btn)
        proxy_test_layout.addStretch(1)
        proxy_test_layout.addWidget(self._translator_save_status)
        proxy_test_layout.addStretch(1)
        proxy_test_layout.addWidget(self._fill_codex_config_switch)
        proxy_test_layout.addWidget(self._translator_test_btn)

        config_form.addRow(form_label("模型分组:"), provider_row_widget)
        config_form.addRow(form_label("API地址:"), self._translator_api_url)
        config_form.addRow(form_label("API密钥:"), self._translator_api_key)
        config_form.addRow(form_label("模型ID:"), model_row_widget)
        config_form.addRow(form_label(""), proxy_test_row)
        layout.addWidget(config_group)

        proxy_group = QGroupBox()
        proxy_form = QFormLayout(proxy_group)
        proxy_form.setContentsMargins(8, 10, 8, 8)
        proxy_form.setHorizontalSpacing(4)
        self._translator_proxy_url = QLineEdit()
        self._translator_proxy_url.setPlaceholderText("socks5://127.0.0.1:1080")

        self._translator_proxy_url_test_btn = ProxyTestButton(self._translator_proxy_url)
        self._translator_proxy_url_test_btn.clicked.connect(self._test_proxy_connection)
        self._translator_proxy_url.textChanged.connect(lambda: self._translator_proxy_url_test_btn.setState("idle"))
        self._translator_proxy_url.setTextMargins(0, 0, 34, 0)
        self._translator_proxy_url.installEventFilter(self)
        _settings_dialog_class()._install_translator_line_edit_context_menus(self)

        proxy_row = QWidget()
        proxy_row_layout = QHBoxLayout(proxy_row)
        proxy_row_layout.setContentsMargins(0, 0, 0, 0)
        proxy_row_layout.setSpacing(6)
        proxy_row_layout.addWidget(self._translator_proxy_url, 1)

        probe_mode = _settings_dialog_class()._network_probe_mode_from_settings(self)
        self._network_probe_mode_group = QButtonGroup(proxy_row)
        self._network_probe_mode_group.setExclusive(True)
        self._network_probe_mode_buttons: dict[str, QToolButton] = {}
        probe_mode_options = (
            ("http_204", "204", "通过 Google 204 响应检测外网可用性"),
            ("tcp_connect", "Ping", "通过 TCP CONNECT 到 google.com:443 检测连通性"),
        )
        for mode, text, tooltip in probe_mode_options:
            button = QToolButton(proxy_row)
            button.setObjectName("NetworkProbeModeButton")
            button.setText(text)
            button.setToolTip(tooltip)
            button.setCheckable(True)
            button.setChecked(mode == probe_mode)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedSize(46 if mode == "http_204" else 52, 28)
            button.setProperty("probeMode", mode)
            button.setStyleSheet(
                "QToolButton#NetworkProbeModeButton {"
                " background: #ffffff; border: 1px solid #cbd5e1; border-radius: 4px;"
                " color: #475569; padding: 0px 6px; font-size: 11px; font-weight: 700; }"
                "QToolButton#NetworkProbeModeButton:hover { background: #f8fafc; color: #0f172a; }"
                "QToolButton#NetworkProbeModeButton:checked {"
                " background: #1e293b; border-color: #1e293b; color: #ffffff; }"
            )
            self._network_probe_mode_group.addButton(button)
            self._network_probe_mode_buttons[mode] = button
            proxy_row_layout.addWidget(button)
        self._network_probe_mode_group.buttonClicked.connect(
            lambda button: _settings_dialog_class()._apply_network_probe_mode(
                self,
                str(button.property("probeMode") or "http_204"),
            )
        )

        proxy_form.addRow(form_label("代理地址:"), proxy_row)
        layout.addWidget(proxy_group)

        tab.installEventFilter(self)

        self._fill_model_combos()
        self._set_combo_selected_value(self._translator_model, current_model)
        self._set_combo_selected_value(self._qa_model, qa_model)
        self._load_translator_model_config(detail_model)
        self._sync_current_model_codex_ui()
        self._position_translator_api_key_eye()
        self._position_translator_proxy_test_btn()
        self._position_translator_api_url_role_label()
        self._position_translator_get_models_btn()
        self._position_translator_provider_btn()
        self._position_translator_model_name_btns()
        self._update_api_url_role_label()

        # 劫持 paintEvent 以添加水印
        def make_watermark_paint_event(widget, original_paint_event, get_role_fn):
            def new_paint_event(event):
                # 绘制原控件
                original_paint_event(event)

                # 获取角色和水印文字
                role = get_role_fn()
                watermark_text = "翻译模型" if role == "translate" else "问答模型"

                # 绘制水印
                from PyQt6.QtGui import QPainter, QFont, QColor
                painter = QPainter(widget)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)

                # 极淡的自适应前景色透明度 (约10%)
                text_color = widget.palette().color(widget.foregroundRole())
                painter.setPen(QColor(text_color.red(), text_color.green(), text_color.blue(), 25))

                font = QFont("Microsoft YaHei", 9)
                font.setItalic(True)
                painter.setFont(font)

                # 裁剪区域限制在内部
                painter.setClipRect(widget.rect())

                w = widget.width()
                h = widget.height()

                # 决定位置数量，避免过于密集
                if w < 180:
                    x_positions = [w / 2]
                else:
                    x_positions = [w * 0.3, w * 0.7]

                for x in x_positions:
                    painter.save()
                    painter.translate(x, h / 2 + 4)
                    painter.rotate(-12)

                    # 居中绘制文字
                    metrics = painter.fontMetrics()
                    tw = metrics.horizontalAdvance(watermark_text)
                    th = metrics.height()
                    painter.drawText(int(-tw / 2), int(th / 4), watermark_text)
                    painter.restore()
            return new_paint_event

        self._translator_provider.paintEvent = make_watermark_paint_event(
            self._translator_provider,
            self._translator_provider.paintEvent,
            self._current_translator_role
        )
        self._translator_api_url.paintEvent = make_watermark_paint_event(
            self._translator_api_url,
            self._translator_api_url.paintEvent,
            self._current_translator_role
        )

        layout.addStretch(1)
        return tab

    def _install_translator_line_edit_context_menus(self) -> None:
        _settings_dialog_class()._install_custom_text_context_menus(self, (
            getattr(self, "_translator_api_url", None),
            getattr(self, "_translator_model_note", None),
            getattr(self, "_translator_api_key", None),
            getattr(self, "_translator_proxy_url", None),
        ))

    def _network_probe_mode_from_settings(self) -> str:
        settings = getattr(self, "_current", None) or getattr(self, "_app_settings", None)
        ui = dict(getattr(settings, "ui", {}) or {})
        return str(normalize_network_probe_settings(ui.get("network_probe"))["mode"])

    def _sync_network_probe_mode_buttons(self, mode: str) -> None:
        buttons = getattr(self, "_network_probe_mode_buttons", {}) or {}
        for button_mode, button in buttons.items():
            button.blockSignals(True)
            try:
                button.setChecked(button_mode == mode)
            finally:
                button.blockSignals(False)

    def _apply_network_probe_mode(self, mode: str) -> None:
        normalized_mode = str(normalize_network_probe_settings({"mode": mode})["mode"])
        settings = getattr(self, "_current", None) or getattr(self, "_app_settings", None)
        ui = dict(getattr(settings, "ui", {}) or {})
        probe_settings = normalize_network_probe_settings(ui.get("network_probe"))
        if str(probe_settings.get("mode")) == normalized_mode:
            _settings_dialog_class()._sync_network_probe_mode_buttons(self, normalized_mode)
            return

        probe_settings["mode"] = normalized_mode

        def _update_mode(latest: AppSettings) -> AppSettings:
            latest_ui = dict(latest.ui)
            latest_probe_settings = normalize_network_probe_settings(
                latest_ui.get("network_probe")
            )
            latest_probe_settings["mode"] = normalized_mode
            latest_ui["network_probe"] = latest_probe_settings
            return AppSettings(
                version=int(latest.version),
                autostart=bool(latest.autostart),
                auto_save=bool(getattr(latest, "auto_save", True)),
                image_output_dir=str(latest.image_output_dir),
                pdf_output_dir=str(latest.pdf_output_dir),
                hotkey=str(latest.hotkey),
                ui=latest_ui,
                notifications_enabled=bool(getattr(latest, "notifications_enabled", False)),
            )

        try:
            current = update_settings(_update_mode)
        except Exception:
            _settings_dialog_class()._sync_network_probe_mode_buttons(
                self,
                str(normalize_network_probe_settings(ui.get("network_probe"))["mode"]),
            )
            return

        self._current = current
        if hasattr(self, "_app_settings"):
            self._app_settings = current
        _settings_dialog_class()._sync_network_probe_mode_buttons(self, normalized_mode)
        _settings_dialog_class()._apply_network_probe_mode_to_owner(
            self,
            current,
            normalized_mode,
        )
        parent = self.parentWidget() if hasattr(self, "parentWidget") else None
        if parent is not None and parent is not self:
            _settings_dialog_class()._apply_network_probe_mode_to_owner(
                parent,
                current,
                normalized_mode,
            )

    @staticmethod
    def _apply_network_probe_mode_to_owner(
        target: object,
        current: AppSettings,
        mode: str,
    ) -> None:
        if hasattr(target, "_current"):
            target._current = current
        if hasattr(target, "_app_settings"):
            target._app_settings = current
        monitor = getattr(target, "_network_probe_monitor", None)
        if monitor is not None:
            monitor.set_probe_kind(mode)
        reset_notifications = getattr(target, "_reset_network_probe_notification_state", None)
        if callable(reset_notifications):
            reset_notifications()

    @staticmethod
    def _install_custom_text_context_menus(owner: object, root: object) -> None:
        text_widget_types = (QLineEdit, QTextEdit, QPlainTextEdit)
        if isinstance(root, text_widget_types):
            widgets = [root]
        elif isinstance(root, (list, tuple, set)):
            widgets = [widget for widget in root if isinstance(widget, text_widget_types)]
        elif isinstance(root, QWidget):
            widgets = []
            if isinstance(root, text_widget_types):
                widgets.append(root)
            widgets.extend(root.findChildren(text_widget_types))
        else:
            return

        for edit in widgets:
            if edit is None:
                continue
            if _settings_dialog_class()._text_widget_overrides_context_menu_event(edit):
                continue
            if bool(edit.property("deepcatCustomContextMenu")):
                continue
            edit.setProperty("deepcatCustomContextMenu", True)
            edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            edit.customContextMenuRequested.connect(
                lambda pos, line_edit=edit, context_owner=owner: _settings_dialog_class()._show_line_edit_context_menu(context_owner, line_edit, pos)
            )

    @staticmethod
    def _text_widget_overrides_context_menu_event(widget: QWidget) -> bool:
        try:
            context_menu_event = type(widget).__dict__.get("contextMenuEvent")
        except Exception:
            return False
        return isinstance(context_menu_event, FunctionType)

    @staticmethod
    def _text_widget_plain_text(widget: QWidget) -> str:
        if isinstance(widget, QLineEdit):
            return str(widget.text() or "")
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            return str(widget.toPlainText() or "")
        return ""

    @staticmethod
    def _text_widget_has_selection(widget: QWidget) -> bool:
        if isinstance(widget, QLineEdit):
            if bool(widget.hasSelectedText()):
                return True
            start = widget.selectionStart()
            end = widget.selectionEnd()
            return start >= 0 and end > start
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            return bool(widget.textCursor().hasSelection())
        return False

    @staticmethod
    def _text_widget_selected_text(widget: QWidget) -> str:
        if isinstance(widget, QLineEdit):
            selected = str(widget.selectedText() or "")
            if selected:
                return selected
            start = widget.selectionStart()
            end = widget.selectionEnd()
            if start >= 0 and end > start:
                return str(widget.text() or "")[start:end]
            return ""
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            return str(widget.textCursor().selectedText() or "").replace("\u2029", "\n")
        return ""

    @staticmethod
    def _text_widget_undo_available(widget: QWidget) -> bool:
        if isinstance(widget, QLineEdit):
            return bool(widget.isUndoAvailable())
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            document = widget.document()
            return bool(document.isUndoAvailable()) if document is not None else False
        return False

    @staticmethod
    def _text_widget_redo_available(widget: QWidget) -> bool:
        if isinstance(widget, QLineEdit):
            return bool(widget.isRedoAvailable())
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            document = widget.document()
            return bool(document.isRedoAvailable()) if document is not None else False
        return False

    @staticmethod
    def _text_widget_delete_selection(widget: QWidget) -> None:
        if isinstance(widget, QLineEdit):
            widget.del_()
            return
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            cursor = widget.textCursor()
            if cursor.hasSelection():
                cursor.removeSelectedText()
                widget.setTextCursor(cursor)

    @staticmethod
    def _clipboard_has_paste_content(widget: QWidget) -> bool:
        app = QApplication.instance()
        if app is None or app.clipboard() is None:
            return False
        mime = app.clipboard().mimeData()
        if mime is None:
            return False
        if isinstance(widget, QLineEdit):
            return bool(mime.hasText() and str(mime.text() or ""))
        return bool(mime.hasText() or mime.hasHtml() or mime.hasImage() or mime.hasUrls())

    @staticmethod
    def _line_edit_context_menu_items(line_edit: QWidget) -> list[tuple[str, Optional[Callable[[], None]], bool]]:
        read_only = bool(line_edit.isReadOnly()) if hasattr(line_edit, "isReadOnly") else False
        has_selection = _settings_dialog_class()._text_widget_has_selection(line_edit)
        has_text = bool(_settings_dialog_class()._text_widget_plain_text(line_edit))
        clipboard_text = ""
        app = QApplication.instance()
        if app is not None and app.clipboard() is not None:
            clipboard_text = str(app.clipboard().text() or "")

        def run(action: Callable[[], None]) -> Callable[[], None]:
            def wrapped() -> None:
                try:
                    action()
                except RuntimeError:
                    pass
            return wrapped

        def copy_selected_text() -> None:
            selected_text = _settings_dialog_class()._text_widget_selected_text(line_edit)
            if not selected_text:
                return
            app_instance = QApplication.instance()
            if app_instance is not None and app_instance.clipboard() is not None:
                app_instance.clipboard().setText(selected_text)

        def cut_selected_text() -> None:
            copy_selected_text()
            _settings_dialog_class()._text_widget_delete_selection(line_edit)

        return [
            ("撤销", run(line_edit.undo), _settings_dialog_class()._text_widget_undo_available(line_edit) and not read_only),
            ("重做", run(line_edit.redo), _settings_dialog_class()._text_widget_redo_available(line_edit) and not read_only),
            ("-", None, False),
            ("剪切", run(cut_selected_text), has_selection and not read_only),
            ("复制", run(copy_selected_text), has_selection),
            ("粘贴", run(line_edit.paste), _settings_dialog_class()._clipboard_has_paste_content(line_edit) and not read_only),
            ("删除", run(lambda: _settings_dialog_class()._text_widget_delete_selection(line_edit)), has_selection and not read_only),
            ("-", None, False),
            ("全选", run(line_edit.selectAll), has_text),
        ]

    def _show_line_edit_context_menu(self, line_edit: QWidget, pos: QPoint) -> None:
        if line_edit is None:
            return
        popup = OcrGenericMenuPopup(
            _settings_dialog_class()._line_edit_context_menu_items(line_edit),
            parent=line_edit,
            active_index=None,
            active_indicator="background",
        )
        self._line_edit_context_menu_popup = popup
        popup.show_at_pos(line_edit.mapToGlobal(pos))

    def _on_add_model_clicked(self) -> None:
        role = self._current_translator_role()

        # 单元测试兼容性退化处理
        if not isinstance(self, QWidget):
            from PyQt6.QtWidgets import QInputDialog
            model_id, ok = QInputDialog.getText(
                self,
                "模型 ID",
                "请输入接口真实模型 ID：",
                QLineEdit.EchoMode.Normal
            )
            model_id = str(model_id or "").strip()
            if not ok or not model_id:
                return
        else:
            from deepcat.ui.settings_dialog.sub_dialogs import _AddModelIdDialog
            dlg = _AddModelIdDialog(self)
            if dlg.exec():
                model_id = str(dlg.name_edit.text() or "").strip()
                if not model_id:
                    return
            else:
                return

        configs = self._translator.get("model_configs") or {}
        note_name = _settings_dialog_class()._unique_model_note_name(model_id, configs)

        # 复制当前模型配置做为默认模板
        current_combo = self._qa_model if role == "qa" else self._translator_model
        current_model = str(getattr(self, "_translator_current_model", "") or self._combo_selected_value(current_combo)).strip()
        current_cfg = dict(configs.get(current_model, {}) or {})
        try:
            field_cfg = dict(self._translator_field_config(note_name))
        except Exception:
            field_cfg = {}
        merged_cfg = {**current_cfg, **field_cfg, "model_name": model_id}
        new_cfg = {
            "base_url": merged_cfg.get("base_url", ""),
            "model_name": model_id,
            "api_key": str(merged_cfg.get("api_key", "") or ""),
            "use_proxy": bool(merged_cfg.get("use_proxy", False)),
            "model_type": infer_translator_model_type(note_name, merged_cfg),
            "provider": str(merged_cfg.get("provider") or self._translator_provider.currentText().strip() or "其它"),
        }

        self._save_current_translator_model_config()
        configs = dict(self._translator.get("model_configs") or {})
        configs[note_name] = new_cfg
        self._translator["model_configs"] = configs

        self._refresh_model_combos_after_catalog_change(note_name, role)
        self._on_translator_model_changed(note_name, role)
        self._load_translator_model_config(note_name)

    def _on_edit_provider_clicked(self) -> None:
        old_provider = self._translator_provider.currentText().strip()
        if not old_provider:
            return

        # 单元测试兼容性退化处理
        if not isinstance(self, QWidget):
            from PyQt6.QtWidgets import QInputDialog
            new_provider, ok = QInputDialog.getText(
                self,
                "分组名称",
                "请输入新的分组名称：",
                QLineEdit.EchoMode.Normal,
                old_provider,
            )
            new_provider = str(new_provider or "").strip()
            new_remark = ""
            if not ok or not new_provider or new_provider == old_provider:
                return
        else:
            from deepcat.ui.settings_dialog.sub_dialogs import _AddProviderDialog
            old_remark = self._translator.get("provider_remarks", {}).get(old_provider, "")
            dlg = _AddProviderDialog(self, name_val=old_provider, remark_val=old_remark)
            if dlg.exec():
                new_provider = str(dlg.name_edit.text() or "").strip()
                new_remark = str(dlg.remark_edit.text() or "").strip()
                if not new_provider or (new_provider == old_provider and new_remark == old_remark):
                    return
            else:
                return

        # 更新 remarks 中的对应备注
        remarks = dict(self._translator.get("provider_remarks") or {})
        if old_provider in remarks:
            del remarks[old_provider]
        remarks[new_provider] = new_remark
        self._translator["provider_remarks"] = remarks

        configs = dict(self._translator.get("model_configs") or {})
        renamed_any = False
        for name, cfg in list(configs.items()):
            cfg_dict = dict(cfg or {})
            provider = str(cfg_dict.get("provider") or infer_translator_model_provider(str(name), cfg_dict)).strip()
            if provider == old_provider:
                cfg_dict["provider"] = new_provider
                configs[name] = cfg_dict
                renamed_any = True
        self._translator["model_configs"] = configs

        # 刷新提供商列表 (这会包含新/修改的分组，并清空后重新载入)
        _settings_dialog_class()._refresh_translator_providers(self)

        # 设置选中项为新的 provider
        self._translator_provider.blockSignals(True)
        try:
            new_idx = self._translator_provider.findText(new_provider)
            if new_idx >= 0:
                self._translator_provider.setCurrentIndex(new_idx)
        finally:
            self._translator_provider.blockSignals(False)

        if renamed_any:
            self._fill_model_combos()
        self._fill_model_name_combobox_items()
        self._persist_translator_settings()

    def _on_edit_model_id_clicked(self) -> None:
        old_model_id = self._translator_model_name.text().strip()
        if not old_model_id:
            return

        # 单元测试兼容性退化处理
        if not isinstance(self, QWidget):
            from PyQt6.QtWidgets import QInputDialog
            new_model_id, ok = QInputDialog.getText(
                self,
                "模型 ID",
                "请输入新的接口真实模型 ID：",
                QLineEdit.EchoMode.Normal,
                old_model_id,
            )
            new_model_id = str(new_model_id or "").strip()
            if not ok or not new_model_id or new_model_id == old_model_id:
                return
        else:
            from deepcat.ui.settings_dialog.sub_dialogs import _AddModelIdDialog
            dlg = _AddModelIdDialog(self, name_val=old_model_id)
            if dlg.exec():
                new_model_id = str(dlg.name_edit.text() or "").strip()
                if not new_model_id or new_model_id == old_model_id:
                    return
            else:
                return

        self._translator_model_name.setText(new_model_id)
        note = str(getattr(self, "_translator_current_model", "") or self._translator_model_note.text()).strip()
        if note:
            configs = dict(self._translator.get("model_configs") or {})
            cfg = dict(configs.get(note, {}) or {})
            cfg.update(self._translator_field_config(note))
            cfg["model_name"] = new_model_id
            configs[note] = cfg
            self._translator["model_configs"] = configs
        self._fill_model_name_combobox_items()
        self._translator_model_name.setText(new_model_id)
        self._refresh_model_combos_after_catalog_change(note, self._current_translator_role())
        self._persist_translator_settings()

    def _clear_translator_fields_for_new_provider(self, provider: str) -> None:
        self._translator_loading = True
        try:
            self._translator_api_url.clear()
            self._translator_model_name.clear()
            if hasattr(self._translator_model_name, "setText"):
                self._translator_model_name.setText("")
            self._translator_model_note.clear()
            self._translator_api_key.clear()
            self._translator_use_proxy.setChecked(False)
            self._translator_current_model = ""
            self._model_id_selection_creates_config = False
            self._fetched_models_list = []
            self._fetched_models_base_url = ""
            self._model_list_plus_btn.setEnabled(False)
            self._sync_translator_config_controls("glm")
            self._fill_model_name_combobox_items()
        finally:
            self._translator_loading = False
        self._update_api_url_role_label()
        _settings_dialog_class()._mark_translator_settings_dirty(self)

    @staticmethod
    def _models_by_provider_from_translator_settings(translator_obj: object, role: str = "") -> dict[str, list[str]]:
        return models_by_provider(translator_obj, role)

    def _sync_provider_popup_model_cache(self) -> None:
        provider_combo = getattr(self, "_translator_provider", None)
        if provider_combo is None:
            return
        try:
            codex_action_handler = getattr(self, "_apply_current_model_codex_config_for_model", None)
            codex_label_handler = getattr(self, "_codex_action_label_for_model", None)
            claude_code_action_handler = getattr(self, "_apply_current_model_claude_code_config_for_model", None)
            claude_code_label_handler = getattr(self, "_claude_code_action_label_for_model", None)
            setattr(provider_combo, "_codex_action_handler", codex_action_handler)
            setattr(provider_combo, "_codex_label_handler", codex_label_handler)
            setattr(
                provider_combo,
                "_claude_code_action_handler",
                claude_code_action_handler,
            )
            setattr(provider_combo, "_claude_code_label_handler", claude_code_label_handler)
            logger.debug(
                "[模型分组下拉] 同步配置按钮处理器: owner=%s codex=%s claude_code=%s",
                type(self).__name__,
                callable(codex_action_handler),
                callable(claude_code_action_handler),
            )
        except Exception:
            logger.exception("[模型分组下拉] 同步配置按钮处理器失败")
        provider_combo.setProperty(
            "providerModelsByProvider",
            {
                "translate": _settings_dialog_class()._models_by_provider_from_translator_settings(self._translator, "translate"),
                "qa": _settings_dialog_class()._models_by_provider_from_translator_settings(self._translator, "qa"),
            },
        )
        provider_combo.setProperty(
            "providerCurrentModels",
            {
                "translate": str(
                    self._translator.get("translate_model", "")
                    or self._translator.get("current_model", "")
                ).strip(),
                "qa": str(
                    self._translator.get("qa_model", "")
                    or self._translator.get("current_model", "")
                ).strip(),
            },
        )
        qa_highlight_colors = _settings_dialog_class()._provider_highlighted_model_colors(self, "qa")
        provider_combo.setProperty("providerHighlightedModels", sorted(qa_highlight_colors.keys()))
        provider_combo.setProperty("providerHighlightedModelColors", qa_highlight_colors)

    def _refresh_translator_providers(self) -> None:
        # 1. 默认内置分组
        removed_providers = {
            normalize_translator_provider(x)
            for x in self._translator.get("removed_providers", [])
            if str(x or "").strip()
        }
        default_providers = [
            "默认分组",
            "免费翻译",
            "Google Gemini",
            "ChatGPT Web",
            CLAUDE_CODE_PROVIDER_NAME,
            "本地部署",
        ]
        providers = [prov for prov in default_providers if prov == "默认分组" or prov not in removed_providers]

        # 2. 从 model_configs 读取已有的 provider
        configs = self._translator.get("model_configs") or {}
        for name, cfg in configs.items():
            cfg_dict = dict(cfg or {}) if isinstance(cfg, dict) else {}
            prov = normalize_translator_provider(
                cfg_dict.get("provider"),
                infer_translator_model_provider(str(name), cfg_dict),
            )
            if prov and prov not in providers:
                providers.append(prov)

        # 3. 从 provider_remarks 读取
        remarks = self._translator.get("provider_remarks") or {}
        for prov in remarks.keys():
            provider = normalize_translator_provider(prov)
            if provider and provider not in removed_providers and provider not in providers:
                providers.append(provider)

        # 4. 加载到下拉列表中
        self._translator_provider.blockSignals(True)
        self._translator_provider.clear()
        self._translator_provider.addItems(providers)
        self._translator_provider.blockSignals(False)
        _settings_dialog_class()._sync_provider_popup_model_cache(self)

    def delete_provider_group(self, prov_name: str) -> None:
        prov_name = str(prov_name or "").strip()
        if not prov_name or prov_name == "默认分组":
            return
        configs = dict(self._translator.get("model_configs") or {})
        affected_models = [
            str(name)
            for name, cfg in configs.items()
            if isinstance(cfg, dict) and str(cfg.get("provider") or "").strip() == prov_name
        ]
        preview = "、".join(affected_models[:8])
        if len(affected_models) > 8:
            preview = f"{preview} 等"
        impact = [
            f"删除模型分组：{prov_name}",
            f"该分组下 {len(affected_models)} 个模型配置会移动到默认分组",
        ]
        if preview:
            impact.append(f"涉及模型：{preview}")
        if not _settings_dialog_class()._confirm_dangerous_action(
            self,
            title="删除分组",
            summary=f"确定要删除模型分组“{prov_name}”吗？",
            impact=impact,
            level="中风险",
            irreversible=False,
        ):
            return

        if hasattr(self, "_save_current_translator_model_config"):
            self._save_current_translator_model_config()

        # 1. 从 provider_remarks 中删除
        remarks = dict(self._translator.get("provider_remarks") or {})
        if prov_name in remarks:
            del remarks[prov_name]
        self._translator["provider_remarks"] = remarks

        # 2. 将属于该分组的自定义模型 provider 改为 "默认分组"
        modified = False
        moved_models: list[str] = []
        for name, cfg in configs.items():
            if isinstance(cfg, dict) and str(cfg.get("provider") or "").strip() == prov_name:
                cfg["provider"] = "默认分组"
                modified = True
                moved_models.append(str(name))
        if modified:
            self._translator["model_configs"] = configs
        removed_providers = {
            str(x or "").strip()
            for x in self._translator.get("removed_providers", [])
            if str(x or "").strip()
        }
        removed_providers.add(prov_name)
        self._translator["removed_providers"] = sorted(removed_providers)

        # 3. 如果当前选中此分组，则切换为默认分组
        if self._translator_provider.currentText().strip() == prov_name:
            self._translator_provider.setCurrentText("默认分组")

        # 4. 刷新下拉框
        _settings_dialog_class()._refresh_translator_providers(self)
        if hasattr(self, "_fill_model_combos"):
            self._fill_model_combos()
        if hasattr(self, "_fill_model_name_combobox_items"):
            self._fill_model_name_combobox_items()
        if moved_models and hasattr(self, "_load_translator_model_config"):
            self._load_translator_model_config(moved_models[0])
        if hasattr(self, "_repaint_model_combos"):
            self._repaint_model_combos()
        if hasattr(self, "_persist_translator_settings"):
            self._persist_translator_settings()

    def clear_provider_models(self, prov_name: str) -> None:
        prov_name = str(prov_name or "").strip()
        if not prov_name:
            return
        configs = dict(self._translator.get("model_configs") or {})
        removed = {str(x).strip() for x in self._translator.get("removed_models", []) if str(x).strip()}
        default_models = set((default_translator_settings().get("model_configs") or {}).keys())

        models_to_remove = []
        for name, cfg in configs.items():
            if isinstance(cfg, dict) and str(cfg.get("provider") or "").strip() == prov_name:
                models_to_remove.append(name)

        if not models_to_remove:
            _settings_dialog_class()._show_feedback_message(
                self,
                success=True,
                title="模型分组",
                summary=f"分组【{prov_name}】下没有模型配置。",
            )
            return
        preview = "、".join(str(name) for name in models_to_remove[:8])
        if len(models_to_remove) > 8:
            preview = f"{preview} 等"
        if not _settings_dialog_class()._confirm_dangerous_action(
            self,
            title="清空分组模型",
            summary=f"确定要清空模型分组“{prov_name}”下的模型配置吗？",
            impact=[
                f"删除模型配置数量：{len(models_to_remove)}",
                f"涉及模型：{preview}",
                "正在使用被删除模型的翻译/问答角色会自动切换到剩余可用模型",
            ],
            level="高风险",
        ):
            return

        if hasattr(self, "_save_current_translator_model_config"):
            self._save_current_translator_model_config()

        for name in models_to_remove:
            configs.pop(name, None)
            if name in default_models:
                removed.add(name)

        self._translator["model_configs"] = configs
        self._translator["removed_models"] = sorted(removed)

        translate_text = self._combo_selected_value(self._translator_model) if hasattr(self, "_combo_selected_value") else ""
        qa_text = self._combo_selected_value(self._qa_model) if hasattr(self, "_combo_selected_value") else ""
        next_translate = translate_text
        next_qa = qa_text

        remaining_models = [k for k in configs.keys() if k not in models_to_remove]
        default_fallback = "gemini-3.5-flash-thinking"
        if remaining_models:
            fallback = remaining_models[0]
        else:
            fallback = default_fallback

        if translate_text in models_to_remove:
            next_translate = fallback
        if qa_text in models_to_remove:
            next_qa = fallback

        self._translator_loading = True
        try:
            if hasattr(self, "_set_combo_selected_value"):
                self._set_combo_selected_value(self._translator_model, next_translate)
                self._set_combo_selected_value(self._qa_model, next_qa)
            if hasattr(self, "_fill_model_combos"):
                self._fill_model_combos()
            if hasattr(self, "_set_combo_selected_value"):
                self._set_combo_selected_value(self._translator_model, next_translate)
                self._set_combo_selected_value(self._qa_model, next_qa)
        finally:
            self._translator_loading = False

        if hasattr(self, "_sync_role_models_from_combos"):
            self._sync_role_models_from_combos()

        current_model = str(getattr(self, "_translator_current_model", "") or "").strip()
        if current_model in models_to_remove:
            current_model = next_translate
            self._translator_current_model = current_model

        if hasattr(self, "_load_translator_model_config"):
            self._load_translator_model_config(str(current_model or self._translator.get("translate_model", next_translate)))

        if hasattr(self, "_fill_model_name_combobox_items"):
            self._fill_model_name_combobox_items()
        if hasattr(self, "_repaint_model_combos"):
            self._repaint_model_combos()
        if hasattr(self, "_persist_translator_settings"):
            self._persist_translator_settings()

    def _on_add_provider_clicked(self) -> None:
        # 单元测试兼容性退化处理
        if not isinstance(self, QWidget):
            from PyQt6.QtWidgets import QInputDialog
            prov_name, ok = QInputDialog.getText(
                self,
                "添加分组",
                "请输入新分组名称：",
                QLineEdit.EchoMode.Normal
            )
            prov_name = prov_name.strip()
            remark = ""
            if not ok or not prov_name:
                return
        else:
            from deepcat.ui.settings_dialog.sub_dialogs import _AddProviderDialog
            dlg = _AddProviderDialog(self)
            if dlg.exec():
                prov_name = str(dlg.name_edit.text() or "").strip()
                remark = str(dlg.remark_edit.text() or "").strip()
                if not prov_name:
                    return
            else:
                return

        # 保存备注
        remarks = dict(self._translator.get("provider_remarks") or {})
        remarks[prov_name] = remark
        self._translator["provider_remarks"] = remarks
        removed_providers = {
            str(x or "").strip()
            for x in self._translator.get("removed_providers", [])
            if str(x or "").strip()
        }
        if prov_name in removed_providers:
            removed_providers.remove(prov_name)
        self._translator["removed_providers"] = sorted(removed_providers)

        self._save_current_translator_model_config()

        # 刷新并选中
        _settings_dialog_class()._refresh_translator_providers(self)
        idx = self._translator_provider.findText(prov_name)
        self._translator_provider.blockSignals(True)
        if idx >= 0:
            self._translator_provider.setCurrentIndex(idx)
        self._translator_provider.blockSignals(False)
        self._clear_translator_fields_for_new_provider(prov_name)
        if hasattr(self, "_persist_translator_settings"):
            self._persist_translator_settings()

    def _on_get_models_clicked(self) -> None:
        if bool(getattr(self, "_fetching_models", False)):
            return
        base_url = self._translator_api_url.text().strip()
        api_key = self._translator_api_key.text().strip()
        if not base_url:
            try:
                _settings_dialog_class()._set_translator_required_field_highlight(self, "base_url", True)
            except Exception:
                pass
            _settings_dialog_class()._show_feedback_message(
                self,
                success=False,
                title="获取模型 ID",
                summary="请先填入有效的 API 地址",
            )
            return

        self._fetching_models = True
        self._get_models_btn.setToolTip("正在从当前 API 地址获取模型 ID 列表...")
        self._get_models_btn.setEnabled(False)
        if hasattr(self, "_new_api_model_btn") and self._new_api_model_btn is not None:
            self._new_api_model_btn.setEnabled(False)

        use_proxy = self._translator_use_proxy.isChecked()
        proxy_url = self._translator_proxy_url.text().strip()

        self._fetch_models_worker = FetchModelsWorker(base_url, api_key, use_proxy, proxy_url)
        self._fetch_models_worker.finished.connect(self._on_fetch_models_finished)
        self._fetch_models_worker.start()

    def _on_new_api_model_clicked(self) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        self._save_current_translator_model_config()
        self._translator_loading = True
        try:
            self._translator_api_url.clear()
            self._translator_model_name.clear()
            if hasattr(self._translator_model_name, "setText"):
                self._translator_model_name.setText("")
            self._translator_model_note.clear()
            self._translator_api_key.clear()
            if hasattr(self, "_translator_use_proxy") and self._translator_use_proxy is not None:
                self._translator_use_proxy.setChecked(False)
            self._translator_current_model = ""
            self._model_id_selection_creates_config = False
            self._fetched_models_list = []
            self._fetched_models_base_url = ""
            if hasattr(self, "_model_list_plus_btn") and self._model_list_plus_btn is not None:
                self._model_list_plus_btn.setEnabled(False)
            self._sync_translator_config_controls("glm")
            self._fill_model_name_combobox_items()
        finally:
            self._translator_loading = False
        self._update_api_url_role_label()
        self._position_translator_get_models_btn()
        _settings_dialog_class()._mark_translator_settings_dirty(self)
        self._translator_model_name.setFocus()

    def _on_fetch_models_finished(self, success: bool, models: list, err_msg: str) -> None:
        self._fetching_models = False
        self._get_models_btn.setToolTip("从当前 API 地址拉取模型 ID 列表")
        self._get_models_btn.setEnabled(True)
        if hasattr(self, "_new_api_model_btn") and self._new_api_model_btn is not None:
            self._new_api_model_btn.setEnabled(True)

        if success and models:
            self._fetched_models_list = models
            self._fetched_models_base_url = self._translator_api_url.text().strip()
            self._model_list_plus_btn.setEnabled(True)
            self._fill_model_name_combobox_items()
            show_popup = getattr(self, "_show_fetched_model_id_popup", None)
            if callable(show_popup):
                show_popup(models)
            else:
                _settings_dialog_class()._show_feedback_message(
                    self,
                    success=True,
                    title="获取模型 ID",
                    summary=f"成功拉取到 {len(models)} 个可用模型 ID",
                )
        else:
            self._fetched_models_list = []
            self._fetched_models_base_url = ""
            self._model_list_plus_btn.setEnabled(False)
            _settings_dialog_class()._show_feedback_message(
                self,
                success=False,
                title="获取模型 ID",
                summary="获取模型 ID 列表失败",
                detail=err_msg or "未知错误",
            )

    def _is_current_fetched_model_id(self, model_id: str) -> bool:
        model_id = str(model_id or "").strip()
        if not model_id:
            return False
        current_api_url = self._normalized_api_url(self._translator_api_url.text())
        fetched_api_url = self._normalized_api_url(getattr(self, "_fetched_models_base_url", ""))
        if not current_api_url or current_api_url != fetched_api_url:
            return False
        fetched_ids = {str(m or "").strip() for m in getattr(self, "_fetched_models_list", [])}
        return model_id in fetched_ids

    def _sync_model_note_from_fetched_model_id(self, model_id: str) -> bool:
        model_id = str(model_id or "").strip()
        if not model_id or not self._is_current_fetched_model_id(model_id):
            return False
        if not hasattr(self, "_translator_model_note") or self._translator_model_note is None:
            return False
        self._model_id_selection_creates_config = True
        note_text = self._translator_model_note.text().strip()
        if not note_text:
            self._translator_model_note.setText(model_id)
            return True
        translator = getattr(self, "_translator", {}) or {}
        configs = dict(translator.get("model_configs") or {}) if isinstance(translator, dict) else {}
        saved_cfg = configs.get(note_text)
        if not isinstance(saved_cfg, dict):
            return False
        saved_identity = _settings_dialog_class()._model_config_identity(self, note_text, saved_cfg)
        current_identity = _settings_dialog_class()._model_config_identity(
            self,
            model_id=model_id,
            base_url=(
                self._translator_api_url.text()
                if hasattr(self, "_translator_api_url") and self._translator_api_url is not None
                else ""
            ),
            api_key=(
                self._translator_api_key.text()
                if hasattr(self, "_translator_api_key") and self._translator_api_key is not None
                else ""
            ),
        )
        if saved_identity.get("base_url") != current_identity.get("base_url"):
            return False
        if saved_identity.get("api_key") != current_identity.get("api_key"):
            return False
        if saved_identity.get("model_id") == current_identity.get("model_id"):
            return False
        self._translator_model_note.setText(model_id)
        return True

    def _current_translator_provider_text(self) -> str:
        if hasattr(self, "_translator_provider") and self._translator_provider is not None:
            return str(self._translator_provider.currentText() or "").strip()
        return ""

    def _model_config_identity(
        self,
        note_name: str = "",
        cfg: Optional[dict[str, object]] = None,
        *,
        base_url: object | None = None,
        model_id: object | None = None,
        api_key: object | None = None,
        provider: object | None = None,
    ) -> dict[str, str]:
        return model_config_identity(
            note_name,
            cfg,
            base_url=base_url,
            model_id=model_id,
            api_key=api_key,
            provider=provider,
        )

    @staticmethod
    def _same_model_config_scope(left: dict[str, str], right: dict[str, str]) -> bool:
        return same_model_config_scope(left, right)

    def _matching_model_config_notes(
        self,
        configs: dict[str, object],
        target_identity: dict[str, str],
        *,
        api_key_mode: str,
    ) -> list[str]:
        matches: list[str] = []
        for note, cfg in dict(configs or {}).items():
            note_name = str(note or "").strip()
            if not note_name:
                continue
            saved_identity = _settings_dialog_class()._model_config_identity(self, note_name, cfg)
            if not _settings_dialog_class()._same_model_config_scope(saved_identity, target_identity):
                continue
            if api_key_mode == "exact":
                if saved_identity.get("api_key") != target_identity.get("api_key"):
                    continue
            elif api_key_mode == "empty":
                if saved_identity.get("api_key_state") != "empty":
                    continue
            else:
                continue
            matches.append(note_name)
        return matches

    def _find_empty_key_saved_model_note(
        self,
        model_id: str,
        *,
        base_url: object | None = None,
        configs: Optional[dict[str, object]] = None,
    ) -> str:
        model_id = str(model_id or "").strip()
        if not model_id:
            return ""
        if configs is None:
            translator = getattr(self, "_translator", {}) or {}
            configs = dict(translator.get("model_configs") or {}) if isinstance(translator, dict) else {}
        target_identity = _settings_dialog_class()._model_config_identity(
            self,
            model_id=model_id,
            base_url=(
                self._translator_api_url.text()
                if base_url is None and hasattr(self, "_translator_api_url") and self._translator_api_url is not None
                else base_url
            ),
            api_key="",
            provider=_settings_dialog_class()._current_translator_provider_text(self),
        )
        current_note = str(getattr(self, "_translator_current_model", "") or "").strip()
        matches = _settings_dialog_class()._matching_model_config_notes(
            self,
            configs,
            target_identity,
            api_key_mode="empty",
        )
        if current_note and current_note in matches:
            return current_note
        return matches[0] if matches else ""

    def _find_matching_saved_model_note(
        self,
        model_id: str,
        *,
        base_url: object | None = None,
        api_key: object | None = None,
        configs: Optional[dict[str, object]] = None,
    ) -> str:
        model_id = str(model_id or "").strip()
        if not model_id:
            return ""
        if configs is None:
            translator = getattr(self, "_translator", {}) or {}
            configs = dict(translator.get("model_configs") or {}) if isinstance(translator, dict) else {}
        api_key_text = str(
            (
                self._translator_api_key.text()
                if api_key is None and hasattr(self, "_translator_api_key") and self._translator_api_key is not None
                else api_key
            )
            or ""
        ).strip()
        if not api_key_text:
            return ""
        target_identity = _settings_dialog_class()._model_config_identity(
            self,
            model_id=model_id,
            base_url=(
                self._translator_api_url.text()
                if base_url is None and hasattr(self, "_translator_api_url") and self._translator_api_url is not None
                else base_url
            ),
            api_key=api_key_text,
            provider=_settings_dialog_class()._current_translator_provider_text(self),
        )
        current_note = str(getattr(self, "_translator_current_model", "") or "").strip()
        matches = _settings_dialog_class()._matching_model_config_notes(
            self,
            configs,
            target_identity,
            api_key_mode="exact",
        )
        if current_note and current_note in matches:
            return current_note
        return matches[0] if matches else ""

    def _handle_selected_model_id(self, model_id: str) -> None:
        model_id = str(model_id or "").strip()
        if not model_id:
            return
        matching_note = _settings_dialog_class()._find_matching_saved_model_note(self, model_id)
        current_note = str(getattr(self, "_translator_current_model", "") or "").strip()
        if matching_note and matching_note != current_note:
            save_current = getattr(self, "_save_current_translator_model_config", None)
            if callable(save_current):
                save_current()
            self._model_id_selection_creates_config = False
            role_getter = getattr(self, "_current_translator_role", None)
            role = role_getter() if callable(role_getter) else "translate"
            try:
                self._on_translator_model_changed(matching_note, role, save_current=False)
            except TypeError:
                self._on_translator_model_changed(matching_note, role)
            return
        self._translator_model_name.setText(model_id)
        if matching_note:
            if hasattr(self, "_translator_model_note") and self._translator_model_note is not None:
                self._translator_model_note.setText(matching_note)
            self._model_id_selection_creates_config = False
        else:
            _settings_dialog_class()._sync_model_note_from_fetched_model_id(self, model_id)
        self._apply_translator_settings()

    def _on_model_id_activated(self, *_args) -> None:
        _settings_dialog_class()._handle_selected_model_id(self, self._translator_model_name.text())

    def _select_model_id_from_list(self, model_id: str) -> None:
        _settings_dialog_class()._handle_selected_model_id(self, model_id)

    def _show_fetched_model_id_popup(self, models: list[str]) -> None:
        clean_models: list[str] = []
        for value in models:
            model_id = str(value or "").strip()
            if model_id and model_id not in clean_models:
                clean_models.append(model_id)
        if not clean_models:
            return

        current_model_id = self._translator_model_name.text().strip()
        popup = _FetchedModelIdPopup(
            clean_models,
            current_model_id,
            self._select_model_id_from_list,
            parent=self._translator_model_name,
        )
        self._model_id_popup = popup
        popup.show_at_pos(self._translator_model_name.mapToGlobal(QPoint(0, self._translator_model_name.height())))

    def _on_model_list_plus_clicked(self) -> None:
        current_api_url = self._normalized_api_url(self._translator_api_url.text())
        online_models = []
        if current_api_url and self._normalized_api_url(getattr(self, "_fetched_models_base_url", "")) == current_api_url:
            online_models = list(getattr(self, "_fetched_models_list", []))
        if online_models:
            self._show_fetched_model_id_popup(online_models)

    def _fill_model_combos(self, current_translate: Optional[str] = None, current_qa: Optional[str] = None) -> None:
        if current_translate is None:
            current_translate = self._combo_selected_value(self._translator_model)
        if current_qa is None:
            current_qa = self._combo_selected_value(self._qa_model)

        self._translator_model.blockSignals(True)
        self._qa_model.blockSignals(True)

        try:
            configs = dict(self._translator.get("model_configs") or {})

            from collections import defaultdict
            grouped = defaultdict(list)
            for name, cfg in configs.items():
                provider = normalize_translator_provider(cfg.get("provider"), infer_translator_model_provider(name, cfg))
                grouped[provider].append(name)

            sorted_providers = sorted(grouped.keys())
            if "其它" in sorted_providers:
                sorted_providers.remove("其它")
                sorted_providers.append("其它")

            translate_model = QStandardItemModel(self._translator_model)
            qa_model = QStandardItemModel(self._qa_model)

            for provider in sorted_providers:
                names = sorted(grouped[provider])
                qa_names = [n for n in names if n not in QA_EXCLUDED_TRANSLATOR_MODEL_NAMES]

                if names:
                    header = QStandardItem(f"[{provider}]")
                    header.setEnabled(False)
                    font = header.font()
                    font.setBold(True)
                    header.setFont(font)
                    translate_model.appendRow(header)
                    for name in names:
                        item = QStandardItem(name)
                        item.setData(name, Qt.ItemDataRole.UserRole)
                        item.setData(_translator_model_search_text(name, configs.get(name, {}), provider), TRANSLATOR_MODEL_SEARCH_ROLE)
                        translate_model.appendRow(item)

                if qa_names:
                    header = QStandardItem(f"[{provider}]")
                    header.setEnabled(False)
                    font = header.font()
                    font.setBold(True)
                    header.setFont(font)
                    qa_model.appendRow(header)
                    for name in qa_names:
                        item = QStandardItem(name)
                        item.setData(name, Qt.ItemDataRole.UserRole)
                        item.setData(_translator_model_search_text(name, configs.get(name, {}), provider), TRANSLATOR_MODEL_SEARCH_ROLE)
                        qa_model.appendRow(item)

            _settings_dialog_class()._apply_codex_highlight_to_item_model(self, translate_model, set())
            qa_highlight_colors = _settings_dialog_class()._provider_highlighted_model_colors(self, "qa")
            _settings_dialog_class()._apply_codex_highlight_to_item_model(
                self,
                qa_model,
                set(qa_highlight_colors.keys()),
                qa_highlight_colors,
            )

            self._translator_model.setModel(translate_model)
            self._qa_model.setModel(qa_model)

            self._set_combo_selected_value(self._translator_model, str(current_translate or ""))
            self._set_combo_selected_value(self._qa_model, str(current_qa or ""))

        finally:
            self._translator_model.blockSignals(False)
            self._qa_model.blockSignals(False)
        _settings_dialog_class()._sync_provider_popup_model_cache(self)

    def _codex_highlighted_model_names(self, purpose: str) -> set[str]:
        if str(purpose or "").strip().lower() != "qa":
            return set()
        translator = getattr(self, "_translator", {})
        if not isinstance(translator, dict) or not bool(translator.get("codex_current_model_config_enabled", False)):
            return set()
        bound_model = str(translator.get("codex_current_model_config_model", "") or "").strip()
        if not bound_model:
            bound_model = str(translator.get("qa_model", "") or "").strip()
        if not bound_model:
            qa_combo = getattr(self, "_qa_model", None)
            if qa_combo is not None:
                try:
                    bound_model = _settings_dialog_class()._combo_selected_value(self, qa_combo)
                except Exception:
                    bound_model = ""
        return {bound_model} if bound_model else set()

    def _claude_code_config_model(self) -> str:
        translator = getattr(self, "_translator", {})
        if not isinstance(translator, dict):
            return ""
        return str(translator.get("claude_code_current_model_config_model", "") or "").strip()

    def _is_current_model_claude_code_configured(self, model_name: str) -> bool:
        model_name = str(model_name or "").strip()
        return bool(model_name) and _settings_dialog_class()._claude_code_config_model(self) == model_name

    def _claude_code_highlighted_model_names(self, purpose: str) -> set[str]:
        if str(purpose or "").strip().lower() != "qa":
            return set()
        bound_model = _settings_dialog_class()._claude_code_config_model(self)
        return {bound_model} if bound_model else set()

    def _provider_highlighted_model_colors(self, purpose: str) -> dict[str, str]:
        if str(purpose or "").strip().lower() != "qa":
            return {}
        colors: dict[str, str] = {}
        for name in _settings_dialog_class()._codex_highlighted_model_names(self, purpose):
            colors[str(name)] = CODEX_CURRENT_MODEL_CHECK_COLOR
        for name in _settings_dialog_class()._claude_code_highlighted_model_names(self, purpose):
            colors[str(name)] = CLAUDE_CODE_CURRENT_MODEL_CHECK_COLOR
        return colors

    def _apply_codex_highlight_to_item_model(
        self,
        item_model: QStandardItemModel | None,
        highlighted_names: set[str],
        highlighted_colors: Optional[dict[str, str]] = None,
    ) -> None:
        if item_model is None:
            return
        normalized_names = {str(name or "").strip() for name in highlighted_names if str(name or "").strip()}
        color_by_name = {
            str(name or "").strip(): str(color or "").strip()
            for name, color in dict(highlighted_colors or {}).items()
            if str(name or "").strip() and str(color or "").strip()
        }
        for row in range(item_model.rowCount()):
            item = item_model.item(row)
            if item is None or not item.isEnabled():
                continue
            item_name = str(item.data(Qt.ItemDataRole.UserRole) or item.text() or "").strip()
            is_highlighted = item_name in normalized_names
            item.setData(is_highlighted, TRANSLATOR_MODEL_CODEX_BOUND_ROLE)
            item_color = color_by_name.get(item_name, CODEX_CURRENT_MODEL_CHECK_COLOR if is_highlighted else "")
            item.setData(item_color if is_highlighted else "", TRANSLATOR_MODEL_HIGHLIGHT_COLOR_ROLE)
            item.setData(
                QBrush(QColor(item_color)) if is_highlighted and item_color else None,
                Qt.ItemDataRole.ForegroundRole,
            )

    def _refresh_codex_model_combo_styles(self) -> None:
        combo_targets = (
            ("translate", getattr(self, "_translator_model", None)),
            ("qa", getattr(self, "_qa_model", None)),
        )
        for purpose, combo in combo_targets:
            if combo is None:
                continue
            model = combo.model()
            if model is None:
                continue
            highlighted_colors = _settings_dialog_class()._provider_highlighted_model_colors(self, purpose)
            _settings_dialog_class()._apply_codex_highlight_to_item_model(
                self,
                model,
                set(highlighted_colors.keys()),
                highlighted_colors,
            )
            combo.update()

    def _set_combo_selected_value(self, combo: QComboBox, raw_value: str) -> None:
        raw_value = str(raw_value or "").strip()
        if not raw_value:
            return
        model = combo.model()
        if model is None:
            return
        for i in range(model.rowCount()):
            item = model.item(i)
            if item is not None:
                stored_val = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
                if stored_val == raw_value:
                    combo.setCurrentIndex(i)
                    return
        item = QStandardItem(raw_value)
        item.setData(raw_value, Qt.ItemDataRole.UserRole)
        model.appendRow(item)
        combo.setCurrentIndex(model.rowCount() - 1)

    def _combo_selected_value(self, combo: QComboBox, fallback: str = "") -> str:
        idx = combo.currentIndex()
        if idx >= 0:
            stored = combo.itemData(idx, Qt.ItemDataRole.UserRole)
            if stored:
                return str(stored).strip()
        text = combo.currentText().strip()
        if text.startswith("[") and text.endswith("]"):
            return str(fallback or "").strip()
        return text or str(fallback or "").strip()

    def _combo_item_value(self, combo: QComboBox, index: int) -> str:
        if int(index) < 0:
            return ""
        stored = combo.itemData(int(index), Qt.ItemDataRole.UserRole)
        if stored:
            return str(stored).strip()
        text = combo.itemText(int(index)).strip()
        if text.startswith("[") and text.endswith("]"):
            return ""
        return text

    def _normalized_api_url(self, value: object) -> str:
        return normalize_api_url(value)

    def _position_translator_get_models_btn(self) -> None:
        if not hasattr(self, "_new_api_model_btn") or self._new_api_model_btn is None:
            return
        w = self._translator_api_url.width()
        h = self._translator_api_url.height()
        btn_w = 22
        btn_h = self._INLINE_ACTION_BUTTON_HEIGHT
        right_margin = 5
        plus_x = w - btn_w - right_margin
        y = int((h - btn_h) / 2)
        self._new_api_model_btn.setGeometry(plus_x, y, btn_w, btn_h)
        self._new_api_model_btn.raise_()

    def _compact_window_height(self) -> None:
        try:
            hint = self.sizeHint()
            target_h = max(420, int(hint.height()) + 8)
            current_w = max(int(self.width()), int(self.minimumWidth()), int(hint.width()))
            if int(self.height()) > int(target_h):
                self.resize(int(current_w), int(target_h))
            elif int(self.height()) <= 0:
                self.resize(int(current_w), int(target_h))
        except Exception:
            pass

    def _ensure_window_on_screen(self, *, restored: bool = False) -> None:
        try:
            frame = QRect(self.frameGeometry())
            if frame.width() <= 20 or frame.height() <= 20:
                hint = self.sizeHint()
                frame = QRect(0, 0, max(int(self.minimumWidth()), int(hint.width())), max(420, int(hint.height()) + 8))
            screen = QGuiApplication.screenAt(frame.center()) or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen is not None else QRect(0, 0, 800, 600)
            visible = frame.intersected(geo)
            frame_area = max(1, int(frame.width()) * int(frame.height()))
            visible_area = max(0, int(visible.width()) * int(visible.height()))
            at_screen_corner = abs(int(frame.left()) - int(geo.left())) <= 2 and abs(int(frame.top()) - int(geo.top())) <= 2
            if (not bool(restored)) or visible_area < int(frame_area * 0.60) or bool(at_screen_corner):
                x = int(geo.x() + (geo.width() - frame.width()) / 2)
                y = int(geo.y() + (geo.height() - frame.height()) / 2)
            else:
                x = int(frame.x())
                y = int(frame.y())
            x = max(int(geo.x()), min(int(x), int(geo.right() - frame.width() + 1)))
            y = max(int(geo.y()), min(int(y), int(geo.bottom() - frame.height() + 1)))
            self.move(int(x), int(y))
        except Exception:
            pass

    def _position_translator_api_key_eye(self) -> None:
        if not hasattr(self, "_translator_api_key_eye"):
            return
        h = max(1, self._translator_api_key.height())
        size = max(24, min(34, h - 6))
        self._translator_api_key_eye.setGeometry(self._translator_api_key.width() - size - 5, int((h - size) / 2), size, size)

    def _position_translator_proxy_test_btn(self) -> None:
        if not hasattr(self, "_translator_proxy_url_test_btn"):
            return
        try:
            h = max(1, self._translator_proxy_url.height())
            size = max(18, min(22, h - 8))
            self._translator_proxy_url_test_btn.setGeometry(
                self._translator_proxy_url.width() - size - 6,
                int((h - size) / 2),
                size,
                size
            )
        except Exception:
            pass

    def _position_translator_api_url_role_label(self) -> None:
        if not hasattr(self, "_translator_api_url_role_label"):
            return
        self._translator_api_url_role_label.setText("")
        self._translator_api_url_role_label.setVisible(False)

    def _update_api_url_role_label(self) -> None:
        if not hasattr(self, "_translator_api_url_role_label"):
            return
        self._translator_api_url_role_label.setText("")
        self._translator_api_url_role_label.setVisible(False)

    def _toggle_translator_api_key_visibility(self, checked: bool) -> None:
        self._translator_api_key.setEchoMode(QLineEdit.EchoMode.Normal if bool(checked) else QLineEdit.EchoMode.Password)
        self._translator_api_key_eye.setToolTip("隐藏 API 密钥" if bool(checked) else "显示 API 密钥")
        self._translator_api_key_eye.update()

    def _position_translator_provider_btn(self) -> None:
        if not hasattr(self, "_add_provider_btn") or self._add_provider_btn is None:
            return
        w = self._translator_provider.width()
        h = self._translator_provider.height()
        btn_w = 20
        btn_h = 20
        plus_x = w - 28 - btn_w + 22
        y = int((h - btn_h) / 2)
        self._add_provider_btn.setGeometry(plus_x, y, btn_w, btn_h)
        if hasattr(self, "_edit_provider_btn") and self._edit_provider_btn is not None:
            self._edit_provider_btn.setGeometry(plus_x - btn_w - 2, y + 2, btn_w, btn_h)
            self._edit_provider_btn.raise_()
        self._add_provider_btn.raise_()
        self._position_translator_provider_hover_hint()

    def _position_translator_provider_hover_hint(self) -> None:
        if not hasattr(self, "_translator_provider_hover_hint") or self._translator_provider_hover_hint is None:
            return
        if not hasattr(self, "_translator_provider") or self._translator_provider is None:
            return
        w = self._translator_provider.width()
        h = self._translator_provider.height()
        btn_w = 20
        plus_x = w - 28 - btn_w + 22
        hint_w = min(128, max(88, self._translator_provider_hover_hint.fontMetrics().horizontalAdvance(self._translator_provider_hover_hint.text()) + 8))
        hint_h = min(22, max(16, h - 6))
        x = max(4, plus_x - btn_w - 2 - hint_w - 6)
        y = int((h - hint_h) / 2)
        self._translator_provider_hover_hint.setGeometry(x, y, hint_w, hint_h)
        self._translator_provider_hover_hint.raise_()

    def _position_translator_model_name_btns(self) -> None:
        if not hasattr(self, "_translator_model_name") or self._translator_model_name is None:
            return
        if not hasattr(self, "_get_models_btn") or self._get_models_btn is None:
            return
        w = self._translator_model_name.width()
        h = self._translator_model_name.height()
        btn_w = _settings_dialog_class()._MODEL_ID_INLINE_BUTTON_SIZE
        btn_h = _settings_dialog_class()._MODEL_ID_INLINE_BUTTON_SIZE
        right_margin = _settings_dialog_class()._MODEL_ID_INLINE_BUTTON_RIGHT_MARGIN
        get_x = max(0, w - btn_w - right_margin)
        y = max(0, int((h - btn_h) / 2))
        self._get_models_btn.setGeometry(get_x, y, btn_w, btn_h)
        self._get_models_btn.raise_()

    def _sync_model_role_combo_widths(self) -> None:
        for combo in (getattr(self, "_translator_model", None), getattr(self, "_qa_model", None)):
            if combo is None:
                continue
            if combo.minimumWidth() != self._MODEL_ROLE_COMBO_WIDTH or combo.maximumWidth() != self._MODEL_ROLE_COMBO_WIDTH:
                combo.setFixedWidth(self._MODEL_ROLE_COMBO_WIDTH)

    def _sync_model_id_combo_empty_state(self) -> None:
        edit = getattr(self, "_translator_model_name", None)
        if edit is None:
            return
        try:
            current_api_url = self._normalized_api_url(self._translator_api_url.text())
            has_items = bool(
                current_api_url
                and self._normalized_api_url(getattr(self, "_fetched_models_base_url", "")) == current_api_url
                and getattr(self, "_fetched_models_list", [])
            )
            edit.setPlaceholderText(_settings_dialog_class()._preferred_model_id_placeholder(self, has_candidates=has_items))
            edit.update()
        except Exception:
            pass

    def _fill_model_name_combobox_items(self) -> None:
        if not hasattr(self, "_translator_model_name") or self._translator_model_name is None:
            return
        try:
            current_api_url = self._normalized_api_url(self._translator_api_url.text())
            candidates = []
            if current_api_url and self._normalized_api_url(getattr(self, "_fetched_models_base_url", "")) == current_api_url:
                for value in getattr(self, "_fetched_models_list", []):
                    model_id = str(value or "").strip()
                    if model_id and model_id not in candidates:
                        candidates.append(model_id)
            if hasattr(self._translator_model_name, "addItems"):
                widget = self._translator_model_name
                current_text = (
                    str(widget.currentText() or "").strip()
                    if hasattr(widget, "currentText")
                    else str(widget.text() or "").strip()
                )
                widget.blockSignals(True)
                try:
                    widget.clear()
                    widget.addItems(candidates)
                    if current_text:
                        if hasattr(widget, "setDisplayText"):
                            widget.setDisplayText(current_text)
                        elif hasattr(widget, "setCurrentText"):
                            widget.setCurrentText(current_text)
                        elif hasattr(widget, "setText"):
                            widget.setText(current_text)
                    elif hasattr(widget, "setCurrentIndex"):
                        widget.setCurrentIndex(-1)
                finally:
                    widget.blockSignals(False)
            _settings_dialog_class()._sync_model_id_combo_empty_state(self)
        except Exception:
            pass

    def _model_combos(self) -> tuple[TranslatorModelComboBox, TranslatorModelComboBox]:
        return (self._translator_model, self._qa_model)

    @staticmethod
    def _initial_translator_detail_model(translator: dict) -> str:
        qa_model = str((translator or {}).get("qa_model", "gemini-3.5-flash-thinking") or "gemini-3.5-flash-thinking")
        if qa_model == "自定义模型":
            return "gemini-3.5-flash-thinking"
        return qa_model

    def _current_translator_role(self) -> str:
        role = str(getattr(self, "_translator_current_role", "") or "").strip().lower()
        if role in {"translate", "qa"}:
            return role
        current_model = str(getattr(self, "_translator_current_model", "") or "").strip()
        translate_model = self._combo_selected_value(self._translator_model)
        qa_model = self._combo_selected_value(self._qa_model)
        if current_model == qa_model and current_model != translate_model:
            return "qa"
        return "translate"

    def _adjacent_combo_value(self, combo: QComboBox, target_value: str) -> str:
        target_value = str(target_value or "").strip()
        if not target_value:
            return ""
        values = [
            self._combo_item_value(combo, i)
            for i in range(combo.count())
            if self._combo_item_value(combo, i)
        ]
        try:
            pos = values.index(target_value)
        except ValueError:
            return ""
        if pos + 1 < len(values):
            return values[pos + 1]
        if pos > 0:
            return values[pos - 1]
        return ""

    def _add_model_to_combos(self, model_name: str) -> None:
        self._fill_model_combos()

    @staticmethod
    def _unique_model_note_name(base_name: str, existing_names, *, current_name: str = "") -> str:
        return unique_model_note_name(base_name, existing_names, current_name=current_name)

    def _refresh_model_combos_after_catalog_change(self, select_model: str = "", role: str = "") -> None:
        select_model = str(select_model or "").strip()
        role = "qa" if str(role) == "qa" else "translate"
        current_translate = self._combo_selected_value(self._translator_model)
        current_qa = self._combo_selected_value(self._qa_model)
        self._fill_model_combos()
        if current_translate:
            self._set_combo_selected_value(self._translator_model, current_translate)
        if current_qa:
            self._set_combo_selected_value(self._qa_model, current_qa)
        if select_model:
            target_combo = self._qa_model if role == "qa" else self._translator_model
            self._set_combo_selected_value(target_combo, select_model)
        self._repaint_model_combos()

    def _sync_role_models_from_combos(self) -> None:
        translate_model = self._combo_selected_value(self._translator_model, "gemini-3.5-flash-thinking")
        qa_model = self._combo_selected_value(self._qa_model, translate_model)
        self._translator["translate_model"] = translate_model
        self._translator["qa_model"] = qa_model
        self._translator["current_model"] = translate_model

    def _repaint_model_combos(self) -> None:
        for combo in self._model_combos():
            try:
                combo.update()
                if combo.view() is not None and combo.view().viewport() is not None:
                    combo.view().viewport().update()
            except Exception:
                pass

    def _broadcast_model_rename(self, old_note: str, new_note: str, cfg: dict) -> None:
        try:
            from PyQt6.QtWidgets import QApplication
            for widget in QApplication.topLevelWidgets():
                if widget is not self and widget.__class__.__name__ in ("MainWindow", "SettingsDialog"):
                    widget_loading_flag = False
                    if hasattr(widget, "_translator_loading"):
                        widget_loading_flag = bool(getattr(widget, "_translator_loading", False))
                        setattr(widget, "_translator_loading", True)
                    try:
                        if hasattr(widget, "_translator") and isinstance(widget._translator, dict):
                            w_configs = dict(widget._translator.get("model_configs") or {})
                            w_configs.pop(old_note, None)
                            w_configs[new_note] = cfg
                            widget._translator["model_configs"] = w_configs

                            # 同步更新下拉框文本
                            if hasattr(widget, "_fill_model_combos"):
                                widget._fill_model_combos()

                            if widget._translator.get("translate_model") == old_note:
                                widget._translator["translate_model"] = new_note
                            if widget._translator.get("qa_model") == old_note:
                                widget._translator["qa_model"] = new_note
                            if widget._translator.get("current_model") == old_note:
                                widget._translator["current_model"] = new_note
                            next_translate = str(widget._translator.get("translate_model", "") or "")
                            next_qa = str(widget._translator.get("qa_model", "") or next_translate)
                            if hasattr(widget, "_fill_model_combos"):
                                try:
                                    widget._fill_model_combos(next_translate, next_qa)
                                except TypeError:
                                    widget._fill_model_combos()
                            if hasattr(widget, "_set_combo_selected_value"):
                                if hasattr(widget, "_translator_model") and widget._translator_model is not None:
                                    widget._set_combo_selected_value(widget._translator_model, next_translate)
                                if hasattr(widget, "_qa_model") and widget._qa_model is not None:
                                    widget._set_combo_selected_value(widget._qa_model, next_qa)

                            if hasattr(widget, "_translator_current_model") and widget._translator_current_model == old_note:
                                widget._translator_current_model = new_note
                    finally:
                        if hasattr(widget, "_translator_loading"):
                            setattr(widget, "_translator_loading", widget_loading_flag)
        except Exception:
            pass

    def _should_clone_model_config_for_api_key_change(
        self,
        note_name: str,
        cfg: dict[str, object],
        configs: dict[str, object],
    ) -> bool:
        note_name = str(note_name or "").strip()
        if not note_name or note_name not in configs:
            return False
        old_cfg = configs.get(note_name)
        if not isinstance(old_cfg, dict):
            return False
        old_identity = _settings_dialog_class()._model_config_identity(self, note_name, old_cfg)
        new_identity = _settings_dialog_class()._model_config_identity(self, note_name, cfg)
        if not _settings_dialog_class()._same_model_config_scope(old_identity, new_identity):
            return False
        old_api_key = old_identity.get("api_key", "")
        new_api_key = new_identity.get("api_key", "")
        return bool(new_api_key and old_api_key and old_api_key != new_api_key)

    def _switch_to_saved_translator_model_note(self, model_note: str) -> None:
        model_note = str(model_note or "").strip()
        if not model_note:
            return
        role = self._current_translator_role()
        self._translator_current_model = model_note
        if role == "qa":
            self._translator["qa_model"] = model_note
        else:
            self._translator["translate_model"] = model_note
            self._translator["current_model"] = model_note
        if hasattr(self, "_refresh_model_combos_after_catalog_change"):
            self._refresh_model_combos_after_catalog_change(model_note, role)
        loader = getattr(self, "_load_translator_model_config", None)
        if callable(loader):
            loader(model_note)
        elif hasattr(self, "_translator_model_note") and hasattr(self._translator_model_note, "setText"):
            self._translator_model_note.setText(model_note)

    def _save_current_translator_model_config(self) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        old_note = str(getattr(self, "_translator_current_model", "")).strip()
        if not old_note:
            return
        new_note = self._translator_model_note.text().strip()
        if not new_note:
            return

        configs = dict(self._translator.get("model_configs") or {})
        cfg = self._translator_field_config(old_note)
        if old_note in configs and isinstance(configs[old_note], dict):
            orig_provider = configs[old_note].get("provider")
            if orig_provider:
                cfg["provider"] = orig_provider

        curr_translate = self._combo_selected_value(self._translator_model) or self._translator.get("translate_model", "")
        curr_qa = self._combo_selected_value(self._qa_model) or self._translator.get("qa_model", "")
        create_from_model_id_selection = bool(getattr(self, "_model_id_selection_creates_config", False))
        if (
            new_note == old_note
            and _settings_dialog_class()._should_clone_model_config_for_api_key_change(self, old_note, cfg, configs)
        ):
            new_note = _settings_dialog_class()._unique_model_note_name(new_note, configs, current_name="")
            if hasattr(self._translator_model_note, "setText"):
                self._translator_model_note.setText(new_note)
            create_from_model_id_selection = True
        if new_note != old_note:
            current_name = "" if create_from_model_id_selection else old_note
            unique_note = _settings_dialog_class()._unique_model_note_name(new_note, configs, current_name=current_name)
            if unique_note != new_note:
                new_note = unique_note
                self._translator_model_note.setText(new_note)

        if new_note != old_note and create_from_model_id_selection:
            configs[new_note] = cfg
            self._translator["model_configs"] = configs
            removed = {str(x).strip() for x in self._translator.get("removed_models", []) if str(x).strip()}
            if new_note in removed:
                removed.remove(new_note)
            self._translator["removed_models"] = sorted(removed)
            self._translator_current_model = new_note

            role = self._current_translator_role()
            next_translate = curr_translate
            next_qa = curr_qa
            if role == "qa":
                next_qa = new_note
            else:
                next_translate = new_note
            if not next_translate:
                next_translate = new_note
            if not next_qa:
                next_qa = next_translate

            self._fill_model_combos(next_translate, next_qa)
            self._set_combo_selected_value(self._translator_model, next_translate)
            self._set_combo_selected_value(self._qa_model, next_qa)
        elif new_note != old_note:
            configs.pop(old_note, None)
            configs[new_note] = cfg
            self._translator["model_configs"] = configs
            if str(self._translator.get("codex_current_model_config_model", "") or "").strip() == old_note:
                self._translator["codex_current_model_config_model"] = new_note
            if str(self._translator.get("claude_code_current_model_config_model", "") or "").strip() == old_note:
                self._translator["claude_code_current_model_config_model"] = new_note
            removed = {str(x).strip() for x in self._translator.get("removed_models", []) if str(x).strip()}
            default_models = set((default_translator_settings().get("model_configs") or {}).keys())
            if old_note in default_models:
                removed.add(old_note)
            if new_note in removed:
                removed.remove(new_note)
            self._translator["removed_models"] = sorted(removed)

            if self._translator.get("translate_model") == old_note:
                self._translator["translate_model"] = new_note
            if self._translator.get("qa_model") == old_note:
                self._translator["qa_model"] = new_note
            if self._translator.get("current_model") == old_note:
                self._translator["current_model"] = new_note

            self._translator_current_model = new_note

            next_translate = new_note if curr_translate == old_note else curr_translate
            next_qa = new_note if curr_qa == old_note else curr_qa
            self._fill_model_combos(next_translate, next_qa)
            self._set_combo_selected_value(self._translator_model, next_translate)
            self._set_combo_selected_value(self._qa_model, next_qa)

            self._broadcast_model_rename(old_note, new_note, cfg)
        else:
            configs[old_note] = cfg
            self._translator["model_configs"] = configs
            self._fill_model_combos()
            self._set_combo_selected_value(self._translator_model, curr_translate)
            self._set_combo_selected_value(self._qa_model, curr_qa)
        self._model_id_selection_creates_config = False
        self._sync_role_models_from_combos()
        self._fill_model_name_combobox_items()

    def _is_tencent_model(self, model_name: str) -> bool:
        name_lower = str(model_name or "").lower()
        return any(x in name_lower for x in ("mt1.5", "mt15", "mt2", "hy-mt", "腾讯"))

    def _load_translator_model_config(self, model_name: str) -> None:
        self._translator_loading = True
        try:
            self._model_id_selection_creates_config = False
            configs = dict(self._translator.get("model_configs") or {})
            cfg = dict(configs.get(str(model_name), {}) or {})
            if not cfg:
                cfg = {"base_url": "", "model_name": str(model_name), "api_key": "", "use_proxy": False, "model_type": "glm"}
            self._translator_api_url.setText(str(cfg.get("base_url", "")))
            model_id_text = str(cfg.get("model_name", str(model_name)))
            self._translator_model_name.setText(model_id_text)
            self._translator_model_note.setText(str(model_name))
            self._translator_api_key.setText(str(cfg.get("api_key", "")))
            self._translator_use_proxy.setChecked(bool(cfg.get("use_proxy", False)))
            self._translator_proxy_url.setText(str(self._translator.get("proxy_url", "socks5://127.0.0.1:1080")))
            self._translator_current_model = str(model_name)
            self._sync_translator_config_controls(str(cfg.get("model_type", "")) or infer_translator_model_type(str(model_name), cfg))

            provider = str(cfg.get("provider") or infer_translator_model_provider(str(model_name), cfg)).strip()
            _settings_dialog_class()._refresh_translator_providers(self)
            self._translator_provider.blockSignals(True)
            try:
                idx = self._translator_provider.findText(provider)
                if idx >= 0:
                    self._translator_provider.setCurrentIndex(idx)
                else:
                    self._translator_provider.addItem(provider)
                    self._translator_provider.setCurrentText(provider)
            finally:
                self._translator_provider.blockSignals(False)

            self._fetched_models_list = []
            self._fetched_models_base_url = ""
            self._model_list_plus_btn.setEnabled(False)
            self._fill_model_name_combobox_items()
        finally:
            self._translator_loading = False

        _settings_dialog_class()._mark_translator_settings_saved(self, transient=False)
        is_tencent = self._is_tencent_model(str(model_name))
        if hasattr(self, "_download_mt15_btn"):
            self._download_mt15_btn.setVisible(is_tencent)
        if hasattr(self, "_download_mt2_btn"):
            self._download_mt2_btn.setVisible(is_tencent)
        if hasattr(self, "_download_progress"):
            is_running = getattr(self, "_active_download_worker", None) is not None and self._active_download_worker.isRunning()
            if is_running:
                self._download_progress.setVisible(is_tencent)
                if hasattr(self, "_download_cancel_btn"):
                    self._download_cancel_btn.setVisible(is_tencent)
            else:
                self._download_progress.setVisible(False)
                if hasattr(self, "_download_cancel_btn"):
                    self._download_cancel_btn.setVisible(False)
        self._update_api_url_role_label()
        if hasattr(self, "_translator_provider"):
            self._translator_provider.update()
        if hasattr(self, "_translator_api_url"):
            self._translator_api_url.update()
        if hasattr(self, "_fill_codex_config_switch") and hasattr(self, "_sync_current_model_codex_ui"):
            self._sync_current_model_codex_ui()

    def _on_download_mt15_clicked(self) -> None:
        url = "https://modelscope.cn/models/Tencent-Hunyuan/HY-MT1.5-1.8B-GGUF/resolve/master/HY-MT1.5-1.8B-Q4_K_M.gguf"
        from deepcat.local_hunyuan_server import repo_root
        root = repo_root()
        dest_dir = root / "models" / "hy-mt-1.8b"
        dest_file = dest_dir / "HY-MT1.5-1.8B-Q4_K_M.gguf"

        if getattr(self, "_active_download_worker", None) is not None and self._active_download_worker.isRunning():
            _settings_dialog_class()._show_feedback_message(
                self,
                success=False,
                title="模型下载",
                summary="当前已有模型文件正在下载中",
                detail="请等待当前下载完成后再开始新的下载。",
            )
            return

        if dest_file.exists():
            reply = QMessageBox.question(
                self,
                "文件已存在",
                f"模型文件 {dest_file.name} 已存在，是否重新下载？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return

        confirm_text = f"将下载MT1.5文件到软件目录models\\hy-mt-1.8b文件夹，是否继续？"
        reply = QMessageBox.question(
            self,
            "确认下载",
            confirm_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_silent_download(url, str(dest_file), "MT1.5")

    def _on_download_mt2_clicked(self) -> None:
        url = "https://modelscope.cn/models/Tencent-Hunyuan/Hy-MT2-1.8B-GGUF/resolve/master/Hy-MT2-1.8B-Q4_K_M.gguf"
        from deepcat.local_hunyuan_server import repo_root
        root = repo_root()
        dest_dir = root / "models" / "hy-mt2-1.8b"
        dest_file = dest_dir / "Hy-MT2-1.8B-Q4_K_M.gguf"

        if getattr(self, "_active_download_worker", None) is not None and self._active_download_worker.isRunning():
            _settings_dialog_class()._show_feedback_message(
                self,
                success=False,
                title="模型下载",
                summary="当前已有模型文件正在下载中",
                detail="请等待当前下载完成后再开始新的下载。",
            )
            return

        if dest_file.exists():
            reply = QMessageBox.question(
                self,
                "文件已存在",
                f"模型文件 {dest_file.name} 已存在，是否重新下载？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return

        confirm_text = f"将下载MT2文件到软件目录models\\hy-mt2-1.8b目录，是否继续？"
        reply = QMessageBox.question(
            self,
            "确认下载",
            confirm_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_silent_download(url, str(dest_file), "MT2")

    def _start_silent_download(self, url: str, dest_path: str, model_name: str) -> None:
        self._download_progress.setValue(0)
        self._download_progress.setVisible(True)
        self._download_cancel_btn.setVisible(True)
        self._download_mt15_btn.setEnabled(False)
        self._download_mt2_btn.setEnabled(False)
        _settings_dialog_class()._show_feedback_message(
            self,
            success=True,
            title="模型下载",
            summary=f"正在下载 {model_name} 模型文件",
        )

        self._active_download_worker = ModelDownloadWorker(url, dest_path, self)
        self._active_download_worker.progress.connect(self._on_download_progress)
        self._active_download_worker.download_finished.connect(
            lambda success, err, m_name=model_name: self._on_download_finished(success, err, m_name)
        )
        self._active_download_worker.finished.connect(self._active_download_worker.deleteLater)
        self._active_download_worker.start()

    def _on_download_progress(self, val: int) -> None:
        self._download_progress.setValue(val)

    def _on_download_finished(self, success: bool, err_msg: str, model_name: str) -> None:
        self._download_progress.setVisible(False)
        self._download_cancel_btn.setVisible(False)
        self._download_mt15_btn.setEnabled(True)
        self._download_mt2_btn.setEnabled(True)

        if success:
            _settings_dialog_class()._show_feedback_message(
                self,
                success=True,
                title="模型下载",
                summary="下载完成",
                detail=f"{model_name} 模型文件已就绪。",
            )
        else:
            if err_msg != "Cancelled":
                _settings_dialog_class()._show_feedback_message(
                    self,
                    success=False,
                    title="模型下载",
                    summary="下载失败",
                    detail=f"{model_name} 模型文件下载失败：{err_msg}",
                )

    def _on_download_cancel_clicked(self) -> None:
        worker = getattr(self, "_active_download_worker", None)
        if worker is not None and worker.isRunning():
            reply = QMessageBox.question(
                self,
                "确认取消",
                "确定要取消当前模型文件的下载吗？这将清除已下载的临时文件。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                request_thread_cancel(worker, wait_ms=80)
                self._download_cancel_btn.setVisible(False)

    def _on_translator_model_changed(self, model_name: str, role: str = "translate", *, save_current: bool = True) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        model_name = str(model_name or "").strip()
        if not model_name:
            return
        role = "qa" if str(role) == "qa" else "translate"
        self._translator_current_role = role
        if save_current:
            self._save_current_translator_model_config()
        self._translator_loading = True
        try:
            if role == "qa":
                if hasattr(self, "_qa_model") and self._qa_model is not None:
                    self._set_combo_selected_value(self._qa_model, str(model_name))
            else:
                if hasattr(self, "_translator_model") and self._translator_model is not None:
                    self._set_combo_selected_value(self._translator_model, str(model_name))
        finally:
            self._translator_loading = False

        if role == "qa":
            self._translator["qa_model"] = str(model_name)
        else:
            self._translator["translate_model"] = str(model_name)
            self._translator["current_model"] = str(model_name)
        self._sync_role_models_from_combos()
        self._load_translator_model_config(str(model_name))
        self._persist_translator_settings()

        # 广播给全局其他 top-level 窗口（MainWindow 和 SettingsDialog 所有实例）
        try:
            from PyQt6.QtWidgets import QApplication
            for widget in QApplication.topLevelWidgets():
                if widget is not self and widget.__class__.__name__ in ("MainWindow", "SettingsDialog"):
                    widget_loading_flag = False
                    if hasattr(widget, "_translator_loading"):
                        widget_loading_flag = bool(getattr(widget, "_translator_loading", False))
                    if hasattr(widget, "_translator_loading"):
                        setattr(widget, "_translator_loading", True)
                    try:
                        if hasattr(widget, "_add_model_to_combos"):
                            widget._add_model_to_combos(str(model_name))

                        if str(role) == "qa":
                            if hasattr(widget, "_qa_model") and widget._qa_model is not None:
                                widget._qa_model.blockSignals(True)
                                if hasattr(widget, "_set_combo_selected_value"):
                                    widget._set_combo_selected_value(widget._qa_model, str(model_name))
                                else:
                                    widget._qa_model.setCurrentText(str(model_name))
                                widget._qa_model.blockSignals(False)
                        else:
                            if hasattr(widget, "_translator_model") and widget._translator_model is not None:
                                widget._translator_model.blockSignals(True)
                                if hasattr(widget, "_set_combo_selected_value"):
                                    widget._set_combo_selected_value(widget._translator_model, str(model_name))
                                else:
                                    widget._translator_model.setCurrentText(str(model_name))
                                widget._translator_model.blockSignals(False)

                        if hasattr(widget, "_load_translator_model_config"):
                            widget._load_translator_model_config(str(model_name))

                        if hasattr(widget, "_translator") and isinstance(widget._translator, dict):
                            if str(role) == "qa":
                                widget._translator["qa_model"] = str(model_name)
                            else:
                                widget._translator["translate_model"] = str(model_name)
                                widget._translator["current_model"] = str(model_name)
                    finally:
                        if hasattr(widget, "_translator_loading"):
                            setattr(widget, "_translator_loading", widget_loading_flag)
        except Exception:
            pass

    def _on_model_combo_activated(self, combo: TranslatorModelComboBox, index: int, role: str) -> None:
        model_name = self._combo_item_value(combo, int(index))
        if model_name:
            self._on_translator_model_changed(model_name, role)

    def _on_model_edit_finished(self, combo: TranslatorModelComboBox, role: str) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        model_name = self._combo_selected_value(combo, "gemini-3.5-flash-thinking")
        self._on_translator_model_changed(model_name, role)

    def _translator_field_config(self, model_name: Optional[str] = None) -> dict[str, object]:
        current_model = str(model_name or self._combo_selected_value(self._translator_model) or "").strip()
        cfg = {
            "base_url": self._translator_api_url.text().strip(),
            "model_name": self._translator_model_name.text().strip(),
            "api_key": self._translator_api_key.text().strip(),
            "use_proxy": bool(self._translator_use_proxy.isChecked()),
            "provider": self._translator_provider.currentText().strip() or "其它",
        }
        cfg["model_type"] = infer_translator_model_type(current_model, cfg)
        return cfg

    def _translator_required_fields(self, cfg: dict[str, object]) -> list[str]:
        model_type = str(cfg.get("model_type", "glm") or "glm").lower()
        if model_type in {"microsoft_free", "google_free"}:
            return ["base_url"]
        if model_type == "deeplx":
            return ["base_url"]
        return ["base_url", "model_name", "api_key"]

    def _translator_required_field_widgets(self) -> dict[str, QWidget]:
        return {
            "base_url": self._translator_api_url,
            "model_name": self._translator_model_name,
            "api_key": self._translator_api_key,
        }

    def _set_translator_required_field_highlight(self, field_key: str, highlighted: bool) -> None:
        widget = _settings_dialog_class()._translator_required_field_widgets(self).get(str(field_key or ""))
        if widget is None:
            return
        try:
            widget.setProperty("missingRequired", bool(highlighted))
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
        except Exception:
            pass

    def _sync_translator_required_field_highlights(self, missing_keys: set[str] | None = None) -> None:
        missing_keys = {str(key or "") for key in (missing_keys or set()) if str(key or "")}
        for key in _settings_dialog_class()._translator_required_field_widgets(self):
            _settings_dialog_class()._set_translator_required_field_highlight(self, key, key in missing_keys)

    def _clear_completed_translator_required_field_highlights(self, cfg: dict[str, object] | None = None) -> None:
        cfg = dict(cfg or _settings_dialog_class()._translator_field_config(self))
        required = set(_settings_dialog_class()._translator_required_fields(self, cfg))
        for key in _settings_dialog_class()._translator_required_field_widgets(self):
            if key not in required or str(cfg.get(key, "") or "").strip():
                _settings_dialog_class()._set_translator_required_field_highlight(self, key, False)

    def _focus_first_missing_translator_field(self, missing_keys: list[str]) -> None:
        widgets = _settings_dialog_class()._translator_required_field_widgets(self)
        for key in missing_keys:
            widget = widgets.get(str(key or ""))
            if widget is None:
                continue
            try:
                widget.setFocus()
                if hasattr(widget, "selectAll"):
                    widget.selectAll()
            except Exception:
                pass
            return

    def _preferred_model_id_placeholder(self, *, has_candidates: bool) -> str:
        model_type = infer_translator_model_type(
            "",
            {
                "base_url": self._translator_api_url.text().strip(),
                "model_name": self._translator_model_name.text().strip(),
                "api_key": self._translator_api_key.text().strip(),
            },
        )
        if model_type == "deeplx":
            return "DeepLX 无需模型 ID"
        if model_type in FREE_TRANSLATOR_TYPES:
            return "免费翻译服务无需模型 ID"
        if has_candidates:
            return "点击选择接口返回的模型 ID"
        if model_type == "anthropic":
            return "Claude 模型 ID"
        if model_type == "openai_responses":
            return "OpenAI Responses 模型 ID"
        if model_type == "openai_images":
            return "生图模型 ID（如 agnes-image-2.1-flash）"
        if model_type == "chatgpt_web":
            return "ChatGPT Web 模型 ID（如 gpt-5-3）"
        return "填写接口模型 ID"

    def _sync_translator_config_controls(self, model_type: str) -> None:
        model_type = str(model_type or "glm").lower()
        is_free = model_type in FREE_TRANSLATOR_TYPES
        self._translator_model_name.setEnabled(not is_free)
        self._translator_api_key.setEnabled(model_type not in {"microsoft_free", "google_free"})
        if model_type == "google_free":
            self._translator_api_url.setPlaceholderText("https://translate.googleapis.com")
        elif model_type == "microsoft_free":
            self._translator_api_url.setPlaceholderText("https://api-edge.cognitive.microsofttranslator.com")
        elif model_type == "deeplx":
            self._translator_api_url.setPlaceholderText(DEEPLX_INFO_ADDRESS)
        elif model_type == "anthropic":
            self._translator_api_url.setPlaceholderText("https://api.anthropic.com/v1/messages")
        elif model_type == "openai_responses":
            self._translator_api_url.setPlaceholderText("https://api.openai.com/v1/responses")
        elif model_type == "openai_images":
            self._translator_api_url.setPlaceholderText("https://api.openai.com/v1/images/generations")
        elif model_type == "chatgpt_web":
            self._translator_api_url.setPlaceholderText("http://127.0.0.1:8082")
        else:
            self._translator_api_url.setPlaceholderText("API 地址")
        if model_type == "deeplx":
            self._translator_model_name.setPlaceholderText("DeepLX 无需模型 ID")
            self._translator_api_key.setPlaceholderText("API 密钥，可不填")
        elif is_free:
            self._translator_model_name.setPlaceholderText("免费翻译服务无需模型 ID")
            self._translator_api_key.setPlaceholderText("免费翻译服务无需 API 密钥")
        elif model_type == "anthropic":
            self._translator_api_url.setPlaceholderText("https://api.anthropic.com/v1/messages")
            self._translator_model_name.setPlaceholderText("Claude 模型 ID")
            self._translator_api_key.setPlaceholderText("Anthropic API 密钥")
        elif model_type == "openai_responses":
            self._translator_api_url.setPlaceholderText("https://api.openai.com/v1/responses")
            self._translator_model_name.setPlaceholderText("OpenAI Responses 模型 ID")
            self._translator_api_key.setPlaceholderText("OpenAI API 密钥")
        elif model_type == "openai_images":
            self._translator_api_url.setPlaceholderText("https://api.openai.com/v1/images/generations")
            self._translator_model_name.setPlaceholderText("生图模型 ID（如 agnes-image-2.1-flash）")
            self._translator_api_key.setPlaceholderText("API 密钥")
        elif model_type == "chatgpt_web":
            self._translator_api_url.setPlaceholderText("http://127.0.0.1:8082")
            self._translator_model_name.setPlaceholderText("ChatGPT Web 模型 ID（如 gpt-5-3）")
            self._translator_api_key.setPlaceholderText("登录GPT后访问https://chatgpt.com/api/auth/session 复制粘贴所有内容")
        else:
            self._translator_api_url.setPlaceholderText("输入新的 API 地址")
            current_api_url = self._normalized_api_url(self._translator_api_url.text())
            has_candidates = bool(
                current_api_url
                and self._normalized_api_url(getattr(self, "_fetched_models_base_url", "")) == current_api_url
                and getattr(self, "_fetched_models_list", [])
            )
            self._translator_model_name.setPlaceholderText(
                _settings_dialog_class()._preferred_model_id_placeholder(self, has_candidates=has_candidates)
            )
            self._translator_api_key.setPlaceholderText("API 密钥")

    def _translator_fields_complete(self) -> bool:
        cfg = self._translator_field_config()
        return all(str(cfg.get(k, "") or "").strip() for k in self._translator_required_fields(cfg))

    def _remember_current_translator_model(self) -> bool:
        raw_current_model = str(getattr(self, "_translator_current_model", "") or "").strip()
        note_model = self._translator_model_note.text().strip()
        if not raw_current_model and not note_model:
            self._translator["proxy_url"] = self._translator_proxy_url.text().strip() or "socks5://127.0.0.1:1080"
            return False
        if raw_current_model and note_model and note_model != raw_current_model:
            self._save_current_translator_model_config()
            self._sync_role_models_from_combos()
            self._translator["proxy_url"] = self._translator_proxy_url.text().strip() or "socks5://127.0.0.1:1080"
            self._update_api_url_role_label()
            return True
        current_model = raw_current_model or note_model or self._combo_selected_value(self._translator_model)
        current_model = str(current_model or "").strip() or "gemini-3.5-flash-thinking"
        configs = dict(self._translator.get("model_configs") or {})
        is_known = current_model in configs or any(combo.findText(current_model) >= 0 for combo in self._model_combos())
        if not is_known and not self._translator_fields_complete():
            self._translator["proxy_url"] = self._translator_proxy_url.text().strip() or "socks5://127.0.0.1:1080"
            return False
        cfg = self._translator_field_config(current_model)
        matching_note = _settings_dialog_class()._find_matching_saved_model_note(
            self,
            str(cfg.get("model_name") or current_model),
            base_url=cfg.get("base_url"),
            api_key=cfg.get("api_key"),
            configs=configs,
        )
        if matching_note and matching_note != current_model:
            _settings_dialog_class()._switch_to_saved_translator_model_note(self, matching_note)
            self._translator["proxy_url"] = self._translator_proxy_url.text().strip() or "socks5://127.0.0.1:1080"
            self._update_api_url_role_label()
            return True
        if _settings_dialog_class()._should_clone_model_config_for_api_key_change(self, current_model, cfg, configs):
            new_note = _settings_dialog_class()._unique_model_note_name(note_model or current_model, configs, current_name="")
            if new_note and new_note != current_model and hasattr(self._translator_model_note, "setText"):
                self._translator_model_note.setText(new_note)
                self._model_id_selection_creates_config = True
                self._save_current_translator_model_config()
                self._sync_role_models_from_combos()
                self._translator["proxy_url"] = self._translator_proxy_url.text().strip() or "socks5://127.0.0.1:1080"
                self._update_api_url_role_label()
                return True
        removed = [
            str(x).strip()
            for x in self._translator.get("removed_models", [])
            if str(x).strip() and str(x).strip() != current_model
        ]
        configs[current_model] = cfg
        self._translator["model_configs"] = configs
        self._translator["removed_models"] = removed
        self._refresh_model_combos_after_catalog_change(current_model, self._current_translator_role())
        self._sync_role_models_from_combos()
        self._translator["proxy_url"] = self._translator_proxy_url.text().strip() or "socks5://127.0.0.1:1080"
        self._translator_current_model = current_model
        self._update_api_url_role_label()
        return True

    def _delete_translator_model(self, model_name: str, role: str = "translate") -> None:
        model_name = str(model_name or "").strip()
        if not model_name:
            return
        self._delete_translator_models(
            [model_name],
            role,
            confirm_title="确认删除",
            confirm_text=f"确定要删除模型配置“{model_name}”吗？",
        )

    def _delete_translator_models(
        self,
        model_names: object,
        role: str = "translate",
        *,
        confirm_title: str = "确认批量删除",
        confirm_text: str | None = None,
    ) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        role = "qa" if str(role) == "qa" else "translate"
        if isinstance(model_names, str):
            raw_names = [model_names]
        else:
            try:
                raw_names = list(model_names or [])
            except TypeError:
                raw_names = []

        names: list[str] = []
        seen: set[str] = set()
        for raw_name in raw_names:
            name = str(raw_name or "").strip()
            if not name or name in seen or name in PROTECTED_TRANSLATOR_MODEL_NAMES:
                continue
            names.append(name)
            seen.add(name)
        if not names:
            return

        if confirm_text is None:
            preview = "、".join(names[:8])
            if len(names) > 8:
                preview = f"{preview} 等"
            confirm_text = f"确定要删除选中的 {len(names)} 个模型配置吗？\n\n{preview}"

        preview = "、".join(names[:8])
        if len(names) > 8:
            preview = f"{preview} 等"
        if not _settings_dialog_class()._confirm_dangerous_action(
            self,
            title=confirm_title,
            summary=confirm_text.split("\n", 1)[0],
            impact=[
                f"删除模型配置数量：{len(names)}",
                f"涉及模型：{preview}",
                "正在使用被删除模型的翻译/问答角色会自动切换到剩余可用模型",
            ],
            level="高风险",
        ):
            return

        translate_text = self._combo_selected_value(self._translator_model)
        qa_text = self._combo_selected_value(self._qa_model)

        self._save_current_translator_model_config()
        configs = dict(self._translator.get("model_configs") or {})
        removed = {str(x).strip() for x in self._translator.get("removed_models", []) if str(x).strip()}
        default_models = set((default_translator_settings().get("model_configs") or {}).keys())
        for name in names:
            configs.pop(name, None)
            if name in default_models:
                removed.add(name)
        if str(self._translator.get("claude_code_current_model_config_model", "") or "").strip() in names:
            self._translator["claude_code_current_model_config_model"] = ""

        remaining_models = [str(name) for name in configs.keys() if str(name or "").strip()]
        default_fallback = "gemini-3.5-flash-thinking"
        translate_fallback = remaining_models[0] if remaining_models else default_fallback
        qa_candidates = [name for name in remaining_models if name not in QA_EXCLUDED_TRANSLATOR_MODEL_NAMES]
        qa_fallback = qa_candidates[0] if qa_candidates else translate_fallback
        next_translate = translate_text if translate_text and translate_text not in names else translate_fallback
        next_qa = qa_text if qa_text and qa_text not in names else qa_fallback

        self._translator["model_configs"] = configs
        self._translator["removed_models"] = sorted(removed)
        self._translator_loading = True
        try:
            self._set_combo_selected_value(self._translator_model, next_translate)
            self._set_combo_selected_value(self._qa_model, next_qa)
            self._fill_model_combos()
            self._set_combo_selected_value(self._translator_model, next_translate)
            self._set_combo_selected_value(self._qa_model, next_qa)
        finally:
            self._translator_loading = False
        self._sync_role_models_from_combos()
        current_model = str(getattr(self, "_translator_current_model", "") or "").strip()
        if current_model in names:
            current_model = next_qa if role == "qa" else next_translate
            self._translator_current_model = current_model
        self._translator_current_role = role
        self._load_translator_model_config(str(current_model or self._translator.get("translate_model", next_translate)))
        if hasattr(self, "_fill_model_name_combobox_items"):
            self._fill_model_name_combobox_items()
        if hasattr(self, "_repaint_model_combos"):
            self._repaint_model_combos()
        self._persist_translator_settings()

    def _first_model_for_provider(self, provider: str) -> str:
        provider = str(provider or "").strip()
        if not provider:
            return ""
        names: list[str] = []
        configs = dict(self._translator.get("model_configs") or {})
        for name, cfg in configs.items():
            model_provider = str(cfg.get("provider") or infer_translator_model_provider(str(name), cfg)).strip()
            if model_provider == provider:
                names.append(str(name))
        return sorted(names)[0] if names else ""

    def _provider_popup_models_by_provider(self, role: str = "") -> dict[str, list[str]]:
        role = "qa" if str(role or "").strip().lower() == "qa" else "translate"
        combo = getattr(self, "_qa_model", None) if role == "qa" else getattr(self, "_translator_model", None)
        grouped_from_combo = _settings_dialog_class()._grouped_models_from_role_combo(combo)
        if grouped_from_combo:
            return grouped_from_combo

        return _settings_dialog_class()._models_by_provider_from_translator_settings(self._translator, role)

    @staticmethod
    def _grouped_models_from_role_combo(combo: QComboBox | None) -> dict[str, list[str]]:
        if combo is None:
            return {}
        model = combo.model()
        if model is None:
            return {}
        grouped: dict[str, list[str]] = {}
        current_provider = ""
        for row in range(model.rowCount()):
            item = model.item(row) if hasattr(model, "item") else None
            if item is not None:
                text = str(item.text() or "").strip()
                enabled = bool(item.isEnabled())
                stored = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            else:
                index = model.index(row, 0)
                text = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
                flags = model.flags(index)
                enabled = bool(flags & Qt.ItemFlag.ItemIsEnabled)
                stored = str(index.data(Qt.ItemDataRole.UserRole) or "").strip()
            if not text:
                continue
            if not enabled and text.startswith("[") and text.endswith("]"):
                current_provider = text.strip("[]").strip()
                if current_provider:
                    grouped.setdefault(current_provider, [])
                continue
            if not enabled:
                continue
            model_name = stored or text
            if current_provider and model_name:
                grouped.setdefault(current_provider, []).append(model_name)
        return {provider: names for provider, names in grouped.items() if names}

    def _provider_popup_current_model(self, role: str = "") -> str:
        role = "qa" if str(role or "").strip().lower() == "qa" else "translate"
        combo = self._qa_model if role == "qa" else self._translator_model
        current_model = self._combo_selected_value(combo)
        if current_model:
            return current_model
        if role == "qa":
            return str(self._translator.get("qa_model", "") or self._translator.get("current_model", "")).strip()
        return str(self._translator.get("translate_model", "") or self._translator.get("current_model", "")).strip()

    def _on_provider_popup_model_selected(self, model_name: str) -> None:
        model_name = str(model_name or "").strip()
        if not model_name or bool(getattr(self, "_translator_loading", False)):
            return
        self._on_translator_model_changed(model_name, self._current_translator_role())

    def _set_translator_provider_value(self, provider: str) -> None:
        provider = str(provider or "").strip()
        if not provider:
            return
        self._translator_provider.blockSignals(True)
        try:
            idx = self._translator_provider.findText(provider)
            if idx >= 0:
                self._translator_provider.setCurrentIndex(idx)
            else:
                self._translator_provider.addItem(provider)
                self._translator_provider.setCurrentText(provider)
        finally:
            self._translator_provider.blockSignals(False)

    def _select_provider_for_new_model(self, provider: str) -> None:
        provider = str(provider or "").strip()
        if not provider or bool(getattr(self, "_translator_loading", False)):
            return
        self._save_current_translator_model_config()
        self._set_translator_provider_value(provider)
        self._clear_translator_fields_for_new_provider(provider)
        self._translator_api_url.setPlaceholderText("输入新的 API 地址")
        self._translator_api_url.setFocus()

    def _move_current_model_to_provider(self, provider: str) -> None:
        provider = str(provider or "").strip()
        if not provider or bool(getattr(self, "_translator_loading", False)):
            return
        current_model = str(getattr(self, "_translator_current_model", "") or "").strip()
        if not current_model:
            self._select_provider_for_new_model(provider)
            return
        current_role = self._current_translator_role()
        self._set_translator_provider_value(provider)
        self._save_current_translator_model_config()

        configs = dict(self._translator.get("model_configs") or {})
        if isinstance(configs.get(current_model), dict):
            cfg = dict(configs.get(current_model) or {})
            cfg["provider"] = provider
            configs[current_model] = cfg
            self._translator["model_configs"] = configs

        self._refresh_model_combos_after_catalog_change(current_model, current_role)
        self._sync_role_models_from_combos()
        self._repaint_model_combos()
        self._fill_model_name_combobox_items()
        self._persist_translator_settings()

    def _save_current_model_with_provider(self, provider: str) -> None:
        provider = str(provider or "").strip()
        if not provider:
            self._save_current_translator_model_config()
            return
        selected_provider = self._translator_provider.currentText().strip()
        self._translator_provider.blockSignals(True)
        try:
            idx = self._translator_provider.findText(provider)
            if idx >= 0:
                self._translator_provider.setCurrentIndex(idx)
            else:
                self._translator_provider.addItem(provider)
                self._translator_provider.setCurrentText(provider)
            self._save_current_translator_model_config()
            restore_idx = self._translator_provider.findText(selected_provider)
            if restore_idx >= 0:
                self._translator_provider.setCurrentIndex(restore_idx)
            elif selected_provider:
                self._translator_provider.addItem(selected_provider)
                self._translator_provider.setCurrentText(selected_provider)
        finally:
            self._translator_provider.blockSignals(False)

    def _on_provider_selected_for_new_model(self) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        self._select_provider_for_new_model(self._translator_provider.currentText().strip())

    def _on_provider_activated(self) -> None:
        self._on_provider_selected_for_new_model()

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
        impact_text = "\n".join(f"- {item}" for item in impact_items) if impact_items else "- 当前配置"
        irreversible_text = "\n\n此操作不可撤销，请确认影响范围后再继续。" if irreversible else ""
        message = f"{summary}\n\n风险级别：{level}\n影响范围：\n{impact_text}{irreversible_text}"
        reply = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _set_translator_save_status(self, text: str, color: str = "#64748b") -> None:
        label = getattr(self, "_translator_save_status", None)
        if label is None:
            return
        try:
            status_text = str(text or "")
            label.setText(status_text)
            label.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 700;")
            label.setVisible(bool(status_text))
        except Exception:
            pass

    def _hide_translator_save_status_later(self, token: int, delay_ms: int = 1800) -> None:
        def hide() -> None:
            if int(getattr(self, "_translator_save_status_token", 0)) == int(token):
                _settings_dialog_class()._set_translator_save_status(self, "", "#64748b")

        QTimer.singleShot(int(delay_ms), hide)

    def _mark_translator_settings_dirty(self, *_args) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        self._translator_save_status_token = int(getattr(self, "_translator_save_status_token", 0)) + 1
        token = int(getattr(self, "_translator_save_status_token", 0))
        _settings_dialog_class()._set_translator_save_status(self, "未保存", "#d97706")
        _settings_dialog_class()._hide_translator_save_status_later(self, token, 1800)

    def _mark_translator_settings_saved(self, *, transient: bool = True) -> None:
        self._translator_save_status_token = int(getattr(self, "_translator_save_status_token", 0)) + 1
        token = int(getattr(self, "_translator_save_status_token", 0))
        if not transient:
            _settings_dialog_class()._set_translator_save_status(self, "", "#64748b")
            return
        _settings_dialog_class()._set_translator_save_status(self, "已自动保存", "#16a34a")
        _settings_dialog_class()._hide_translator_save_status_later(self, token, 1800)

    def _apply_translator_settings(self) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        changed = self._remember_current_translator_model()
        try:
            _settings_dialog_class()._clear_completed_translator_required_field_highlights(self)
        except Exception:
            pass
        self._fill_model_name_combobox_items()
        if changed:
            self._persist_translator_settings()
            _settings_dialog_class()._mark_translator_settings_saved(self, transient=True)

    def _apply_auto_copy_answers(self) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        enabled = bool(self._auto_copy_switch.isChecked())
        self._translator["auto_copy_answers"] = enabled
        self._persist_translator_settings()

    def _local_translation_service_config(self) -> tuple[str, int, str]:
        host = str(self._translator.get("local_translation_service_host", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
        try:
            port = int(self._translator.get("local_translation_service_port", 11888) or 11888)
        except Exception:
            port = 11888
        port = int(max(1, min(65535, port)))
        api_key = str(self._translator.get("local_translation_service_api_key", "sk-deepcat-local") or "")
        return host, port, api_key

    def _set_local_translation_service_switch_checked(self, checked: bool) -> None:
        switch = getattr(self, "_local_translation_service_switch", None)
        if switch is None:
            return
        try:
            switch.blockSignals(True)
            switch.setChecked(bool(checked))
        finally:
            switch.blockSignals(False)

    def _sync_local_translation_service_state(self, *, show_errors: bool = False) -> bool:
        enabled = bool(self._translator.get("local_translation_service_enabled", False))
        try:
            if enabled:
                from deepcat.translation_server import (
                    probe_background_translation_server,
                    start_background_translation_server,
                )

                host, port, api_key = self._local_translation_service_config()
                start_background_translation_server(host=host, port=port, api_key=api_key)
                self._set_local_translation_service_switch_checked(True)
                report = probe_background_translation_server()
                _settings_dialog_class()._apply_local_translation_service_probe_report(
                    self, report, show_notice=show_errors
                )
                # bind 成功只代表端口被占用；自检不通过说明服务在跑但客户端连不上，
                # 交给放行按钮处理，而不是把开关回滚掉。
                return True

            from deepcat.translation_server import stop_background_translation_server

            stop_background_translation_server()
            self._set_local_translation_service_switch_checked(False)
            _settings_dialog_class()._clear_local_translation_service_probe_report(self)
            switch = getattr(self, "_local_translation_service_switch", None)
            if switch is not None:
                switch.setToolTip("勾选后启动 DeepCat 本地 API 服务，提供翻译和问答的 OpenAI 兼容接口。")
            return True
        except Exception as exc:
            self._translator["local_translation_service_enabled"] = False
            self._persist_translator_settings()
            self._set_local_translation_service_switch_checked(False)
            _settings_dialog_class()._clear_local_translation_service_probe_report(self)
            if show_errors:
                try:
                    _settings_dialog_class()._show_feedback_message(
                        self,
                        success=False,
                        title="本地 API 服务",
                        summary="服务启动失败",
                        detail=str(exc),
                    )
                except Exception:
                    pass
            return False

    def _apply_local_translation_service_enabled(self) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        enabled = bool(self._local_translation_service_switch.isChecked())
        self._translator["local_translation_service_enabled"] = enabled
        self._persist_translator_settings()
        self._sync_local_translation_service_state(show_errors=True)

    def _local_translation_service_base_url(self, report: object = None) -> str:
        port = int(getattr(report, "port", 0) or 0)
        if port <= 0:
            _, port, _ = _settings_dialog_class()._local_translation_service_config(self)
        return f"http://127.0.0.1:{port}/v1/chat/completions"

    def _create_local_translation_firewall_button(self) -> QPushButton:
        button = QPushButton("一键允许")
        button.setToolTip("以管理员身份为 DeepCat 添加 Windows 防火墙入站放行规则，然后重新自检。")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setVisible(False)
        dialog_class = _settings_dialog_class()
        dialog_class._style_translator_footer_button(button, 84)
        # MainWindow 的内嵌设置页没有继承本 mixin，按钮回调统一从 SettingsDialog 解析。
        button.clicked.connect(lambda *_args: dialog_class._on_local_translation_firewall_allow_clicked(self))
        return button

    def _local_translation_firewall_allow_available(self) -> bool:
        """只有打包态、且自检确认连接被防火墙丢弃时才提供一键允许。"""
        report = getattr(self, "_local_translation_service_probe_report", None)
        if report is None or not bool(getattr(report, "is_blocked", False)):
            return False
        from deepcat.utils.windows_firewall import is_packaged_app

        return is_packaged_app()

    def _refresh_local_translation_firewall_button(self) -> None:
        button = getattr(self, "_local_translation_firewall_btn", None)
        if button is None:
            return
        footer_active = bool(getattr(self, "_translator_footer_active", True))
        allowed = _settings_dialog_class()._local_translation_firewall_allow_available(self)
        button.setVisible(footer_active and allowed)

    def _apply_local_translation_service_probe_report(self, report: object, *, show_notice: bool = False) -> bool:
        """记录回环自检结果，刷新开关提示与放行按钮，并返回服务是否真的可达。"""
        dialog_class = _settings_dialog_class()
        setattr(self, "_local_translation_service_probe_report", report)
        healthy = bool(getattr(report, "is_healthy", False))
        reason = str(getattr(report, "reason", "") or "")
        switch = getattr(self, "_local_translation_service_switch", None)
        if switch is not None:
            if healthy:
                base_url = dialog_class._local_translation_service_base_url(self, report)
                switch.setToolTip(f"本地 API 服务已启动，回环自检通过：{base_url}")
            else:
                switch.setToolTip(f"本地 API 服务自检未通过。{reason}")
        dialog_class._refresh_local_translation_firewall_button(self)
        if show_notice and not healthy:
            dialog_class._show_local_translation_service_notice(
                self,
                success=False,
                summary="本地 API 服务自检未通过",
                detail=reason,
            )
        return healthy

    def _clear_local_translation_service_probe_report(self) -> None:
        setattr(self, "_local_translation_service_probe_report", None)
        _settings_dialog_class()._refresh_local_translation_firewall_button(self)

    def _show_local_translation_service_notice(self, *, success: object, summary: str, detail: str = "") -> None:
        try:
            _settings_dialog_class()._show_feedback_message(
                self,
                success=success,
                title="本地 API 服务",
                summary=summary,
                detail=detail,
            )
        except Exception:
            logger.exception("显示本地 API 服务提示失败")

    def _on_local_translation_firewall_allow_clicked(self) -> None:
        from deepcat.utils.windows_firewall import request_firewall_allow

        dialog_class = _settings_dialog_class()
        button = getattr(self, "_local_translation_firewall_btn", None)
        if button is not None:
            button.setEnabled(False)
        try:
            issued, message = request_firewall_allow()
        finally:
            if button is not None:
                button.setEnabled(True)
        if not issued:
            dialog_class._show_local_translation_service_notice(
                self, success=False, summary="未能添加防火墙规则", detail=message
            )
            return
        dialog_class._show_local_translation_service_notice(self, success="loading", summary=message)
        QTimer.singleShot(
            800,
            lambda: dialog_class._reverify_local_translation_service_after_firewall_allow(self),
        )

    def _reverify_local_translation_service_after_firewall_allow(self) -> None:
        from deepcat.translation_server import (
            background_translation_server_address,
            probe_background_translation_server,
        )

        dialog_class = _settings_dialog_class()
        button = getattr(self, "_local_translation_firewall_btn", None)
        if button is not None:
            button.setEnabled(False)
        try:
            if background_translation_server_address() is None:
                dialog_class._clear_local_translation_service_probe_report(self)
                dialog_class._show_local_translation_service_notice(
                    self,
                    success=False,
                    summary="本地 API 服务已停止",
                    detail="请重新勾选「本地API」开关后再试。",
                )
                return
            report = probe_background_translation_server()
        finally:
            if button is not None:
                button.setEnabled(True)
        if dialog_class._apply_local_translation_service_probe_report(self, report):
            dialog_class._show_local_translation_service_notice(
                self, success=True, summary="防火墙规则已生效，本地 API 服务可以访问。"
            )
            return
        dialog_class._show_local_translation_service_notice(
            self,
            success=False,
            summary="防火墙规则已添加，但自检仍未通过",
            detail=f"{getattr(report, 'reason', '')}；可在「Windows 安全中心 → 防火墙和网络保护」中检查 DeepCat 规则。",
        )

    def _apply_codex_config_enabled(self) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        enabled = bool(self._codex_config_switch.isChecked())
        previous_enabled = bool(self._translator.get("codex_config_enabled", False))
        try:
            client_was_running = _settings_dialog_class()._apply_codex_config_with_client_restart(self, enabled)
        except Exception as e:
            def rollback():
                old = bool(self._codex_config_switch.blockSignals(True))
                try:
                    self._codex_config_switch.setChecked(previous_enabled)
                finally:
                    self._codex_config_switch.blockSignals(old)
            QTimer.singleShot(0, rollback)

            if "用户取消了配置更改" not in str(e):
                _settings_dialog_class()._show_feedback_message(
                    self,
                    success=False,
                    title="Codex 配置",
                    summary="配置应用失败",
                    detail=str(e),
                )
            return

        self._translator["codex_config_enabled"] = enabled
        if enabled and hasattr(self, "_fill_codex_config_switch"):
            self._translator["codex_current_model_config_enabled"] = False
            self._translator["codex_current_model_config_model"] = ""
            old = bool(self._fill_codex_config_switch.blockSignals(True))
            try:
                self._fill_codex_config_switch.setChecked(False)
            finally:
                self._fill_codex_config_switch.blockSignals(old)
        self._persist_translator_settings()
        if enabled and not client_was_running:
            _settings_dialog_class()._show_codex_config_next_step(self)
        if hasattr(self, "_fill_codex_config_switch") and hasattr(self, "_sync_current_model_codex_ui"):
            self._sync_current_model_codex_ui()

    def _apply_codex_config_with_client_restart(self, enabled: bool, **kwargs) -> bool:
        """运行中的桌面客户端必须完整退出后再写配置，完成后恢复运行状态。"""
        from pathlib import Path
        from PyQt6.QtWidgets import QWidget
        CHATGPT_PROCESS_NAMES = {"chatgpt", "chatgpt.exe"}

        if isinstance(self, QWidget):
            if not hasattr(self, "_launch_client_with_feedback"):
                self._launch_client_with_feedback = TranslatorSettingsMixin._launch_client_with_feedback.__get__(self, type(self))
            if not hasattr(self, "_start_client_launch_detection"):
                self._start_client_launch_detection = TranslatorSettingsMixin._start_client_launch_detection.__get__(self, type(self))

        running_processes = list_codex_client_process_names()
        client_was_running = bool(running_processes)

        if client_was_running:
            normalized_names = {Path(name).name.casefold() for name in running_processes}
            client_name = "ChatGPT" if (normalized_names & CHATGPT_PROCESS_NAMES) else "Codex"

            ret = QMessageBox.question(
                self,
                "Codex 配置",
                f"检测到 {client_name} 客户端正在运行，修改配置需要关闭客户端进程树。\n是否现在关闭客户端并继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret != QMessageBox.StandardButton.Yes:
                raise RuntimeError("用户取消了配置更改")

            try:
                stop_codex_client()
            except Exception as e:
                raise RuntimeError(f"无法关闭 {client_name} 客户端：{e}")

        try:
            apply_codex_config(enabled, **kwargs)
        except Exception as config_error:
            if client_was_running:
                try:
                    launch_codex_client()
                except Exception as launch_error:
                    raise RuntimeError(
                        f"{config_error}\n原客户端恢复启动也失败：{launch_error}"
                    ) from config_error
            raise

        if client_was_running:
            if hasattr(self, "_launch_client_with_feedback"):
                self._launch_client_with_feedback(client_name)
            else:
                launch_codex_client()
        return client_was_running

    def _set_current_model_codex_switch_checked(self, checked: bool) -> None:
        switch = getattr(self, "_fill_codex_config_switch", None)
        if switch is None:
            return
        old = bool(switch.blockSignals(True))
        try:
            switch.setChecked(bool(checked))
        finally:
            switch.blockSignals(old)

    def _current_model_codex_config_model(self) -> str:
        return str(self._translator.get("codex_current_model_config_model", "") or "").strip()

    def _ensure_current_model_codex_binding(self) -> str:
        if not bool(self._translator.get("codex_current_model_config_enabled", False)):
            return ""
        bound_model = self._current_model_codex_config_model()
        if bound_model:
            return bound_model
        bound_model = str(self._translator.get("qa_model", "") or "").strip()
        if not bound_model and hasattr(self, "_qa_model") and self._qa_model is not None:
            bound_model = self._combo_selected_value(self._qa_model)
        if bound_model:
            self._translator["codex_current_model_config_model"] = bound_model
        return bound_model

    def _is_current_model_codex_configured(self, model_name: str) -> bool:
        model_name = str(model_name or "").strip()
        if not model_name or not bool(self._translator.get("codex_current_model_config_enabled", False)):
            return False
        return self._ensure_current_model_codex_binding() == model_name

    def _sync_current_model_codex_ui(self) -> None:
        role = self._current_translator_role()
        current_model = str(getattr(self, "_translator_current_model", "") or "").strip()
        if role == "qa" and not current_model and hasattr(self, "_qa_model") and self._qa_model is not None:
            current_model = self._combo_selected_value(self._qa_model)
        checked = role == "qa" and self._is_current_model_codex_configured(current_model)

        switch = getattr(self, "_fill_codex_config_switch", None)
        if switch is not None:
            old = bool(switch.blockSignals(True))
            try:
                switch.setChecked(bool(checked))
                switch.setVisible(False)
                switch.setEnabled(False)
            finally:
                switch.blockSignals(old)

        _settings_dialog_class()._refresh_codex_model_combo_styles(self)
        _settings_dialog_class()._sync_provider_popup_model_cache(self)

        qa_combo = getattr(self, "_qa_model", None)
        if qa_combo is not None:
            qa_model = self._combo_selected_value(qa_combo)
            provider_combo = getattr(self, "_translator_provider", None)
            cached_highlight_colors = provider_combo.property("providerHighlightedModelColors") if provider_combo is not None else None
            qa_highlight_colors = (
                {
                    str(name or "").strip(): str(color or "").strip()
                    for name, color in dict(cached_highlight_colors or {}).items()
                    if str(name or "").strip() and str(color or "").strip()
                }
                if isinstance(cached_highlight_colors, dict)
                else _settings_dialog_class()._provider_highlighted_model_colors(self, "qa")
            )
            qa_combo.setProperty(
                "activeCheckColor",
                qa_highlight_colors.get(qa_model, "#111827"),
            )
            qa_combo.update()

        translate_combo = getattr(self, "_translator_model", None)
        if translate_combo is not None:
            translate_combo.setProperty("activeCheckColor", "#111827")
            translate_combo.update()

    def _codex_action_label_for_model(self, model_name: str) -> str:
        return "还原Codex" if self._is_current_model_codex_configured(model_name) else "填入Codex"

    def _claude_code_action_label_for_model(self, model_name: str) -> str:
        return "还原CC" if _settings_dialog_class()._is_current_model_claude_code_configured(self, model_name) else "填入CC"

    def _apply_current_model_claude_code_config_for_model(self, model_name: str) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            logger.info("[Claude Code 配置] 当前模型配置被跳过：设置正在加载")
            return
        model_name = str(model_name or "").strip()
        if not model_name:
            logger.warning("[Claude Code 配置] 当前模型配置被跳过：模型名为空")
            return
        should_restore = _settings_dialog_class()._is_current_model_claude_code_configured(self, model_name)
        logger.info(
            "[Claude Code 配置] 开始处理当前模型配置: model=%s restore=%s owner=%s",
            model_name,
            should_restore,
            type(self).__name__,
        )
        try:
            self._on_translator_model_changed(model_name, "qa")
        except Exception:
            logger.exception("切换 Claude Code 目标问答模型失败")

        if should_restore:
            try:
                path = restore_claude_code_config()
            except Exception as e:
                _settings_dialog_class()._show_feedback_message(
                    self,
                    success=False,
                    title="Claude Code 配置",
                    summary="配置还原失败",
                    detail=str(e),
                )
                if hasattr(self, "_sync_current_model_codex_ui"):
                    self._sync_current_model_codex_ui()
                elif hasattr(self, "_sync_provider_popup_model_cache"):
                    self._sync_provider_popup_model_cache()
                return
            self._translator["claude_code_current_model_config_model"] = ""
            if hasattr(self, "_persist_translator_settings"):
                self._persist_translator_settings()
            if hasattr(self, "_sync_current_model_codex_ui"):
                self._sync_current_model_codex_ui()
            elif hasattr(self, "_sync_provider_popup_model_cache"):
                self._sync_provider_popup_model_cache()
            _settings_dialog_class()._show_feedback_message(
                self,
                success=True,
                title="Claude Code 配置",
                summary="配置还原成功",
                detail=f"路径：{path}\n已恢复填入 CC 前的配置。",
            )
            return

        configs = dict(self._translator.get("model_configs") or {})
        cfg = dict(configs.get(model_name) or {})
        if not cfg:
            try:
                cfg = dict(self._translator_field_config(model_name) or {})
            except Exception:
                cfg = {}

        base_url = str(cfg.get("base_url", "") or "").strip()
        model_id = str(cfg.get("model_name", "") or model_name).strip()
        api_key = str(cfg.get("api_key", "") or "").strip()
        label_map = {"base_url": "API地址", "model_name": "模型ID", "api_key": "API密钥"}
        missing = []
        if not base_url:
            missing.append(label_map["base_url"])
        if not model_id:
            missing.append(label_map["model_name"])
        if not api_key:
            missing.append(label_map["api_key"])
        if missing:
            _settings_dialog_class()._show_feedback_message(
                self,
                success=False,
                title="Claude Code 配置",
                summary="配置缺少必要信息",
                detail="请先填写：" + "、".join(missing),
            )
            return

        try:
            path = apply_claude_code_config(base_url=base_url, model=model_id, api_key=api_key)
        except Exception as e:
            _settings_dialog_class()._show_feedback_message(
                self,
                success=False,
                title="Claude Code 配置",
                summary="配置写入失败",
                detail=str(e),
            )
            return
        self._translator["claude_code_current_model_config_model"] = model_name
        if hasattr(self, "_persist_translator_settings"):
            self._persist_translator_settings()
        if hasattr(self, "_sync_current_model_codex_ui"):
            self._sync_current_model_codex_ui()
        elif hasattr(self, "_sync_provider_popup_model_cache"):
            self._sync_provider_popup_model_cache()
        _settings_dialog_class()._show_feedback_message(
            self,
            success=True,
            title="Claude Code 配置",
            summary="配置写入成功",
            detail=f"路径：{path}\n请重启 Claude Code 或重新打开终端使配置生效。",
        )

    def _apply_current_model_codex_config_for_model(self, model_name: str) -> None:
        model_name = str(model_name or "").strip()
        if not model_name:
            logger.warning("[Codex 配置] 当前模型配置被跳过：模型名为空")
            return
        logger.info("[Codex 配置] 开始处理当前模型配置: model=%s owner=%s", model_name, type(self).__name__)
        should_restore = self._is_current_model_codex_configured(model_name)
        previous_enabled = bool(self._translator.get("codex_current_model_config_enabled", False))

        switched_model = False
        if not bool(getattr(self, "_translator_loading", False)):
            try:
                self._on_translator_model_changed(model_name, "qa", save_current=False)
                switched_model = True
            except TypeError as exc:
                if "save_current" in str(exc):
                    try:
                        self._on_translator_model_changed(model_name, "qa")
                        switched_model = True
                    except Exception:
                        logger.exception("切换 Codex 目标问答模型失败")
                else:
                    logger.exception("切换 Codex 目标问答模型失败")
            except Exception:
                logger.exception("切换 Codex 目标问答模型失败")

        if not switched_model:
            self._translator_current_role = "qa"
            self._translator_current_model = model_name
            self._translator["qa_model"] = model_name
            qa_combo = getattr(self, "_qa_model", None)
            if qa_combo is not None:
                try:
                    old = bool(qa_combo.blockSignals(True))
                    try:
                        self._set_combo_selected_value(qa_combo, model_name)
                    finally:
                        qa_combo.blockSignals(old)
                except Exception:
                    pass

        self._set_current_model_codex_switch_checked(not should_restore)
        if should_restore:
            if previous_enabled:
                try:
                    _settings_dialog_class()._apply_codex_config_with_client_restart(self, False)
                except Exception as e:
                    def rollback_model_1():
                        self._sync_current_model_codex_ui()
                    QTimer.singleShot(0, rollback_model_1)

                    if "用户取消了配置更改" not in str(e):
                        _settings_dialog_class()._show_feedback_message(
                            self,
                            success=False,
                            title="Codex 配置",
                            summary="配置还原失败",
                            detail=str(e),
                        )
                    return
            self._translator["codex_current_model_config_enabled"] = False
            self._translator["codex_current_model_config_model"] = ""
            self._persist_translator_settings()
            self._sync_current_model_codex_ui()
            return

        configs = dict(self._translator.get("model_configs") or {})
        cfg = dict(configs.get(model_name) or {})
        if not cfg:
            try:
                cfg = dict(self._translator_field_config(model_name) or {})
            except Exception:
                cfg = {}

        label_map = {"base_url": "API地址", "model_name": "模型ID", "api_key": "API密钥"}
        missing = [
            label_map.get(key, key)
            for key in ("base_url", "model_name", "api_key")
            if not str(cfg.get(key, "") or "").strip()
        ]
        if missing:
            self._translator["codex_current_model_config_enabled"] = False
            self._translator["codex_current_model_config_model"] = ""
            self._persist_translator_settings()
            self._set_current_model_codex_switch_checked(False)
            self._sync_current_model_codex_ui()
            _settings_dialog_class()._show_feedback_message(
                self,
                success=False,
                title="Codex 配置",
                summary="配置缺少必要信息",
                detail="请先填写：" + "、".join(missing),
            )
            return

        try:
            client_was_running = _settings_dialog_class()._apply_codex_config_with_client_restart(
                self,
                True,
                base_url=str(cfg.get("base_url", "") or "").strip(),
                model=str(cfg.get("model_name", "") or model_name).strip(),
                api_key=str(cfg.get("api_key", "") or "").strip(),
            )
        except Exception as e:
            self._translator["codex_current_model_config_enabled"] = False
            self._translator["codex_current_model_config_model"] = ""
            self._persist_translator_settings()

            def rollback_model_2():
                self._set_current_model_codex_switch_checked(False)
                self._sync_current_model_codex_ui()
            QTimer.singleShot(0, rollback_model_2)

            if "用户取消了配置更改" not in str(e):
                _settings_dialog_class()._show_feedback_message(
                    self,
                    success=False,
                    title="Codex 配置",
                    summary="配置写入失败",
                    detail=str(e),
                )
            return
        self._translator["codex_config_enabled"] = False
        self._translator["codex_current_model_config_enabled"] = True
        self._translator["codex_current_model_config_model"] = model_name
        if hasattr(self, "_codex_config_switch"):
            old = bool(self._codex_config_switch.blockSignals(True))
            try:
                self._codex_config_switch.setChecked(False)
            finally:
                self._codex_config_switch.blockSignals(old)
        self._persist_translator_settings()
        self._sync_current_model_codex_ui()
        if not client_was_running:
            _settings_dialog_class()._show_codex_config_next_step(self)

    def _apply_current_model_codex_config_enabled(self, *_args) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        role = self._current_translator_role()
        if role != "qa":
            self._translator["codex_current_model_config_enabled"] = bool(self._translator.get("codex_current_model_config_enabled", False))
            self._set_current_model_codex_switch_checked(False)
            self._sync_current_model_codex_ui()
            return

        enabled = bool(self._fill_codex_config_switch.isChecked())
        previous_enabled = bool(self._translator.get("codex_current_model_config_enabled", False))

        if not enabled:
            if previous_enabled:
                try:
                    _settings_dialog_class()._apply_codex_config_with_client_restart(self, False)
                except Exception as e:
                    _settings_dialog_class()._show_feedback_message(
                        self,
                        success=False,
                        title="Codex 配置",
                        summary="配置还原失败",
                        detail=str(e),
                    )
                    self._sync_current_model_codex_ui()
                    return
            self._translator["codex_current_model_config_enabled"] = False
            self._translator["codex_current_model_config_model"] = ""
            self._persist_translator_settings()
            self._sync_current_model_codex_ui()
            return

        self._remember_current_translator_model()
        current_model, cfg, _use_proxy, _proxy_url = self._current_translator_runtime_config()

        label_map = {"base_url": "API地址", "model_name": "模型ID", "api_key": "API密钥"}
        missing = [
            label_map.get(key, key)
            for key in ("base_url", "model_name", "api_key")
            if not str(cfg.get(key, "") or "").strip()
        ]
        if missing:
            self._translator["codex_current_model_config_enabled"] = False
            self._translator["codex_current_model_config_model"] = ""
            self._persist_translator_settings()
            self._set_current_model_codex_switch_checked(False)
            self._sync_current_model_codex_ui()
            _settings_dialog_class()._show_feedback_message(
                self,
                success=False,
                title="Codex 配置",
                summary="配置缺少必要信息",
                detail="请先填写：" + "、".join(missing),
            )
            return

        try:
            client_was_running = _settings_dialog_class()._apply_codex_config_with_client_restart(
                self,
                True,
                base_url=str(cfg.get("base_url", "") or "").strip(),
                model=str(cfg.get("model_name", "") or current_model).strip(),
                api_key=str(cfg.get("api_key", "") or "").strip(),
            )
        except Exception as e:
            self._translator["codex_current_model_config_enabled"] = False
            self._translator["codex_current_model_config_model"] = ""
            self._persist_translator_settings()

            def rollback_current():
                self._set_current_model_codex_switch_checked(False)
                self._sync_current_model_codex_ui()
            QTimer.singleShot(0, rollback_current)

            if "用户取消了配置更改" not in str(e):
                _settings_dialog_class()._show_feedback_message(
                    self,
                    success=False,
                    title="Codex 配置",
                    summary="配置写入失败",
                    detail=str(e),
                )
            return
        self._translator["codex_config_enabled"] = False
        self._translator["codex_current_model_config_enabled"] = True
        self._translator["codex_current_model_config_model"] = current_model
        if hasattr(self, "_codex_config_switch"):
            old = bool(self._codex_config_switch.blockSignals(True))
            try:
                self._codex_config_switch.setChecked(False)
            finally:
                self._codex_config_switch.blockSignals(old)
        self._persist_translator_settings()
        self._sync_current_model_codex_ui()
        if not client_was_running:
            _settings_dialog_class()._show_codex_config_next_step(self)

    def _show_codex_config_next_step(self) -> None:
        from pathlib import Path
        from PyQt6.QtWidgets import QWidget
        from deepcat.utils.codex_config import _resolve_codex_app_dir

        if isinstance(self, QWidget):
            if not hasattr(self, "_launch_client_with_feedback"):
                self._launch_client_with_feedback = TranslatorSettingsMixin._launch_client_with_feedback.__get__(self, type(self))
            if not hasattr(self, "_start_client_launch_detection"):
                self._start_client_launch_detection = TranslatorSettingsMixin._start_client_launch_detection.__get__(self, type(self))

        app_dir = _resolve_codex_app_dir()
        is_chatgpt = False
        if app_dir:
            for part in reversed(app_dir.parts):
                if part.startswith("OpenAI.ChatGPT_"):
                    is_chatgpt = True
                    break
        client_name = "ChatGPT" if is_chatgpt else "Codex"

        ret = QMessageBox.question(
            self,
            "Codex 配置",
            f"已写入 Codex 配置。是否立即打开 {client_name} 使配置生效？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        if hasattr(self, "_launch_client_with_feedback"):
            self._launch_client_with_feedback(client_name)
        else:
            try:
                launch_codex_client()
            except Exception as e:
                _settings_dialog_class()._show_feedback_message(
                    self,
                    success=False,
                    title="Codex 配置",
                    summary=f"{client_name} 启动失败",
                    detail=str(e),
                )

    def _launch_client_with_feedback(self, client_name: str) -> None:
        try:
            launch_codex_client()
        except Exception as e:
            _settings_dialog_class()._show_feedback_message(
                self,
                success=False,
                title="Codex 配置",
                summary=f"{client_name} 启动失败",
                detail=str(e),
            )
            return

        _settings_dialog_class()._show_feedback_message(
            self,
            success="loading",
            title="Codex 配置",
            summary=f"{client_name} 启动中，请稍候...",
        )
        self._start_client_launch_detection(client_name)

    def _start_client_launch_detection(self, client_name: str) -> None:
        if hasattr(self, "_client_launch_timer") and self._client_launch_timer is not None:
            try:
                self._client_launch_timer.stop()
            except Exception:
                pass

        timer = QTimer(self)
        timer.setInterval(500)

        counter = 0
        max_checks = 40

        def check_status():
            nonlocal counter
            counter += 1
            if check_client_window_loaded(client_name):
                timer.stop()
                _settings_dialog_class()._show_feedback_message(
                    self,
                    success=True,
                    title="Codex 配置",
                    summary=f"{client_name} 启动成功",
                )
                return

            if counter >= max_checks:
                timer.stop()
                _settings_dialog_class()._show_feedback_message(
                    self,
                    success=False,
                    title="Codex 配置",
                    summary=f"{client_name} 启动超时",
                    detail="客户端可能已在后台启动，或需要您手动打开确认。",
                )

        timer.timeout.connect(check_status)
        self._client_launch_timer = timer
        timer.start()

    def _ensure_prompt_settings_popup_state(self) -> None:
        if not hasattr(self, "_prompt_settings_popup"):
            self._prompt_settings_popup = None
        timer = getattr(self, "_prompt_settings_popup_hide_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: _settings_dialog_class()._hide_prompt_settings_popup_if_outside(self))
            self._prompt_settings_popup_hide_timer = timer

    def _cursor_over_prompt_settings_popup_area(self) -> bool:
        for widget in (getattr(self, "_prompt_settings_btn", None), getattr(self, "_prompt_settings_popup", None)):
            if widget is None:
                continue
            try:
                if widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(QCursor.pos())):
                    return True
            except RuntimeError:
                continue
            except Exception:
                pass
        return False

    def _schedule_prompt_settings_popup_hide(self, delay_ms: int = 180) -> None:
        _settings_dialog_class()._ensure_prompt_settings_popup_state(self)
        try:
            self._prompt_settings_popup_hide_timer.start(max(0, int(delay_ms)))
        except Exception:
            pass

    def _hide_prompt_settings_popup_if_outside(self) -> None:
        popup = getattr(self, "_prompt_settings_popup", None)
        try:
            if popup is None or not popup.isVisible():
                self._prompt_settings_popup_hide_timer.stop()
                return
        except RuntimeError:
            self._prompt_settings_popup = None
            self._prompt_settings_popup_hide_timer.stop()
            return
        if _settings_dialog_class()._cursor_over_prompt_settings_popup_area(self):
            _settings_dialog_class()._schedule_prompt_settings_popup_hide(self, 220)
            return
        try:
            if popup is not None:
                popup.close()
            self._prompt_settings_popup_hide_timer.stop()
        except RuntimeError:
            self._prompt_settings_popup = None
        except Exception:
            pass

    def _handle_prompt_settings_popup_hover_event(self, obj, event) -> bool:
        _settings_dialog_class()._ensure_prompt_settings_popup_state(self)
        event_type = event.type()
        if obj is getattr(self, "_prompt_settings_popup", None):
            if event_type in {QEvent.Type.Enter, QEvent.Type.HoverEnter}:
                try:
                    self._prompt_settings_popup_hide_timer.stop()
                except Exception:
                    pass
            elif event_type in {QEvent.Type.Leave, QEvent.Type.HoverLeave, QEvent.Type.Hide, QEvent.Type.Close}:
                _settings_dialog_class()._schedule_prompt_settings_popup_hide(self)
            return False
        if obj is getattr(self, "_prompt_settings_btn", None):
            if event_type in {QEvent.Type.Enter, QEvent.Type.HoverEnter}:
                _settings_dialog_class()._show_prompt_settings_popup(self)
            elif event_type in {QEvent.Type.Leave, QEvent.Type.HoverLeave}:
                _settings_dialog_class()._schedule_prompt_settings_popup_hide(self)
            return False
        return False

    def _show_prompt_settings_popup(self) -> None:
        _settings_dialog_class()._ensure_prompt_settings_popup_state(self)
        anchor = self._prompt_settings_btn
        try:
            self._prompt_settings_popup_hide_timer.stop()
        except Exception:
            pass
        popup = getattr(self, "_prompt_settings_popup", None)
        try:
            if popup is not None and popup.isVisible():
                return
        except RuntimeError:
            popup = None
            self._prompt_settings_popup = None
        popup = OcrGenericMenuPopup(
            _settings_dialog_class()._prompt_action_menu_items(self),
            parent=anchor,
            active_indicator="background",
        )
        popup.setFixedWidth(PROMPT_ACTION_POPUP_WIDTH)
        self._prompt_settings_popup = popup
        popup.destroyed.connect(lambda *_: setattr(self, "_prompt_settings_popup", None))
        popup.installEventFilter(self)
        btn_pos = anchor.mapToGlobal(QPoint(0, 0))
        popup_w = popup.width()
        popup_h = popup.height()
        y = btn_pos.y() - popup_h - 8

        win_pos = self.mapToGlobal(QPoint(0, 0))
        win_w = self.width()
        max_x = win_pos.x() + win_w - popup_w - 12

        x = btn_pos.x() + (anchor.width() - popup_w) // 2
        if x > max_x:
            x = max_x

        popup.show_at_pos(QPoint(x, y))
        _settings_dialog_class()._schedule_prompt_settings_popup_hide(self, 220)

    def _prompt_action_menu_items(self) -> list[tuple[str, Callable[[], None], bool]]:
        names = self._prompt_button_names()

        def schedule(action: str) -> Callable[[], None]:
            return lambda: QTimer.singleShot(0, lambda action=action: self._on_prompt_edit_action(action))

        return [
            (names["reply"], schedule("reply"), True),
            (names["optimize"], schedule("optimize"), True),
            (names["explain"], schedule("explain"), True),
            (names["summarize"], schedule("summarize"), True),
        ]

    def _prompt_button_names(self) -> dict[str, str]:
        translator = normalize_translator_settings(getattr(self, "_translator", {}))
        return {
            "reply": str(translator.get("reply_prompt_button_name", DEFAULT_REPLY_PROMPT_BUTTON_NAME) or DEFAULT_REPLY_PROMPT_BUTTON_NAME),
            "optimize": str(translator.get("ai_search_prompt_button_name", DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME) or DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME),
            "explain": str(translator.get("explain_prompt_button_name", DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME) or DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME),
            "summarize": str(translator.get("summary_prompt_button_name", DEFAULT_SUMMARY_PROMPT_BUTTON_NAME) or DEFAULT_SUMMARY_PROMPT_BUTTON_NAME),
        }

    def _on_prompt_edit_action(self, action: str) -> None:
        if action == "reply":
            self._edit_translator_prompt(
                "reply_prompt",
                "回复提示词",
                DEFAULT_REPLY_PROMPT,
                "reply_prompt_button_name",
                DEFAULT_REPLY_PROMPT_BUTTON_NAME,
            )
        elif action == "optimize":
            self._edit_translator_prompt(
                "ai_search_prompt",
                "搜索提示词",
                DEFAULT_AI_SEARCH_PROMPT,
                "ai_search_prompt_button_name",
                DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
            )
        elif action == "explain":
            self._edit_translator_prompt(
                "explain_prompt",
                "解释提示词",
                DEFAULT_EXPLAIN_PROMPT,
                "explain_prompt_button_name",
                DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
            )
        elif action == "summarize":
            self._edit_translator_prompt(
                "summary_prompt",
                "总结提示词",
                DEFAULT_SUMMARY_PROMPT,
                "summary_prompt_button_name",
                DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
            )

    def _apply_selection_translate_enabled(self) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        enabled = bool(self._selection_translate_switch.isChecked())
        self._translator["selection_translate_enabled"] = enabled
        self._persist_translator_settings()
        try:
            if self._on_selection_translate_changed is not None:
                self._on_selection_translate_changed(enabled)
        except Exception:
            pass

    def _apply_selection_popup_enabled(self) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        popup_enabled = bool(self._selection_popup_switch.isChecked())
        if popup_enabled and not bool(self._selection_translate_switch.isChecked()):
            self._selection_translate_switch.setChecked(True)
        self._translator["selection_popup_enabled"] = bool(popup_enabled)
        self._persist_translator_settings()
        try:
            if self._on_selection_translate_changed is not None:
                self._on_selection_translate_changed(bool(self._selection_translate_switch.isChecked()))
        except Exception:
            pass

    def _apply_ocr_translate_enabled(self) -> None:
        if bool(getattr(self, "_translator_loading", False)):
            return
        enabled = bool(self._ocr_translate_switch.isChecked())
        self._translator["ocr_translate_enabled"] = bool(enabled)
        self._persist_translator_settings()

    def _edit_translator_prompt(
        self,
        key: str,
        title: str,
        default_text: str,
        name_key: str = "",
        default_button_name: str = "",
    ) -> None:
        from deepcat.ui.settings_dialog.sub_dialogs import PromptEditDialog
        current = str(self._translator.get(str(key), default_text) or default_text)
        current_name = str(self._translator.get(str(name_key), default_button_name) or default_button_name)
        dlg = PromptEditDialog(self, title, current, default_text, current_name, default_button_name)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text = dlg.prompt_text() or str(default_text or "")
        self._translator[str(key)] = text
        if name_key:
            self._translator[str(name_key)] = dlg.button_name() or str(default_button_name or "")

        # 将自定义提示词与按钮名称同步存入 SQLite (prompt.db)
        try:
            from deepcat.prompt_store import PromptStore
            store = PromptStore()
            store.save_prompt(str(key), text)
            if name_key:
                store.save_prompt(str(name_key), dlg.button_name() or str(default_button_name or ""))
        except Exception as e:
            logger.error("Failed to save custom prompt to prompt.db: %s", e)

        self._persist_translator_settings()

    def _current_translator_runtime_config(self) -> tuple[str, dict[str, object], bool, str]:
        current_model = str(getattr(self, "_translator_current_model", "") or self._combo_selected_value(self._translator_model)).strip() or "gemini-3.5-flash-thinking"
        cfg = self._translator_field_config(current_model)
        proxy_url = self._translator_proxy_url.text().strip() or "socks5://127.0.0.1:1080"
        return current_model, cfg, bool(cfg.get("use_proxy", False)), proxy_url

    def _clear_translator_proxy_selection(self) -> None:
        try:
            self._translator_proxy_url.deselect()
            self._translator_proxy_url.setCursorPosition(len(self._translator_proxy_url.text()))
            if self._translator_proxy_url.hasFocus():
                self._translator_proxy_url.clearFocus()
        except Exception:
            pass

    def _test_translator_connection(self) -> None:
        if self._translator_test_worker is not None and self._translator_test_worker.isRunning():
            return
        self._clear_translator_proxy_selection()
        current_model, cfg, use_proxy, proxy_url = self._current_translator_runtime_config()
        self._persist_translator_settings()
        self._clear_translator_proxy_selection()
        label_map = {"base_url": "API地址", "model_name": "模型ID", "api_key": "API密钥"}
        missing_keys = [
            key
            for key in self._translator_required_fields(cfg)
            if not str(cfg.get(key, "") or "").strip()
        ]
        if missing_keys:
            _settings_dialog_class()._sync_translator_required_field_highlights(self, set(missing_keys))
            _settings_dialog_class()._focus_first_missing_translator_field(self, missing_keys)
            missing_labels = [label_map.get(key, key) for key in missing_keys]
            try:
                self._translator_test_btn.setToolTip("请先填写：" + "、".join(missing_labels))
            except Exception:
                pass
            return
        _settings_dialog_class()._sync_translator_required_field_highlights(self, set())
        try:
            self._translator_test_btn.setToolTip("")
        except Exception:
            pass
        if self._translator_testing:
            return
        self._translator_testing = True
        self._remember_current_translator_model()
        self._persist_translator_settings()
        self._translator_test_btn.setText("测试中…")
        self._clear_translator_proxy_selection()
        worker = TranslatorConnectionTestWorker(cfg, use_proxy=use_proxy, proxy_url=proxy_url)
        self._translator_test_worker = worker
        worker.tested.connect(self._on_translator_test_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_translator_test_finished(self, success: bool, message: str, elapsed: float) -> None:
        self._translator_testing = False
        self._translator_test_btn.setText("模型测试")
        self._clear_translator_proxy_selection()
        summary = "连通成功" if bool(success) else "测试失败"
        first_line = str(message or "").strip().splitlines()[0] if str(message or "").strip() else ""
        if first_line:
            summary = first_line.split("：", 1)[0].strip() or summary
        _settings_dialog_class()._show_feedback_message(
            self,
            success=bool(success),
            title="模型测试",
            summary=summary,
            detail=f"{message}\n用时：{float(elapsed):.2f}秒",
        )
        self._clear_translator_proxy_selection()
        self._translator_test_worker = None

    def _test_proxy_connection(self) -> None:
        if self._proxy_test_worker is not None and self._proxy_test_worker.isRunning():
            return

        proxy_url = self._translator_proxy_url.text().strip()
        if not proxy_url:
            _settings_dialog_class()._show_feedback_message(
                self,
                success=False,
                title="代理测试",
                summary="测试缺少必要信息",
                detail="请输入代理地址后再进行测试。",
            )
            return

        self._translator_proxy_url_test_btn.setState("testing")

        worker = ProxyConnectionTestWorker(proxy_url)
        self._proxy_test_worker = worker
        worker.tested.connect(self._on_proxy_test_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_proxy_test_finished(self, success: bool, message: str, elapsed: float) -> None:
        self._proxy_test_worker = None
        if success:
            self._translator_proxy_url_test_btn.setState("success", latency=elapsed)
            _settings_dialog_class()._show_feedback_message(
                self,
                success=True,
                title="代理测试",
                summary="测试通过",
                detail=f"{message}\n延迟：{elapsed:.1f}毫秒",
            )
        else:
            self._translator_proxy_url_test_btn.setState("failure", error_msg=message)
            _settings_dialog_class()._show_feedback_message(
                self,
                success=False,
                title="代理测试",
                summary="测试失败",
                detail=f"{message}\n延迟：{elapsed:.1f}毫秒",
            )

    def _persist_translator_settings(self) -> None:
        translator = normalize_translator_settings(self._translator)

        def _mut(s: AppSettings) -> AppSettings:
            return AppSettings(
                version=int(s.version),
                autostart=bool(s.autostart),
                auto_save=bool(getattr(s, "auto_save", True)),
                image_output_dir=str(s.image_output_dir),
                pdf_output_dir=str(s.pdf_output_dir),
                hotkey=str(s.hotkey),
                ui={**dict(s.ui), "translator": translator},
                notifications_enabled=bool(getattr(s, "notifications_enabled", False)),
            )

        self._current = update_settings(_mut, validate_dirs=True)
        self._translator = normalize_translator_settings((self._current.ui or {}).get("translator"))
