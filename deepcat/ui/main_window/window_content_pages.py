from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import sys
import threading
import time
import ctypes
import html
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote, urlparse
from PyQt6.QtCore import (
    Qt,
    QThread,
    QTimer,
    QRect,
    QSize,
    QByteArray,
    QEvent,
    QEventLoop,
    QPoint,
    QDate,
    QDateTime,
    QTime,
    pyqtSignal,
    QUrl,
    QObject,
    QFileInfo,
)
from PyQt6.QtGui import (
    QAction,
    QGuiApplication,
    QIcon,
    QColor,
    QCursor,
    QPainter,
    QPen,
    QImage,
    QKeySequence,
    QPixmap,
    QDesktopServices,
    QTextCharFormat,
    QTextCursor,
    QTextListFormat,
    QTextDocument,
    QFont,
    QBrush,
    QPalette,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QAbstractItemView,
    QButtonGroup,
    QCalendarWidget,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileIconProvider,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMenu,
    QStyle,
    QFrame,
    QCheckBox,
    QLineEdit,
)
from deepcat import __version__
from deepcat.config import Config
from deepcat.input.hotkey_format import pynput_to_qt, qt_to_pynput
from deepcat.input.hotkey_listener import GlobalStartHotkey
from deepcat.settings_store import (
    AppSettings,
    DEFAULT_CAT_REMINDER,
    DEFAULT_EXPLAIN_PROMPT,
    DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
    DEFAULT_AI_SEARCH_PROMPT,
    DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
    DEFAULT_REPLY_PROMPT,
    DEFAULT_REPLY_PROMPT_BUTTON_NAME,
    DEFAULT_SUMMARY_PROMPT,
    DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
    COPY_ON_CAPTURE_MODE_COPY_CLOSE,
    COPY_ON_CAPTURE_MODE_COPY_KEEP,
    COPY_ON_CAPTURE_MODE_OFF,
    DEFAULT_COPY_ON_CAPTURE_MODE,
    decode_qbytearray,
    encode_qbytearray,
    coerce_auto_backup_interval_minutes,
    get_image_output_dir,
    get_pdf_output_dir,
    infer_translator_model_type,
    load_settings,
    normalize_cat_reminder_settings,
    normalize_later_read_settings,
    normalize_feature_visibility,
    normalize_copy_on_capture_mode,
    normalize_todo_items,
    normalize_annotation_style,
    normalize_log_settings,
    normalize_output_dir,
    normalize_translator_settings,
    normalize_updater_settings,
    update_settings,
    update_settings_fields,
    update_ui_settings,
    validate_settings_output_dirs_async,
    validate_output_dir,
)
from deepcat.ui.app_icon import create_app_icon
from deepcat.ui.cat_reminder import CatReminderSession
from deepcat.ui.countdown_overlay import CountdownOverlay
from deepcat.ui.floating_bar import FloatingBar
from deepcat.ui.network_probe import NetworkProbeMonitor
from deepcat.ui.notifications import CaptureNotificationState
from deepcat.ui.popup_behavior import POPUP_EXACT_WIDTH_PROPERTY
from deepcat.ui.settings_dialog import (
    ProviderSwitchHintDelegate,
    SettingsDialog,
    TranslatorConnectionTestWorker,
    ProxyConnectionTestWorker,
    UpdateCheckWorker,
    UpdateDownloadWorker,
)
from deepcat.ui.selection_border_overlay import SelectionBorderOverlay, SelectionShadeOverlay
from deepcat.ui.capture_worker import CaptureWorker
from deepcat.ui.region_overlay import RegionOverlay, SelectedRegion, logical_rect_to_physical_tuple
from deepcat.ui.post_capture_actions import PostCaptureActions, ModernPopupComboBox
from deepcat.ui.tab_list_popup import GroupedNoteListPopup, RoundedListPopup
from deepcat.ui.task_feedback import TaskFeedback
from deepcat.table_notes_store import TableNotesStore, default_ima_config
from deepcat.translation_history_store import TranslationHistoryStore
from deepcat.todo_store import TodoStore
from deepcat.later_read_store import LaterReadStore
from deepcat.drive_cleaner import now_iso
from deepcat.utils.autostart import is_autostart_enabled, set_autostart
from deepcat.utils.crash_reporter import write_crash_breadcrumb
from deepcat.utils.logger import get_log_dir, get_log_file_path, get_logger
from deepcat.utils.paths import get_app_dir

