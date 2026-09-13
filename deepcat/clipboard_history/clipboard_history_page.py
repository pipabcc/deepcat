from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6.QtCore import QEvent, Qt, QPoint, QRect, QSize, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QClipboard, QColor, QGuiApplication, QDesktopServices, QFontMetrics, QIcon, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from deepcat.clipboard_history.clipboard_database import ClipboardDatabase
from deepcat.clipboard_history.query_worker import ClipboardQuery, ClipboardQueryController
from deepcat.clipboard_history.clipboard_monitor import ClipboardMonitor
from deepcat.clipboard_history.code_runner import is_runnable_web_code, preview_code_in_browser
from deepcat.settings_store import load_settings, save_settings
from deepcat.ui.post_capture_actions import SmoothToolTip, ModernPopupComboBox, OcrGenericMenuPopup
from deepcat.ui.timer_scope import single_shot_scoped
from deepcat.utils.logger import get_logger

logger = get_logger("clipboard_history")

PAGE_SIZE = 50

_TYPE_LABELS: dict[str, Any] = {
    "text": lambda content: f"{len(str(content))}字符",
    "file_path": "文件路径",
    "folder_path": "文件夹",
    "multi_file": "多文件",
    "url": "URL",
    "image": "图片",
}

_CONTENT_TYPES = [
    ("全部分类", ""),
    ("纯文本", "text"),
    ("文件路径", "file_path"),
    ("文件夹", "folder_path"),
    ("多文件", "multi_file"),
    ("URL", "url"),
    ("图片", "image"),
    ("字符数", "char_count"),
]


#: 标题省略计算只取前 N 个字符。列表标题宽度有限（即使窗口最大化也显示不了几百字符），
#: 但剪贴板原文可能长达上万字符；对全文做 elidedText 会让每条记录的每次重绘都变成毫秒级开销。
_TITLE_SOURCE_MAX_CHARS = 1024

_ICON_CACHE: dict[str, QIcon] = {}


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "ui" / "assets"


def _asset_icon(name: str, fallback: Optional[QIcon] = None) -> QIcon:
    """按文件名缓存图标。QIcon 从 SVG 文件构造需要解析磁盘文件，逐条记录重复调用开销明显。"""
    cached = _ICON_CACHE.get(name)
    if cached is not None:
        return cached
    path = _assets_dir() / name
    icon = QIcon(str(path)) if path.exists() else (fallback or QIcon())
    _ICON_CACHE[name] = icon
    return icon


def _format_type_label(content_type: str, content: str) -> str:
    lbl = _TYPE_LABELS.get(content_type, content_type)
    if callable(lbl):
        return lbl(content)
    return str(lbl)


def _format_timestamp(ts: str) -> str:
    try:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(str(ts).strip(), fmt).replace(tzinfo=timezone.utc).astimezone()
                return dt.strftime("%m/%d %H:%M")
            except Exception:
                continue
    except Exception:
        pass
    return str(ts)


def _single_line_preview(text: str) -> str:
    return " ".join(str(text or "").split())


def _title_preview(text: object) -> str:
    """标题省略只会显示很短的前缀；先按上限截断再压缩空白，
    避免对上万字符的剪贴板原文做整串处理拖慢列表刷新。"""
    return _single_line_preview(str(text or "")[:_TITLE_SOURCE_MAX_CHARS * 4])[:_TITLE_SOURCE_MAX_CHARS]


