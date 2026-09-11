from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QTreeWidgetItem
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QCursor, QFont, QGuiApplication
from PyQt6.QtWidgets import QPushButton, QHBoxLayout, QFrame, QWidget, QGraphicsDropShadowEffect, QLabel, QLineEdit, QVBoxLayout, QDialog, QListWidget
from deepcat.ui.tab_list_popup import RoundedListPopup
from PyQt6.QtWidgets import QFrame, QPushButton
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton


class SmoothNoteIntegrationDialog(QDialog):
    def __init__(self, title: str, default_name: str, items: list[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # 拖拽相关属性
        self._drag_position = QPoint()
        self._drag_active = False

        # 选择结果
        self.result_action = ""  # "new" 或 "append"
        self.result_title = ""   # 目标笔记本名称

        # 主外层布局，提供 margins 保证阴影不会被裁剪
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # 容器 Frame
        self.container = QFrame(self)
        self.container.setObjectName("SmoothIntegrationContainer")
        self.container.setStyleSheet("""
            QFrame#SmoothIntegrationContainer {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
            }
        """)

        # 精致弥散阴影
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(20, 20, 20, 20)
        container_layout.setSpacing(12)

        # 新建笔记本标签引导字
        new_note_label = QLabel("新建笔记本：", self)
        new_note_label.setStyleSheet("color: #64748b; font-size: 12px; font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;")
        container_layout.addWidget(new_note_label)

        # ---------------- 新建笔记本区域 ----------------
        new_area_layout = QHBoxLayout()
        new_area_layout.setContentsMargins(0, 0, 0, 0)
        new_area_layout.setSpacing(8)

        self.line_edit = QLineEdit(self)
        self.line_edit.setText(default_name)
        self.line_edit.setStyleSheet("""
            QLineEdit {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 13px;
                color: #1e293b;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QLineEdit:focus {
                border: 1px solid #cbd5e1;
                background-color: #ffffff;
            }
        """)
        self.line_edit.selectAll()
        new_area_layout.addWidget(self.line_edit, 1)

        self.new_btn = QPushButton("新建", self)
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #ffffff;
                border: 1px solid #1e293b;
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #0f172a;
                border-color: #0f172a;
            }
            QPushButton:pressed {
                background-color: #020617;
                border-color: #020617;
            }
        """)
        self.new_btn.clicked.connect(self._on_new_clicked)
        new_area_layout.addWidget(self.new_btn)

        container_layout.addLayout(new_area_layout)

        # 分割轻线
        sep_line = QFrame(self)
        sep_line.setFrameShape(QFrame.Shape.HLine)
        sep_line.setStyleSheet("background-color: #f1f5f9; max-height: 1px; border: none;")
        container_layout.addWidget(sep_line)

        # ---------------- 追加笔记本区域 ----------------
        desc_append = QLabel("或 选择已有笔记本进行追加：", self)
        desc_append.setStyleSheet("color: #64748b; font-size: 12px; font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;")
        container_layout.addWidget(desc_append)

        from PyQt6.QtWidgets import QListWidget
        self.list_widget = QListWidget(self)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list_widget.addItems(items)
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 6px;
                font-size: 13px;
                color: #334155;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                outline: 0px;
            }
            QListWidget::item {
                padding: 8px 12px;
                border-radius: 6px;
                margin-bottom: 2px;
            }
            QListWidget::item:hover {
                background-color: #f1f5f9;
                color: #0f172a;
            }
            QListWidget::item:selected {
                background-color: #e2e8f0;
                color: #0f172a;
                font-weight: 600;
            }
        """)
        self.list_widget.setMinimumHeight(120)
        self.list_widget.setMaximumHeight(180)
        if items:
            self.list_widget.setCurrentRow(0)

        # 双击列表项直接触发追加
        self.list_widget.itemDoubleClicked.connect(self._on_append_clicked)
        container_layout.addWidget(self.list_widget)

        # ---------------- 底部控制区域 ----------------
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(8)
        bottom_layout.addStretch()

        # 取消按钮 (经典浅灰)
        self.cancel_btn = QPushButton("取消", self)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #475569;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #e2e8f0;
                color: #1e293b;
                border-color: #cbd5e1;
            }
            QPushButton:pressed {
                background-color: #cbd5e1;
                border-color: #cbd5e1;
            }
        """)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        bottom_layout.addWidget(self.cancel_btn)

        # 追加按钮 (高端 Slate 800)
        self.append_btn = QPushButton("追加", self)
        self.append_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.append_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #ffffff;
                border: 1px solid #1e293b;
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #0f172a;
                border-color: #0f172a;
            }
            QPushButton:pressed {
                background-color: #020617;
                border-color: #020617;
            }
        """)
        self.append_btn.clicked.connect(self._on_append_clicked)
        bottom_layout.addWidget(self.append_btn)

        container_layout.addLayout(bottom_layout)
        main_layout.addWidget(self.container)

        # 回车直接触发新建
        self.line_edit.returnPressed.connect(self._on_new_clicked)

        # 设置输入框焦点
        self.line_edit.setFocus()

        # 合理的默认大小
        self.setMinimumWidth(400)
        self.new_btn.setFixedWidth(70)
        self.cancel_btn.setFixedWidth(70)
        self.append_btn.setFixedWidth(70)

        # 显式约束按钮与输入框物理高度齐平一致，调整为舒展大气的 36px 物理高度，并完美水平对齐
        self.line_edit.setFixedHeight(36)
        self.new_btn.setFixedHeight(36)
        self.cancel_btn.setFixedHeight(36)
        self.append_btn.setFixedHeight(36)

    def _on_new_clicked(self) -> None:
        val = self.line_edit.text().strip()
        if not val:
            return
        self.result_action = "new"
        self.result_title = val
        self.accept()

    def _on_append_clicked(self) -> None:
        selected = self.list_widget.currentItem()
        if not selected:
            return
        self.result_action = "append"
        self.result_title = selected.text()
        self.accept()

    def _on_cancel_clicked(self) -> None:
        self.result_action = ""
        self.result_title = ""
        self.reject()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_active:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_active = False

    @classmethod
    def get_integration_result(cls, title: str, default_name: str, items: list[str], parent: Optional[QWidget] = None) -> tuple[str, str, bool]:
        dialog = cls(title, default_name, items, parent)
        # 居中显示于父窗口，若无 parent 则居中于当前鼠标所在屏幕
        if parent:
            parent_geo = parent.geometry()
            parent_center = parent.mapToGlobal(parent_geo.center() - parent_geo.topLeft())
            dialog.move(parent_center - QPoint(dialog.width() // 2, dialog.height() // 2))
        else:
            from PyQt6.QtGui import QGuiApplication, QCursor
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            if screen:
                screen_geo = screen.geometry()
                dialog.adjustSize()
                dialog.move(
                    int(screen_geo.x() + (screen_geo.width() - dialog.width()) // 2),
                    int(screen_geo.y() + (screen_geo.height() - dialog.height()) // 2)
                )
        res = dialog.exec()
        if res == QDialog.DialogCode.Accepted:
            return dialog.result_action, dialog.result_title, True
        return "", "", False


class GroupedSmoothNoteIntegrationDialog(QDialog):
    def __init__(self, title: str, default_name: str, tabs: list[dict[str, Any]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.tabs = tabs

        # 拖拽相关属性
        self._drag_position = QPoint()
        self._drag_active = False

        # 选择结果
        self.result_action = ""  # "new" 或 "append"
        self.result_title = ""   # 目标笔记本名称
        self.result_group = ""   # 分组名
        self.result_index = -1    # 追加目标的 tab 索引

        # 主外层布局，提供 margins 保证阴影不会被裁剪
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # 容器 Frame
        self.container = QFrame(self)
        self.container.setObjectName("SmoothIntegrationContainer")
        self.container.setStyleSheet("""
            QFrame#SmoothIntegrationContainer {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
            }
        """)

        # 精致弥散阴影
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(20, 20, 20, 20)
        container_layout.setSpacing(12)

        # 新建笔记本标签引导字
        new_note_label = QLabel("新建笔记本：", self)
        new_note_label.setStyleSheet("color: #64748b; font-size: 12px; font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;")
        container_layout.addWidget(new_note_label)

        # ---------------- 新建笔记本区域 ----------------
        new_area_layout = QHBoxLayout()
        new_area_layout.setContentsMargins(0, 0, 0, 0)
        new_area_layout.setSpacing(8)

        self.group_btn = QPushButton(self)
        self.group_btn.setText("未分组")
        self.group_btn.setObjectName("GroupSelectButton")
        self.group_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.group_btn.setStyleSheet("""
            QPushButton#GroupSelectButton {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 13px;
                color: #1e293b;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                text-align: left;
                min-width: 100px;
                max-width: 140px;
            }
            QPushButton#GroupSelectButton:hover {
                background-color: #f1f5f9;
                border-color: #cbd5e1;
            }
            QPushButton#GroupSelectButton:pressed {
                background-color: #e2e8f0;
            }
        """)
        self.group_btn.clicked.connect(self._show_group_select_menu)
        new_area_layout.addWidget(self.group_btn)

        self.line_edit = QLineEdit(self)
        self.line_edit.setText(default_name)
        self.line_edit.setStyleSheet("""
            QLineEdit {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 13px;
                color: #1e293b;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QLineEdit:focus {
                border: 1px solid #cbd5e1;
                background-color: #ffffff;
            }
        """)
        self.line_edit.selectAll()
        new_area_layout.addWidget(self.line_edit, 1)

        self.new_btn = QPushButton("新建", self)
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #ffffff;
                border: 1px solid #1e293b;
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #0f172a;
                border-color: #0f172a;
            }
            QPushButton:pressed {
                background-color: #020617;
                border-color: #020617;
            }
        """)
        self.new_btn.clicked.connect(self._on_new_clicked)
        new_area_layout.addWidget(self.new_btn)

        container_layout.addLayout(new_area_layout)

        # 分割轻线
        sep_line = QFrame(self)
        sep_line.setFrameShape(QFrame.Shape.HLine)
        sep_line.setStyleSheet("background-color: #f1f5f9; max-height: 1px; border: none;")
        container_layout.addWidget(sep_line)

        # ---------------- 追加笔记本区域 ----------------
        desc_append = QLabel("或 选择已有笔记本进行追加：", self)
        desc_append.setStyleSheet("color: #64748b; font-size: 12px; font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;")
        container_layout.addWidget(desc_append)

        from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem
        self.tree_widget = QTreeWidget(self)
        self.tree_widget.setColumnCount(1)
        self.tree_widget.setHeaderHidden(True)
        self.tree_widget.setStyleSheet("""
            QTreeWidget {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 6px;
                font-size: 13px;
                color: #334155;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                outline: 0px;
            }
            QTreeWidget::item {
                padding: 6px 4px;
                border-radius: 4px;
            }
            QTreeWidget::item:hover {
                background-color: #f1f5f9;
                color: #0f172a;
            }
            QTreeWidget::item:selected {
                background-color: #e2e8f0;
                color: #0f172a;
                font-weight: 600;
            }
        """)
        self.tree_widget.setMinimumHeight(120)
        self.tree_widget.setMaximumHeight(180)

        # 将已有的笔记本按分组进行整理装填
        grouped_tabs: dict[str, list[dict[str, Any]]] = {}
        for index, tab in enumerate(tabs):
            name = tab.get("name", "").strip()
            if not name:
                continue
            gname = tab.get("group_name", "").strip()
            if not gname:
                gname = "未分组"
            if gname not in grouped_tabs:
                grouped_tabs[gname] = []
            grouped_tabs[gname].append({"index": index, "name": name})

        sorted_gnames = sorted(list(grouped_tabs.keys()))
        if "未分组" in sorted_gnames:
            sorted_gnames.remove("未分组")
            sorted_gnames = ["未分组"] + sorted_gnames

        from PyQt6.QtGui import QFont
        bold_font = QFont()
        bold_font.setBold(True)

        first_note_item = None
        for gname in sorted_gnames:
            group_item = QTreeWidgetItem(self.tree_widget)
            group_item.setText(0, gname)
            group_item.setFont(0, bold_font)
            # 禁用选择
            group_item.setFlags(group_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            # 默认折叠
            group_item.setExpanded(False)

            for note in grouped_tabs[gname]:
                note_item = QTreeWidgetItem(group_item)
                name = str(note.get("name", ""))
                note_item.setText(0, name)
                note_item.setData(0, Qt.ItemDataRole.UserRole, int(note.get("index", -1)))
                if first_note_item is None:
                    first_note_item = note_item

        # 默认选中第一个笔记本，并展开其父分组
        if first_note_item is not None:
            first_note_item.parent().setExpanded(True)
            self.tree_widget.setCurrentItem(first_note_item)

        # 双击列表项直接触发追加
        self.tree_widget.itemClicked.connect(self._on_tree_item_clicked)
        self.tree_widget.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        container_layout.addWidget(self.tree_widget)

        # ---------------- 底部控制区域 ----------------
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(8)
        bottom_layout.addStretch()

        # 取消按钮 (经典浅灰)
        self.cancel_btn = QPushButton("取消", self)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #475569;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #e2e8f0;
                color: #1e293b;
                border-color: #cbd5e1;
            }
            QPushButton:pressed {
                background-color: #cbd5e1;
                border-color: #cbd5e1;
            }
        """)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        bottom_layout.addWidget(self.cancel_btn)

        # 追加按钮 (高端 Slate 800)
        self.append_btn = QPushButton("追加", self)
        self.append_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.append_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #ffffff;
                border: 1px solid #1e293b;
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #0f172a;
                border-color: #0f172a;
            }
            QPushButton:pressed {
                background-color: #020617;
                border-color: #020617;
            }
        """)
        self.append_btn.clicked.connect(self._on_append_clicked)
        bottom_layout.addWidget(self.append_btn)

        container_layout.addLayout(bottom_layout)
        main_layout.addWidget(self.container)

        # 回车直接触发新建
        self.line_edit.returnPressed.connect(self._on_new_clicked)

        # 设置输入框焦点
        self.line_edit.setFocus()

        # 合理的默认大小
        self.setMinimumWidth(400)
        self.new_btn.setFixedWidth(70)
        self.cancel_btn.setFixedWidth(70)
        self.append_btn.setFixedWidth(70)

        # 显式约束按钮与输入框物理高度齐平一致，调整为舒展大气的 36px 物理高度，并完美水平对齐
        self.line_edit.setFixedHeight(36)
        self.group_btn.setFixedHeight(36)
        self.new_btn.setFixedHeight(36)
        self.cancel_btn.setFixedHeight(36)
        self.append_btn.setFixedHeight(36)

    def _show_group_select_menu(self) -> None:
        groups = sorted(list({t.get("group_name", "").strip() for t in self.tabs if t.get("group_name", "").strip()}))
        items = []
        items.append({"text": "未分组", "group_name": ""})
        for g in groups:
            items.append({"text": g, "group_name": g})

        active_index = -1
        current_g = self.result_group
        for idx, item in enumerate(items):
            if item.get("group_name") == current_g:
                active_index = idx
                break

        from deepcat.ui.tab_list_popup import RoundedListPopup

        def on_selected(item: dict[str, Any]) -> None:
            g_name = item.get("group_name", "")
            self.result_group = g_name
            self.group_btn.setText(g_name if g_name else "未分组")

        popup = RoundedListPopup(
            items,
            on_selected,
            active_index=active_index,
            empty_text="暂无分组",
            parent=self
        )
        self._group_select_popup = popup
        popup.show_for_anchor(self.group_btn, align_right=False, bounds_widget=self)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if item.childCount() > 0:
            item.setExpanded(not item.isExpanded())

    def _on_tree_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if item.childCount() == 0:
            self._on_append_clicked()

    def _on_new_clicked(self) -> None:
        val = self.line_edit.text().strip()
        if not val:
            return
        self.result_action = "new"
        self.result_title = val
        self.accept()

    def _on_append_clicked(self) -> None:
        selected = self.tree_widget.currentItem()
        if not selected or selected.childCount() > 0:
            return
        self.result_action = "append"
        self.result_title = selected.text(0)
        try:
            self.result_index = int(selected.data(0, Qt.ItemDataRole.UserRole))
        except Exception:
            self.result_index = -1

        # 寻找对应的分组名
        parent_item = selected.parent()
        if parent_item:
            g_name = parent_item.text(0).strip()
            if g_name == "未分组":
                g_name = ""
            self.result_group = g_name
        else:
            self.result_group = ""

        self.accept()

    def _on_cancel_clicked(self) -> None:
        self.result_action = ""
        self.result_title = ""
        self.result_group = ""
        self.result_index = -1
        self.reject()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_active:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_active = False

    @classmethod
    def get_integration_result(cls, title: str, default_name: str, tabs: list[dict[str, Any]], parent: Optional[QWidget] = None) -> tuple[str, str, str, int, bool]:
        dialog = cls(title, default_name, tabs, parent)
        # 居中显示于父窗口，若无 parent 则居中于当前鼠标所在屏幕
        if parent:
            parent_geo = parent.geometry()
            parent_center = parent.mapToGlobal(parent_geo.center() - parent_geo.topLeft())
            dialog.move(parent_center - QPoint(dialog.width() // 2, dialog.height() // 2))
        else:
            from PyQt6.QtGui import QGuiApplication, QCursor
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            if screen:
                screen_geo = screen.geometry()
                dialog.adjustSize()
                dialog.move(
                    int(screen_geo.x() + (screen_geo.width() - dialog.width()) // 2),
                    int(screen_geo.y() + (screen_geo.height() - dialog.height()) // 2)
                )
        res = dialog.exec()
        if res == QDialog.DialogCode.Accepted:
            return dialog.result_action, dialog.result_title, dialog.result_group, dialog.result_index, True
        return "", "", "", -1, False