from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND, MAIN_WINDOW_BACKGROUND_COLORREF, MODULE_BACKGROUND, _RESOURCE_SHORTCUT_MAX_ITEMS
from deepcat.ui.main_window.helpers import (
    _date_key,
    _normalize_resource_shortcut_kind,
    _normalize_resource_shortcut_target,
    _normalize_resource_shortcuts,
    _resource_shortcut_default_title,
)
from deepcat.ui.main_window.misc_widgets import (
    RoundedTextEditContainer,
    _DoubleClickLabel,
    _DraggableTile,
    _ModernComboStyle,
    _MouseClickOnlyComboBox,
    _SidebarHoverFilter,
    _SidebarNavButton,
    _SwitchCheckBox,
)
from deepcat.ui.main_window.todo import _TodoCalendarWidget, _TodoEditDialog, _TodoListItemWidget, _TodoReminderPopup, _style_todo_combo_popup_view
from deepcat.ui.main_window.later_read import _LaterReadEditDialog, _LaterReadItemWidget, _LaterReadPinnedFoldToggleWidget
from deepcat.ui.main_window.notifications import NotificationPopup, _CatRestReminderPopup
from deepcat.ui.main_window.tab_password import _TabPasswordSetDialog, _TabPasswordVerifyDialog
from deepcat.ui.main_window.ima import _ImaSettingsDialog, _ImaSyncWorker
from deepcat.ui.main_window.notes import ImagePreviewDialog, ScrollResultPrepareWorker, _NotesEditor, _NotesTable, _ReturnDownDelegate
from deepcat.ui.main_window.drive_cleaner import _DriveCleanerWindow
from deepcat.ui.main_window.resource_shortcuts import _DeleteShortcutPopup, _ResourceShortcutDialog, _TodoResourceWindow
from deepcat.ui.main_window.stashed_captures import _StashedCapturesDialog
from deepcat.ui.main_window.compact import _CompactListWindow, _GroupManageDialog, StyledDialog, StyledInputDialog


# --- Mixin Imports for Physical Splitting ---
from deepcat.ui.main_window.capture import CaptureMixin
from deepcat.ui.main_window.tray import TrayMixin
from deepcat.ui.main_window.translator import TranslatorMixin
from deepcat.ui.main_window.todo import TodoMixin
from deepcat.ui.main_window.later_read import LaterReadMixin
from deepcat.ui.main_window.notes import NotesMixin
from deepcat.ui.main_window.notifications import CatReminderMixin
from deepcat.ui.main_window.resource_shortcuts import ResourceShortcutsMixin


