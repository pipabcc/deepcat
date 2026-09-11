from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional
from PyQt6.QtCore import QEvent, QPoint, QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QPushButton, QHBoxLayout, QWidget, QToolButton, QSizePolicy
from deepcat.settings_store import (
    DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
    DEFAULT_REPLY_PROMPT_BUTTON_NAME,
    DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
    DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
)
from deepcat.ui.popup_behavior import set_disable_global_tooltip
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton

from deepcat.ui.post_capture_actions._shared import logger
from deepcat.ui.post_capture_actions.tooltips import SmoothToolTip


class PromptSettingsPopup(QWidget):
    def __init__(self, parent: QWidget, on_action_triggered: Callable[[str], None]) -> None:
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._parent = parent
        self._on_action_triggered = on_action_triggered
        self.setObjectName("PromptSettingsPopup")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.btn_reply = QPushButton(DEFAULT_REPLY_PROMPT_BUTTON_NAME)
        self.btn_optimize = QPushButton(DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME)
        self.btn_explain = QPushButton(DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME)
        self.btn_summarize = QPushButton(DEFAULT_SUMMARY_PROMPT_BUTTON_NAME)

        self._buttons = [self.btn_reply, self.btn_optimize, self.btn_explain, self.btn_summarize]
        self._button_map = {
            "reply": self.btn_reply,
            "optimize": self.btn_optimize,
            "ai_search": self.btn_optimize,
            "explain": self.btn_explain,
            "summarize": self.btn_summarize,
        }

        from PyQt6.QtGui import QIcon
        from PyQt6.QtCore import QSize
        asset_dir = Path(__file__).resolve().parent.parent / "assets"

        self.btn_reply.setIcon(QIcon(str(asset_dir / "icon_selection_reply.svg")))
        self.btn_optimize.setIcon(QIcon(str(asset_dir / "icon_selection_search.svg")))
        self.btn_explain.setIcon(QIcon(str(asset_dir / "icon_selection_explain.svg")))
        self.btn_summarize.setIcon(QIcon(str(asset_dir / "icon_selection_summary.svg")))

        for btn in self._buttons:
            btn.setIconSize(QSize(13, 13))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(btn)

        self.btn_reply.clicked.connect(lambda: self._trigger("reply"))
        self.btn_optimize.clicked.connect(lambda: self._trigger("optimize"))
        self.btn_explain.clicked.connect(lambda: self._trigger("explain"))
        self.btn_summarize.clicked.connect(lambda: self._trigger("summarize"))

        self.setStyleSheet("""
            QWidget#PromptSettingsPopup {
                background: transparent;
                border: none;
            }
            QPushButton {
                background: #f6f8fb;
                color: #334155;
                border: none;
                border-radius: 6px;
                padding: 5px 12px;
                font-weight: 700;
                font-size: 12px;
                min-height: 24px;
            }
            QPushButton:hover {
                background: #e7eef8;
                color: #0f172a;
            }
            QPushButton:pressed {
                background: rgb(156, 163, 175);
            }
        """)

    def _trigger(self, action: str) -> None:
        self.close()
        self._on_action_triggered(action)

    def set_button_names(self, names: dict[str, object]) -> None:
        defaults = {
            "reply": DEFAULT_REPLY_PROMPT_BUTTON_NAME,
            "optimize": DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
            "explain": DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
            "summarize": DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
        }
        for action, button in self._button_map.items():
            if action == "ai_search":
                continue
            text = str(names.get(action, defaults.get(action, "")) or defaults.get(action, "")).strip()
            button.setText(text or defaults.get(action, ""))


