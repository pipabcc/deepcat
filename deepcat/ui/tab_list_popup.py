from __future__ import annotations

from typing import Any, Callable, Optional

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QToolButton, QPushButton, QVBoxLayout, QWidget

from deepcat.ui.popup_behavior import mark_anchor_popup_closed, should_skip_anchor_popup


class RoundedListPopup(QWidget):
    """用于表格、笔记本选择列表的紧凑圆角弹窗。"""

    MAX_WIDTH = 200
    MAX_HEIGHT = 450
    MIN_WIDTH = 160
    ITEM_HEIGHT = 32
    PANEL_PADDING = 12

    def __init__(
        self,
        items: list[dict[str, Any]],
        on_selected: Callable[[dict[str, Any]], None],
        *,
        active_index: int = -1,
        empty_text: str = "暂无内容",
        max_height: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        try:
            flags |= Qt.WindowType.NoDropShadowWindowHint
        except AttributeError:
            pass
        super().__init__(parent, flags)
        self.setObjectName("RoundedListPopupRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._scroll: Optional[QScrollArea] = None
        self._active_button: Optional[QPushButton | QToolButton] = None
        self._max_height = int(max_height) if max_height is not None else self.MAX_HEIGHT
        self._build(items, on_selected, active_index=active_index, empty_text=empty_text)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

    def _build(
        self,
        items: list[dict[str, Any]],
        on_selected: Callable[[dict[str, Any]], None],
        *,
        active_index: int,
        empty_text: str,
    ) -> None:
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(0)

        panel = QWidget(self)
        panel.setObjectName("RoundedListPopupPanel")

        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(12)
        shadow.setColor(QColor(15, 23, 42, 38))
        shadow.setOffset(0, 3)
        panel.setGraphicsEffect(shadow)

        root_layout.addWidget(panel)

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(6, 6, 6, 6)
        panel_layout.setSpacing(0)

        scroll = QScrollArea(panel)
        scroll.setObjectName("RoundedListPopupScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        panel_layout.addWidget(scroll)
        self._scroll = scroll

        content = QWidget()
        content.setObjectName("RoundedListPopupContent")
        list_layout = QVBoxLayout(content)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(2)
        scroll.setWidget(content)

        render_items = items if items else [{"text": empty_text, "_disabled": True}]
        names = [str(item.get("text", "") or "") for item in render_items]
        metrics = QFontMetrics(self.font())
        widest = max((metrics.horizontalAdvance(name) for name in names), default=0)
        popup_width = min(self.MAX_WIDTH, max(self.MIN_WIDTH, widest + 54))
        content_height = len(render_items) * self.ITEM_HEIGHT + max(0, len(render_items) - 1) * list_layout.spacing()
        max_height = max(self.ITEM_HEIGHT + self.PANEL_PADDING, self._max_height)
        popup_height = min(max_height, content_height + self.PANEL_PADDING)
        needs_scroll = content_height + self.PANEL_PADDING > max_height
        text_width = popup_width - (58 if needs_scroll else 44)

        self.setFixedSize(popup_width + 16, popup_height + 16)
        content.setFixedWidth(popup_width - self.PANEL_PADDING - (12 if needs_scroll else 0))

        self.setStyleSheet("""
            QWidget#RoundedListPopupRoot {
                background: transparent;
            }
            QWidget#RoundedListPopupPanel {
                background: #ffffff;
                border: 1px solid #dfe4ec;
                border-radius: 8px;
            }
            QScrollArea#RoundedListPopupScroll,
            QScrollArea#RoundedListPopupScroll > QWidget,
            QWidget#RoundedListPopupContent {
                background: transparent;
                border: none;
            }
            QPushButton#RoundedListPopupItem,
            QPushButton#RoundedListPopupItemActive,
            QPushButton#RoundedListPopupItemDisabled,
            QToolButton#RoundedListPopupItem,
            QToolButton#RoundedListPopupItemActive,
            QToolButton#RoundedListPopupItemDisabled {
                background: transparent !important;
                border: none !important;
                border-radius: 6px !important;
                color: #374151 !important;
                font-size: 13px !important;
                font-weight: 500 !important;
                padding: 0 10px !important;
                text-align: left !important;
            }
            QPushButton#RoundedListPopupItem:hover,
            QToolButton#RoundedListPopupItem:hover {
                background: #f3f4f6 !important;
                color: #111827 !important;
            }
            QPushButton#RoundedListPopupItemActive,
            QToolButton#RoundedListPopupItemActive {
                background: #e5e7eb !important;
                color: #111827 !important;
                font-weight: 800 !important;
                text-align: left !important;
            }
            QPushButton#RoundedListPopupItemDisabled,
            QToolButton#RoundedListPopupItemDisabled {
                color: #94a3b8 !important;
            }
            QScrollBar:vertical {
                width: 8px;
                background: transparent;
                margin: 2px 1px 2px 0;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: rgba(148, 163, 184, 0.45);
                border-radius: 4px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(100, 116, 139, 0.65);
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
                background: transparent;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)

        for i, item in enumerate(render_items):
            name = names[i]
            disabled = bool(item.get("_disabled", False))
            is_active = i == active_index and not disabled
            item_btn = QPushButton(content)
            item_btn.setFlat(True)
            if disabled:
                item_btn.setObjectName("RoundedListPopupItemDisabled")
                item_btn.setEnabled(False)
            else:
                item_btn.setObjectName("RoundedListPopupItemActive" if is_active else "RoundedListPopupItem")
            item_btn.setText(self._elide_text(name, metrics, text_width))
            item_btn.setToolTip(name)
            item_btn.setCursor(Qt.CursorShape.PointingHandCursor if not disabled else Qt.CursorShape.ArrowCursor)
            item_btn.setFixedHeight(self.ITEM_HEIGHT)
            item_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if not disabled:
                item_btn.clicked.connect(lambda _=False, payload=dict(item): self._select(payload, on_selected))
            list_layout.addWidget(item_btn)
            if is_active:
                self._active_button = item_btn

    def _select(self, item: dict[str, Any], on_selected: Callable[[dict[str, Any]], None]) -> None:
        self.close()
        on_selected(item)

    @staticmethod
    def _elide_text(text: str, metrics: QFontMetrics, max_width: int) -> str:
        return metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, max_width))

    def eventFilter(self, obj, event) -> bool:
        if obj is self and event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
            mark_anchor_popup_closed(getattr(self, "_anchor", None), self)
        return super().eventFilter(obj, event)

    def show_for_anchor(
        self,
        anchor: QWidget,
        *,
        align_right: bool,
        prefer_above: bool = False,
        prefer_above_from_bottom: bool = False,
        offset: QPoint | None = None,
        bounds_widget: QWidget | None = None,
    ) -> None:
        self._anchor = anchor
        if should_skip_anchor_popup(anchor, self):
            self.close()
            return
        self.installEventFilter(self)
        offset = offset or QPoint(0, 0)
        anchor_top_left = anchor.mapToGlobal(QPoint(0, 0))
        anchor_bottom_left = anchor.mapToGlobal(QPoint(0, anchor.height()))
        if align_right:
            x = anchor_bottom_left.x() + anchor.width() - self.width() + offset.x() + 8
        else:
            x = anchor_top_left.x() + offset.x() - 8
        if prefer_above and prefer_above_from_bottom:
            y = anchor_bottom_left.y() - self.height() + offset.y() + 8
        elif prefer_above:
            y = anchor_top_left.y() - self.height() + offset.y() + 8
        else:
            y = anchor_bottom_left.y() + offset.y() - 8

        margin = 6
        screen = QGuiApplication.screenAt(anchor.mapToGlobal(anchor.rect().center()))
        screen_geo = screen.availableGeometry() if screen is not None else QGuiApplication.primaryScreen().availableGeometry()
        bounds = screen_geo
        if bounds_widget is not None:
            widget_geo = bounds_widget.frameGeometry()
            if widget_geo.isValid():
                widget_bounds = screen_geo.intersected(widget_geo)
                if widget_bounds.width() >= self.width() + margin * 2 and widget_bounds.height() >= 80:
                    bounds = widget_bounds

        if not prefer_above and y + self.height() > bounds.bottom() + 1 - margin:
            y = anchor_top_left.y() - self.height() - offset.y()
        elif prefer_above and y < bounds.top() + margin:
            y = anchor_bottom_left.y() + offset.y()

        max_x = bounds.right() - self.width() + 1 - margin
        min_x = bounds.left() + margin
        max_y = bounds.bottom() - self.height() + 1 - margin
        min_y = bounds.top() + margin
        self.move(QPoint(max(min_x, min(x, max_x)), max(min_y, min(y, max_y))))
        self.show()
        self._scroll_to_active()

    def _scroll_to_active(self) -> None:
        if self._scroll is None or self._active_button is None:
            return
        try:
            self._active_button.setFocus(Qt.FocusReason.PopupFocusReason)
        except RuntimeError:
            return

        def ensure_active_visible() -> None:
            try:
                if self._scroll is not None and self._active_button is not None:
                    self._scroll.ensureWidgetVisible(self._active_button, 0, 0)
            except RuntimeError:
                pass

        QTimer.singleShot(0, ensure_active_visible)


class GroupedNoteListPopup(QWidget):
    """用于展示分组笔记列表的圆角弹窗，所有分组始终展开，顶部带搜索框。"""

    MAX_WIDTH = 240
    MAX_HEIGHT = 450
    MIN_WIDTH = 180
    ITEM_HEIGHT = 30
    HEADER_HEIGHT = 24
    SEARCH_HEIGHT = 30

    def __init__(
        self,
        items: list[dict[str, Any]],
        on_selected: Callable[[dict[str, Any]], None],
        *,
        active_index: int = -1,
        initially_expanded: bool = False,
        empty_text: str = "暂无笔记本",
        max_height: int | None = None,
        on_context_menu: Callable[[QPoint, Optional[dict[str, Any]]], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        try:
            flags |= Qt.WindowType.NoDropShadowWindowHint
        except AttributeError:
            pass
        super().__init__(parent, flags)
        self.setObjectName("GroupedNotePopupRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)

        self._on_selected = on_selected
        self._on_context_menu = on_context_menu
        self._empty_text = empty_text
        self._max_height = int(max_height) if max_height is not None else self.MAX_HEIGHT
        self._active_index = int(active_index)
        self._active_button: Optional[QPushButton] = None
        self._scroll: Optional[QScrollArea] = None

        # 定位参数
        self._anchor: QWidget | None = None
        self._align_right = False
        self._prefer_above = False
        self._prefer_above_from_bottom = False
        self._offset = QPoint(0, 0)
        self._bounds_widget: QWidget | None = None

        # 数据分组
        self._groups: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            gname = str(item.get("group_name", "") or "").strip()
            if not gname:
                gname = "未分组"
            if gname not in self._groups:
                self._groups[gname] = []
            self._groups[gname].append(item)

        # 排序分组名：未分组在最前
        all_group_names = sorted(list(self._groups.keys()))
        if "未分组" in all_group_names:
            all_group_names.remove("未分组")
            self._sorted_group_names = ["未分组"] + all_group_names
        else:
            self._sorted_group_names = all_group_names

        # 用于搜索过滤的 widget 引用
        self._group_header_widgets: dict[str, QWidget] = {}
        self._note_button_widgets: list[tuple[str, QPushButton, dict[str, Any]]] = []
        self._empty_label: Optional[QWidget] = None
        self._no_results_label: Optional[QWidget] = None
        self._search_edit = None

        self._build()

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#dfe4ec"), 1))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
        painter.end()
        super().paintEvent(event)

    # ------------------------------------------------------------------
    # 构建 UI
    # ------------------------------------------------------------------

    def _build(self) -> None:
        from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit

        metrics = QFontMetrics(self.font())

        # 计算弹窗宽度
        names: list[str] = []
        for gname in self._sorted_group_names:
            names.append(gname)
            for item in self._groups[gname]:
                names.append(str(item.get("name", "") or ""))
        widest = max((metrics.horizontalAdvance(n) for n in names), default=0) if names else 0
        popup_width = min(self.MAX_WIDTH, max(self.MIN_WIDTH, widest + 54))
        text_width = popup_width - 44

        # ── 主布局 ──
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        # ── 搜索框 ──
        search_edit = QLineEdit(self)
        search_edit.setObjectName("NotePopupSearch")
        search_edit.setPlaceholderText("搜索笔记…")
        search_edit.setFixedHeight(self.SEARCH_HEIGHT)
        search_edit.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        search_edit.setInputMethodHints(Qt.InputMethodHint.ImhNone)
        search_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        search_edit.setClearButtonEnabled(True)
        search_edit.setStyleSheet("""
            QLineEdit#NotePopupSearch {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 0 8px;
                font-size: 12px;
                color: #334155;
                font-family: 'Microsoft YaHei', 'Segoe UI', system-ui;
            }
            QLineEdit#NotePopupSearch:focus {
                border-color: #94a3b8;
                background: #ffffff;
            }
        """)
        search_edit.textChanged.connect(self._on_search_text_changed)
        main_layout.addWidget(search_edit)
        self._search_edit = search_edit

        # ── 滚动区域 ──
        scroll = QScrollArea(self)
        scroll.setObjectName("NotePopupScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("""
            QScrollArea#NotePopupScroll,
            QScrollArea#NotePopupScroll > QWidget {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                width: 6px; background: transparent;
                margin: 2px 1px 2px 0; border: none;
            }
            QScrollBar::handle:vertical {
                background: rgba(148, 163, 184, 0.45);
                border-radius: 3px; min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(100, 116, 139, 0.65);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        main_layout.addWidget(scroll)
        self._scroll = scroll

        # ── 内容面板 ──
        content = QWidget()
        content.setObjectName("NotePopupContent")
        content.setStyleSheet("QWidget#NotePopupContent { background: transparent; }")
        list_layout = QVBoxLayout(content)
        list_layout.setContentsMargins(0, 2, 0, 2)
        list_layout.setSpacing(1)
        scroll.setWidget(content)
        self._content_widget = content
        self._list_layout = list_layout

        # 右键菜单注册
        for ctx_w in (self, scroll, content):
            ctx_w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            ctx_w.customContextMenuRequested.connect(
                lambda pos, w=ctx_w: self._show_context_menu(w.mapToGlobal(pos))
            )
        if scroll.viewport() is not None:
            scroll.viewport().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            scroll.viewport().customContextMenuRequested.connect(
                lambda pos: self._show_context_menu(scroll.viewport().mapToGlobal(pos))
            )

        # ── 空态 ──
        if not self._sorted_group_names:
            empty_lbl = QLabel(self._empty_text, content)
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setFixedHeight(self.ITEM_HEIGHT)
            empty_lbl.setStyleSheet("color: #94a3b8; font-size: 12px; background: transparent;")
            list_layout.addWidget(empty_lbl)
            self._empty_label = empty_lbl

        # ── 分组 + 笔记列表 ──
        content_height = 0
        total_widget_count = 0

        for gname in self._sorted_group_names:
            # 分组标题带背景
            header = QWidget(content)
            header.setObjectName("NotePopupGroupHeader")
            header.setFixedHeight(self.HEADER_HEIGHT)
            h_lay = QHBoxLayout(header)
            h_lay.setContentsMargins(8, 0, 8, 0)
            h_lay.setSpacing(0)
            h_label = QLabel(
                metrics.elidedText(gname, Qt.TextElideMode.ElideRight, text_width - 16),
                header,
            )
            h_label.setToolTip(gname)
            h_label.setStyleSheet(
                "color: #64748b; font-size: 11px; font-weight: 700; "
                "background: transparent; letter-spacing: 0.3px; "
                "font-family: 'Microsoft YaHei', 'Segoe UI', system-ui;"
            )
            h_lay.addWidget(h_label)
            h_lay.addStretch()
            header.setStyleSheet(
                "QWidget#NotePopupGroupHeader { background: #f1f5f9; border-radius: 4px; }"
            )
            header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            header.customContextMenuRequested.connect(
                lambda pos, w=header: self._show_context_menu(w.mapToGlobal(pos))
            )
            list_layout.addWidget(header)
            self._group_header_widgets[gname] = header
            content_height += self.HEADER_HEIGHT
            total_widget_count += 1

            # 该分组下的笔记条目
            for item in self._groups[gname]:
                note_name = str(item.get("name", "未命名") or "未命名")
                is_active = False
                try:
                    is_active = int(item.get("index", -1)) == self._active_index
                except Exception:
                    pass

                # 预先处理笔记内容/表格内容用于搜索
                import re
                html_content = item.get("html", "")
                if html_content:
                    plain_text = re.sub('<[^<]+?>', '', html_content).lower()
                else:
                    plain_text = ""
                    if "data" in item and isinstance(item["data"], list):
                        cell_texts = []
                        for row in item["data"]:
                            if isinstance(row, list):
                                for cell in row:
                                    cell_texts.append(str(cell or ""))
                            elif isinstance(row, dict):
                                for val in row.values():
                                    cell_texts.append(str(val or ""))
                        plain_text = " ".join(cell_texts).lower()

                btn = QPushButton(content)
                btn.setFlat(True)
                btn.setFixedHeight(self.ITEM_HEIGHT)
                btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setText(metrics.elidedText(note_name, Qt.TextElideMode.ElideRight, text_width - 12))
                btn.setToolTip(note_name)

                if is_active:
                    btn.setObjectName("RoundedListPopupSubItemActive")
                    btn.setStyleSheet(
                        "QPushButton { background: #e5e7eb; border: none; border-radius: 6px; "
                        "font-size: 12px; font-weight: 700; color: #111827; padding: 0 10px 0 18px; text-align: left; "
                        "font-family: 'Microsoft YaHei', 'Segoe UI', system-ui; } "
                        "QPushButton:hover { background: #dbeafe; color: #111827; }"
                    )
                    self._active_button = btn
                else:
                    btn.setObjectName("RoundedListPopupSubItem")
                    btn.setStyleSheet(
                        "QPushButton { background: transparent; border: none; border-radius: 6px; "
                        "font-size: 12px; font-weight: 500; color: #374151; padding: 0 10px 0 18px; text-align: left; "
                        "font-family: 'Microsoft YaHei', 'Segoe UI', system-ui; } "
                        "QPushButton:hover { background: #f3f4f6; color: #111827; }"
                    )

                btn.clicked.connect(lambda _=False, payload=dict(item): self._select(payload))
                btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, b=btn, p=dict(item): self._show_context_menu(b.mapToGlobal(pos), p)
                )
                list_layout.addWidget(btn)
                self._note_button_widgets.append((gname, btn, item, plain_text))
                content_height += self.ITEM_HEIGHT
                total_widget_count += 1

        # 搜索无结果提示（默认隐藏）
        no_res = QLabel("无匹配笔记", content)
        no_res.setAlignment(Qt.AlignmentFlag.AlignCenter)
        no_res.setFixedHeight(self.ITEM_HEIGHT)
        no_res.setStyleSheet("color: #94a3b8; font-size: 12px; background: transparent;")
        no_res.hide()
        list_layout.addWidget(no_res)
        self._no_results_label = no_res
        list_layout.addStretch()

        # ── 计算弹窗固定高度 ──
        if not self._sorted_group_names:
            content_height = self.ITEM_HEIGHT

        content_height += max(0, total_widget_count - 1) * list_layout.spacing()
        # search(30) + spacing(4) + scroll + margins(top6 + bot6)
        available_scroll = self._max_height - self.SEARCH_HEIGHT - 4 - 12
        scroll_height = min(available_scroll, content_height + 4)
        popup_height = self.SEARCH_HEIGHT + 4 + scroll_height + 12

        self.setFixedSize(popup_width, popup_height)

    # ------------------------------------------------------------------
    # 搜索过滤
    # ------------------------------------------------------------------

    def _on_search_text_changed(self, text: str) -> None:
        keyword = text.strip().lower()
        any_visible = False

        for gname in self._sorted_group_names:
            group_has_match = False
            for group_name, btn, item, plain_text in self._note_button_widgets:
                if group_name != gname:
                    continue
                note_name = str(item.get("name", "") or "").lower()
                match = not keyword or (keyword in note_name) or (keyword in plain_text)
                btn.setVisible(match)
                if match:
                    group_has_match = True

            header = self._group_header_widgets.get(gname)
            if header is not None:
                header.setVisible(group_has_match)
            if group_has_match:
                any_visible = True

        if self._no_results_label is not None:
            self._no_results_label.setVisible(not any_visible and bool(keyword))
        if self._empty_label is not None:
            self._empty_label.setVisible(not any_visible and not keyword)

    # ------------------------------------------------------------------
    # 选择 / 右键
    # ------------------------------------------------------------------

    def _select(self, item: dict[str, Any]) -> None:
        self.close()
        self._on_selected(item)

    def _show_context_menu(self, global_pos: QPoint, item: Optional[dict[str, Any]] = None) -> None:
        if self._on_context_menu is not None:
            self._on_context_menu(global_pos, item)

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    @staticmethod
    def _elide_text(text: str, metrics: QFontMetrics, max_width: int) -> str:
        return metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, max_width))

    def eventFilter(self, obj, event) -> bool:
        if obj is self and event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
            mark_anchor_popup_closed(getattr(self, "_anchor", None), self)
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # 定位 / 显示
    # ------------------------------------------------------------------

    def show_for_anchor(
        self,
        anchor: QWidget,
        *,
        align_right: bool,
        prefer_above: bool = False,
        prefer_above_from_bottom: bool = False,
        offset: QPoint | None = None,
        bounds_widget: QWidget | None = None,
    ) -> None:
        if should_skip_anchor_popup(anchor, self):
            self.close()
            return
        self.installEventFilter(self)
        self._anchor = anchor
        self._align_right = align_right
        self._prefer_above = prefer_above
        self._prefer_above_from_bottom = prefer_above_from_bottom
        self._offset = offset or QPoint(0, 0)
        self._bounds_widget = bounds_widget

        self._position_popup()
        self.show()
        self.raise_()
        self.activateWindow()

        # 聚焦搜索框
        QTimer.singleShot(0, self._focus_search)
        QTimer.singleShot(80, self._focus_search)
        self._scroll_to_active()

    def _focus_search(self) -> None:
        try:
            if self._search_edit is not None:
                self._search_edit.setFocus(Qt.FocusReason.PopupFocusReason)
                input_method = QGuiApplication.inputMethod()
                input_method.update(
                    Qt.InputMethodQuery.ImEnabled
                    | Qt.InputMethodQuery.ImHints
                    | Qt.InputMethodQuery.ImCursorRectangle
                    | Qt.InputMethodQuery.ImSurroundingText
                    | Qt.InputMethodQuery.ImCurrentSelection
                )
        except Exception:
            pass

    def _scroll_to_active(self) -> None:
        if self._scroll is None or self._active_button is None:
            return

        def ensure_active_visible() -> None:
            try:
                if self._scroll is not None and self._active_button is not None:
                    self._scroll.ensureWidgetVisible(self._active_button, 0, 0)
            except RuntimeError:
                pass

        QTimer.singleShot(0, ensure_active_visible)

    def _position_popup(self) -> None:
        anchor = self._anchor
        if anchor is None:
            return
        offset = self._offset

        anchor_top_left = anchor.mapToGlobal(QPoint(0, 0))
        anchor_bottom_left = anchor.mapToGlobal(QPoint(0, anchor.height()))
        if self._align_right:
            x = anchor_bottom_left.x() + anchor.width() - self.width() + offset.x() + 8
        else:
            x = anchor_top_left.x() + offset.x() - 8
        if self._prefer_above and self._prefer_above_from_bottom:
            y = anchor_bottom_left.y() - self.height() + offset.y() + 8
        elif self._prefer_above:
            y = anchor_top_left.y() - self.height() + offset.y() + 8
        else:
            y = anchor_bottom_left.y() + offset.y() - 8

        margin = 6
        screen = QGuiApplication.screenAt(anchor.mapToGlobal(anchor.rect().center()))
        screen_geo = (
            screen.availableGeometry()
            if screen is not None
            else QGuiApplication.primaryScreen().availableGeometry()
        )
        bounds = screen_geo
        if self._bounds_widget is not None:
            widget_geo = self._bounds_widget.frameGeometry()
            if widget_geo.isValid():
                widget_bounds = screen_geo.intersected(widget_geo)
                if widget_bounds.width() >= self.width() + margin * 2 and widget_bounds.height() >= 80:
                    bounds = widget_bounds

        if not self._prefer_above and y + self.height() > bounds.bottom() + 1 - margin:
            y = anchor_top_left.y() - self.height() - offset.y()
        elif self._prefer_above and y < bounds.top() + margin:
            y = anchor_bottom_left.y() + offset.y()

        max_x = bounds.right() - self.width() + 1 - margin
        min_x = bounds.left() + margin
        max_y = bounds.bottom() - self.height() + 1 - margin
        min_y = bounds.top() + margin

        x = max(min_x, min(x, max_x))
        y = max(min_y, min(y, max_y))

        # 屏幕边缘避让最终兜底保护
        max_screen_x = screen_geo.right() - self.width() + 1 - margin
        min_screen_x = screen_geo.left() + margin
        max_screen_y = screen_geo.bottom() - self.height() + 1 - margin
        min_screen_y = screen_geo.top() + margin

        final_x = max(min_screen_x, min(x, max_screen_x))
        final_y = max(min_screen_y, min(y, max_screen_y))

        self.move(QPoint(final_x, final_y))
