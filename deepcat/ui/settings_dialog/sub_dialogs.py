from __future__ import annotations

from pathlib import Path
from typing import Callable
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from deepcat.settings_store import (
    DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
    DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
    DEFAULT_REPLY_PROMPT_BUTTON_NAME,
    DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
    PROMPT_BUTTON_NAME_MAX_LENGTH,
)
from deepcat.ui.main_window.compact import StyledDialog


def get_card_icon_pixmap(icon_type: str, size: int = 30) -> QPixmap:
    from PyQt6.QtGui import QPixmap, QPainter, QColor, QBrush, QPen, QFont, QPainterPath
    from PyQt6.QtCore import Qt, QRectF, QPointF

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    rect = QRectF(0, 0, size, size)

    # 颜色配置
    if icon_type == "translate":
        bg_color = QColor("#dde1ff")
        stroke_color = QColor("#0040df")
    elif icon_type in ("code", "search"):
        bg_color = QColor("#d0e1fb")
        stroke_color = QColor("#505f76")
    elif icon_type == "summarize":
        bg_color = QColor("#e6f4ea")
        stroke_color = QColor("#137333")
    elif icon_type == "write":
        bg_color = QColor("#ffdad6")
        stroke_color = QColor("#ba1a1a")
    elif icon_type == "musk":
        bg_color = QColor("#f1f5f9") # 太空灰
        stroke_color = QColor("#0f172a") # 深邃黑
    elif icon_type == "buffett":
        bg_color = QColor("#fef3c7") # 琥珀金
        stroke_color = QColor("#b45309") # 琥珀棕
    elif icon_type == "decision":
        bg_color = QColor("#e0f2fe") # 澄蓝
        stroke_color = QColor("#0369a1") # 深海蓝
    elif icon_type == "creative":
        bg_color = QColor("#fce7f3") # 浪漫粉
        stroke_color = QColor("#be185d") # 玫瑰红
    elif icon_type == "code_expert":
        bg_color = QColor("#ece9fc") # 极光紫
        stroke_color = QColor("#5b21b6") # 深紫罗兰
    elif icon_type == "marketing":
        bg_color = QColor("#ffedd5") # 亮橙
        stroke_color = QColor("#c2410c") # 柿子红
    elif icon_type == "healing":
        bg_color = QColor("#dcfce7") # 复苏绿
        stroke_color = QColor("#15803d") # 松石绿
    elif icon_type == "interview":
        bg_color = QColor("#ccfbf1") # 浅碧绿
        stroke_color = QColor("#0f766e") # 深湖绿
    elif icon_type == "english":
        bg_color = QColor("#e0e7ff") # 皇家浅蓝
        stroke_color = QColor("#4338ca") # 皇家深蓝
    elif icon_type == "weekly":
        bg_color = QColor("#f3e8ff") # 梦幻淡紫
        stroke_color = QColor("#6b21a8") # 罗兰深紫
    else:
        bg_color = QColor("#f1f5f9")
        stroke_color = QColor("#64748b")

    painter.setBrush(QBrush(bg_color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(rect, size * 0.26, size * 0.26)

    cx, cy = rect.center().x(), rect.center().y()
    scale = size / 30.0

    painter.setBrush(QBrush(Qt.GlobalColor.transparent))

    if icon_type == "translate":
        font = QFont("Microsoft YaHei")
        font.setBold(False)
        font.setWeight(QFont.Weight.Medium)
        font.setPointSizeF(8.5 * scale)
        painter.setFont(font)
        painter.setPen(stroke_color)
        painter.drawText(QRectF(cx - 8.5 * scale, cy - 9.5 * scale, 12 * scale, 12 * scale), Qt.AlignmentFlag.AlignCenter, "文")
        painter.drawText(QRectF(cx - 1.5 * scale, cy - 0.5 * scale, 12 * scale, 12 * scale), Qt.AlignmentFlag.AlignCenter, "A")
    elif icon_type == "code":
        painter.setPen(QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawRoundedRect(QRectF(cx - 7 * scale, cy - 7 * scale, 14 * scale, 14 * scale), 2.5 * scale, 2.5 * scale)
        painter.drawLine(QPointF(cx - 7 * scale, cy - 3 * scale), QPointF(cx + 7 * scale, cy - 3 * scale))

        old_pen = painter.pen()
        painter.setBrush(QBrush(stroke_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx - 4 * scale, cy - 5 * scale), 1.0 * scale, 1.0 * scale)
        painter.drawEllipse(QPointF(cx - 1.5 * scale, cy - 5 * scale), 1.0 * scale, 1.0 * scale)
        painter.drawEllipse(QPointF(cx + 1 * scale, cy - 5 * scale), 1.0 * scale, 1.0 * scale)

        painter.setPen(old_pen)
        painter.setBrush(QBrush(Qt.GlobalColor.transparent))
        path_prompt = QPainterPath()
        path_prompt.moveTo(cx - 3 * scale, cy - 1 * scale)
        path_prompt.lineTo(cx - 0.5 * scale, cy + 1 * scale)
        path_prompt.lineTo(cx - 3 * scale, cy + 3 * scale)
        painter.drawPath(path_prompt)
        painter.drawLine(QPointF(cx + 1 * scale, cy + 3 * scale), QPointF(cx + 4 * scale, cy + 3 * scale))
    elif icon_type == "search":
        painter.setPen(QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawEllipse(QPointF(cx - 2.0 * scale, cy - 2.0 * scale), 4.2 * scale, 4.2 * scale)
        painter.drawLine(QPointF(cx + 0.8 * scale, cy + 0.8 * scale), QPointF(cx + 6.0 * scale, cy + 6.0 * scale))
    elif icon_type == "summarize":
        painter.setPen(QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawRoundedRect(QRectF(cx - 6 * scale, cy - 8 * scale, 12 * scale, 16 * scale), 2.0 * scale, 2.0 * scale)
        painter.drawLine(QPointF(cx - 3 * scale, cy - 4 * scale), QPointF(cx + 3 * scale, cy - 4 * scale))
        painter.drawLine(QPointF(cx - 3 * scale, cy), QPointF(cx + 3 * scale, cy))
        painter.drawLine(QPointF(cx - 3 * scale, cy + 4 * scale), QPointF(cx + 1 * scale, cy + 4 * scale))
    elif icon_type == "write":
        pen_shaft = QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_shaft)
        painter.drawLine(QPointF(cx - 6 * scale, cy + 6 * scale), QPointF(cx + 1 * scale, cy - 1 * scale))

        pen_tip = QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_tip)
        painter.drawLine(QPointF(cx + 1 * scale, cy - 1 * scale), QPointF(cx + 3 * scale, cy - 3 * scale))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(stroke_color))
        sx, sy = cx + 5 * scale, cy - 5.5 * scale
        r1 = 2.8 * scale
        path_s1 = QPainterPath()
        path_s1.moveTo(sx, sy - r1)
        path_s1.quadTo(sx, sy, sx + r1, sy)
        path_s1.quadTo(sx, sy, sx, sy + r1)
        path_s1.quadTo(sx, sy, sx - r1, sy)
        path_s1.quadTo(sx, sy, sx, sy - r1)
        painter.drawPath(path_s1)

        sx2, sy2 = cx + 0, cy - 6.5 * scale
        r2 = 1.4 * scale
        path_s2 = QPainterPath()
        path_s2.moveTo(sx2, sy2 - r2)
        path_s2.quadTo(sx2, sy2, sx2 + r2, sy2)
        path_s2.quadTo(sx2, sy2, sx2, sy2 + r2)
        path_s2.quadTo(sx2, sy2, sx2 - r2, sy2)
        path_s2.quadTo(sx2, sy2, sx2, sy2 - r2)
        painter.drawPath(path_s2)

        sx3, sy3 = cx + 5.5 * scale, cy - 0.5 * scale
        r3 = 1.2 * scale
        path_s3 = QPainterPath()
        path_s3.moveTo(sx3, sy3 - r3)
        path_s3.quadTo(sx3, sy3, sx3 + r3, sy3)
        path_s3.quadTo(sx3, sy3, sx3, sy3 + r3)
        path_s3.quadTo(sx3, sy3, sx3 - r3, sy3)
        path_s3.quadTo(sx3, sy3, sx3, sy3 - r3)
        painter.drawPath(path_s3)
    elif icon_type == "musk":
        path_rocket = QPainterPath()
        path_rocket.moveTo(cx, cy - 8 * scale)
        path_rocket.lineTo(cx + 3 * scale, cy - 2 * scale)
        path_rocket.lineTo(cx + 3 * scale, cy + 4 * scale)
        path_rocket.lineTo(cx + 5 * scale, cy + 6 * scale)
        path_rocket.lineTo(cx + 2 * scale, cy + 6 * scale)
        path_rocket.lineTo(cx, cy + 3 * scale)
        path_rocket.lineTo(cx - 2 * scale, cy + 6 * scale)
        path_rocket.lineTo(cx - 5 * scale, cy + 6 * scale)
        path_rocket.lineTo(cx - 3 * scale, cy + 4 * scale)
        path_rocket.lineTo(cx - 3 * scale, cy - 2 * scale)
        path_rocket.closeSubpath()
        painter.setPen(QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path_rocket)
        painter.drawLine(QPointF(cx, cy + 5 * scale), QPointF(cx, cy + 8 * scale))
    elif icon_type == "buffett":
        painter.setPen(QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path_s = QPainterPath()
        path_s.moveTo(cx + 2.5 * scale, cy - 3.5 * scale)
        path_s.cubicTo(cx - 2.5 * scale, cy - 3.5 * scale, cx - 2.5 * scale, cy + 0.5 * scale, cx, cy + 0.5 * scale)
        path_s.cubicTo(cx + 2.5 * scale, cy + 0.5 * scale, cx + 2.5 * scale, cy + 4.5 * scale, cx - 2.5 * scale, cy + 4.5 * scale)
        painter.drawPath(path_s)
        painter.drawLine(QPointF(cx, cy - 6 * scale), QPointF(cx, cy + 6 * scale))
    elif icon_type == "decision":
        painter.setPen(QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QPointF(cx, cy + 6 * scale), QPointF(cx, cy + 1 * scale))
        painter.drawLine(QPointF(cx, cy + 1 * scale), QPointF(cx - 5 * scale, cy - 3 * scale))
        painter.drawLine(QPointF(cx, cy + 1 * scale), QPointF(cx + 5 * scale, cy - 3 * scale))
        painter.setBrush(QBrush(stroke_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy + 6 * scale), 2.0 * scale, 2.0 * scale)
        painter.drawEllipse(QPointF(cx - 5 * scale, cy - 3 * scale), 2.0 * scale, 2.0 * scale)
        painter.drawEllipse(QPointF(cx + 5 * scale, cy - 3 * scale), 2.0 * scale, 2.0 * scale)
    elif icon_type == "creative":
        painter.setPen(QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QPointF(cx - 6 * scale, cy + 6 * scale), QPointF(cx + 3 * scale, cy - 3 * scale))
        path_feather = QPainterPath()
        path_feather.moveTo(cx - 2 * scale, cy + 2 * scale)
        path_feather.cubicTo(cx - 1 * scale, cy - 1 * scale, cx + 4 * scale, cy - 6 * scale, cx + 6 * scale, cy - 8 * scale)
        path_feather.cubicTo(cx + 5 * scale, cy - 4 * scale, cx + 0 * scale, cy + 0 * scale, cx - 2 * scale, cy + 2 * scale)
        painter.drawPath(path_feather)

        painter.setBrush(QBrush(stroke_color))
        painter.setPen(Qt.PenStyle.NoPen)
        sx, sy = cx - 6 * scale, cy + 2 * scale
        r = 1.0 * scale
        path_star = QPainterPath()
        path_star.moveTo(sx, sy - r)
        path_star.quadTo(sx, sy, sx + r, sy)
        path_star.quadTo(sx, sy, sx, sy + r)
        path_star.quadTo(sx, sy, sx - r, sy)
        path_star.quadTo(sx, sy, sx, sy - r)
        painter.drawPath(path_star)
    elif icon_type == "code_expert":
        painter.setPen(QPen(stroke_color, 1.4 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path_left = QPainterPath()
        path_left.moveTo(cx - 3 * scale, cy - 5 * scale)
        path_left.lineTo(cx - 7 * scale, cy)
        path_left.lineTo(cx - 3 * scale, cy + 5 * scale)
        painter.drawPath(path_left)

        path_right = QPainterPath()
        path_right.moveTo(cx + 3 * scale, cy - 5 * scale)
        path_right.lineTo(cx + 7 * scale, cy)
        path_right.lineTo(cx + 3 * scale, cy + 5 * scale)
        painter.drawPath(path_right)

        painter.drawLine(QPointF(cx + 1 * scale, cy - 6 * scale), QPointF(cx - 1 * scale, cy + 6 * scale))
    elif icon_type == "marketing":
        painter.setPen(QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path_horn = QPainterPath()
        path_horn.moveTo(cx - 5 * scale, cy - 2 * scale)
        path_horn.lineTo(cx - 1 * scale, cy - 2 * scale)
        path_horn.lineTo(cx + 4 * scale, cy - 6 * scale)
        path_horn.lineTo(cx + 4 * scale, cy + 6 * scale)
        path_horn.lineTo(cx - 1 * scale, cy + 2 * scale)
        path_horn.lineTo(cx - 5 * scale, cy + 2 * scale)
        path_horn.closeSubpath()
        painter.drawPath(path_horn)
        painter.drawLine(QPointF(cx - 2 * scale, cy + 1 * scale), QPointF(cx - 4 * scale, cy + 5 * scale))

        painter.drawLine(QPointF(cx + 7 * scale, cy - 3 * scale), QPointF(cx + 8 * scale, cy - 1 * scale))
        painter.drawLine(QPointF(cx + 8 * scale, cy), QPointF(cx + 8 * scale, cy))
        painter.drawLine(QPointF(cx + 7 * scale, cy + 3 * scale), QPointF(cx + 8 * scale, cy + 1 * scale))
    elif icon_type == "healing":
        painter.setPen(QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path_heart = QPainterPath()
        path_heart.moveTo(cx, cy - 3 * scale)
        path_heart.cubicTo(cx - 3 * scale, cy - 6 * scale, cx - 7 * scale, cy - 3 * scale, cx - 7 * scale, cy + 1 * scale)
        path_heart.cubicTo(cx - 7 * scale, cy + 4 * scale, cx - 3 * scale, cy + 6.5 * scale, cx, cy + 9.5 * scale)
        path_heart.cubicTo(cx + 3 * scale, cy + 6.5 * scale, cx + 7 * scale, cy + 1 * scale, cx + 7 * scale, cy - 3 * scale)
        path_heart.cubicTo(cx + 7 * scale, cy - 6 * scale, cx + 3 * scale, cy - 3 * scale, cx, cy - 3 * scale)
        painter.drawPath(path_heart)
    elif icon_type == "interview":
        painter.setPen(QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawRoundedRect(QRectF(cx - 7 * scale, cy - 7 * scale, 10 * scale, 8 * scale), 2 * scale, 2 * scale)
        path_tail1 = QPainterPath()
        path_tail1.moveTo(cx - 4 * scale, cy + 1 * scale)
        path_tail1.lineTo(cx - 6 * scale, cy + 3 * scale)
        path_tail1.lineTo(cx - 6 * scale, cy + 1 * scale)
        painter.drawPath(path_tail1)

        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(QRectF(cx - 2 * scale, cy - 2 * scale, 10 * scale, 8 * scale), 2 * scale, 2 * scale)
        painter.setBrush(QBrush(Qt.GlobalColor.transparent))
        painter.drawRoundedRect(QRectF(cx - 2 * scale, cy - 2 * scale, 10 * scale, 8 * scale), 2 * scale, 2 * scale)
        path_tail2 = QPainterPath()
        path_tail2.moveTo(cx + 4 * scale, cy + 6 * scale)
        path_tail2.lineTo(cx + 6 * scale, cy + 8 * scale)
        path_tail2.lineTo(cx + 6 * scale, cy + 6 * scale)
        painter.drawPath(path_tail2)
    elif icon_type == "english":
        font = QFont("Microsoft YaHei")
        font.setBold(True)
        font.setPointSizeF(9.0 * scale)
        painter.setFont(font)
        painter.setPen(stroke_color)
        painter.drawText(QRectF(cx - 10 * scale, cy - 8 * scale, 20 * scale, 16 * scale), Qt.AlignmentFlag.AlignCenter, "EN")
    elif icon_type == "weekly":
        painter.setPen(QPen(stroke_color, 1.2 * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawRoundedRect(QRectF(cx - 6 * scale, cy - 7 * scale, 12 * scale, 14 * scale), 1.5 * scale, 1.5 * scale)
        painter.drawLine(QPointF(cx - 3 * scale, cy - 9 * scale), QPointF(cx - 3 * scale, cy - 7 * scale))
        painter.drawLine(QPointF(cx + 3 * scale, cy - 9 * scale), QPointF(cx + 3 * scale, cy - 7 * scale))
        painter.drawLine(QPointF(cx - 2 * scale, cy - 2 * scale), QPointF(cx + 3 * scale, cy - 2 * scale))
        painter.drawLine(QPointF(cx - 2 * scale, cy + 2 * scale), QPointF(cx + 3 * scale, cy + 2 * scale))
        path_tick = QPainterPath()
        path_tick.moveTo(cx - 4.5 * scale, cy - 2.5 * scale)
        path_tick.lineTo(cx - 3.5 * scale, cy - 1.5 * scale)
        path_tick.lineTo(cx - 2.5 * scale, cy - 3.5 * scale)
        painter.drawPath(path_tick)
    else:
        painter.setPen(QPen(stroke_color, 1.2 * scale))
        painter.drawEllipse(QRectF(cx - 5 * scale, cy - 5 * scale, 10 * scale, 10 * scale))

    painter.end()
    return pixmap


class _CardIconComboBox(QComboBox):
    def showPopup(self) -> None:
        current: QWidget | None = self
        while current is not None:
            show_popup = getattr(current, "_show_icon_type_popup", None)
            if callable(show_popup):
                show_popup()
                return
            current = current.parentWidget()
        super().showPopup()


class _AddProviderDialog(StyledDialog):
    def __init__(self, parent: QWidget | None = None, name_val: str = "", remark_val: str = "") -> None:
        if parent is not None and not isinstance(parent, QWidget):
            parent = None
        super().__init__(parent)
        if name_val:
            self.setWindowTitle("编辑分组")
        else:
            self.setWindowTitle("添加分组")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        form_layout = QFormLayout()
        form_layout.setSpacing(8)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("请输入新分组名称")
        self.name_edit.setStyleSheet("QLineEdit { padding: 6px; border: 1px solid #cbd5e1; border-radius: 4px; }")
        if name_val:
            self.name_edit.setText(name_val)

        self.remark_edit = QLineEdit()
        self.remark_edit.setPlaceholderText("请输入分组备注（可选）")
        self.remark_edit.setStyleSheet("QLineEdit { padding: 6px; border: 1px solid #cbd5e1; border-radius: 4px; }")
        if remark_val:
            self.remark_edit.setText(remark_val)

        form_layout.addRow("分组名称：", self.name_edit)
        form_layout.addRow("分组备注：", self.remark_edit)
        layout.addLayout(form_layout)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch(1)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #475569;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #e2e8f0;
                color: #1e293b;
            }
            QPushButton:pressed {
                background-color: #cbd5e1;
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)

        self.ok_btn = QPushButton("确认")
        self.ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #0f172a;
            }
            QPushButton:pressed {
                background-color: #020617;
            }
        """)
        self.ok_btn.clicked.connect(self.accept)

        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.ok_btn)
        layout.addLayout(btn_layout)


class _AddModelIdDialog(StyledDialog):
    def __init__(self, parent: QWidget | None = None, name_val: str = "") -> None:
        if parent is not None and not isinstance(parent, QWidget):
            parent = None
        super().__init__(parent)
        if name_val:
            self.setWindowTitle("编辑模型 ID")
        else:
            self.setWindowTitle("填写模型 ID")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        form_layout = QFormLayout()
        form_layout.setSpacing(8)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("请输入接口真实模型 ID，如 gpt-5、glm-4-flash")
        self.name_edit.setToolTip("模型 ID 会写入 model_name，用于请求 API；它不是模型别名。")
        self.name_edit.setStyleSheet("QLineEdit { padding: 6px; border: 1px solid #cbd5e1; border-radius: 4px; }")
        if name_val:
            self.name_edit.setText(name_val)

        form_layout.addRow("模型 ID：", self.name_edit)
        layout.addLayout(form_layout)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch(1)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #475569;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #e2e8f0;
                color: #1e293b;
            }
            QPushButton:pressed {
                background-color: #cbd5e1;
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)

        self.ok_btn = QPushButton("确认")
        self.ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #0f172a;
            }
            QPushButton:pressed {
                background-color: #020617;
            }
        """)
        self.ok_btn.clicked.connect(self.accept)

        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.ok_btn)
        layout.addLayout(btn_layout)


class PromptEditDialog(StyledDialog):
    def __init__(
        self,
        parent: QWidget,
        title: str,
        text: str,
        default_text: str,
        button_name: str = "",
        default_button_name: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(620, 540)
        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {MAIN_WINDOW_BACKGROUND};
            }}
            QLabel {{
                color: #111827;
                font-size: 13px;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }}
            QLineEdit {{
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 4px 10px;
                color: #111827;
                font-size: 13px;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }}
            QLineEdit:hover {{
                border: 1px solid #cbd5e1;
            }}
            QLineEdit:focus {{
                border: 1px solid #cbd5e1;
                background: white;
            }}
            QTextEdit {{
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 8px;
                color: #111827;
                font-size: 13px;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }}
            QTextEdit:hover {{
                border: 1px solid #cbd5e1;
            }}
            QTextEdit:focus {{
                border: 1px solid #cbd5e1;
                background: white;
            }}
        """)
        self._default_text = str(default_text or "")
        self._default_button_name = str(default_button_name or "").strip()
        self._button_name_editor = QLineEdit()
        self._button_name_editor.setMaxLength(int(PROMPT_BUTTON_NAME_MAX_LENGTH))
        self._button_name_editor.setPlaceholderText(self._default_button_name or "按钮名称")
        self._button_name_editor.setText(str(button_name or self._default_button_name or "").strip())
        self._editor = QTextEdit()
        self._editor.setAcceptRichText(False)
        self._editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._editor.setPlainText(str(text or default_text or ""))
        tips = QLabel("可使用 [划词内容] 作为占位符；没有占位符时，软件会把划词内容自动追加到提示词后面。")
        tips.setWordWrap(True)
        name_row = QWidget()
        name_layout = QHBoxLayout(name_row)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(8)
        name_label = QLabel("按钮名称")
        name_label.setFixedWidth(64)
        name_layout.addWidget(name_label)
        name_layout.addWidget(self._button_name_editor, 1)
        reset_btn = QPushButton("恢复默认")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #0f172a;
            }
            QPushButton:pressed {
                background-color: #020617;
            }
        """)

        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #475569;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #e2e8f0;
                color: #1e293b;
            }
            QPushButton:pressed {
                background-color: #cbd5e1;
            }
        """)

        ok_btn = QPushButton("确认")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QPushButton:hover {
                background-color: #0f172a;
            }
            QPushButton:pressed {
                background-color: #020617;
            }
        """)

        reset_btn.clicked.connect(self._reset_to_default)
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self.accept)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addWidget(reset_btn)
        button_row.addStretch(1)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(ok_btn)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(name_row)
        layout.addWidget(tips)
        layout.addWidget(self._editor, 1)
        layout.addLayout(button_row)
        self.setLayout(layout)
        try:
            from deepcat.ui.settings_dialog.dialog import SettingsDialog
            SettingsDialog._install_custom_text_context_menus(self, self)
        except Exception:
            pass

        reset_btn.setFixedWidth(80)
        cancel_btn.setFixedWidth(80)
        ok_btn.setFixedWidth(80)

    def prompt_text(self) -> str:
        return str(self._editor.toPlainText() or "").strip()

    def button_name(self) -> str:
        return str(self._button_name_editor.text() or "").strip()

    def _reset_to_default(self) -> None:
        self._button_name_editor.setText(self._default_button_name)
        self._editor.setPlainText(self._default_text)

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


class PromptSettingsEditPopup(QWidget):
    def __init__(self, parent: QWidget, on_action_triggered: Callable[[str], None]) -> None:
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._parent = parent
        self._on_action_triggered = on_action_triggered
        self.setObjectName("PromptSettingsEditPopup")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.btn_reply = QPushButton(DEFAULT_REPLY_PROMPT_BUTTON_NAME)
        self.btn_optimize = QPushButton(DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME)
        self.btn_explain = QPushButton(DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME)
        self.btn_summarize = QPushButton(DEFAULT_SUMMARY_PROMPT_BUTTON_NAME)
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

        for btn in [self.btn_reply, self.btn_optimize, self.btn_explain, self.btn_summarize]:
            btn.setIconSize(QSize(13, 13))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: #f6f8fb;
                    color: #334155;
                    border: none;
                    border-radius: 6px;
                    padding: 5px 12px;
                    font-size: 12px;
                    font-weight: 700;
                    text-align: left;
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
            layout.addWidget(btn)

        self.btn_reply.clicked.connect(lambda: self._trigger("reply"))
        self.btn_optimize.clicked.connect(lambda: self._trigger("optimize"))
        self.btn_explain.clicked.connect(lambda: self._trigger("explain"))
        self.btn_summarize.clicked.connect(lambda: self._trigger("summarize"))

        self.setStyleSheet("""
            QWidget#PromptSettingsEditPopup {
                background: transparent;
                border: none;
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


class AddChatCardDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        title: str = "添加快捷卡片",
        initial_title: str = "",
        initial_icon_type: str = "write",
        initial_prompt: str = ""
    ) -> None:
        from PyQt6.QtCore import Qt, QPoint, QSize
        from PyQt6.QtGui import QIcon, QColor
        from PyQt6.QtWidgets import (
            QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
            QTextEdit, QPushButton, QWidget, QGraphicsDropShadowEffect
        )
        from pathlib import Path
        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND

        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(540, 460)

        self._drag_position = QPoint()

        # 1. 投影外层容器
        container = QWidget(self)
        container.setObjectName("DialogContainer")
        container.setStyleSheet(f"""
            QWidget#DialogContainer {{
                background-color: {MAIN_WINDOW_BACKGROUND};
                border: 1px solid rgba(226, 232, 240, 0.8);
                border-radius: 12px;
            }}
        """)

        # 发光微悬浮阴影
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setColor(QColor(15, 23, 42, 38))
        shadow.setOffset(0, 4)
        container.setGraphicsEffect(shadow)

        # 对话框主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # 2. 自定义标题栏 (TitleBar)
        self.title_bar = QWidget(container)
        self.title_bar.setFixedHeight(40)
        self.title_bar.setObjectName("TitleBar")
        self.title_bar.setStyleSheet(f"""
            QWidget#TitleBar {{
                background-color: {MAIN_WINDOW_BACKGROUND};
                border-top-left-radius: 11px;
                border-top-right-radius: 11px;
                border-bottom: 1px solid rgba(226, 232, 240, 0.5);
            }}
            QLabel#TitleLabel {{
                color: #0f172a;
                font-size: 14px;
                font-weight: 700;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }}
        """)

        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(16, 0, 12, 0)

        # 标题文本
        self.title_label = QLabel(title, self.title_bar)
        self.title_label.setObjectName("TitleLabel")
        title_layout.addWidget(self.title_label)
        title_layout.addStretch(1)

        # 关闭按钮
        close_btn = QPushButton(self.title_bar)
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        asset_dir = Path(__file__).resolve().parent.parent / "assets"
        close_btn.setIcon(QIcon(str(asset_dir / "icon_action_close.svg")))
        close_btn.setIconSize(QSize(12, 12))
        close_btn.setStyleSheet("""
            QPushButton {
                border: none;
                background: transparent;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #ef4444;
            }
        """)
        close_btn.clicked.connect(self.reject)
        title_layout.addWidget(close_btn)

        container_layout.addWidget(self.title_bar)

        # 3. 表单内容区
        content_widget = QWidget(container)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(18, 16, 18, 16)
        content_layout.setSpacing(10)

        content_widget.setStyleSheet("""
            QLabel {
                color: #475569;
                font-size: 13px;
                font-weight: 600;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QLineEdit {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 6px 12px;
                color: #1f2937;
                font-size: 13px;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QLineEdit:hover {
                border: 1px solid #cbd5e1;
            }
            QLineEdit:focus {
                border: 1px solid #3b82f6;
            }
            QTextEdit {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 8px;
                color: #1f2937;
                font-size: 13px;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }
            QTextEdit:hover {
                border: 1px solid #cbd5e1;
            }
            QTextEdit:focus {
                border: 1px solid #3b82f6;
            }
        """)

        # 字段1：卡片标题
        content_layout.addWidget(QLabel("卡片标题"))
        self._title_editor = QLineEdit(content_widget)
        self._title_editor.setPlaceholderText("请输入卡片展示的标题")
        self._title_editor.setText(initial_title)
        content_layout.addWidget(self._title_editor)

        # 字段2：卡片图标类型
        content_layout.addWidget(QLabel("卡片图标类型"))
        self._icon_combo_popup = None
        self._icon_combo = _CardIconComboBox(content_widget)

        # 添加 10 个适合提示词的内置默认图标
        musk_icon = QIcon(get_card_icon_pixmap("musk", 32))
        buffett_icon = QIcon(get_card_icon_pixmap("buffett", 32))
        decision_icon = QIcon(get_card_icon_pixmap("decision", 32))
        creative_icon = QIcon(get_card_icon_pixmap("creative", 32))
        code_expert_icon = QIcon(get_card_icon_pixmap("code_expert", 32))
        marketing_icon = QIcon(get_card_icon_pixmap("marketing", 32))
        healing_icon = QIcon(get_card_icon_pixmap("healing", 32))
        interview_icon = QIcon(get_card_icon_pixmap("interview", 32))
        english_icon = QIcon(get_card_icon_pixmap("english", 32))
        weekly_icon = QIcon(get_card_icon_pixmap("weekly", 32))

        self._icon_combo.addItem(musk_icon, "马斯克分身", "musk")
        self._icon_combo.addItem(buffett_icon, "巴菲特分身", "buffett")
        self._icon_combo.addItem(decision_icon, "决策助手", "decision")
        self._icon_combo.addItem(creative_icon, "创意写作", "creative")
        self._icon_combo.addItem(code_expert_icon, "代码专家", "code_expert")
        self._icon_combo.addItem(marketing_icon, "营销文案", "marketing")
        self._icon_combo.addItem(healing_icon, "情绪疗愈", "healing")
        self._icon_combo.addItem(interview_icon, "求职面试", "interview")
        self._icon_combo.addItem(english_icon, "英语教练", "english")
        self._icon_combo.addItem(weekly_icon, "周报整理", "weekly")

        upload_icon = QIcon(str(asset_dir / "icon_todo_edit.svg"))
        self._icon_combo.addItem(upload_icon, "上传自定义图片...", "custom_upload")

        # 针对旧版默认卡片做优雅兼容性回显支持：如果当前卡片仍使用旧图标，在下拉列表中临时呈现它，下次修改为新图标后自动消失
        builtin_old_icons = {
            "translate": ("多语翻译", "translate"),
            "search": ("AI搜索", "search"),
            "summarize": ("长文提炼", "summarize"),
            "write": ("文本润色", "write")
        }
        if initial_icon_type in builtin_old_icons:
            display_name, old_type = builtin_old_icons[initial_icon_type]
            old_icon = QIcon(get_card_icon_pixmap(initial_icon_type, 32))
            insert_idx = self._icon_combo.count() - 1  # 插入在"上传自定义图片..."之前
            self._icon_combo.insertItem(insert_idx, old_icon, display_name, old_type)

        # 样式与 AI 对话窗口右键菜单样式一致的极致现代美学
        arrow_path = asset_dir / "icon_combo_arrow.svg"
        arrow_url = str(arrow_path).replace("\\", "/")

        self._icon_combo.setStyleSheet(f"""
            QComboBox {{
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 6px 12px;
                color: #1f2937;
                font-size: 13px;
            }}
            QComboBox:hover {{
                border: 1px solid #cbd5e1;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 30px;
                border-left: none;
            }}
            QComboBox::down-arrow {{
                image: url({arrow_url});
                width: 10px;
                height: 10px;
            }}
            QComboBox QAbstractItemView {{
                background-color: #ffffff;
                border: 1px solid #dfe4ec;
                border-radius: 8px;
                padding: 4px;
                outline: 0px;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 32px;
                padding: 0 12px;
                border-radius: 6px;
                color: #374151;
            }}
            QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:hover {{
                background-color: #f3f4f6;
                color: #111827;
            }}
        """)

        # 选中初始图标值
        builtin_types = {"musk", "buffett", "decision", "creative", "code_expert", "marketing", "healing", "interview", "english", "weekly",
                         "translate", "search", "summarize", "write", "code"}
        is_custom_initial = initial_icon_type not in builtin_types
        if is_custom_initial and initial_icon_type:
            from deepcat.utils.paths import get_app_dir
            full_path = get_app_dir() / initial_icon_type
            if full_path.exists():
                custom_icon = QIcon(str(full_path))
                display_name = f"自定义图标: {full_path.name}"
                insert_idx = self._icon_combo.count() - 1
                self._icon_combo.insertItem(insert_idx, custom_icon, display_name, initial_icon_type)
                self._icon_combo.setCurrentIndex(insert_idx)
            else:
                self._icon_combo.setCurrentIndex(0)
        else:
            matched = False
            for idx in range(self._icon_combo.count()):
                if self._icon_combo.itemData(idx) == initial_icon_type:
                    self._icon_combo.setCurrentIndex(idx)
                    matched = True
                    break
            if not matched:
                self._icon_combo.setCurrentIndex(0)

        self._icon_combo.currentIndexChanged.connect(self._on_icon_changed)
        content_layout.addWidget(self._icon_combo)

        # 字段3：提示词内容
        content_layout.addWidget(QLabel("提示词内容"))
        self._editor = QTextEdit(content_widget)
        self._editor.setAcceptRichText(False)
        self._editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._editor.setPlaceholderText("请输入该卡片触发的自定义提示词。")
        self._editor.setPlainText(initial_prompt)
        content_layout.addWidget(self._editor)

        # 4. 确认与取消
        btn_row = QWidget(content_widget)
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 8, 0, 0)
        btn_layout.setSpacing(12)
        btn_layout.addStretch(1)

        cancel_btn = QPushButton("取消", btn_row)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #475569;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 6px 18px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #e2e8f0;
                color: #1e293b;
            }
        """)
        cancel_btn.clicked.connect(self.reject)

        ok_btn = QPushButton("确认", btn_row)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 18px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #0f172a;
            }
        """)
        ok_btn.clicked.connect(self.accept)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        content_layout.addWidget(btn_row)

        container_layout.addWidget(content_widget, 1)

    def mousePressEvent(self, event) -> None:
        from PyQt6.QtCore import Qt
        if event.button() == Qt.MouseButton.LeftButton:
            title_bar_pos = self.title_bar.mapFromGlobal(event.globalPosition().toPoint())
            if self.title_bar.rect().contains(title_bar_pos):
                self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event) -> None:
        from PyQt6.QtCore import Qt
        if event.buttons() == Qt.MouseButton.LeftButton and not self._drag_position.isNull():
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def _show_icon_type_popup(self) -> None:
        from PyQt6.QtCore import QPoint
        from deepcat.ui.post_capture_actions import OcrGenericMenuPopup

        items = []
        active_index = None
        popup_item_index = 0
        for index in range(self._icon_combo.count()):
            text = self._icon_combo.itemText(index)
            if self._icon_combo.itemData(index) == "custom_upload" and items:
                items.append(("-", None, False))
            if index == self._icon_combo.currentIndex():
                active_index = popup_item_index
            icon = self._icon_combo.itemIcon(index)
            items.append((text, lambda _checked=False, idx=index: self._select_icon_type_index(idx), True, icon))
            popup_item_index += 1

        popup = OcrGenericMenuPopup(
            items,
            parent=self._icon_combo,
            active_index=active_index,
            match_parent_width=True,
            match_parent_width_exact=True,
            active_indicator="background",
        )
        self._icon_combo_popup = popup
        popup.show_at_pos(self._icon_combo.mapToGlobal(QPoint(0, self._icon_combo.height() + 4)))

    def _select_icon_type_index(self, index: int) -> None:
        if 0 <= index < self._icon_combo.count():
            self._icon_combo.setCurrentIndex(index)

    def _on_icon_changed(self, index: int) -> None:
        data = self._icon_combo.currentData()
        if data == "custom_upload":
            # 阻塞信号，防止接下来 insertItem 和 setCurrentIndex 时再次触发此函数导致重复弹窗
            self._icon_combo.blockSignals(True)
            try:
                from PyQt6.QtWidgets import QFileDialog
                from PyQt6.QtGui import QIcon, QPixmap
                from PyQt6.QtCore import Qt
                import time
                from deepcat.utils.paths import get_app_dir

                file_path, _ = QFileDialog.getOpenFileName(
                    self, "选择自定义图片图标", "", "图片文件 (*.png *.jpg *.jpeg *.svg)"
                )
                if file_path:
                    try:
                        src_path = Path(file_path)
                        dest_dir = get_app_dir() / "data" / "custom_icons"
                        dest_dir.mkdir(parents=True, exist_ok=True)

                        # 统一保存为 .png，以支持透明背景与多格式兼容
                        new_name = f"custom_icon_{int(time.time())}.png"
                        dest_path = dest_dir / new_name

                        # 只压缩存储 32x32 的小 icon 图标，不存储大原图
                        pixmap = QPixmap(file_path)
                        if not pixmap.isNull():
                            scaled_pix = pixmap.scaled(
                                32, 32,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation
                            )
                            scaled_pix.save(str(dest_path), "PNG")
                            rel_path = f"data/custom_icons/{new_name}"

                            custom_icon = QIcon(str(dest_path))
                            display_text = f"自定义图标: {src_path.name}"
                            insert_idx = self._icon_combo.count() - 1
                            self._icon_combo.insertItem(insert_idx, custom_icon, display_text, rel_path)
                            self._icon_combo.setCurrentIndex(insert_idx)
                        else:
                            self._icon_combo.setCurrentIndex(0)
                    except Exception as e:
                        self._icon_combo.setCurrentIndex(0)
                else:
                    self._icon_combo.setCurrentIndex(0)
            finally:
                self._icon_combo.blockSignals(False)

    def card_title(self) -> str:
        return self._title_editor.text().strip()

    def card_icon_type(self) -> str:
        return self._icon_combo.currentData()

    def card_prompt(self) -> str:
        return self._editor.toPlainText().strip()