class WindowContentPagesMixin:
    def _initialize_clipboard_history_background(self) -> None:
        try:
            if not self._feature_enabled("clipboard_history"):
                return
            if not hasattr(self, "_clipboard_history_page") or self._clipboard_history_page is None:
                return
            self._clipboard_history_page.initialize(refresh=True)
        except Exception:
            get_logger().exception("后台初始化复制记录失败")
        finally:
            self._refresh_hover_cursor()

    def _build_about_page(self) -> QWidget:
        page = self._make_page("关于", action_widget=self._make_usage_guide_button())
        content_layout: QVBoxLayout = page._content_layout  # type: ignore[attr-defined]
        content_layout.setSpacing(9)
        self._about_status_label = self._make_inline_status_label()
        content_layout.addWidget(self._about_status_label)

        info, form = self._form_card("软件信息")
        info.layout().setContentsMargins(16, 14, 16, 14)
        form.setVerticalSpacing(12)

        repo = QLabel('<a href="https://github.com/pipabcc/deepcat">https://github.com/pipabcc/deepcat</a>')
        repo.setOpenExternalLinks(True)
        repo.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        updater_settings = normalize_updater_settings((getattr(self._app_settings, "ui", {}) or {}).get("updater"))
        self._check_update_btn = QPushButton("检查更新")
        self._check_update_btn.setFixedWidth(86)
        self._auto_update_switch = QCheckBox("自动更新")
        self._auto_update_switch.setChecked(bool(updater_settings.get("auto_update_enabled", False)))
        version_row = self._row(QLabel(str(__version__)), self._check_update_btn, self._auto_update_switch)
        github_row = QWidget()
        github_layout = QHBoxLayout(github_row)
        github_layout.setContentsMargins(0, 0, 0, 0)
        github_layout.setSpacing(8)
        feedback_btn = QPushButton("反馈/问题上报")
        feedback_btn.setObjectName("BtnSmallSecondary")
        feedback_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        feedback_btn.setFixedWidth(112)
        feedback_btn.clicked.connect(self._open_feedback_url)
        github_layout.addWidget(repo)
        github_layout.addStretch(1)
        github_layout.addWidget(feedback_btn)
        form.addRow("软件名称:", QLabel("DeepCat（deepcat.exe）"))
        form.addRow("版本号:", version_row)
        form.addRow("适用平台:", QLabel("Windows 10 / 11"))
        form.addRow("开源协议:", QLabel("GPL-3.0-or-later"))
        form.addRow("GitHub:", github_row)
        content_layout.addWidget(info)

        log_settings = normalize_log_settings(dict(getattr(self._app_settings, "ui", {}) or {}).get("logging"))
        log_card, log_layout = self._card("日志")
        log_card.layout().setContentsMargins(16, 14, 16, 14)
        log_layout.setSpacing(10)

        open_log_btn = QPushButton("打开日志目录")
        open_log_btn.setObjectName("BtnSmallSecondary")
        open_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_log_btn.setFixedWidth(100)
        open_log_btn.clicked.connect(self._open_log_dir)
        self._app_log_switch = QCheckBox("app.log")
        self._app_log_switch.setChecked(bool(log_settings.get("app_log_enabled", False)))
        self._app_log_switch.setToolTip("开启后记录应用错误日志")
        self._crash_log_switch = QCheckBox("crash.log")
        self._crash_log_switch.setChecked(bool(log_settings.get("crash_log_enabled", False)))
        self._crash_log_switch.setToolTip("开启后记录崩溃诊断日志")
        log_desc = QLabel("默认不记录日志；需要排查问题时可临时开启，关闭后将停止写入对应日志文件。")
        log_desc.setWordWrap(True)
        log_desc.setStyleSheet("color: rgba(15, 23, 42, 0.62);")
        log_layout.addWidget(log_desc)
        log_layout.addWidget(self._row(self._app_log_switch, self._crash_log_switch, open_log_btn))
        content_layout.addWidget(log_card)

        privacy_group, privacy_layout = self._card("隐私说明")
        privacy_group.layout().setContentsMargins(16, 14, 16, 14)
        privacy_layout.setSpacing(10)
        privacy_tips = QLabel(
            '<div style="line-height: 1.6; color: #1e293b; font-size: 13px;">'
            'DeepCat 严格遵循本地优先与数据安全原则。软件运行期间产生的所有截图、文本记录与个人配置均仅存储于您的本地设备中。'
            '本软件未内置任何遥测统计或后台数据采集服务，不会主动读取、收集或上传您的任何个人数据与操作行为。'
            '</div>'
        )
        privacy_tips.setWordWrap(True)
        privacy_layout.addWidget(privacy_tips)
        content_layout.addWidget(privacy_group)
        content_layout.addStretch(1)
        return page

    def _open_usage_guide_page(self) -> None:
        """打开表格记事页面，并定位到标题为"DeepCat使用指南"的记事本。

        若未找到该记事本，则创建一个同名记事本，并加载主程序所在目录的
        README.md 作为使用指南内容。
        """
        # 切换到表格记事页面（index 5）
        self._switch_page(5)
        # 确保表格记事页面已初始化
        if not hasattr(self, "_note_tabs") or not self._note_tabs:
            return
        # 切换到记事本模式
        self._switch_table_notes_mode("note")

        # 查找标题为"DeepCat使用指南"的记事本
        target_name = "DeepCat使用指南"
        for idx, tab in enumerate(self._note_tabs):
            if str(tab.get("name", "")).strip() == target_name:
                # 找到目标记事本，切换过去
                if idx != self._active_note_tab:
                    self._on_tab_clicked("note", idx)
                return

        # 未找到目标记事本，创建新记事本并加载 README.md
        self._create_usage_guide_note_from_readme(target_name)

    def _create_usage_guide_note_from_readme(self, note_name: str) -> None:
        """创建名为 note_name 的记事本，并加载主程序目录下 README.md 的内容。"""
        # 读取 README.md 并转换为 HTML
        readme_html = self._load_readme_as_html()
        # 保存当前记事本数据
        self._save_current_note_tab_data()
        # 创建新记事本 tab
        from deepcat.table_notes_store import default_ima_config
        new_index = len(self._note_tabs)
        self._note_tabs.append({
            "name": note_name,
            "html": readme_html,
            "group_name": "",
            "ima_config": default_ima_config(),
        })
        self._active_note_tab = new_index
        self._note_tab_unlocked = True
        # 加载新记事本内容到编辑器
        self._load_table_notes_view_safely(self._load_current_note_tab_data)
        self._refresh_tab_bars()
        self._save_table_notes_settings()
        self._scroll_active_tab_into_view("note")

    def _load_readme_as_html(self) -> str:
        """读取主程序所在目录的 README.md，转换为 HTML 返回。

        读取或转换失败时返回空字符串，绝不抛出异常。
        """
        try:
            from deepcat.utils.paths import get_app_dir
            from deepcat.ui.markdown_renderer import MarkdownRenderer

            readme_path = get_app_dir() / "README.md"
            if not readme_path.exists():
                return ""
            md_text = readme_path.read_text(encoding="utf-8", errors="ignore")
            if not md_text.strip():
                return ""
            return MarkdownRenderer.to_html(md_text)
        except Exception:
            return ""

    def _build_usage_guide_page(self) -> QWidget:
        back_btn = QPushButton("返回关于")
        back_btn.setObjectName("BtnSmallSecondary")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.setIcon(self._asset_icon("icon_action_arrow.svg"))
        back_btn.setIconSize(QSize(14, 14))
        back_btn.setFixedWidth(96)
        back_btn.clicked.connect(lambda *_: self._switch_page(self._ABOUT_PAGE_INDEX))

        page = QWidget()
        page.setObjectName("UsageGuidePage")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)
        outer.addWidget(self._make_header("使用指南", action_widget=back_btn))

        body = QWidget()
        body.setObjectName("UsageGuideBody")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)

        sidebar = QFrame()
        sidebar.setObjectName("UsageGuideSidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar.setFixedWidth(188)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 10, 10, 10)
        sidebar_layout.setSpacing(8)

        self._usage_guide_search = QLineEdit()
        self._usage_guide_search.setObjectName("UsageGuideSearch")
        self._usage_guide_search.setPlaceholderText("搜索功能或操作...")
        self._usage_guide_search.setClearButtonEnabled(True)
        self._usage_guide_search.setFixedHeight(30)
        sidebar_layout.addWidget(self._usage_guide_search)

        self._usage_guide_nav_title = QLabel("目录")
        self._usage_guide_nav_title.setObjectName("UsageGuideNavTitle")
        sidebar_layout.addWidget(self._usage_guide_nav_title)

        self._usage_guide_nav = QListWidget()
        self._usage_guide_nav.setObjectName("UsageGuideNav")
        self._usage_guide_nav.setFrameShape(QFrame.Shape.NoFrame)
        self._usage_guide_nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._usage_guide_nav.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        sidebar_layout.addWidget(self._usage_guide_nav, 1)

        self._usage_guide_scroll = QScrollArea()
        self._usage_guide_scroll.setObjectName("UsageGuideScroll")
        self._usage_guide_scroll.setWidgetResizable(True)
        self._usage_guide_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._usage_guide_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("UsageGuideContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        self._usage_guide_scroll.setWidget(content)

        self._usage_guide_sections: list[dict[str, Any]] = []
        for index, section in enumerate(self._usage_guide_data()):
            section_id = f"usage-guide-{index}"
            card = self._make_usage_guide_section(
                str(section.get("title", "")),
                str(section.get("summary", "")),
                list(section.get("items", []) or []),
            )
            content_layout.addWidget(card)

            item = QListWidgetItem(str(section.get("title", "")))
            item.setData(Qt.ItemDataRole.UserRole, section_id)
            item.setSizeHint(QSize(0, 34))
            self._usage_guide_nav.addItem(item)

            search_text = " ".join(
                [
                    str(section.get("title", "")),
                    str(section.get("summary", "")),
                    str(section.get("keywords", "")),
                    *[str(v) for v in section.get("items", []) or []],
                ]
            ).lower()
            self._usage_guide_sections.append(
                {
                    "id": section_id,
                    "widget": card,
                    "item": item,
                    "search_text": search_text,
                }
            )

        self._usage_guide_empty = QLabel("没有匹配的使用说明")
        self._usage_guide_empty.setObjectName("UsageGuideEmpty")
        self._usage_guide_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._usage_guide_empty.setVisible(False)
        content_layout.addWidget(self._usage_guide_empty)
        content_layout.addStretch(1)

        self._usage_guide_nav.itemClicked.connect(
            lambda item: self._scroll_to_usage_guide_section(str(item.data(Qt.ItemDataRole.UserRole) or ""))
        )
        self._usage_guide_search.textChanged.connect(self._filter_usage_guide)
        if self._usage_guide_nav.count() > 0:
            self._usage_guide_nav.setCurrentRow(0)
        self._set_usage_guide_nav_count(self._usage_guide_nav.count(), self._usage_guide_nav.count(), searching=False)

        body_layout.addWidget(sidebar)
        body_layout.addWidget(self._usage_guide_scroll, 1)
        outer.addWidget(body, 1)

        page.setStyleSheet(
            f"""
            QWidget#UsageGuidePage {{
                background: {MAIN_WINDOW_BACKGROUND};
            }}
            QFrame#UsageGuideSidebar,
            QFrame#UsageGuideSection {{
                background: #ffffff;
                border: 1px solid #e5eaf2;
                border-radius: 8px;
            }}
            QLineEdit#UsageGuideSearch {{
                min-height: 30px;
                border: 1px solid #dbe3ee;
                border-radius: 7px;
                background: #ffffff;
                color: #0f172a;
                padding: 0 9px;
                font-size: 12px;
            }}
            QLineEdit#UsageGuideSearch:hover,
            QLineEdit#UsageGuideSearch:focus {{
                border-color: #94a3b8;
            }}
            QLabel#UsageGuideNavTitle {{
                color: #64748b;
                font-size: 12px;
                font-weight: 800;
                padding-left: 2px;
            }}
            QListWidget#UsageGuideNav {{
                background: transparent;
                border: none;
                outline: none;
                color: #334155;
                font-size: 12px;
            }}
            QListWidget#UsageGuideNav::item {{
                border: none;
                border-radius: 6px;
                padding: 7px 8px;
                margin: 1px 0;
            }}
            QListWidget#UsageGuideNav::item:hover {{
                background: #f1f5f9;
            }}
            QListWidget#UsageGuideNav::item:selected {{
                background: #e0f2fe;
                color: #075985;
                font-weight: 800;
            }}
            QScrollArea#UsageGuideScroll,
            QScrollArea#UsageGuideScroll > QWidget > QWidget,
            QWidget#UsageGuideContent {{
                background: {MAIN_WINDOW_BACKGROUND};
            }}
            QLabel#UsageGuideSectionTitle {{
                color: #0f172a;
                font-size: 15px;
                font-weight: 900;
            }}
            QLabel#UsageGuideSectionSummary {{
                color: #64748b;
                font-size: 12px;
            }}
            QLabel#UsageGuideStepIndex {{
                background: #e0f2fe;
                color: #0369a1;
                border-radius: 11px;
                font-size: 11px;
                font-weight: 900;
            }}
            QLabel#UsageGuideStepText {{
                color: #1e293b;
                font-size: 12px;
            }}
            QLabel#UsageGuideEmpty {{
                color: #64748b;
                background: #ffffff;
                border: 1px solid #e5eaf2;
                border-radius: 8px;
                padding: 22px;
                font-size: 13px;
                font-weight: 700;
            }}
            """
        )
        return page

    def _usage_guide_data(self) -> list[dict[str, Any]]:
        return [
            {
                "title": "功能列表",
                "keywords": "功能列表 功能总览 目录 截图 OCR AI 模型 稍后阅读 复制记录 表格记事 工具 数据管理",
                "summary": "按功能模块快速了解 DeepCat 能做什么，适合第一次打开指南时先扫一遍。",
                "items": [
                    "截图与录屏：框选截图、全屏截图、滚动长截图、截图后保存/复制、录屏和贴图。",
                    "截图后处理：标注、文字、箭头、矩形、模糊遮挡、OCR 识别和识别后继续问答。",
                    "AI 能力：划词翻译、解释、总结、回复、提示词优化、AI 搜索、独立对话和附件图片输入。",
                    "图片生成：通过本地 OpenAI 兼容接口或网页端上游生成图片，并支持参考图附件和结果下载。",
                    "模型管理：维护模型分组、API 地址、密钥、模型 ID、模型别名，并支持填入 Codex 和 Claude Code。",
                    "效率数据：稍后阅读、复制记录、全局超级搜索、表格记事、富文本记事本和 IMA 同步。",
                    "系统工具：磁盘清理、软件残留扫描、大文件/重复文件整理、自定义快捷入口、休息提醒和待办。",
                    "维护管理：数据备份、恢复、WebDAV 自动备份、更新检查、日志开关和隐私说明。",
                ],
            },
            {
                "title": "快速开始",
                "keywords": "首页 初次使用 快捷键 开始 截图 目录",
                "summary": "先确认截图输出、快捷键和模型配置，再按自己的工作流使用快捷入口。",
                "items": [
                    "在“截图设置”选择截图模式、保存模式、输出格式和保存目录。",
                    "在“模型管理”配置翻译模型和问答模型；需要网页端能力时开启本地 API。",
                    "左侧导航可进入复制记录、表格记事、稍后阅读、模型管理、实用工具、截图设置、数据管理和关于。",
                    "左下角快捷按钮可快速开始截图、打开 AI 对话、新建待办或打开截图目录。",
                ],
            },
            {
                "title": "截图、保存与录屏",
                "keywords": "框选截图 全屏截图 保存 复制 输出格式 png jpg pdf 录制 录屏",
                "summary": "用于日常截图、复制、保存和在截图后的工具条中开始录屏。",
                "items": [
                    "点击“开始截图”或使用框选截图快捷键，拖拽选择区域后松开鼠标完成截图。",
                    "在截图设置里切换框选截图、全屏截图或滚动截图，并设置 PNG、JPG、PDF 等输出格式。",
                    "保存模式可选择手动保存或自动保存；勾选自动复制后，截图结果会同步写入剪贴板。",
                    "截图后在浮动工具条点击“录制”可开始录屏，再次点击停止并保存为视频文件。",
                ],
            },
            {
                "title": "滚动截图",
                "keywords": "长截图 滚动截图 自动滚动 手动滚动 CDP 速度 合并",
                "summary": "适合网页、文档、聊天记录等需要向下拼接的长内容。",
                "items": [
                    "在截图设置中选择滚动截图，设置等待策略、反向滚动和增强滚动等参数。",
                    "点击开始截图后框选要捕获的滚动区域，程序会自动滚动并拼接完整图片。",
                    "网页滚动不稳定时可启用 CDP 模式，并按页面提示使用对应端口连接浏览器。",
                    "需要归档时可开启合并 PDF 或合并图片，便于把多段结果整理成单个文件。",
                ],
            },
            {
                "title": "截图后标注与编辑",
                "keywords": "标注 画笔 箭头 矩形 文字 模糊 马赛克 橡皮 贴图 置顶",
                "summary": "截图完成后可直接标注、遮挡敏感内容、复制或固定在屏幕上。",
                "items": [
                    "截图完成后使用工具条进入画笔、箭头、矩形、文字、模糊、橡皮等编辑操作。",
                    "在“截图设置”的标注设置里调整颜色、线宽、线型和按钮组样式。",
                    "点击复制可把结果写入剪贴板，点击保存会按当前目录和格式落盘。",
                    "点击贴图或置顶可把截图固定在屏幕上，便于对照资料或临时记录。",
                ],
            },
            {
                "title": "OCR 与划词工具",
                "keywords": "OCR 识别 翻译 划词 解释 总结 回复 优化 搜索 浮窗",
                "summary": "识别图片文字，或对鼠标划选的文本执行翻译、解释、总结和 AI 搜索。",
                "items": [
                    "截图后点击 OCR 可识别区域内文字，识别结果支持复制、翻译和继续提问。",
                    "在模型管理底部开启“划词开关”和“划词浮窗”，划选文字后即可触发快捷操作。",
                    "划词功能支持翻译、解释、总结、回复、优化和 AI 搜索，相关提示词可在“提示词”中调整。",
                    "勾选“自动复制”后，翻译或问答结果会自动复制到剪贴板。",
                ],
            },
            {
                "title": "AI 对话与附件",
                "keywords": "AI对话 问答 翻译 附件 图片 文件 历史 重新生成 复制",
                "summary": "用于独立问答、带图对话、翻译历史回看和结果复用。",
                "items": [
                    "点击左下角 AI 按钮或模型管理页右上角“AI对话”打开对话窗口。",
                    "输入问题后发送，可按当前问答模型生成回复；需要带图时从输入区添加图片附件。",
                    "对话历史可在窗口内查看、继续、复制、删除或批量整理。",
                    "遇到网络类失败时先检查代理和模型配置，再使用重新发送或重新生成。",
                ],
            },
            {
                "title": "图片生成",
                "keywords": "生图 图片生成 v1 images generations 附件 参考图 下载",
                "summary": "通过本地 OpenAI 兼容接口或 AI 对话面板发起生图，并保存生成结果。",
                "items": [
                    "在支持图片生成的模型配置中填好 API 地址、模型 ID 和密钥。",
                    "调用本地接口 /v1/images/generations 时，提示词会发送给专用生图流程。",
                    "需要参考图时添加图片附件，程序会按网页端上传方式把图片送到上游。",
                    "生成完成后在结果区右键图片可下载，或从对话历史中回看生成记录。",
                ],
            },
            {
                "title": "模型管理",
                "keywords": "模型 分组 API地址 API密钥 模型ID 刷新列表 Codex Claude Code 测试连接",
                "summary": "集中维护翻译模型、问答模型、模型分组和外部工具配置。",
                "items": [
                    "在模型管理中选择分组，填写 API 地址、API 密钥、模型 ID 和模型别名。",
                    "点击刷新模型列表后选择模型 ID，程序会按已有别名自动处理重复后缀。",
                    "分组名称列表中的 Codex 或 Claude Code 默认分组可把当前模型配置写入对应工具配置。",
                    "修改后使用测试连接确认可用，再选择为翻译模型或问答模型。",
                ],
            },
            {
                "title": "提示词、历史与本地 API",
                "keywords": "提示词 本地API Web2API OpenAI兼容 历史记录 代理",
                "summary": "管理划词提示词、本地 OpenAI 兼容服务和历史记录。",
                "items": [
                    "在模型管理底部点击“提示词”，可编辑回复、搜索、解释和总结等默认提示词。",
                    "开启“本地API”后，DeepCat 会提供翻译、问答和图片生成相关的 OpenAI 兼容接口。",
                    "需要网页端上游时，按配置填写代理、模型和密钥，并留意网络错误提示。",
                    "历史记录会保存常用对话和翻译结果，方便之后检索、复制或继续对话。",
                ],
            },
            {
                "title": "稍后阅读",
                "keywords": "稍后阅读 链接 收藏 标题 网站 搜索 排序 已读",
                "summary": "保存临时链接和待读内容，支持搜索、排序和快速打开。",
                "items": [
                    "通过快捷键或页面入口把当前链接保存到稍后阅读。",
                    "列表支持按时间、标题等维度筛选或排序，也可以搜索标题和网址。",
                    "悬停记录可执行打开、复制、删除等操作，批量模式可一次整理多条记录。",
                    "左下角快捷浮窗可打开紧凑列表，适合边工作边取用链接。",
                ],
            },
            {
                "title": "复制记录",
                "keywords": "剪贴板 复制记录 搜索 固定 删除 图片 文本 代码",
                "summary": "自动记录剪贴板内容，方便回找文本、代码片段、图片和文件路径。",
                "items": [
                    "进入“复制记录”可查看最近复制的文本、代码、图片或文件路径。",
                    "使用搜索框定位内容，常用记录可固定到顶部。",
                    "悬停记录可复制、删除或查看详情；批量整理可一次删除多条。",
                    "不想记录的敏感关键词可在相关设置中加入排除列表。",
                ],
            },
            {
                "title": "表格记事",
                "keywords": "表格 记事本 标签页 分组 密码锁 IMA 知识库 图片 附件",
                "summary": "在同一页面维护轻量表格、富文本笔记和分组标签页。",
                "items": [
                    "点击“切换记事本”在表格和记事本之间切换，标签栏可新增、重命名、分组或删除。",
                    "表格支持基础编辑、颜色、删除线、合并单元格和常用格式操作。",
                    "记事本支持富文本、图片、附件、表格插入和右键菜单快捷操作。",
                    "需要保护内容时可启用密码锁；配置 IMA 后可追加到笔记或导入知识库。",
                ],
            },
            {
                "title": "实用工具与休息待办",
                "keywords": "实用工具 磁盘清理 软件卸载 大文件 重复文件 待办 提醒 休息",
                "summary": "提供系统清理、快捷入口、待办提醒和休息提醒。",
                "items": [
                    "在“实用工具”中打开磁盘清理，可扫描临时文件、大文件、重复文件和软件残留。",
                    "执行清理前可先预演，确认候选项后再删除或导出报告。",
                    "休息待办支持新建待办、设置提醒时间、重要提醒、再次提醒和完成状态。",
                    "实用工具页还可添加自定义快捷入口，方便打开常用网址、文件或程序。",
                ],
            },
            {
                "title": "超级搜索",
                "keywords": "超级搜索 全局搜索 复制记录 表格记事 稍后阅读 AI历史",
                "summary": "从左侧顶部搜索框统一检索主要数据。",
                "items": [
                    "在左侧顶部输入关键词，会自动切换到全局超级搜索结果页。",
                    "搜索结果覆盖复制记录、表格记事、稍后阅读和 AI 对话历史。",
                    "每个结果旁边提供复制、跳转或打开操作，便于快速回到原功能页。",
                    "清空搜索框后会回到输入前所在页面。",
                ],
            },
            {
                "title": "数据管理",
                "keywords": "数据管理 备份 恢复 WebDAV 自动备份 清理 导出",
                "summary": "负责本地数据的备份、恢复、自动备份和清理。",
                "items": [
                    "进入“数据管理”可查看应用数据并执行手动备份或恢复。",
                    "配置 WebDAV 后可把备份同步到远端，自动备份按设置的间隔运行。",
                    "恢复前建议先备份当前数据，避免覆盖近期修改。",
                    "清理数据时只处理明确选择的范围，重要资料应先确认备份可用。",
                ],
            },
            {
                "title": "关于、更新与日志",
                "keywords": "关于 更新 日志 app.log crash.log 隐私 GitHub 版本",
                "summary": "查看软件版本、开源信息、隐私说明和排障开关。",
                "items": [
                    "关于页显示软件名称、版本号、适用平台、许可证和 GitHub 地址。",
                    "点击“检查更新”可手动检测新版本；开启自动更新后会按设置自动检查。",
                    "默认不记录日志，排查问题时可临时开启 app.log 或 crash.log。",
                    "隐私说明中会展示本地存储和数据处理原则。",
                ],
            },
            {
                "title": "常见问题",
                "keywords": "问题 错误 网络 代理 快捷键 OCR 图片 附件 模型",
                "summary": "遇到异常时优先从配置、权限、网络和模型能力四个方向排查。",
                "items": [
                    "AI 请求失败时检查代理、API 地址、密钥、模型 ID 和上游服务状态。",
                    "快捷键无效时确认没有被其他软件占用，并重新保存快捷键设置。",
                    "OCR 或截图结果异常时先调整截图范围和等待策略。",
                    "附件图片没有出现在网页端时，确认当前模型支持图片输入，并查看发送时是否真正附加了图片。",
                ],
            },
        ]

    def _make_usage_guide_section(self, title: str, summary: str, items: list[Any]) -> QFrame:
        card = QFrame()
        card.setObjectName("UsageGuideSection")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setObjectName("UsageGuideSectionTitle")
        layout.addWidget(title_label)

        summary_label = QLabel(summary)
        summary_label.setObjectName("UsageGuideSectionSummary")
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)

        for index, text in enumerate(items, start=1):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            index_label = QLabel(str(index))
            index_label.setObjectName("UsageGuideStepIndex")
            index_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            index_label.setFixedSize(22, 22)
            row_layout.addWidget(index_label, 0, Qt.AlignmentFlag.AlignTop)

            text_label = QLabel(str(text))
            text_label.setObjectName("UsageGuideStepText")
            text_label.setWordWrap(True)
            text_label.setMinimumWidth(0)
            row_layout.addWidget(text_label, 1)
            layout.addWidget(row)

        return card

    def _filter_usage_guide(self, text: str) -> None:
        terms = [term for term in re.split(r"\s+", str(text or "").strip().lower()) if term]
        first_visible_id = ""
        first_visible_item: Optional[QListWidgetItem] = None
        has_visible = False
        visible_count = 0
        total_count = len(getattr(self, "_usage_guide_sections", []))

        for entry in getattr(self, "_usage_guide_sections", []):
            haystack = str(entry.get("search_text", ""))
            visible = not terms or all(term in haystack for term in terms)
            if visible:
                visible_count += 1
            widget = entry.get("widget")
            item = entry.get("item")
            if isinstance(widget, QWidget):
                widget.setVisible(visible)
            if isinstance(item, QListWidgetItem):
                item.setHidden(not visible)
                if visible and first_visible_item is None:
                    first_visible_item = item
                    first_visible_id = str(entry.get("id", ""))
            has_visible = has_visible or visible

        empty = getattr(self, "_usage_guide_empty", None)
        if isinstance(empty, QLabel):
            empty.setVisible(not has_visible)

        nav = getattr(self, "_usage_guide_nav", None)
        if isinstance(nav, QListWidget) and first_visible_item is not None:
            nav.blockSignals(True)
            try:
                nav.setCurrentItem(first_visible_item)
            finally:
                nav.blockSignals(False)
        elif isinstance(nav, QListWidget):
            nav.clearSelection()
        self._set_usage_guide_nav_count(visible_count, total_count, searching=bool(terms))
        if first_visible_id:
            self._scroll_to_usage_guide_section(first_visible_id)

    def _set_usage_guide_nav_count(self, visible_count: int, total_count: int, *, searching: bool) -> None:
        label = getattr(self, "_usage_guide_nav_title", None)
        if not isinstance(label, QLabel):
            return
        if searching:
            label.setText(f"目录 · 匹配 {int(visible_count)}/{int(total_count)}")
        else:
            label.setText(f"目录 · 全部 {int(total_count)}")
