from __future__ import annotations

import time
from pathlib import Path
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFontMetrics, QGuiApplication, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QPushButton, QHBoxLayout, QFrame, QWidget, QGraphicsDropShadowEffect, QLabel, QVBoxLayout, QScrollArea
from deepcat.settings_store import infer_translator_model_type, load_settings, normalize_translator_settings
from deepcat.ui.popup_behavior import set_disable_global_tooltip
from PyQt6.QtWidgets import QFrame, QPushButton
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame
from PyQt6.QtGui import QIcon, QColor
from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton

from deepcat.ui.post_capture_actions.tooltips import RedDotLabel, SmoothToolTip
from deepcat.ui.post_capture_actions.workers import OcrTranslationWorker
from deepcat.ui.thread_utils import request_thread_cancel


class SelectionActionPopup(QWidget):
    actionSelected = pyqtSignal(str)

    def __init__(self, text: str = "") -> None:
        super().__init__(None)
        self._text = str(text or "").strip()
        self._worker = None
        self._hover_translation_triggered = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pos = QPoint(0, 0)
        self._buttons: list[QPushButton] = []
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self.close)

        # 专属 40ms 全系统全局鼠标左键物理点击状态检查定时器，摆脱外部取词监听器的干扰，提供 100% 稳定的自闭环销毁
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(40)
        self._poll_timer.timeout.connect(self._check_external_click)

        # 悬停触发定时器
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._trigger_hover_translation)

        # 实例化专属高精无延迟 ToolTip
        self._tooltip = SmoothToolTip()

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        # 1. 划词悬浮智能翻译卡片 (QFrame)，默认隐藏且高度为 0
        self._translation_card = QFrame(self)
        self._translation_card.setObjectName("TranslationCard")
        self._translation_card.setVisible(False)
        self._translation_card.setMaximumHeight(0)

        shadow_card = QGraphicsDropShadowEffect(self._translation_card)
        shadow_card.setBlurRadius(14)
        shadow_card.setColor(QColor(15, 23, 42, 38))
        shadow_card.setOffset(0, 4)
        self._translation_card.setGraphicsEffect(shadow_card)

        card_layout = QVBoxLayout(self._translation_card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(6)

        self._header = QWidget()
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self._title_icon = QLabel()
        self._title_icon.setText("🐱")
        self._title_icon.setStyleSheet("font-size: 11px;")

        self._title_label = QLabel("划词智能翻译")
        self._title_label.setStyleSheet("font-weight: bold; font-size: 11px; color: #1e293b;")

        self._model_tag = QLabel("[加载中]")
        self._model_tag.setStyleSheet("font-size: 9px; color: #64748b; background-color: #f1f5f9; padding: 2px 5px; border-radius: 3px;")

        header_layout.addWidget(self._title_icon)
        header_layout.addWidget(self._title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self._model_tag)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #e2e8f0; margin-top: 2px; margin-bottom: 2px;")

        # 精致的滚动区域，防爆高度
        from PyQt6.QtWidgets import QScrollArea
        self._scroll_area = QScrollArea(self._translation_card)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area.setStyleSheet(
            "QScrollArea { background: transparent; }"
            "QScrollBar:vertical {"
            "    border: none; background: transparent; width: 4px;"
            "    margin: 0px; border-radius: 2px;"
            "}"
            "QScrollBar::handle:vertical {"
            "    background: #cbd5e1; min-height: 12px; border-radius: 2px;"
            "}"
            "QScrollBar::handle:vertical:hover {"
            "    background: #94a3b8;"
            "}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
            "    border: none; background: none; height: 0px;"
            "}"
        )

        self._content_label = QLabel("准备翻译...")
        self._content_label.setWordWrap(True)
        self._content_label.setStyleSheet(
            "font-size: 13.5px; color: #1e293b; line-height: 1.5; font-family: 'Microsoft YaHei', sans-serif; background: transparent;"
        )
        self._content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._scroll_area.setWidget(self._content_label)

        asset_dir = Path(__file__).resolve().parent.parent / "assets"

        # 精致的右下角复制按钮
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(0)
        bottom_layout.addStretch(1)

        self._copy_btn = QPushButton()
        self._copy_btn.setIcon(QIcon(str(asset_dir / "icon_action_copy.svg")))
        self._copy_btn.setIconSize(QSize(13, 13))
        self._copy_btn.setFixedSize(22, 22)
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.setToolTip("复制翻译结果")
        self._copy_btn.setStyleSheet(
            "QPushButton {"
            "    background: #f1f5f9; border: none; border-radius: 4px;"
            "}"
            "QPushButton:hover {"
            "    background: #e2e8f0;"
            "}"
        )
        self._copy_btn.clicked.connect(self._copy_translation_result)
        bottom_layout.addWidget(self._copy_btn)

        card_layout.addWidget(self._header)
        card_layout.addWidget(sep)
        card_layout.addWidget(self._scroll_area)
        card_layout.addLayout(bottom_layout)

        # 2. 原来的按钮容器栏
        self._container = QFrame(self)
        self._container.setObjectName("ToolbarContainer")

        shadow = QGraphicsDropShadowEffect(self._container)
        shadow.setBlurRadius(14)
        shadow.setColor(QColor(15, 23, 42, 38))  # 亮色下浅 Slate 阴影
        shadow.setOffset(0, 4)
        self._container.setGraphicsEffect(shadow)

        container_layout = QHBoxLayout(self._container)
        container_layout.setContentsMargins(18, 6, 6, 6) # 左侧加宽到 18px，留出红点空间
        container_layout.setSpacing(2)

        # 绝对定位小红点感应区，覆盖整个最左侧 18px 宽度留白感应区域
        self._red_dot = RedDotLabel(self._container)
        self._red_dot.move(0, 0)
        self._red_dot.hovered.connect(self._on_red_dot_hovered)
        self._red_dot.unhovered.connect(self._on_red_dot_unhovered)

        prompt_actions = [
            ("icon_action_copy.svg", "复制划词", "copy"),
            ("icon_selection_search.svg", "AI搜索", "ai_search"),
            ("icon_action_notebook.svg", "添加到笔记本", "add_to_note"),
        ]
        analysis_actions = [
            ("icon_selection_translate.svg", "翻译划词内容", "translate"),
            ("icon_selection_explain.svg", "解释划词内容", "explain"),
            ("icon_selection_summary.svg", "总结划词内容", "summary"),
        ]

        def add_buttons(actions_list):
            for icon_name, tooltip, action in actions_list:
                btn = QPushButton()
                btn.setIcon(QIcon(str(asset_dir / icon_name)))
                btn.setIconSize(QSize(16, 16))
                btn.setToolTip(tooltip)
                set_disable_global_tooltip(btn)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                btn.setFixedSize(30, 28)
                btn.clicked.connect(lambda _=False, a=action: self._choose_action(a))

                # 为每个按钮挂载事件过滤器
                btn.installEventFilter(self)

                self._buttons.append(btn)
                container_layout.addWidget(btn)

        add_buttons(prompt_actions)

        # 亮色主题的浅灰线型分割线
        sep_bar = QFrame()
        sep_bar.setFixedWidth(1)
        sep_bar.setStyleSheet("background-color: #e2e8f0; margin: 5px 4px;")
        container_layout.addWidget(sep_bar)

        add_buttons(analysis_actions)

        root.addWidget(self._translation_card)
        root.addWidget(self._container)
        self.setLayout(root)

        # 亮色精致磨砂主题样式（完美对齐截图工具栏）
        self.setStyleSheet(
            "QFrame#ToolbarContainer {"
            "    background: #ffffff;"
            "    border: 1px solid #cbd5e1;"
            "    border-radius: 8px;"
            "}"
            "QFrame#TranslationCard {"
            "    background: #ffffff;"
            "    border: 1px solid #cbd5e1;"
            "    border-radius: 8px;"
            "}"
            "QPushButton {"
            "    background: transparent;"
            "    border: none;"
            "    border-radius: 6px;"
            "    color: #475569;"
            "    font-weight: 500;"
            "    font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;"
            "    font-size: 11px;"
            "}"
            "QPushButton:hover {"
            "    background: rgba(15, 23, 42, 0.045);"
            "    color: #0f172a;"
            "}"
            "QPushButton:pressed {"
            "    background: rgba(15, 23, 42, 0.09);"
            "}"
            "QPushButton:disabled {"
            "    background: transparent;"
            "    color: #cbd5e1;"
            "}"
        )

        self._esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        self._esc_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._esc_shortcut.activated.connect(self.close)
        self.set_ready(True)

    def _copy_translation_result(self) -> None:
        text = self._content_label.text().strip()
        if text and text not in {"准备翻译...", "正在翻译中..."}:
            try:
                QApplication.clipboard().setText(text)
                # 精准向上弹出“已复制”气泡
                self._tooltip.show_text("已复制", self._copy_btn.mapToGlobal(QPoint(11, -12)), padding=14)
            except Exception:
                pass

    def _on_red_dot_hovered(self) -> None:
        self._hover_timer.start(200)  # 悬停超过 200ms 触发

    def _on_red_dot_unhovered(self) -> None:
        self._hover_timer.stop()

    def _trigger_hover_translation(self) -> None:
        if bool(getattr(self, "_hover_translation_triggered", False)):
            return
        self._hover_translation_triggered = True
        self._hover_timer.stop()
        self._close_timer.stop()
        try:
            self._poll_timer.stop()
        except Exception:
            pass
        try:
            self._tooltip.hide()
        except Exception:
            pass
        self.hide()

        def emit_hover_translate() -> None:
            try:
                if not sip.isdeleted(self):
                    self.actionSelected.emit("hover_translate")
            except Exception:
                pass

        QTimer.singleShot(0, emit_hover_translate)

    def _get_translate_config(self) -> tuple[dict[str, object], bool, str]:
        from deepcat.settings_store import load_settings, normalize_translator_settings, infer_translator_model_type
        settings = load_settings()
        translator = normalize_translator_settings((getattr(settings, "ui", {}) or {}).get("translator"))
        current_model = str(translator.get("translate_model", "") or translator.get("current_model", "Google翻译"))
        configs = dict(translator.get("model_configs") or {})
        cfg = dict(configs.get(current_model, {}) or {})
        cfg["model_type"] = infer_translator_model_type(current_model, cfg)
        cfg["display_name"] = current_model
        use_proxy = bool(cfg.get("use_proxy", False))
        proxy_url = str(translator.get("proxy_url", "socks5://127.0.0.1:1080"))
        return cfg, use_proxy, proxy_url

    def _on_translation_delta(self, chunk: str) -> None:
        chunk_text = str(chunk or "")
        marker = getattr(OcrTranslationWorker, "STREAM_REPLACE_MARKER", "DEEPCAT_STREAM_REPLACE::")
        if chunk_text.startswith(marker):
            self._stream_text = chunk_text[len(marker):]
        else:
            self._stream_text += chunk_text
        self._content_label.setText(self._stream_text)

        content_h = self._content_label.sizeHint().height()
        target_card_h = max(150, min(400, content_h + 46))

        self._translation_card.setMaximumHeight(target_card_h)

        self.adjustSize()
        new_window_h = self.height()

        # 实时根据新窗口高度顶起 y，使底边缘绝对静止不动！
        if hasattr(self, "_fixed_bottom"):
            self.move(self.x(), self._fixed_bottom - new_window_h)

    def _on_translation_finished(self, result: str, success: bool, error_msg: str) -> None:
        if success:
            res_str = str(result or "").strip()
            if not res_str:
                self._content_label.setText("翻译结果为空")
            else:
                self._content_label.setText(res_str)
        else:
            self._content_label.setText(f"翻译失败：{error_msg}")

        content_h = self._content_label.sizeHint().height()
        target_card_h = max(150, min(400, content_h + 46))
        self._translation_card.setMaximumHeight(target_card_h)

        self.adjustSize()
        new_window_h = self.height()
        if hasattr(self, "_fixed_bottom"):
            self.move(self.x(), self._fixed_bottom - new_window_h)

    def eventFilter(self, obj, event) -> bool:
        if obj in self._buttons:
            t = event.type()
            if t == QEvent.Type.Enter:
                try:
                    # 触碰按钮瞬时定位并呼出 SmoothToolTip
                    pos = obj.mapToGlobal(QPoint(int(obj.width() / 2), int(obj.height() + 4)))
                    tooltip_text = str(obj.toolTip()).strip()
                    if tooltip_text:
                        self._tooltip.show_text(tooltip_text, pos, padding=21)
                except Exception:
                    pass
            elif t == QEvent.Type.Leave:
                try:
                    self._tooltip.hide()
                except Exception:
                    pass
            elif t == QEvent.Type.ToolTip:
                # 彻底屏蔽系统原生的灰色方形 ToolTip
                return True
        return super().eventFilter(obj, event)

    def _choose_action(self, action: str) -> None:
        self.actionSelected.emit(str(action))

    def set_ready(self, ready: bool) -> None:
        for btn in self._buttons:
            btn.setEnabled(bool(ready))

    def enterEvent(self, event) -> None:
        self._close_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if bool(getattr(self, "_hover_translation_triggered", False)):
            self._close_timer.stop()
            super().leaveEvent(event)
            return
        # 如果翻译弹窗已经展开显示，绝对不要再触发自动关闭，永远保持常驻，直到用户点击外部
        if self._translation_card.isVisible():
            super().leaveEvent(event)
            return
        self._close_timer.start(3000)
        super().leaveEvent(event)

    def closeEvent(self, event) -> None:
        try:
            self._hover_timer.stop()
        except Exception:
            pass
        if self._worker is not None:
            request_thread_cancel(self._worker, wait_ms=50)
            self._worker = None
        try:
            self._poll_timer.stop()
        except Exception:
            pass
        try:
            self._tooltip.close()
        except Exception:
            pass
        super().closeEvent(event)

    def _check_external_click(self) -> None:
        try:
            import ctypes
            from PyQt6.QtGui import QCursor
            # 检测全局鼠标左键是否被物理按下 (VK_LBUTTON = 0x01)
            if bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000):
                pos = QCursor.pos()
                # 如果点击位置在浮窗边界矩形外部，立即自我销毁关闭，提供极速顺畅的点击响应
                if not self.geometry().contains(pos):
                    self._tooltip.hide()
                    self.close()
        except Exception:
            pass

    def show_at(self, x: int, y: int) -> None:
        self._pos = QPoint(int(x), int(y))
        self.adjustSize()
        screen = QGuiApplication.screenAt(self._pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
        visible_w = self.width() - 20
        visible_h = self.height() - 20
        px = int(self._pos.x() + 2 - 10)
        py = int(self._pos.y() - visible_h / 2 + 10 - 10) + 18  # 往下偏移一行五号字大小的距离（大约 18 像素）
        margin = 8
        px_min = int(geo.left() + margin - 10)
        px_max = int(geo.right() - self.width() - margin + 10 + 1)
        py_min = int(geo.top() + margin - 10)
        py_max = int(geo.bottom() - self.height() - margin + 10 + 1)
        px = max(px_min, min(px, px_max))
        py = max(py_min, min(py, py_max))
        self.move(px, py)
        self.show()
        self.raise_()
        try:
            QApplication.processEvents()
        except Exception:
            pass
        self._close_timer.start(3000)

        # 牢牢锚定初次显示后的绝对底部 Y 坐标物理基准，为之后的顶起补偿提供牢固保障！
        self._fixed_bottom = self.y() + self.height()

        # 展示浮窗的同时启动物理左键轮询，确保在其它任何区域点击时立即自销毁
        self._poll_timer.start()


class SelectionHoverTranslationPopup(QWidget):
    def __init__(self, text: str = "") -> None:
        super().__init__(None)
        self._text = str(text or "").strip()
        self._worker = None
        self._stream_text = ""
        self._tooltip = SmoothToolTip()
        self._model_display = "加载中"
        self._translation_started_ts = 0.0
        self._translation_finished_ts = 0.0
        self._copyable_text = ""
        self._window_width = 500
        self._min_window_height = 170
        self._max_window_height = 300
        self._anchor_bottom = 0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(self._window_width)
        self.setMinimumHeight(self._min_window_height)
        self.setMaximumHeight(self._max_window_height)
        self.resize(self._window_width, self._min_window_height)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(40)
        self._poll_timer.timeout.connect(self._check_external_click)
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(100)
        self._elapsed_timer.timeout.connect(self._update_elapsed_label)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        self._card = QFrame(self)
        self._card.setObjectName("HoverTranslationCard")
        self._card.setFixedHeight(150)
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(14)
        shadow.setColor(QColor(15, 23, 42, 38))
        shadow.setOffset(0, 4)
        self._card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(6)

        header = QWidget(self._card)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        title_icon = QLabel()
        title_icon.setText("🐱")
        title_icon.setStyleSheet("font-size: 11px;")
        title_label = QLabel("划词智能翻译")
        title_label.setStyleSheet("font-weight: bold; font-size: 11px; color: #1e293b;")
        self._model_tag = QLabel("[加载中]")
        self._model_tag.setStyleSheet("font-size: 9px; color: #64748b; background-color: #f1f5f9; padding: 2px 5px; border-radius: 3px;")

        header_layout.addWidget(title_icon)
        header_layout.addWidget(title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self._model_tag)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #e2e8f0; margin-top: 2px; margin-bottom: 2px;")

        from PyQt6.QtWidgets import QScrollArea
        self._scroll_area = QScrollArea(self._card)
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area.setStyleSheet(
            "QScrollArea { background: transparent; }"
            "QScrollBar:vertical { border: none; background: transparent; width: 4px; margin: 0px; border-radius: 2px; }"
            "QScrollBar::handle:vertical { background: #cbd5e1; min-height: 12px; border-radius: 2px; }"
            "QScrollBar::handle:vertical:hover { background: #94a3b8; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { border: none; background: none; height: 0px; }"
        )

        self._content_label = QLabel("准备翻译...")
        self._content_label.setWordWrap(True)
        self._content_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._content_label.setStyleSheet(
            "font-size: 13.5px; color: #1e293b; line-height: 1.5; font-family: 'Microsoft YaHei', sans-serif; background: transparent;"
        )
        self._content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._scroll_area.setWidget(self._content_label)
        try:
            self._scroll_area.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            self._scroll_area.viewport().installEventFilter(self)
        except Exception:
            pass
        self._content_label.installEventFilter(self)

        card_layout.addWidget(header)
        card_layout.addWidget(sep)
        card_layout.addWidget(self._scroll_area, 1)
        root.addWidget(self._card)

        self.setStyleSheet(
            "QFrame#HoverTranslationCard {"
            "    background: #ffffff;"
            "    border: 1px solid #cbd5e1;"
            "    border-radius: 8px;"
            "}"
        )

        self._esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        self._esc_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._esc_shortcut.activated.connect(self.close)

    def _content_height_for_width(self, width: int) -> int:
        width = max(1, int(width))
        text = str(self._content_label.text() or " ")
        try:
            self._content_label.setFixedWidth(width)
            h = int(self._content_label.heightForWidth(width))
            if h <= 0:
                self._content_label.adjustSize()
                h = int(self._content_label.sizeHint().height())
            return max(18, h + 2)
        except Exception:
            metrics = QFontMetrics(self._content_label.font())
            flags = (
                int(Qt.TextFlag.TextWordWrap.value)
                | int(Qt.AlignmentFlag.AlignLeft.value)
                | int(Qt.AlignmentFlag.AlignTop.value)
            )
            return max(18, int(metrics.boundingRect(QRect(0, 0, width, 10000), flags, text).height() + 4))

    def _apply_window_height(self, target_h: int) -> None:
        target_h = max(int(self._min_window_height), min(int(target_h), int(self._max_window_height)))
        old_bottom = int(self._anchor_bottom or (self.y() + self.height()))
        self.resize(int(self._window_width), int(target_h))
        self._card.setFixedHeight(max(1, int(target_h) - 20))
        if self.isVisible():
            screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
            margin = 8
            new_y = int(old_bottom - target_h)
            new_y = max(int(geo.top() + margin), min(new_y, int(geo.bottom() - target_h - margin + 1)))
            self.move(self.x(), new_y)

    def _sync_content_extent(self) -> None:
        viewport_w = max(1, int(self._scroll_area.viewport().width()))
        viewport_h = max(1, int(self._scroll_area.viewport().height()))
        content_h = self._content_height_for_width(viewport_w)
        self._content_label.resize(viewport_w, max(viewport_h, content_h))

    def _resize_to_content(self) -> None:
        try:
            viewport_w = max(1, int(self._scroll_area.viewport().width()))
            content_h = self._content_height_for_width(viewport_w)
            chrome_h = max(0, int(self.height()) - int(self._scroll_area.viewport().height()))
            target_h = chrome_h + content_h + 2
            self._apply_window_height(target_h)
            self._sync_content_extent()
        except Exception:
            pass

    def show_near(self, anchor: object, fallback_x: int = 0, fallback_y: int = 0) -> None:
        if isinstance(anchor, QRect) and not anchor.isNull():
            screen_pos = anchor.center()
            x = int(anchor.left())
            y = int(anchor.bottom() - self.height() + 1)
        else:
            screen_pos = QPoint(int(fallback_x), int(fallback_y))
            x = int(fallback_x - 8)
            y = int(fallback_y - self.height() // 2)
        screen = QGuiApplication.screenAt(screen_pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
        margin = 8
        x = max(int(geo.left() + margin), min(int(x), int(geo.right() - self.width() - margin + 1)))
        y = max(int(geo.top() + margin), min(int(y), int(geo.bottom() - self.height() - margin + 1)))
        self.move(int(x), int(y))
        self._anchor_bottom = int(self.y() + self.height())
        self.show()
        self.raise_()
        self._resize_to_content()
        self._poll_timer.start()
        self._start_translation()

    def _copy_translation_result(self) -> None:
        text = str(getattr(self, "_copyable_text", "") or "").strip()
        if not text:
            text = self._content_label.text().strip()
        if text and text not in {"准备翻译...", "正在翻译中..."} and not text.startswith("正在翻译中..."):
            try:
                QApplication.clipboard().setText(text)
                self._tooltip.show_text("已复制", QCursor.pos(), padding=14)
            except Exception:
                pass

    def eventFilter(self, obj, event) -> bool:
        if obj is self._content_label or obj is self._scroll_area.viewport():
            if event.type() == QEvent.Type.MouseButtonRelease:
                try:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self._copy_translation_result()
                except Exception:
                    self._copy_translation_result()
        return super().eventFilter(obj, event)

    def _get_translate_config(self) -> tuple[dict[str, object], bool, str]:
        settings = load_settings()
        translator = normalize_translator_settings((getattr(settings, "ui", {}) or {}).get("translator"))
        current_model = str(translator.get("translate_model", "") or translator.get("current_model", "Google翻译"))
        configs = dict(translator.get("model_configs") or {})
        cfg = dict(configs.get(current_model, {}) or {})
        cfg["model_type"] = infer_translator_model_type(current_model, cfg)
        cfg["display_name"] = current_model
        use_proxy = bool(cfg.get("use_proxy", False))
        proxy_url = str(translator.get("proxy_url", "socks5://127.0.0.1:1080"))
        return cfg, use_proxy, proxy_url

    def _elapsed_seconds(self) -> float:
        started = float(self._translation_started_ts or 0.0)
        if started <= 0:
            return 0.0
        ended = float(self._translation_finished_ts or 0.0)
        now = ended if ended > 0 else time.monotonic()
        return max(0.0, float(now - started))

    def _update_elapsed_label(self) -> None:
        model = str(self._model_display or "AI")
        if float(self._translation_started_ts or 0.0) > 0:
            self._model_tag.setText(f"[{model} {self._elapsed_seconds():.1f}s]")
            if not str(self._stream_text or "").strip():
                self._content_label.setText(f"正在翻译中... {self._elapsed_seconds():.1f}s")
                self._resize_to_content()
        else:
            self._model_tag.setText(f"[{model}]")

    def _start_translation(self) -> None:
        text = str(self._text or "").strip()
        if not text:
            self._content_label.setText("未获取到选中文字")
            return
        try:
            cfg, use_proxy, proxy_url = self._get_translate_config()
        except Exception as exc:
            self._content_label.setText(f"翻译设置读取失败：{exc}")
            return
        self._model_display = str(cfg.get("display_name", "AI") or "AI")
        self._translation_started_ts = time.monotonic()
        self._translation_finished_ts = 0.0
        self._update_elapsed_label()
        self._elapsed_timer.start()
        self._content_label.setText(f"正在翻译中... {self._elapsed_seconds():.1f}s")
        self._stream_text = ""
        self._copyable_text = ""
        self._worker = OcrTranslationWorker(
            text=text,
            source_lang="自动检测",
            target_lang="中英互译",
            model_config=cfg,
            use_proxy=use_proxy,
            proxy_url=proxy_url,
            task="translate",
            enable_conversation_append=False,
        )
        self._worker.translation_delta.connect(self._on_translation_delta)
        self._worker.translation_finished.connect(self._on_translation_finished)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_translation_delta(self, chunk: str) -> None:
        chunk_text = str(chunk or "")
        marker = getattr(OcrTranslationWorker, "STREAM_REPLACE_MARKER", "DEEPCAT_STREAM_REPLACE::")
        if chunk_text.startswith(marker):
            self._stream_text = chunk_text[len(marker):]
        else:
            self._stream_text += chunk_text
        self._copyable_text = self._stream_text
        self._content_label.setText(self._stream_text or "正在翻译中...")
        self._resize_to_content()

    def _on_translation_finished(self, result: str, success: bool, error_msg: str) -> None:
        self._translation_finished_ts = time.monotonic()
        self._elapsed_timer.stop()
        self._update_elapsed_label()
        if success:
            text = str(result or "").strip()
            self._copyable_text = text
            self._content_label.setText(text or "翻译结果为空")
        else:
            self._copyable_text = ""
            self._content_label.setText(f"翻译失败：{error_msg}")
        self._resize_to_content()

    def _on_worker_finished(self) -> None:
        worker = self.sender()
        if self._worker is worker:
            self._worker = None

    def _check_external_click(self) -> None:
        try:
            import ctypes
            if bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000):
                pos = QCursor.pos()
                if not self.geometry().contains(pos):
                    self.close()
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        try:
            self._poll_timer.stop()
        except Exception:
            pass
        try:
            self._elapsed_timer.stop()
        except Exception:
            pass
        try:
            self._tooltip.close()
        except Exception:
            pass
        worker = self._worker
        self._worker = None
        if worker is not None:
            request_thread_cancel(worker, wait_ms=50)
        super().closeEvent(event)