class MonitorToggle(QWidget):
    """监控开关组件（Toggle Switch 样式）"""

    toggled = pyqtSignal(bool)

    def __init__(self, initial_state: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = bool(initial_state)
        self.setFixedSize(42, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if self._checked != checked:
            self._checked = checked
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._checked = not self._checked
            self.update()
            self.toggled.emit(self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRect(1, 2, 40, 20)
        track_color = QColor("#2F3D56") if self._checked else QColor("#cbd5e1")
        painter.setBrush(track_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(track, 10, 10)

        thumb_color = QColor("#ffffff")
        painter.setBrush(thumb_color)
        thumb_x = 21 if self._checked else 3
        painter.drawEllipse(QRect(thumb_x, 4, 16, 16))

        painter.end()


class SearchBar(QWidget):
    """搜索输入框组件"""

    search_changed = pyqtSignal(str)
    search_cleared = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._on_debounce)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._line_edit = QLineEdit()
        self._line_edit.setPlaceholderText("输入文本或路径关键词")
        self._line_edit.setClearButtonEnabled(True)
        self._line_edit.textChanged.connect(self._on_text_changed)
        self._line_edit.setFixedWidth(180)
        self._line_edit.setFixedHeight(30)
        self._line_edit.setStyleSheet("""
            QLineEdit {
                min-height: 30px;
                max-height: 30px;
                padding: 0 10px;
                border: 1px solid #e2e8f0;
                border-radius: 7px;
                background: white;
                color: #1f2937;
                font-size: 13px;
            }
            QLineEdit:hover {
                border-color: #cbd5e1;
            }
            QLineEdit:focus {
                border-color: #94a3b8;
            }
        """)
        layout.addWidget(self._line_edit)

    def _on_text_changed(self, text: str) -> None:
        self._debounce_timer.stop()
        if not text:
            self.search_cleared.emit()
        else:
            self._debounce_timer.start(300)

    def _on_debounce(self) -> None:
        self.search_changed.emit(self._line_edit.text().strip())

    def text(self) -> str:
        return self._line_edit.text().strip()

    def clear_text(self) -> None:
        self._line_edit.clear()


class TypeFilterCombo(ModernPopupComboBox):
    """类型筛选下拉组件"""

    filter_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("matchPopupWidthToParent", True)
        self.setProperty("activeIndicator", "background")
        for label, value in _CONTENT_TYPES:
            self.addItem(label, value)
        self.setMinimumWidth(100)
        self.setFixedHeight(30)
        self.setStyleSheet("""
            QComboBox {
                min-height: 30px;
                max-height: 30px;
                padding: 0 10px;
                border: 1px solid #e2e8f0;
                border-radius: 7px;
                background: white;
                color: #1f2937;
                font-size: 13px;
            }
            QComboBox:hover {
                border-color: #cbd5e1;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border-left-width: 0px;
                border-top-right-radius: 7px;
                border-bottom-right-radius: 7px;
            }
            QComboBox::down-arrow {
                image: url(%s);
                width: 10px;
                height: 10px;
            }
        """ % (str((_assets_dir() / "icon_combo_arrow.svg").resolve()).replace("\\", "/")))
        self.currentIndexChanged.connect(self._on_changed)

    def _on_changed(self, index: int) -> None:
        value = self.itemData(index) or ""
        self.filter_changed.emit(str(value))

    def current_type(self) -> str:
        return str(self.currentData() or "")


class StatisticsBar(QWidget):
    """统计栏组件"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setToolTip("双击该处空白位置打开批量管理")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._label = QLabel("0/0条 0 KB")
        self._label.setToolTip(self.toolTip())
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color: #6b7280; font-size: 12px;")
        layout.addWidget(self._label, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def update_stats(self, current_count: int, total_count: Optional[int], data_size: str) -> None:
        if total_count is None:
            self._label.setText(f"{current_count} 条 | {data_size}")
        else:
            self._label.setText(f"{current_count} / {total_count} 条 | {data_size}")

    def show_pending(self) -> None:
        self._label.setText("正在读取…")


class _RecordItemWidget(QWidget):
    """单条记录展示组件（类似稍后阅读列表项）"""

    run_requested = pyqtSignal(int)
    copy_requested = pyqtSignal(int)
    delete_requested = pyqtSignal(int)
    pin_requested = pyqtSignal(int, bool)
    edit_requested = pyqtSignal(int)
    selection_changed = pyqtSignal(int, bool)
    clicked = pyqtSignal(int)

    def __init__(
        self,
        record_data: tuple,
        *,
        time_text: str,
        copy_icon: QIcon,
        delete_icon: QIcon,
        pin_icon: QIcon,
        edit_icon: QIcon,
        run_icon: Optional[QIcon] = None,
        batch_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._record = record_data
        self._record_id = int(record_data[0])
        self._raw_content = str(record_data[1] or "")
        self._content_type = "text" if str(record_data[2] or "text") == "code_snippet" else str(record_data[2] or "text")
        self._file_path = str(record_data[4] or "")
        self._is_pinned = bool(record_data[11])
        self._batch_mode = bool(batch_mode)
        self._run_icon = run_icon or _asset_icon("icon_code_preview.svg")
        self._copy_icon = copy_icon
        self._pin_icon = pin_icon
        self._edit_icon = edit_icon
        self._delete_icon = delete_icon
        self._is_runnable = is_runnable_web_code(self._raw_content, self._content_type, self._file_path)
        self.setObjectName("ClipboardRecordItem")
        self.setMouseTracking(True)
        self.setFixedHeight(34)
        # 提示窗口是带 Tool 标志的顶层窗口，逐条记录创建代价高。改为首次悬停时再创建。
        self._tooltip: Optional[SmoothToolTip] = None
        # 标题省略结果的缓存键：(宽度, 字体 key)
        self._title_cache_key: Optional[tuple[int, object]] = None

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 2, 3, 2)
        root.setSpacing(5)

        self._checkbox: Optional[QCheckBox] = None

        self._dot = QLabel("•")
        self._dot.setFixedWidth(12)
        self._dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dot_color = "#f59e0b" if self._is_pinned else "#94a3b8"
        self._dot.setStyleSheet(f"color: {self._dot_color}; font-size: 18px; font-weight: 900;")
        self._dot.setMouseTracking(True)
        root.addWidget(self._dot)

        title_source = self._file_path if self._file_path and self._content_type in ("file_path", "folder_path", "image") else self._raw_content
        # 只保留用于省略显示的前缀；完整原文仍保留在 _raw_content / _file_path 中供提示、复制使用。
        self._title_source = _title_preview(title_source)
        self._title_btn = QPushButton("")
        self._title_btn.setObjectName("ClipboardRecordTitleButton")
        self._title_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title_btn.setMinimumWidth(0)
        self._title_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._title_btn.clicked.connect(lambda *_: self.clicked.emit(self._record_id))
        root.addWidget(self._title_btn, 1)

        self._right_box = QWidget()
        self._right_box.setFixedHeight(28)
        self._right_box.setFixedWidth(162)
        root.addWidget(self._right_box)

        right_layout = QHBoxLayout(self._right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        type_text = _format_type_label(self._content_type, self._raw_content)
        self._type_label = QLabel(type_text)
        self._type_label.setObjectName("ClipboardRecordType")
        self._type_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._type_label.setFixedWidth(80)
        right_layout.addWidget(self._type_label)

        self._time = QLabel(str(time_text))
        self._time.setObjectName("ClipboardRecordTime")
        self._time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._time.setFixedWidth(78)
        right_layout.addWidget(self._time)

        self._action_box = QWidget(self._right_box)
        action_layout = QHBoxLayout(self._action_box)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(4)
        self._action_layout = action_layout

        if self._is_runnable:
            self._run_btn = self._create_run_button()
            action_layout.addWidget(self._run_btn)
        else:
            self._run_btn = None

        self._copy_btn = QToolButton()
        self._copy_btn.setObjectName("ClipboardRecordIconButton")
        self._copy_btn.setToolTip("")
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.setFixedSize(22, 22)
        self._copy_btn.clicked.connect(lambda *_: self.copy_requested.emit(self._record_id))
        action_layout.addWidget(self._copy_btn)

        self._pin_btn = QToolButton()
        self._pin_btn.setObjectName("ClipboardRecordIconButton")
        self._pin_btn.setToolTip("")
        self._pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pin_btn.setFixedSize(22, 22)
        self._pin_btn.clicked.connect(lambda *_: self.pin_requested.emit(self._record_id, not self._is_pinned))
        action_layout.addWidget(self._pin_btn)

        self._edit_btn = QToolButton()
        self._edit_btn.setObjectName("ClipboardRecordIconButton")
        self._edit_btn.setToolTip("")
        self._edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_btn.setFixedSize(22, 22)
        self._edit_btn.clicked.connect(lambda *_: self.edit_requested.emit(self._record_id))
        action_layout.addWidget(self._edit_btn)

        self._delete_btn = QToolButton()
        self._delete_btn.setObjectName("ClipboardRecordIconButton")
        self._delete_btn.setToolTip("")
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.setFixedSize(22, 22)
        self._delete_btn.clicked.connect(lambda *_: self.delete_requested.emit(self._record_id))
        action_layout.addWidget(self._delete_btn)

        self._action_box.setFixedSize(self._action_box.sizeHint())
        self._action_height = self._action_box.sizeHint().height()

        self._dot.installEventFilter(self)
        self._title_btn.installEventFilter(self)
        self.ensurePolished()
        self.set_batch_mode(batch_mode)

    def set_batch_mode(self, enabled: bool) -> None:
        """原地切换勾选框，兼容首屏和查询刷新复用的记录控件。"""
        self._batch_mode = bool(enabled)
        if self._batch_mode and self._checkbox is None:
            self._checkbox = QCheckBox(self)
            self._checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
            self._checkbox.stateChanged.connect(
                lambda state: self.selection_changed.emit(self._record_id, bool(state))
            )
            self.layout().insertWidget(0, self._checkbox)
        if self._checkbox is not None:
            was_blocked = self._checkbox.blockSignals(True)
            try:
                self._checkbox.setChecked(False)
                self._checkbox.setVisible(self._batch_mode)
            finally:
                self._checkbox.blockSignals(was_blocked)
        self.hide_tooltip()
        self._set_actions_visible(False)
        self.layout().activate()
        self._refresh_title_text()

    def setChecked(self, checked: bool) -> None:
        if self._checkbox is not None:
            self._checkbox.setChecked(bool(checked) and self._batch_mode)

    def isChecked(self) -> bool:
        if self._checkbox is not None:
            return bool(self._checkbox.isChecked())
        return False

    def record_id(self) -> int:
        return self._record_id

    def _create_run_button(self) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName("ClipboardRecordIconButton")
        btn.setToolTip("在浏览器中运行预览")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(22, 22)
        btn.clicked.connect(lambda *_: self.run_requested.emit(self._record_id))
        return btn

    def _set_run_button_enabled(self, enabled: bool) -> None:
        """按需增删“运行预览”按钮：不同搜索结果的该状态会变化，原地调整避免整表重建。"""
        if enabled and self._run_btn is None:
            self._run_btn = self._create_run_button()
            self._action_layout.insertWidget(0, self._run_btn)
        elif not enabled and self._run_btn is not None:
            button = self._run_btn
            self._run_btn = None
            self._action_layout.removeWidget(button)
            button.setParent(None)
            button.deleteLater()
        self._resize_action_box()

    def _resize_action_box(self) -> None:
        """重算操作区宽度。被临时隐藏的按钮不参与布局计算，因此先临时显示再取布局尺寸，
        保证与 ``__init__`` 中「所有按钮可见」时的结果一致。"""
        layout = self._action_layout
        hidden: list[QWidget] = []
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if widget is not None and widget.isHidden():
                widget.setVisible(True)
                hidden.append(widget)
        hint = layout.sizeHint()
        for widget in hidden:
            widget.setVisible(False)
        self._action_box.setFixedSize(hint.width(), self._action_height)

    def apply_record(self, record_data: tuple, *, time_text: str) -> bool:
        """把本控件原地复用为另一条记录。

        命中时每条记录的成本从「新建约 10 个子控件」降到「改几个文本」。
        """
        record_id = int(record_data[0])
        raw_content = str(record_data[1] or "")
        content_type = "text" if str(record_data[2] or "text") == "code_snippet" else str(record_data[2] or "text")
        file_path = str(record_data[4] or "")
        is_pinned = bool(record_data[11])

        self._record = record_data
        self._record_id = record_id
        self._raw_content = raw_content
        self._content_type = content_type
        self._file_path = file_path
        self._is_pinned = is_pinned

        runnable = is_runnable_web_code(raw_content, content_type, file_path)
        if runnable != self._is_runnable:
            self._is_runnable = runnable
            self._set_run_button_enabled(runnable)
            self._set_actions_visible(self._action_box.isVisible())

        if is_pinned != (self._dot_color == "#f59e0b"):
            self._dot_color = "#f59e0b" if is_pinned else "#94a3b8"
            self._dot.setStyleSheet(f"color: {self._dot_color}; font-size: 18px; font-weight: 900;")

        title_source = file_path if file_path and content_type in ("file_path", "folder_path", "image") else raw_content
        self._title_source = _title_preview(title_source)
        self._title_cache_key = None

        self._type_label.setText(_format_type_label(content_type, raw_content))
        self._time.setText(str(time_text))

        if self._batch_mode and self._checkbox is not None:
            was_blocked = self._checkbox.blockSignals(True)
            try:
                self._checkbox.setChecked(False)
            finally:
                self._checkbox.blockSignals(was_blocked)
        self.hide_tooltip()
        self._refresh_title_text()
        self._position_action_box()
        return True

    def hide_tooltip(self) -> None:
        tip = self._tooltip
        if tip is None:
            return
        try:
            tip.hide()
        except Exception:
            pass

    def _ensure_tooltip(self) -> SmoothToolTip:
        tip = self._tooltip
        if tip is None:
            tip = SmoothToolTip()
            self._tooltip = tip
        return tip

    def _set_actions_visible(self, visible: bool) -> None:
        if self._batch_mode:
            visible = False
        self._position_action_box()
        if self._run_btn is not None:
            self._run_btn.setIcon(self._run_icon if visible else QIcon())
            self._run_btn.setEnabled(bool(visible))
            self._run_btn.setVisible(bool(visible))
        self._copy_btn.setIcon(self._copy_icon if visible else QIcon())
        self._pin_btn.setIcon(self._pin_icon if visible else QIcon())
        self._edit_btn.setIcon(self._edit_icon if visible else QIcon())
        self._delete_btn.setIcon(self._delete_icon if visible else QIcon())
        self._copy_btn.setEnabled(bool(visible))
        self._pin_btn.setEnabled(bool(visible))
        self._edit_btn.setEnabled(bool(visible))
        self._delete_btn.setEnabled(bool(visible))
        self._action_box.setVisible(bool(visible))
        if visible:
            self._action_box.raise_()

    def _position_action_box(self) -> None:
        try:
            self._action_box.move(
                max(0, self._right_box.width() - self._action_box.width()),
                max(0, int((self._right_box.height() - self._action_box.height()) / 2)),
            )
        except Exception:
            pass

    def _refresh_title_text(self, width: Optional[int] = None) -> None:
        target_width = self._title_btn.contentsRect().width() if width is None else int(width)
        # 省略结果只取决于（标题原文, 可用宽度, 字体）。列表滚动/重绘会高频触发本方法，
        # 命中缓存直接返回，避免每条记录每次重绘都重算 elidedText。
        cache_key = (target_width, self._title_btn.font().key())
        if cache_key == self._title_cache_key:
            return
        self._title_cache_key = cache_key
        text = QFontMetrics(self._title_btn.font()).elidedText(
            self._title_source,
            Qt.TextElideMode.ElideRight,
            max(0, target_width),
        )
        if self._title_btn.text() != text:
            self._title_btn.setText(text)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.layout().activate()
        self._refresh_title_text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.layout().activate()
        self._position_action_box()
        self._refresh_title_text()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._title_btn and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
            QEvent.Type.Paint,
        }:
            # 标签页切换、滚动条和字体变化都可能重排标题，必须在当前帧绘制前完成省略。
            self._refresh_title_text()
        if watched is self._dot:
            if event.type() in {QEvent.Type.HoverEnter, QEvent.Type.Enter}:
                main_win = self.window()
                if main_win is not None and main_win.isMinimized():
                    return super().eventFilter(watched, event)
                tip_text = self._raw_content if not self._file_path else self._file_path
                if tip_text:
                    # 截断过长的文本，避免tooltip卡顿
                    if len(tip_text) > 2000:
                        tip_text = tip_text[:2000] + "\n...(内容过长，已截断)"
                    title_top = self._dot.mapToGlobal(QPoint(0, 0))
                    title_bottom = self._dot.mapToGlobal(QPoint(0, self._dot.height()))
                    anchor_x = max(0, int(self._dot.width() / 2))
                    pos = self._dot.mapToGlobal(QPoint(anchor_x, self._dot.height() + 6))
                    direction = "below"
                    parent_widget = self.parentWidget()
                    max_width = 0
                    max_height = 0
                    if parent_widget:
                        max_width = max(100, parent_widget.width() - 20)
                        max_height = max(50, parent_widget.height() - 20)
                    screen = QGuiApplication.screenAt(title_bottom) or QGuiApplication.primaryScreen()
                    if screen is not None:
                        geo = screen.availableGeometry()
                        below_space = int(geo.bottom() - title_bottom.y() - 10)
                        above_space = int(title_top.y() - geo.top() - 10)
                        if below_space < 120 and above_space > below_space:
                            direction = "above"
                            pos = self._dot.mapToGlobal(QPoint(anchor_x, -6))
                            max_height = min(max_height or above_space, max(40, above_space))
                        else:
                            max_height = min(max_height or below_space, max(40, below_space))
                    self._ensure_tooltip().show_text(
                        tip_text,
                        pos,
                        direction=direction,
                        alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        max_width=max_width,
                        max_height=max_height,
                    )
            elif event.type() in {QEvent.Type.HoverLeave, QEvent.Type.Leave}:
                self.hide_tooltip()
        return super().eventFilter(watched, event)

    def enterEvent(self, event) -> None:
        self._set_actions_visible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._set_actions_visible(False)
        super().leaveEvent(event)


class _PinnedFoldToggleWidget(QWidget):
    """置顶项自动折叠/展开的切换控制项"""
    clicked = pyqtSignal()

    def __init__(self, total_count: int, expand: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(30)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 2, 15, 2)
        layout.setSpacing(5)

        self._label = QLabel()
        self._label.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 500;")
        layout.addWidget(self._label, 1, Qt.AlignmentFlag.AlignCenter)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("PinnedFoldToggleWidget")
        self.setStyleSheet("""
            QWidget#PinnedFoldToggleWidget {
                background-color: #f8fafc;
                border-bottom: 1px solid #e2e8f0;
            }
            QWidget#PinnedFoldToggleWidget:hover {
                background-color: #f1f5f9;
            }
        """)
        self.update_state(total_count, expand)

    def update_state(self, total_count: int, expand: bool) -> None:
        if expand:
            self._label.setText(f"展开其余 {total_count - 3} 项置顶记录... ∨")
        else:
            self._label.setText("折叠多余置顶记录... ∧")

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _ClipboardEmptyStateWidget(QWidget):
    def __init__(
        self,
        title: str,
        description: str,
        action_text: str,
        action_callback: Optional[Callable[[], None]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ClipboardEmptyState")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(7)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_label = QLabel(str(title or ""))
        title_label.setObjectName("ClipboardEmptyTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        desc_label = QLabel(str(description or ""))
        desc_label.setObjectName("ClipboardEmptyDescription")
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        action_btn = QPushButton(str(action_text or ""))
        action_btn.setObjectName("ClipboardEmptyAction")
        action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        action_btn.setVisible(bool(action_text))
        if callable(action_callback):
            def run_action() -> None:
                # 刷新会销毁当前空状态列表项。先彻底隐藏控件，避免 Qt 在父项
                # 被移除时把仍处于点击处理中的按钮短暂提升为顶层窗口。
                action_btn.setEnabled(False)
                self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
                self.hide()
                QTimer.singleShot(0, action_callback)

            action_btn.clicked.connect(run_action)
        layout.addWidget(action_btn, alignment=Qt.AlignmentFlag.AlignCenter)


class RecordListWidget(QListWidget):
    """记录列表组件（支持分页加载、批量操作）"""

    run_requested = pyqtSignal(int)
    copy_requested = pyqtSignal(int)
    delete_requested = pyqtSignal(int)
    pin_requested = pyqtSignal(int, bool)
    edit_requested = pyqtSignal(int)
    item_clicked = pyqtSignal(int)
    load_more_requested = pyqtSignal()
    selection_changed = pyqtSignal(int, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ClipboardRecordList")
        self.viewport().setObjectName("ClipboardRecordListViewport")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSpacing(0)
        # 动态属性 hasItems：空状态时为 False，QSS 据此去掉 ::item 的 border-bottom，
        # 避免空状态 item 上下方出现多余分割线（Qt QSS 的 :last 伪类对 QListWidget 不可靠）。
        self.setProperty("hasItems", False)
        self.setStyleSheet("""
            QListWidget#ClipboardRecordList {
                background: #ffffff;
                border: 1px solid #f1f5f9;
                border-radius: 12px;
                color: #111827;
                font-size: 13px;
            }
            QListWidget#ClipboardRecordList::item {
                background: transparent;
                border: none;
                border-bottom: 1px solid #e5e7eb;
                padding: 0px;
            }
            QListWidget#ClipboardRecordList[hasItems="false"]::item {
                border-bottom: none;
            }
            QListWidget#ClipboardRecordList::item:selected {
                color: #111827;
                background: rgba(19, 104, 232, 0.06);
            }
            QWidget#ClipboardRecordItem {
                background: transparent;
            }
            QPushButton#ClipboardRecordTitleButton {
                text-align: left;
                border: none;
                background: transparent;
                color: #111827;
                font-size: 13px;
                font-weight: 400;
                padding: 0px;
            }
            QPushButton#ClipboardRecordTitleButton:hover {
                color: #1368e8;
            }
            QLabel#ClipboardRecordType {
                color: rgba(100, 116, 139, 0.48);
                font-size: 12px;
            }
            QLabel#ClipboardRecordTime {
                color: #64748b;
                font-size: 12px;
            }
            QToolButton#ClipboardRecordIconButton {
                background: rgba(255,255,255,0.86);
                border: 1px solid #d8e0ec;
                border-radius: 6px;
            }
            QToolButton#ClipboardRecordIconButton:disabled {
                background: transparent;
                border-color: transparent;
            }
            QToolButton#ClipboardRecordIconButton:hover {
                background: #eef4ff;
                border-color: #b8cdf5;
            }
            QWidget#ClipboardRecordListViewport {
                background: transparent;
            }
            QWidget#ClipboardEmptyState {
                background: transparent;
            }
            QLabel#ClipboardEmptyTitle {
                color: #1e293b;
                font-size: 14px;
                font-weight: 800;
            }
            QLabel#ClipboardEmptyDescription {
                color: #64748b;
                font-size: 12px;
            }
            QPushButton#ClipboardEmptyAction {
                background: #f1f5f9;
                color: #1e293b;
                border: none;
                border-radius: 8px;
                padding: 5px 14px;
                font-size: 12px;
                font-weight: 800;
                min-height: 26px;
            }
            QPushButton#ClipboardEmptyAction:hover {
                background: #e2e8f0;
                color: #0f172a;
            }
            QPushButton#ClipboardEmptyAction:pressed {
                background: #cbd5e1;
            }
        """)
        self.setUniformItemSizes(True)
        self.viewport().setAutoFillBackground(False)

        self._current_offset = 0
        self._loading_more = False
        self._has_more = True
        self._record_widgets: dict[int, _RecordItemWidget] = {}
        self._batch_mode = False
        self._loaded_records: list[tuple] = []
        self._loaded_pinned: list[tuple] = []
        self._pinned_folded = True
        self._toggle_item = None
        self._empty_item: Optional[QListWidgetItem] = None
        self._empty_title = "还没有复制记录"
        self._empty_description = "开启记录后，复制的文本、图片和文件路径会自动出现在这里。"
        self._empty_action_text = "开启记录"
        self._empty_action_callback: Optional[Callable[[], None]] = None

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        self._add_icon = _asset_icon("icon_action_copy.svg")
        self._del_icon = _asset_icon("icon_todo_delete.svg")
        self._pin_icon = _asset_icon("icon_action_pin.svg")
        self._edit_icon = _asset_icon("icon_todo_edit.svg")
        self._run_icon = _asset_icon("icon_code_preview.svg")

    def set_empty_state(
        self,
        title: str,
        description: str,
        action_text: str,
        action_callback: Optional[Callable[[], None]],
    ) -> None:
        self._empty_title = str(title or "")
        self._empty_description = str(description or "")
        self._empty_action_text = str(action_text or "")
        self._empty_action_callback = action_callback

    def set_batch_mode(self, enabled: bool) -> None:
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        enabled = bool(enabled)
        if self._batch_mode == enabled:
            return
        self._batch_mode = enabled
        for widget in self._record_widgets.values():
            widget.set_batch_mode(enabled)
        self._sync_item_widget_layout()

    def load_records(self, records: list[tuple], pinned: list[tuple]) -> None:
        self._hide_record_tooltips()
        # 行数与置顶折叠结构不变时原地复用行控件（搜索输入过程中最常见），
        # 避免每次关键词变化都重建 50 个记录控件阻塞主线程。
        try:
            if self._update_records_in_place(records, pinned):
                return
        except Exception as error:
            logger.warning("原地刷新复制记录行失败，回退整表重建：%s", error)
        self._loaded_records = list(records)
        self._loaded_pinned = list(pinned)

        self._pinned_folded = True

        self._add_icon = _asset_icon("icon_action_copy.svg")
        self._del_icon = _asset_icon("icon_todo_delete.svg")
        self._pin_icon = _asset_icon("icon_action_pin.svg")
        self._edit_icon = _asset_icon("icon_todo_edit.svg")

        self._rebuild_list()

        self._current_offset = len(records)
        self._has_more = len(records) >= PAGE_SIZE
        self._loading_more = False
        self._schedule_layout_sync()

    def _update_records_in_place(self, records: list[tuple], pinned: list[tuple]) -> bool:
        """结构一致时原地刷新行内容；不满足条件时返回 False，由调用方走整表重建。"""
        total = len(pinned) + len(records)
        if total <= 0 or self._empty_item is not None:
            return False
        if total != len(self._record_widgets):
            return False
        if (len(pinned) > 3) != (self._toggle_item is not None):
            return False

        widgets: list[_RecordItemWidget] = []
        for i in range(self.count()):
            widget = self.itemWidget(self.item(i))
            if isinstance(widget, _RecordItemWidget):
                widgets.append(widget)
        if len(widgets) != total:
            return False

        # 置顶行在前，普通行在后，与 _rebuild_list 的插入顺序一致。
        ordered = list(pinned) + list(records)
        refreshed: dict[int, _RecordItemWidget] = {}
        for widget, record in zip(widgets, ordered):
            if not widget.apply_record(record, time_text=_format_timestamp(str(record[7] or ""))):
                return False
            refreshed[int(record[0])] = widget

        self._loaded_pinned = list(pinned)
        self._loaded_records = list(records)
        self._record_widgets = refreshed
        self._pinned_folded = True
        self._current_offset = len(records)
        self._has_more = len(records) >= PAGE_SIZE
        self._loading_more = False
        self._update_has_items_state(has_items=True)
        self._apply_fold_visibility()
        self._schedule_layout_sync()
        return True

    def append_records(self, records: list[tuple]) -> None:
        loaded_ids = {record[0] for record in self._loaded_records}
        for rec in records:
            if rec[0] not in loaded_ids:
                loaded_ids.add(rec[0])
                self._loaded_records.append(rec)
                self._add_record_item(rec)
        self._current_offset += len(records)
        if len(records) < PAGE_SIZE:
            self._has_more = False
        self._loading_more = False
        self._schedule_layout_sync()

    def prepend_record(self, record: tuple) -> None:
        is_pinned = bool(record[11]) if len(record) > 11 else False
        if is_pinned:
            existing_ids = {r[0] for r in self._loaded_pinned}
            if record[0] not in existing_ids:
                self._loaded_pinned.insert(0, record)

            self._add_record_item(record, index=0)
            self._sync_toggle_widget_presence()
            self._apply_fold_visibility()
        else:
            existing_ids = {r[0] for r in self._loaded_records}
            if record[0] not in existing_ids:
                self._loaded_records.insert(0, record)

            pinned_count = len(self._loaded_pinned)
            insert_idx = pinned_count
            if pinned_count > 3:
                insert_idx += 1

            self._add_record_item(record, index=insert_idx)
            self._sync_toggle_widget_presence()
            self._apply_fold_visibility()

    def remove_record(self, record_id: int) -> None:
        self._loaded_pinned = [r for r in self._loaded_pinned if int(r[0]) != record_id]
        self._loaded_records = [r for r in self._loaded_records if int(r[0]) != record_id]

        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if isinstance(widget, _RecordItemWidget) and widget.record_id() == record_id:
                self.takeItem(i)
                self._record_widgets.pop(record_id, None)
                break

        self._sync_toggle_widget_presence()
        self._apply_fold_visibility()
        if not self._loaded_pinned and not self._loaded_records:
            self.clear()
            self._record_widgets.clear()
            self._toggle_item = None
            self._empty_item = None
            self._add_empty_state_item()

    def clear_records(self) -> None:
        self._hide_record_tooltips()
        self.clear()
        self._record_widgets.clear()
        self._loaded_records = []
        self._loaded_pinned = []
        self._toggle_item = None
        self._empty_item = None
        self._current_offset = 0
        self._has_more = False
        self._loading_more = False

    def selected_record_ids(self) -> list[int]:
        ids: list[int] = []
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if isinstance(widget, _RecordItemWidget) and widget.isChecked():
                ids.append(widget.record_id())
        return ids

    def record_count(self) -> int:
        """返回真实记录数，不包含空状态和置顶折叠控制项。"""
        return len(self._record_widgets)

    def _rebuild_list(self) -> None:
        self._hide_record_tooltips()
        self.clear()
        self._record_widgets.clear()
        self._toggle_item = None
        self._empty_item = None

        pinned = self._loaded_pinned
        records = self._loaded_records

        for rec in pinned:
            self._add_record_item(rec)

        if len(pinned) > 3:
            self._add_fold_toggle_item(len(pinned))

        for rec in records:
            self._add_record_item(rec)

        if not pinned and not records:
            self._add_empty_state_item()
        else:
            self._update_has_items_state(has_items=True)

        self._apply_fold_visibility()

    def _add_empty_state_item(self) -> None:
        row = QListWidgetItem()
        row.setFlags(Qt.ItemFlag.NoItemFlags)
        row.setSizeHint(QSize(0, 132))
        self.addItem(row)
        self._empty_item = row
        self.setItemWidget(
            row,
            _ClipboardEmptyStateWidget(
                self._empty_title,
                self._empty_description,
                self._empty_action_text,
                self._empty_action_callback,
                self,
            ),
        )
        self._update_has_items_state(has_items=False)
        self._schedule_layout_sync()

    def _update_has_items_state(self, *, has_items: bool) -> None:
        """切换 hasItems 动态属性并刷新 QSS，控制空状态下 ::item 的 border-bottom 显隐。"""
        if self.property("hasItems") == has_items:
            return
        self.setProperty("hasItems", has_items)
        # 属性变化后需 unpolish/polish 触发 QSS 重新求值
        try:
            self.style().unpolish(self)
            self.style().polish(self)
        except Exception:
            pass

    def _add_fold_toggle_item(self, total_count: int) -> None:
        row = QListWidgetItem()
        row.setSizeHint(QSize(0, 30))
        self.addItem(row)
        self._toggle_item = row
        widget = _PinnedFoldToggleWidget(total_count, self._pinned_folded)
        widget.clicked.connect(self._toggle_pinned_fold)
        self.setItemWidget(row, widget)

    def _toggle_pinned_fold(self) -> None:
        self._pinned_folded = not self._pinned_folded
        self._apply_fold_visibility()

    def _sync_toggle_widget_presence(self) -> None:
        pinned_count = len(self._loaded_pinned)
        if pinned_count > 3:
            if self._toggle_item is None:
                row = QListWidgetItem()
                row.setSizeHint(QSize(0, 30))
                self.insertItem(pinned_count, row)
                self._toggle_item = row
                widget = _PinnedFoldToggleWidget(pinned_count, self._pinned_folded)
                widget.clicked.connect(self._toggle_pinned_fold)
                self.setItemWidget(row, widget)
            else:
                current_idx = self.row(self._toggle_item)
                if current_idx != pinned_count and current_idx != -1:
                    self.takeItem(current_idx)
                    self.insertItem(pinned_count, self._toggle_item)
                    widget = _PinnedFoldToggleWidget(pinned_count, self._pinned_folded)
                    widget.clicked.connect(self._toggle_pinned_fold)
                    self.setItemWidget(self._toggle_item, widget)
        else:
            if self._toggle_item is not None:
                idx = self.row(self._toggle_item)
                if idx != -1:
                    self.takeItem(idx)
                self._toggle_item = None
            else:
                for i in range(self.count()):
                    item = self.item(i)
                    widget = self.itemWidget(item)
                    if isinstance(widget, _PinnedFoldToggleWidget):
                        self.takeItem(i)
                        break

    def _apply_fold_visibility(self) -> None:
        pinned_count = len(self._loaded_pinned)
        # Handle showing all pinned items if pinned count is <= 3, and make sure they are visible
        if pinned_count <= 3:
            for i in range(pinned_count):
                self.setRowHidden(i, False)
            return

        hide_excess = self._pinned_folded
        for i in range(3, pinned_count):
            self.setRowHidden(i, hide_excess)

        if self._toggle_item is not None:
            widget = self.itemWidget(self._toggle_item)
            if isinstance(widget, _PinnedFoldToggleWidget):
                widget.update_state(pinned_count, self._pinned_folded)

        self._schedule_layout_sync()

    def _add_record_item(self, record: tuple, index: int = -1) -> None:
        if self._empty_item is not None:
            empty_row = self.row(self._empty_item)
            if empty_row >= 0:
                self.takeItem(empty_row)
            self._empty_item = None

        if not hasattr(self, "_add_icon") or self._add_icon is None:
            self._add_icon = _asset_icon("icon_action_copy.svg")
            self._del_icon = _asset_icon("icon_todo_delete.svg")
            self._pin_icon = _asset_icon("icon_action_pin.svg")
            self._edit_icon = _asset_icon("icon_todo_edit.svg")
            self._run_icon = _asset_icon("icon_code_preview.svg")

        record_id = int(record[0])
        row = QListWidgetItem()
        row.setSizeHint(QSize(0, 34))
        if index < 0:
            self.addItem(row)
        else:
            self.insertItem(index, row)
        widget = _RecordItemWidget(
            record,
            time_text=_format_timestamp(str(record[7] or "")),
            copy_icon=self._add_icon,
            delete_icon=self._del_icon,
            pin_icon=self._pin_icon,
            edit_icon=self._edit_icon,
            run_icon=self._run_icon,
            batch_mode=self._batch_mode,
        )
        widget.run_requested.connect(self.run_requested.emit)
        widget.copy_requested.connect(self.copy_requested.emit)
        widget.delete_requested.connect(self.delete_requested.emit)
        widget.pin_requested.connect(self.pin_requested.emit)
        widget.edit_requested.connect(self.edit_requested.emit)
        widget.clicked.connect(self.item_clicked.emit)
        widget.selection_changed.connect(self.selection_changed.emit)
        self.setItemWidget(row, widget)
        self._record_widgets[record_id] = widget

    def _schedule_layout_sync(self) -> None:
        QTimer.singleShot(0, self._sync_item_widget_layout)
        QTimer.singleShot(80, self._sync_item_widget_layout)

    def _sync_item_widget_layout(self) -> None:
        try:
            self.doItemsLayout()
            self.updateGeometries()
            for widget in list(self._record_widgets.values()):
                try:
                    widget.updateGeometry()
                    widget._refresh_title_text(widget._title_btn.contentsRect().width())
                    widget._position_action_box()
                    widget.update()
                except RuntimeError:
                    continue
                except Exception:
                    continue
            self.viewport().update()
        except Exception:
            pass

    def _hide_record_tooltips(self) -> None:
        for widget in list(self._record_widgets.values()):
            try:
                widget.hide_tooltip()
            except RuntimeError:
                continue
            except Exception:
                continue

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_item_widget_layout()
        self._schedule_layout_sync()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._schedule_layout_sync()

    def _on_scroll(self, value: int) -> None:
        self._hide_record_tooltips()
        if not self._has_more or self._loading_more:
            return
        scrollbar = self.verticalScrollBar()
        if scrollbar.maximum() > 0 and value >= scrollbar.maximum() - 4:
            self._loading_more = True
            self.load_more_requested.emit()

    def leaveEvent(self, event) -> None:
        self._hide_record_tooltips()
        super().leaveEvent(event)

    def _show_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        widget = self.itemWidget(item)
        if not isinstance(widget, _RecordItemWidget):
            return

        record_id = widget.record_id()
        is_pinned = widget._is_pinned
        content_type = widget._content_type
        is_runnable = getattr(widget, "_is_runnable", False)

        self._hide_record_tooltips()
        menu_items = []
        if is_runnable:
            menu_items.append(("在浏览器中运行预览", lambda rid=record_id: self.run_requested.emit(rid), True))
        menu_items.extend([
            ("复制", lambda rid=record_id: self.copy_requested.emit(rid), True),
            ("编辑", lambda rid=record_id: self.edit_requested.emit(rid), True),
        ])
        if content_type in ("file_path", "image"):
            menu_items.append(("打开文件", lambda rid=record_id: self._open_file(rid), True))
            menu_items.append(("打开文件夹", lambda rid=record_id: self._open_folder(rid), True))
        elif content_type in ("folder_path", "multi_file"):
            menu_items.append(("打开文件夹", lambda rid=record_id: self._open_folder(rid), True))
        menu_items.extend([
            ("-", None, False),
            ("取消置顶" if is_pinned else "置顶", lambda rid=record_id, pinned=is_pinned: self.pin_requested.emit(rid, not pinned), True),
            ("-", None, False),
            ("删除", lambda rid=record_id: self.delete_requested.emit(rid), True),
        ])

        popup = OcrGenericMenuPopup(menu_items, parent=self)
        popup.show_at_pos(self.viewport().mapToGlobal(pos))

    def _record_paths(self, widget: _RecordItemWidget) -> list[str]:
        content_type = str(getattr(widget, "_content_type", "") or "")
        if content_type == "multi_file":
            paths: list[str] = []
            try:
                import json

                record = getattr(widget, "_record", ())
                metadata = record[13] if len(record) > 13 else {}
                if isinstance(metadata, str):
                    metadata = json.loads(metadata or "{}")
                if isinstance(metadata, dict):
                    files = metadata.get("files")
                    if isinstance(files, list):
                        paths.extend(str(x) for x in files if str(x or "").strip())
            except Exception:
                paths = []
            if not paths:
                paths = [line.strip() for line in str(getattr(widget, "_raw_content", "") or "").splitlines() if line.strip()]
            return paths
        path = str(getattr(widget, "_file_path", "") or "").strip()
        if path:
            return [path]
        raw = str(getattr(widget, "_raw_content", "") or "").strip()
        return [raw] if raw else []

    def _open_file(self, record_id: int) -> None:
        widget = self._record_widgets.get(record_id)
        if widget is None:
            return
        paths = self._record_paths(widget)
        if not paths:
            return
        try:
            import os
            path = paths[0]
            if os.path.isfile(path):
                os.startfile(path)
        except Exception as e:
            logger.error(f"Open file failed: {e}")

    def _open_folder(self, record_id: int) -> None:
        widget = self._record_widgets.get(record_id)
        if widget is None:
            return
        paths = self._record_paths(widget)
        if not paths:
            return
        try:
            import os

            seen: set[str] = set()
            for path in paths:
                folder = path if os.path.isdir(path) else os.path.dirname(path)
                if not folder:
                    continue
                folder = os.path.abspath(folder)
                key = folder.lower()
                if key in seen:
                    continue
                seen.add(key)
                if os.path.isdir(folder):
                    os.startfile(folder)
                    return
        except Exception as e:
            logger.error(f"Open folder failed: {e}")


class _EditRecordDialog(QDialog):
    """编辑记录对话框（支持 Ctrl+F 搜索定位定位）"""

    def __init__(self, content: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {MAIN_WINDOW_BACKGROUND};
            }}
        """)
        self.setWindowTitle("编辑记录")
        self.setMinimumSize(450, 450)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self._text_edit = QTextEdit()
        self._text_edit.setPlainText(content)
        self._text_edit.setStyleSheet("""
            QTextEdit {
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 6px;
                font-size: 13px;
                color: #111827;
                background-color: white;
            }
            QTextEdit:hover {
                border: 1px solid #cbd5e1;
            }
            QTextEdit:focus {
                border: 1px solid #94a3b8;
            }
        """)
        layout.addWidget(self._text_edit)

        # ------------------ Ctrl+F 搜索定位面板 ------------------
        self._search_panel = QWidget()
        self._search_panel.setVisible(False)
        self._search_panel.setStyleSheet("""
            QWidget {
                background-color: #f9fafb;
                border: 1px solid #e5e7eb;
                border-radius: 6px;
            }
        """)
        search_layout = QHBoxLayout(self._search_panel)
        search_layout.setContentsMargins(8, 4, 8, 4)
        search_layout.setSpacing(6)

        search_icon = QLabel("🔍")
        search_icon.setStyleSheet("border: none; background: transparent; font-size: 12px;")
        search_layout.addWidget(search_icon)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("输入关键字搜索... (Enter 下一个 / Shift+Enter 上一个)")
        self._search_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 12px;
                background-color: white;
                color: #111827;
            }
            QLineEdit:hover {
                border: 1px solid #cbd5e1;
            }
            QLineEdit:focus {
                border: 1px solid #94a3b8;
            }
        """)
        search_layout.addWidget(self._search_input, 1)

        self._search_status = QLabel("")
        self._search_status.setStyleSheet("border: none; background: transparent; color: #ef4444; font-size: 12px;")
        search_layout.addWidget(self._search_status)

        self._prev_btn = QPushButton("上一个")
        self._prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_btn.setStyleSheet("""
            QPushButton {
                background: #1e293b;
                border: 1px solid #1e293b;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                color: white;
            }
            QPushButton:hover {
                background: #334155;
                border-color: #334155;
            }
            QPushButton:pressed {
                background: #0f172a;
                border-color: #0f172a;
            }
        """)
        search_layout.addWidget(self._prev_btn)

        self._next_btn = QPushButton("下一个")
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.setStyleSheet("""
            QPushButton {
                background: #1e293b;
                border: 1px solid #1e293b;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                color: white;
            }
            QPushButton:hover {
                background: #334155;
                border-color: #334155;
            }
            QPushButton:pressed {
                background: #0f172a;
                border-color: #0f172a;
            }
        """)
        search_layout.addWidget(self._next_btn)

        self._close_btn = QToolButton()
        self._close_btn.setText("✕")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setStyleSheet("""
            QToolButton {
                border: none;
                background: transparent;
                color: #9ca3af;
                font-size: 12px;
                font-weight: bold;
                padding: 2px;
            }
            QToolButton:hover {
                color: #4b5563;
            }
        """)
        search_layout.addWidget(self._close_btn)

        layout.addWidget(self._search_panel)
        # --------------------------------------------------------

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self.accept)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(save_btn)
        layout.addLayout(button_row)

        # 样式表微调，按钮符合整体现代风格
        save_btn.setStyleSheet("""
            QPushButton {
                background: #1e293b;
                border: 1px solid #1e293b;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 13px;
                color: white;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #334155;
                border-color: #334155;
            }
            QPushButton:pressed {
                background: #0f172a;
                border-color: #0f172a;
            }
        """)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: #1e293b;
                border: 1px solid #1e293b;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 13px;
                color: white;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #334155;
                border-color: #334155;
            }
            QPushButton:pressed {
                background: #0f172a;
                border-color: #0f172a;
            }
        """)

        # 绑定信号
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._find_next)
        self._prev_btn.clicked.connect(self._find_prev)
        self._next_btn.clicked.connect(self._find_next)
        self._close_btn.clicked.connect(self._hide_search_panel)

        # 注册全局 Ctrl+F 快捷键
        from PyQt6.QtGui import QKeySequence, QShortcut
        self._shortcut_find = QShortcut(QKeySequence("Ctrl+F"), self)
        self._shortcut_find.activated.connect(self._show_search_panel)

        # 注册输入框内的 Shift+Enter 快捷键查找上一个
        self._shortcut_prev = QShortcut(QKeySequence("Shift+Return"), self._search_input)
        self._shortcut_prev.activated.connect(self._find_prev)

    def keyPressEvent(self, event) -> None:
        # 当搜索面板显示时，按 Esc 键仅关闭搜索面板，避免直接关闭编辑框丢失修改
        if event.key() == Qt.Key.Key_Escape and self._search_panel.isVisible():
            self._hide_search_panel()
            event.accept()
            return
        super().keyPressEvent(event)

    def _show_search_panel(self) -> None:
        self._search_panel.setVisible(True)
        self._search_input.setFocus()
        self._search_input.selectAll()
        # 打开面板时如果输入框有内容，立即高亮
        if self._search_input.text():
            self._on_search_text_changed(self._search_input.text())

    def _hide_search_panel(self) -> None:
        self._search_panel.setVisible(False)
        self._text_edit.setFocus()

    def _on_search_text_changed(self, text: str) -> None:
        if not text:
            self._search_status.setText("")
            self._search_input.setStyleSheet("""
                QLineEdit {
                    border: 1px solid #d1d5db;
                    border-radius: 4px;
                    padding: 3px 6px;
                    font-size: 12px;
                    background-color: white;
                    color: #111827;
                }
                QLineEdit:focus {
                    border: 1px solid #1368e8;
                }
            """)
            return

        # 当输入改变时，将光标设到当前视口最前，开始匹配第一个
        cursor = self._text_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self._text_edit.setTextCursor(cursor)

        found = self._text_edit.find(text)
        self._update_search_ui_status(found)

    def _find_next(self) -> None:
        query = self._search_input.text()
        if not query:
            return

        # 尝试向下查找
        found = self._text_edit.find(query)
        if not found:
            # 循环查找：移至文档开头再查一次
            cursor = self._text_edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            self._text_edit.setTextCursor(cursor)
            found = self._text_edit.find(query)

        self._update_search_ui_status(found)

    def _find_prev(self) -> None:
        query = self._search_input.text()
        if not query:
            return

        from PyQt6.QtGui import QTextDocument
        # 尝试向上查找
        found = self._text_edit.find(query, QTextDocument.FindFlag.FindBackward)
        if not found:
            # 循环查找：移至文档结尾再查一次
            cursor = self._text_edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self._text_edit.setTextCursor(cursor)
            found = self._text_edit.find(query, QTextDocument.FindFlag.FindBackward)

        self._update_search_ui_status(found)

    def _update_search_ui_status(self, found: bool) -> None:
        if not self._search_input.text():
            self._search_status.setText("")
            self._search_input.setStyleSheet("""
                QLineEdit {
                    border: 1px solid #d1d5db;
                    border-radius: 4px;
                    padding: 3px 6px;
                    font-size: 12px;
                    background-color: white;
                    color: #111827;
                }
                QLineEdit:focus {
                    border: 1px solid #1368e8;
                }
            """)
            return

        if found:
            self._search_status.setText("")
            self._search_input.setStyleSheet("""
                QLineEdit {
                    border: 1px solid #10b981;
                    border-radius: 4px;
                    padding: 3px 6px;
                    font-size: 12px;
                    background-color: #ecfdf5;
                    color: #065f46;
                }
            """)
        else:
            self._search_status.setText("未找到匹配项")
            self._search_input.setStyleSheet("""
                QLineEdit {
                    border: 1px solid #f87171;
                    border-radius: 4px;
                    padding: 3px 6px;
                    font-size: 12px;
                    background-color: #fee2e2;
                    color: #991b1b;
                }
            """)

    def content(self) -> str:
        return self._text_edit.toPlainText()

    def _apply_caption_color(self) -> None:
        try:
            import os
            import ctypes
            if os.name != "nt":
                return
            from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND_COLORREF
            dwmapi = ctypes.windll.dwmapi
            hwnd = int(self.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            dwmapi.DwmSetWindowAttribute(
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


class ClipboardHistoryPage(QWidget):
    """复制记录标签页（主入口）"""

    capture_requested = pyqtSignal()
    pin_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._database: Optional[ClipboardDatabase] = None
        self._monitor: Optional[ClipboardMonitor] = None
        self._current_keyword = ""
        self._current_type_filter = ""
        self._is_page_visible = False
        self._batch_mode = False
        self._initialized = False
        self._query_controller = ClipboardQueryController(self)
        self._query_controller.completed.connect(self._on_query_completed)
        self._query_controller.failed.connect(self._on_query_failed)
        self._queries_suspended = False
        self._dirty = True

        self._build_ui()
        self._prepare_initial_view()

    def _prepare_initial_view(self) -> None:
        snapshot = ClipboardDatabase.read_initial_snapshot(limit=PAGE_SIZE)
        if snapshot is None:
            self._record_list.set_empty_state("正在读取复制记录", "记录准备好后会自动显示。", "", None)
            self._record_list.load_records([], [])
            self._statistics_bar.show_pending()
            return
        self._record_list.set_empty_state(*self._clipboard_empty_state_config())
        self._record_list.load_records(snapshot["records"], snapshot["pinned"])
        self._record_list._has_more = snapshot["has_more"]
        total, data_size = snapshot["statistics"]
        self._statistics_bar.update_stats(self._record_list.record_count(), total, data_size)

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

    def _show_inline_status(
        self,
        text: str,
        *,
        tone: str = "info",
        auto_hide_ms: int = 2600,
    ) -> None:
        label = getattr(self, "_inline_status_label", None)
        if not isinstance(label, QLabel):
            return
        message = str(text or "").strip()
        if not message:
            label.hide()
            return
        parent = self._prepare_floating_status_label(label)
        if parent is None:
            return
        palette = {
            "success": ("#166534", "#f0fdf4", "#bbf7d0"),
            "error": ("#991b1b", "#fef2f2", "#fecaca"),
            "warning": ("#92400e", "#fffbeb", "#fde68a"),
            "info": ("#334155", "#f8fafc", "#cbd5e1"),
        }
        color, background, border = palette.get(str(tone or "info"), palette["info"])
        token = int(label.property("statusToken") or 0) + 1
        label.setProperty("statusToken", token)
        label.setText(message)
        label.setStyleSheet(
            "QLabel#ClipboardInlineStatus {"
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
        if int(auto_hide_ms or 0) > 0:
            single_shot_scoped(
                int(auto_hide_ms),
                label,
                lambda current=token, target=label: target.hide()
                if isinstance(target, QLabel) and int(target.property("statusToken") or 0) == current
                else None,
            )

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        # Header
        header = QWidget()
        header.setObjectName("PageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        title_box = QWidget()
        title_layout = QVBoxLayout(title_box)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(2)

        title_row = QWidget()
        title_row_layout = QHBoxLayout(title_row)
        title_row_layout.setContentsMargins(0, 0, 0, 0)
        title_row_layout.setSpacing(8)
        title_label = QLabel("复制记录")
        title_label.setObjectName("HeaderTitle")
        self._monitor_toggle = MonitorToggle()
        title_row_layout.addWidget(title_label)
        title_row_layout.addWidget(self._monitor_toggle)
        title_row_layout.addStretch(1)
        title_layout.addWidget(title_row)

        subtitle = QLabel("自动保存剪贴板文本、图片和文件路径")
        subtitle.setObjectName("HeaderSubtitle")
        title_layout.addWidget(subtitle)
        header_layout.addWidget(title_box, 1)

        pin_btn = QPushButton("快捷浮窗")
        pin_btn.setObjectName("BtnPrimary")
        pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pin_btn.setIcon(_asset_icon("icon_pin_white.svg"))
        pin_btn.setIconSize(QSize(20, 20))
        pin_btn.setMinimumSize(132, 42)
        pin_btn.setMaximumSize(132, 42)
        pin_btn.clicked.connect(lambda *_: self.pin_requested.emit())
        header_layout.addWidget(pin_btn)

        outer.addWidget(header)

        # Body scroll
        body = QScrollArea()
        body.setObjectName("PageScroll")
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body.setWidgetResizable(True)
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.viewport().setObjectName("PageScrollViewport")
        body.viewport().setAutoFillBackground(True)
        body.viewport().setStyleSheet("QWidget#PageScrollViewport { background: #f8fafc; }")
        content = QWidget()
        content.setObjectName("PageContent")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)

        # Card
        card = QGroupBox()
        card.setObjectName("SectionGroup")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 12, 10, 9)
        card_layout.setSpacing(8)

        # Toolbar
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(8)

        self._search_bar = SearchBar()
        toolbar_layout.addWidget(self._search_bar)

        self._statistics_bar = StatisticsBar()
        toolbar_layout.addWidget(self._statistics_bar, 1)

        # 批量操作热区
        self._batch_hotspot = QWidget()
        self._batch_hotspot.setObjectName("ClipboardBatchHotspot")
        self._batch_hotspot.setToolTip("双击空白区域进入批量操作")
        self._batch_hotspot.setMinimumHeight(30)
        self._batch_hotspot.setMinimumWidth(0)
        self._batch_hotspot.setVisible(False)
        hotspot_layout = QHBoxLayout(self._batch_hotspot)
        hotspot_layout.setContentsMargins(0, 0, 0, 0)
        hotspot_layout.setSpacing(6)

        self._batch_select_all = QCheckBox("全选")
        self._batch_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._batch_select_all.setFixedWidth(52)
        self._batch_select_all.setVisible(False)
        self._batch_sel_count_label = QLabel("已选择 0 条")
        self._batch_sel_count_label.setObjectName("ClipboardBatchSelCount")
        self._batch_sel_count_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        hotspot_layout.addWidget(self._batch_sel_count_label)
        hotspot_layout.addStretch(1)
        hotspot_layout.addWidget(self._batch_select_all)

        self._batch_delete = QPushButton("删除")
        self._batch_delete.setObjectName("BtnSmallDanger")
        self._batch_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._batch_delete.setFixedSize(52, 28)
        self._batch_delete.setVisible(False)
        hotspot_layout.addWidget(self._batch_delete)

        self._batch_done = QPushButton("完成")
        self._batch_done.setObjectName("BtnSmallPrimary")
        self._batch_done.setCursor(Qt.CursorShape.PointingHandCursor)
        self._batch_done.setFixedSize(56, 28)
        self._batch_done.setVisible(False)
        hotspot_layout.addWidget(self._batch_done)

        def _on_hotspot_double_click(event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._toggle_batch_mode(not self._batch_mode)
            QWidget.mouseDoubleClickEvent(self._batch_hotspot, event)

        self._batch_hotspot.mouseDoubleClickEvent = _on_hotspot_double_click
        toolbar_layout.addWidget(self._batch_hotspot, 1)

        def _on_stats_double_click(event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._toggle_batch_mode(True)
            QWidget.mouseDoubleClickEvent(self._statistics_bar, event)

        self._statistics_bar.mouseDoubleClickEvent = _on_stats_double_click

        self._filter_btn = QToolButton()
        self._filter_btn.setObjectName("ClipboardFilterButton")
        self._filter_btn.setText("过滤")
        self._filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._filter_btn.setFixedHeight(30)
        self._filter_btn.setStyleSheet("""
            QToolButton {
                min-height: 30px;
                max-height: 30px;
                padding: 0 12px;
                border-radius: 7px;
                border: 1px solid #e2e8f0;
                background: #ffffff;
                color: #22324c;
                font-size: 13px;
                font-weight: 800;
            }
            QToolButton:hover {
                background: rgba(30, 41, 59, 0.06);
                border-color: rgba(30, 41, 59, 0.3);
            }
        """)
        self._filter_btn.clicked.connect(self._edit_filter_keywords)
        toolbar_layout.addWidget(self._filter_btn)

        self._type_filter = TypeFilterCombo()
        toolbar_layout.addWidget(self._type_filter)

        card_layout.addWidget(toolbar)

        self._inline_status_label = QLabel()
        self._inline_status_label.setObjectName("ClipboardInlineStatus")
        self._inline_status_label.setWordWrap(True)
        self._inline_status_label.setVisible(False)
        self._inline_status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card_layout.addWidget(self._inline_status_label)

        # Record list
        self._record_list = RecordListWidget()
        card_layout.addWidget(self._record_list, 1)

        content_layout.addWidget(card)
        body.setWidget(content)
        outer.addWidget(body, 1)

        # Signals
        self._monitor_toggle.toggled.connect(self._on_monitor_toggled)
        self._search_bar.search_changed.connect(self._on_search_changed)
        self._search_bar.search_cleared.connect(self._on_search_cleared)
        self._type_filter.filter_changed.connect(self._on_type_filter_changed)
        self._record_list.run_requested.connect(self._on_run_requested)
        self._record_list.copy_requested.connect(self._on_copy_requested)
        self._record_list.delete_requested.connect(self._on_delete_requested)
        self._record_list.pin_requested.connect(self._on_pin_requested)
        self._record_list.edit_requested.connect(self._on_edit_requested)
        self._record_list.item_clicked.connect(self._on_item_clicked)
        self._record_list.load_more_requested.connect(self._load_more_records)
        self._record_list.selection_changed.connect(self._on_selection_changed)

        self._batch_select_all.stateChanged.connect(self._on_batch_select_all)
        self._batch_delete.clicked.connect(self._on_batch_delete)
        self._batch_done.clicked.connect(lambda: self._toggle_batch_mode(False))

    def _ensure_monitor(self, enabled: Optional[bool] = None) -> None:
        if self._monitor is None:
            self._monitor = ClipboardMonitor(self)
            self._monitor.path_detected.connect(self._on_path_detected)
        if enabled is None:
            if hasattr(self, "_monitor_toggle"):
                enabled = bool(self._monitor_toggle.isChecked())
            else:
                enabled = True
        if enabled:
            self._monitor.start()
        else:
            self._monitor.stop()

    def initialize(self, *, refresh: bool = True) -> None:
        if hasattr(self, "_filter_btn"):
            self._filter_btn.setToolTip(self._filter_tooltip())
        if self._initialized:
            if getattr(self._query_controller, "_closed", False):
                self._query_controller.reopen()
            if self._monitor is None:
                self._ensure_monitor()
            if bool(refresh):
                self._refresh_list()
            return

        if getattr(self._query_controller, "_closed", False):
            self._query_controller.reopen()

        if self._database is None:
            try:
                self._database = ClipboardDatabase()
            except Exception as e:
                logger.error(f"Clipboard database init failed: {e}")
                self._database = None
                return

        try:
            settings = load_settings()
            ui = settings.ui
            from deepcat.settings_store import normalize_clipboard_history_settings
            ch_settings = normalize_clipboard_history_settings(ui.get("clipboard_history"))
            enabled = bool(ch_settings.get("monitor_enabled", True))
            if hasattr(self, "_monitor_toggle"):
                self._monitor_toggle.setChecked(enabled)
        except Exception as e:
            logger.error(f"Load clipboard settings failed: {e}")
            enabled = False

        self._ensure_monitor(enabled=enabled)

        self._initialized = True
        if bool(refresh):
            self._refresh_list()

    def cleanup(self) -> None:
        self._query_controller.close()
        try:
            if self._monitor is not None:
                self._monitor.cleanup()
                self._monitor = None
        except Exception as e:
            logger.error(f"Monitor cleanup error: {e}")
        self._initialized = False
        self._dirty = True

    def on_page_shown(self) -> None:
        self._is_page_visible = True
        try:
            self.setUpdatesEnabled(False)
            if not self._initialized:
                self.initialize(refresh=True)
            elif not self._query_controller.busy:
                self._refresh_list()
            self._record_list._sync_item_widget_layout()
        except Exception:
            pass
        finally:
            try:
                self.setUpdatesEnabled(True)
                self.update()
            except Exception:
                pass

    def on_page_hidden(self) -> None:
        self._is_page_visible = False
        self._record_list._hide_record_tooltips()

    def _on_monitor_toggled(self, enabled: bool) -> None:
        if not self._initialized:
            self.initialize(refresh=False)
        if self._monitor is None:
            return
        try:
            if enabled:
                self._monitor.start()
            else:
                self._monitor.stop()
            settings = load_settings()
            ui = dict(settings.ui)
            ch = dict(ui.get("clipboard_history", {}))
            ch["monitor_enabled"] = enabled
            ui["clipboard_history"] = ch
            settings = settings.__class__(
                version=settings.version,
                autostart=settings.autostart,
                auto_save=settings.auto_save,
                image_output_dir=settings.image_output_dir,
                pdf_output_dir=settings.pdf_output_dir,
                hotkey=settings.hotkey,
                ui=ui,
                notifications_enabled=getattr(settings, "notifications_enabled", False),
            )
            save_settings(settings)
        except Exception as e:
            logger.error(f"Monitor toggle failed: {e}")
            self._monitor_toggle.setChecked(not enabled)

    def _on_path_detected(self, data: dict[str, Any]) -> None:
        if self._database is None or self._queries_suspended:
            return
        if self._should_skip_record(data):
            return
        try:
            record_id = self._database.add_record(
                content=data.get("content", ""),
                content_type=data.get("content_type", "text"),
                file_type=data.get("file_type", ""),
                file_path=data.get("file_path", ""),
                file_size=data.get("file_size", 0),
                source_app=data.get("source_app", "Unknown"),
                metadata=data.get("metadata", {}),
            )
            if record_id is not None:
                if self._is_page_visible and (self._current_keyword.strip() or self._current_type_filter):
                    self._refresh_list()
                elif self._is_page_visible:
                    # 核心：为了防止列表中已存在该 ID 的旧 Widget 导致重复显示，
                    # 我们在 prepend 之前，先将其移出列表！
                    self._record_list.remove_record(record_id)

                    rec = self._database.get_record_by_id(record_id)
                    if rec:
                        self._record_list.prepend_record(rec)
                        self._update_statistics()
                        if self._batch_mode:
                            self._update_batch_selection_controls()
                else:
                    self._dirty = True
            if record_id is not None:
                try:
                    main_win = self.window()
                    if main_win is not None and hasattr(main_win, "_compact_window_clipboard"):
                        compact_win = getattr(main_win, "_compact_window_clipboard", None)
                        if compact_win is not None:
                            compact_win.load_data()
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Handle path detected failed: {e}")

    def _on_search_changed(self, keyword: str) -> None:
        self._current_keyword = keyword
        self._refresh_list()

    def _on_search_cleared(self) -> None:
        self._current_keyword = ""
        self._refresh_list()

    def _on_type_filter_changed(self, content_type: str) -> None:
        self._current_type_filter = content_type
        self._refresh_list()

    def _clear_empty_state_filters(self) -> None:
        self._current_keyword = ""
        self._current_type_filter = ""
        if hasattr(self, "_search_bar"):
            old_blocked = self._search_bar.blockSignals(True)
            try:
                self._search_bar.clear_text()
            finally:
                self._search_bar.blockSignals(old_blocked)
        if hasattr(self, "_type_filter"):
            old_blocked = self._type_filter.blockSignals(True)
            try:
                self._type_filter.setCurrentIndex(0)
            finally:
                self._type_filter.blockSignals(old_blocked)
        self._refresh_list()

    def _enable_monitor_from_empty_state(self) -> None:
        if not self._initialized:
            self.initialize(refresh=False)
        self._monitor_toggle.setChecked(True)
        self._on_monitor_toggled(True)
        self._refresh_list()

    def _clipboard_empty_state_config(self) -> tuple[str, str, str, Optional[Callable[[], None]]]:
        if self._current_keyword.strip() or self._current_type_filter:
            return (
                "没有匹配的复制记录",
                "换个关键词，或清空筛选。",
                "清空筛选",
                self._clear_empty_state_filters,
            )
        if not self._monitor_toggle.isChecked():
            return (
                "还没有复制记录",
                "开启记录后，复制的文本、图片和文件路径会自动出现在这里。",
                "开启记录",
                self._enable_monitor_from_empty_state,
            )
        return (
            "还没有复制记录",
            "复制文本、图片或文件路径后，这里会自动保存记录。",
            "",
            None,
        )

    def suspend_queries(self) -> None:
        self._queries_suspended = True
        if not self._query_controller.cancel(wait_ms=2000):
            raise RuntimeError("复制记录查询尚未结束，请稍后重试恢复")

    def resume_queries(self) -> None:
        self._queries_suspended = False
        self._dirty = True
        if self._is_page_visible:
            self._refresh_list()

    def _refresh_list(self) -> None:
        if self._database is None or self._queries_suspended:
            return
        self._record_list.set_empty_state(*self._clipboard_empty_state_config())
        self._query_controller.submit(
            self._database, keyword=self._current_keyword.strip(),
            content_type=self._current_type_filter or None, limit=PAGE_SIZE,
        )

    def _load_more_records(self) -> None:
        if self._database is None or self._queries_suspended or self._query_controller.busy:
            self._record_list._loading_more = False
            return
        self._query_controller.submit(
            self._database, keyword=self._current_keyword.strip(),
            content_type=self._current_type_filter or None, limit=PAGE_SIZE,
            offset=self._record_list._current_offset,
        )

    def _on_query_completed(self, query: ClipboardQuery, payload: object) -> None:
        if self._queries_suspended:
            return
        data = dict(payload)
        if not query.statistics_only:
            records = data["records"]
            if query.offset:
                self._record_list.append_records(records)
            else:
                pinned = data.get("pinned", [])
                if records != self._record_list._loaded_records or pinned != self._record_list._loaded_pinned:
                    self._record_list.load_records(records, pinned)
                elif not records and not pinned:
                    self._record_list.load_records([], [])
            self._record_list._has_more = bool(data["has_more"])
            self._dirty = False
        total, data_size = data["statistics"]
        self._statistics_bar.update_stats(self._record_list.record_count(), total, data_size)
        if self._batch_mode:
            self._update_batch_selection_controls()

    def _on_query_failed(self, message: str) -> None:
        self._record_list._loading_more = False
        logger.error("复制记录查询失败: %s", message)
        self._show_inline_status(f"读取复制记录失败：{message}", tone="error")

    def _on_item_clicked(self, record_id: int) -> None:
        if self._batch_mode:
            widget = self._record_list._record_widgets.get(record_id)
            if widget is not None:
                widget.setChecked(not widget.isChecked())
            return
        if self._database is None:
            return
        try:
            rec = self._database.get_record_by_id(record_id)
            if rec is None:
                return
            content_type = str(rec[2] or "text")
            content = str(rec[1] or "")
            file_path = str(rec[4] or "")
            import os
            if content_type == "url" and content:
                url = QUrl.fromUserInput(content)
                if url.isValid():
                    QDesktopServices.openUrl(url)
            elif content_type in ("file_path", "image") and file_path and os.path.isfile(file_path):
                os.startfile(file_path)
            elif content_type == "folder_path" and file_path and os.path.isdir(file_path):
                os.startfile(file_path)
            else:
                self._on_copy_requested(record_id)
        except Exception as e:
            logger.error(f"Item click failed: {e}")

    def _on_run_requested(self, record_id: int) -> None:
        if self._database is None:
            self._show_inline_status("复制记录数据库未就绪。", tone="error")
            return
        try:
            rec = self._database.get_record_by_id(record_id)
            if rec is None:
                self._show_inline_status("未找到要运行预览的记录。", tone="warning")
                return
            content = str(rec[1] or "")
            content_type = str(rec[2] or "text")
            file_path = str(rec[4] or "")

            # 剪贴板中的 HTML 会在默认浏览器的完整用户配置下执行（含 <script>），
            # 来源不可信时可能借助网页脚本访问网络/内网。打开前必须经用户确认。
            from deepcat.ui.main_window.compact import StyledMessageBox
            preview = content if len(content) <= 600 else content[:600] + "\n..."
            box = StyledMessageBox(self)
            box.setWindowTitle("确认运行代码预览")
            box.setText(
                "即将在默认浏览器中打开该剪贴板内容的网页预览。\n"
                "其中的脚本代码将被完整执行，请确认来源可信。\n\n"
                f"───── 内容预览 ─────\n{preview}"
            )
            box.setIcon(QMessageBox.Icon.Warning)
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            yes_btn = box.button(QMessageBox.StandardButton.Yes)
            no_btn = box.button(QMessageBox.StandardButton.No)
            if yes_btn is not None:
                yes_btn.setText("运行")
            if no_btn is not None:
                no_btn.setText("取消")
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                self._show_inline_status("已取消代码预览。", tone="info", auto_hide_ms=2000)
                return

            ok = preview_code_in_browser(content, content_type, file_path)
            if ok:
                self._show_inline_status("已在默认浏览器中打开代码预览。", tone="success", auto_hide_ms=2000)
            else:
                self._show_inline_status("打开代码预览失败。", tone="error", auto_hide_ms=3000)
        except Exception as e:
            logger.error(f"Run code preview failed: {e}")
            self._show_inline_status(f"运行代码预览失败：{e}", tone="error", auto_hide_ms=3000)

    def _on_copy_requested(self, record_id: int) -> None:
        if self._database is None:
            self._show_inline_status("复制记录数据库未就绪。", tone="error")
            return
        try:
            rec = self._database.get_record_by_id(record_id)
            if rec is None:
                self._show_inline_status("未找到要复制的记录。", tone="warning")
                return
            content = str(rec[1] or "")
            content_type = str(rec[2] or "text")
            file_path = str(rec[4] or "")

            clipboard = QGuiApplication.clipboard()
            if content_type in ("file_path", "folder_path", "image") and file_path:
                copy_text = file_path
            else:
                copy_text = content
            try:
                if self._monitor is not None:
                    self._monitor.ignore_next_content(copy_text)
            except Exception:
                pass
            clipboard.setText(copy_text)

            # 1. 更新数据库中的使用次数与时间戳，无损保留所有重要属性
            self._database.update_usage_count(record_id)
            self._database.touch_timestamp(record_id)

            # 2. 内存 UI 无损置顶与避让：从当前位置移除该记录 Widget，重新插入到合适位置（在Pinned项下方）
            self._record_list.remove_record(record_id)
            updated_rec = self._database.get_record_by_id(record_id)
            if updated_rec:
                self._record_list.prepend_record(updated_rec)
                self._update_statistics()

            # 3. 同步推送至主窗口的紧凑历史面板
            try:
                main_win = self.window()
                if main_win is not None and hasattr(main_win, "_compact_window_clipboard"):
                    compact_win = getattr(main_win, "_compact_window_clipboard", None)
                    if compact_win is not None:
                        compact_win.load_data()
            except Exception:
                pass
            self._show_inline_status("已复制到剪贴板。", tone="success", auto_hide_ms=1500)
        except Exception as e:
            logger.error(f"Copy failed: {e}")
            self._show_inline_status(f"复制失败：{e}", tone="error", auto_hide_ms=5200)

    def _on_delete_requested(self, record_id: int) -> None:
        if self._database is None:
            self._show_inline_status("复制记录数据库未就绪。", tone="error")
            return
        try:
            self._database.delete_record(record_id)
            self._record_list.remove_record(record_id)
            self._update_statistics()
            self._show_inline_status("已删除 1 条复制记录。", tone="success")
        except Exception as e:
            logger.error(f"Delete failed: {e}")
            self._show_inline_status(f"删除失败：{e}", tone="error", auto_hide_ms=5200)

    def _on_pin_requested(self, record_id: int, is_pinned: bool) -> None:
        if self._database is None:
            self._show_inline_status("复制记录数据库未就绪。", tone="error")
            return
        try:
            self._database.update_pin_status(record_id, is_pinned)
            self._refresh_list()
            self._show_inline_status("已置顶该记录。" if is_pinned else "已取消置顶该记录。", tone="success")
        except Exception as e:
            logger.error(f"Pin failed: {e}")
            self._show_inline_status(f"置顶状态更新失败：{e}", tone="error", auto_hide_ms=5200)

    def _on_edit_requested(self, record_id: int) -> None:
        if self._database is None:
            return
        try:
            rec = self._database.get_record_by_id(record_id)
            if rec is None:
                return
            content = str(rec[1] or "")
            dialog = _EditRecordDialog(content, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                new_content = dialog.content()
                self._database.update_record(record_id, new_content)
                self._refresh_list()
        except Exception as e:
            logger.error(f"Edit failed: {e}")

    def _on_open_file_requested(self, record_id: int) -> None:
        if self._database is None:
            return
        try:
            rec = self._database.get_record_by_id(record_id)
            if rec is None:
                return
            file_path = str(rec[4] or "")
            if file_path and (os.path.isfile(file_path) or os.path.isdir(file_path)):
                os.startfile(file_path)
        except Exception as e:
            logger.error(f"Open file failed: {e}")

    def _update_statistics(self, *, async_stats: bool = True) -> None:
        if self._database is None or self._queries_suspended:
            return
        # 在途页面查询已经包含统计，不再重复启动线程或同步读库。
        if not self._query_controller.busy:
            self._query_controller.submit(
                self._database, content_type=self._current_type_filter or None,
                statistics_only=True,
            )

    def _update_batch_selection_controls(self) -> None:
        if not hasattr(self, "_batch_sel_count_label") or not hasattr(self, "_record_list"):
            return
        selected_count = len(self._record_list.selected_record_ids())
        self._batch_sel_count_label.setText(f"{selected_count} 条")
        selectable = [
            widget for widget in self._record_list._record_widgets.values() if not widget._is_pinned
        ]
        was_blocked = self._batch_select_all.blockSignals(True)
        try:
            self._batch_select_all.setChecked(
                bool(selectable) and all(widget.isChecked() for widget in selectable)
            )
        finally:
            self._batch_select_all.blockSignals(was_blocked)
        self._batch_select_all.setEnabled(bool(selectable))
        self._batch_delete.setEnabled(selected_count > 0)

    def _toggle_batch_mode(self, enabled: bool) -> None:
        self._batch_mode = bool(enabled)
        is_batch = bool(enabled)
        self._batch_select_all.setVisible(is_batch)
        self._batch_delete.setVisible(is_batch)
        self._batch_done.setVisible(is_batch)
        self._statistics_bar.setVisible(not is_batch)
        self._batch_hotspot.setVisible(is_batch)
        self._record_list.set_batch_mode(is_batch)
        self._update_batch_selection_controls()

    def _on_batch_select_all(self, state: int) -> None:
        if not self._batch_mode:
            return
        checked = bool(state)
        was_blocked = self._record_list.blockSignals(True)
        try:
            for widget in self._record_list._record_widgets.values():
                # 全选沿用置顶保护，完成后统一更新计数，避免逐行重复扫描列表。
                widget.setChecked(checked and not widget._is_pinned)
        finally:
            self._record_list.blockSignals(was_blocked)
        self._update_batch_selection_controls()

    def _on_batch_delete(self) -> None:
        ids = self._record_list.selected_record_ids()
        if not ids:
            self._show_inline_status("请先选择要删除的复制记录。", tone="warning")
            return
        from deepcat.ui.main_window.compact import StyledMessageBox
        box = StyledMessageBox(self)
        box.setWindowTitle("确认删除")
        box.setText(f"确定要删除选中的 {len(ids)} 条记录吗？")
        box.setIcon(QMessageBox.Icon.Question)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        yes_btn = box.button(QMessageBox.StandardButton.Yes)
        no_btn = box.button(QMessageBox.StandardButton.No)
        if yes_btn is not None:
            yes_btn.setText("是")
        if no_btn is not None:
            no_btn.setText("否")
        box.setDefaultButton(QMessageBox.StandardButton.No)
        reply = box.exec()
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._database is not None:
            try:
                count = len(ids)
                self._database.delete_records(ids)
                self._toggle_batch_mode(False)
                self._refresh_list()
                self._show_inline_status(f"已删除 {count} 条复制记录。", tone="success")
            except Exception as e:
                logger.error(f"Batch delete failed: {e}")
                self._show_inline_status(f"批量删除失败：{e}", tone="error", auto_hide_ms=5200)

    def _on_selection_changed(self, record_id: int, checked: bool) -> None:
        self._update_batch_selection_controls()

    def _filter_keywords(self) -> list[str]:
        try:
            settings = load_settings()
            clipboard = settings.ui.get("clipboard_history", {})
            raw = clipboard.get("filter_keywords", [])
            if not isinstance(raw, list):
                raw = []
            return [str(x).strip() for x in raw if str(x).strip()]
        except Exception:
            return []

    def _filter_tooltip(self) -> str:
        keywords = self._filter_keywords()
        if not keywords:
            return "过滤忽略设置（未启用）"
        return "忽略过滤以下关键词的复制历史：\n" + "，".join(keywords)

    def _edit_filter_keywords(self) -> None:
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QHBoxLayout, QPushButton
        current = "，".join(self._filter_keywords())
        dialog = QDialog(self)
        dialog.setWindowTitle("过滤关键词")
        dialog.setModal(True)
        dialog.setMinimumWidth(440)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        tip = QLabel("包含关键词的文本将不再保存到复制历史记录，使用逗号分割关键词")
        tip.setWordWrap(True)
        root.addWidget(tip)
        editor = QTextEdit()
        editor.setAcceptRichText(False)
        editor.setPlainText(current)
        line_h = max(18, int(editor.fontMetrics().lineSpacing()))
        editor.setMinimumHeight(line_h * 10 + 18)
        editor.setPlaceholderText("纯水，人工智能，tag，快问快答")
        root.addWidget(editor)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("BtnSmallSecondary")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("BtnSmallPrimary")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(dialog.reject)
        save_btn.clicked.connect(dialog.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            settings = load_settings()
            clipboard = dict(settings.ui.get("clipboard_history", {}) or {})
            clipboard["filter_keywords"] = str(editor.toPlainText() or "")
            settings.ui["clipboard_history"] = clipboard
            save_settings(settings)

            if hasattr(self, "_filter_btn"):
                self._filter_btn.setToolTip(self._filter_tooltip())
        except Exception as e:
            logger.error(f"Save filter keywords failed: {e}")

    def _should_skip_record(self, data: dict[str, Any]) -> bool:
        content = str(data.get("content", "")).lower()
        file_path = str(data.get("file_path", "")).lower()
        haystack = f"{content}\n{file_path}"
        for keyword in self._filter_keywords():
            if keyword.lower() in haystack:
                return True
        return False
