from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QStyleOptionViewItem, QStyledItemDelegate, QStyle


class DeletableModelItemDelegate(QStyledItemDelegate):
    @staticmethod
    def delete_rect(rect: QRect) -> QRect:
        size = 18
        return QRect(int(rect.right() - size - 8), int(rect.center().y() - size / 2), size, size)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        opt = QStyleOptionViewItem(option)
        opt.rect = QRect(option.rect)
        opt.rect.setRight(max(opt.rect.left(), opt.rect.right() - 34))
        super().paint(painter, opt, index)
        text = str(index.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not text:
            return
        r = self.delete_rect(option.rect).adjusted(4, 4, -4, -4)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(120, 130, 142), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(r.topLeft(), r.bottomRight())
        painter.drawLine(r.topRight(), r.bottomLeft())
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(34, int(size.height())))
        return size


class ProviderSwitchHintDelegate(QStyledItemDelegate):
    ACTION_TEXT = "移动当前模型到此分组"

    @classmethod
    def action_rect(cls, rect: QRect, metrics) -> QRect:
        action_w = max(104, int(metrics.horizontalAdvance(cls.ACTION_TEXT)) + 24)
        action_h = min(max(22, int(metrics.height()) + 8), max(18, int(rect.height()) - 2))
        return QRect(
            int(rect.right() - action_w - 8),
            int(rect.center().y() - action_h / 2),
            int(action_w),
            int(action_h),
        )

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        opt = QStyleOptionViewItem(option)
        show_hint = bool(option.state & (QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Selected))
        if show_hint:
            metrics = opt.fontMetrics
            action_rect = self.action_rect(option.rect, metrics)
            opt.rect = QRect(option.rect)
            opt.rect.setRight(max(opt.rect.left() + 24, action_rect.left() - 8))

        super().paint(painter, opt, index)

        if not show_hint:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = painter.font()
        font.setPointSize(max(font.pointSize() + 1, 10))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setBrush(QColor("#e2e8f0"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(action_rect, 6, 6)
        painter.setPen(QColor("#475569"))
        painter.drawText(action_rect, Qt.AlignmentFlag.AlignCenter, self.ACTION_TEXT)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(32, int(size.height())))
        return size