class HoverIconButton(QPushButton):
    def __init__(self, name: str, normal_icon_path: str, hover_icon_path: str, tooltip: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__("", parent)
        self._button_name = name
        self._normal_icon_path = normal_icon_path
        self._hover_icon_path = hover_icon_path

        from PyQt6.QtGui import QIcon
        from PyQt6.QtCore import QSize
        self.normal_icon = QIcon(normal_icon_path)
        self.hover_icon = QIcon(hover_icon_path)
        self.setIcon(self.normal_icon)
        self.setIconSize(QSize(18, 18))
        self.setToolTip(tooltip)
        set_disable_global_tooltip(self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName(f"HoverIconButton_{name}")

    def sizeHint(self) -> QSize:
        from PyQt6.QtCore import QSize
        return QSize(30, 30)

    def setText(self, text: str) -> None:
        from pathlib import Path
        from PyQt6.QtGui import QIcon
        asset_dir = Path(self._normal_icon_path).parent
        if text == "中止":
            stop_normal = str(asset_dir / "icon_chat_stop_outline.svg")
            stop_hover = str(asset_dir / "icon_chat_stop_filled.svg")
            self.normal_icon = QIcon(stop_normal)
            self.hover_icon = QIcon(stop_hover)
            self.setToolTip("中止任务")
        else:
            self.normal_icon = QIcon(self._normal_icon_path)
            self.hover_icon = QIcon(self._hover_icon_path)
            if self._button_name == "translate":
                self.setToolTip("翻译")
            elif self._button_name == "qa":
                if text == "发送":
                    self.setToolTip("发送追问")
                else:
                    self.setToolTip("发起问答")
            elif self._button_name == "prompt":
                self.setToolTip("快捷")
        super().setText("")

        if self.underMouse():
            self.setIcon(self.hover_icon)
        else:
            self.setIcon(self.normal_icon)

    def enterEvent(self, event) -> None:
        self.setIcon(self.hover_icon)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.setIcon(self.normal_icon)
        super().leaveEvent(event)


class OutputQuickActionBar(QWidget):
    """输出区下方的常用动作入口。"""

    _ACTIONS: tuple[tuple[str, str, str], ...] = (
        ("add_chat_card", "添加快捷卡片", "icon_chat_prompt_outline.svg"),
        ("switch_model", "模型切换", "icon_nav_ai.svg"),
        ("clipboard_window", "复制记录浮窗", "icon_nav_clipboard.svg"),
        ("later_read_window", "稍后阅读浮窗", "icon_nav_read.svg"),
        ("region_capture", "框选截图", "icon_nav_camera.svg"),
        ("new_todo", "新建待办", "icon_todo_add.svg"),
        ("rest_todo", "休息待办", "icon_nav_todo.svg"),
        ("drive_cleaner", "磁盘清理", "icon_nav_cleaner.svg"),
        ("main_window", "软件主界面", "deepcat_logo.svg"),
    )

    def __init__(
        self,
        action_handler: Optional[Callable[[str], None]],
        asset_dir: Path,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._action_handler = action_handler
        self._tooltip = SmoothToolTip()
        self._buttons: list[QToolButton] = []
        self.setObjectName("OutputQuickActionBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)
        layout.addStretch(1)
        for action_id, label, icon_name in self._ACTIONS:
            btn = QToolButton(self)
            btn.setObjectName("OutputQuickActionButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIcon(QIcon(str(asset_dir / icon_name)))
            # 闪电图标设计留白较少导致视觉上偏大，精细微调为 14x14 以在视觉尺寸上与右侧自带留白的图标完全均衡一致
            if action_id == "add_chat_card":
                btn.setIconSize(QSize(14, 14))
            else:
                btn.setIconSize(QSize(18, 18))
            btn.setToolTip(label)
            set_disable_global_tooltip(btn)
            btn.setProperty("quickActionId", action_id)
            btn.setProperty("tipText", label)
            btn.setFixedSize(34, 30)
            btn.clicked.connect(lambda _checked=False, action=action_id: self._trigger_action(action))
            btn.installEventFilter(self)
            layout.addWidget(btn)
            self._buttons.append(btn)
        layout.addStretch(1)
        self.setStyleSheet(
            "QWidget#OutputQuickActionBar {"
            "    background: #f8fafc;"
            "    border: none;"
            "}"
            "QToolButton#OutputQuickActionButton {"
            "    background: rgba(255,255,255,0.88);"
            "    border: 1px solid rgba(203,213,225,0.92);"
            "    border-radius: 8px;"
            "}"
            "QToolButton#OutputQuickActionButton:hover {"
            "    background: #ffffff;"
            "    border-color: rgba(59,130,246,0.45);"
            "}"
            "QToolButton#OutputQuickActionButton:pressed {"
            "    background: #e2e8f0;"
            "}"
        )

    def _trigger_action(self, action_id: str) -> None:
        handler = self._action_handler
        if handler is None:
            return
        try:
            handler(str(action_id or ""))
        except Exception:
            logger.exception("输出区快捷动作执行失败: %s", action_id)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Enter and isinstance(obj, QWidget):
            tip = str(obj.property("tipText") or "").strip()
            if tip:
                pos = obj.mapToGlobal(QPoint(obj.width() // 2, -4))
                self._tooltip.show_text(tip, pos, direction="above")
        elif event.type() in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress):
            self._tooltip.hide()
        return super().eventFilter(obj, event)

    def find_button(self, action_id: str) -> Optional[QToolButton]:
        for btn in self._buttons:
            if btn.property("quickActionId") == action_id:
                return btn
        return None
