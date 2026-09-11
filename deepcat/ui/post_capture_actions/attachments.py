from __future__ import annotations

from pathlib import Path
from typing import Optional
from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QFrame, QWidget, QGraphicsDropShadowEffect, QLabel, QVBoxLayout, QToolButton
from PyQt6.QtWidgets import QFrame
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame
from PyQt6.QtGui import QPainter, QPixmap, QPen, QColor, QFont
from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtWidgets import QWidget, QLabel


class _AttachmentPreviewChip(QFrame):
    delete_requested = pyqtSignal(int)

    _TEXT_EXTENSIONS = {
        ".txt", ".md", ".markdown", ".log", ".csv", ".tsv", ".json", ".xml",
        ".yaml", ".yml", ".ini", ".conf", ".cfg", ".toml",
    }
    _CODE_EXTENSIONS = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".htm", ".sh",
        ".bat", ".ps1", ".rs", ".go", ".c", ".cpp", ".h", ".hpp", ".java",
        ".kt", ".swift", ".php", ".rb", ".sql",
    }

    def __init__(
        self,
        index: int,
        label: str,
        preview_path: str = "",
        attachment: Optional[dict] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._index = int(index)
        self._label_text = str(label or "附件").strip() or "附件"
        self._attachment = dict(attachment or {})
        self.setObjectName("AttachmentPreviewChip")
        self.setFixedSize(54, 54)
        self.setToolTip(self._tooltip_text())
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setStyleSheet(
            "QFrame#AttachmentPreviewChip {"
            " background:transparent;"
            " border:none;"
            "}"
            "QFrame#AttachmentPreviewChip:hover {"
            " background:transparent;"
            " border:none;"
            "}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(0)

        self._preview_label = QLabel(self)
        self._preview_label.setFixedSize(44, 44)
        self._preview_label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setToolTip(self.toolTip())
        self._preview_label.setStyleSheet(
            "QLabel {"
            " background:#f8fafc;"
            " border:1px solid #dbe3ee;"
            " border-radius:7px;"
            " color:#64748b;"
            " font-size:10px;"
            " font-weight:700;"
            "}"
        )
        self._preview_shadow = QGraphicsDropShadowEffect(self._preview_label)
        self._preview_shadow.setBlurRadius(10)
        self._preview_shadow.setOffset(0, 1)
        self._preview_shadow.setColor(QColor(15, 23, 42, 48))
        self._preview_label.setGraphicsEffect(self._preview_shadow)
        layout.addWidget(self._preview_label)

        preview = QPixmap(str(preview_path or "")) if preview_path else QPixmap()
        if not preview.isNull():
            self._preview_label.setPixmap(
                preview.scaled(
                    QSize(42, 42),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self._preview_label.setPixmap(self._make_file_icon_pixmap())

        self._delete_btn = QToolButton(self)
        self._delete_btn.setObjectName("AttachmentPreviewDeleteBtn")
        self._delete_btn.setText("×")
        self._delete_btn.setFixedSize(16, 16)
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.setToolTip(f"移除 {self._label_text}")
        self._delete_btn.setStyleSheet(
            "QToolButton#AttachmentPreviewDeleteBtn {"
            " background:rgba(15,23,42,0.72);"
            " color:white;"
            " border:none;"
            " border-radius:8px;"
            " padding:0px;"
            " font-size:11px;"
            " font-weight:700;"
            "}"
            "QToolButton#AttachmentPreviewDeleteBtn:hover { background:#ef4444; }"
        )
        self._delete_btn.clicked.connect(lambda _checked=False: self.delete_requested.emit(self._index))
        self._delete_btn.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._delete_btn.move(self.width() - self._delete_btn.width() - 2, 2)

    def _tooltip_text(self) -> str:
        kind_label = {
            "text": "文本文件",
            "code": "代码文件",
            "pdf": "PDF 文件",
            "document": "文档文件",
            "spreadsheet": "表格文件",
            "presentation": "演示文稿",
            "archive": "压缩包",
            "image": "图片文件",
            "unknown": "未知类型文件",
        }.get(self._file_kind(), "附件")
        return f"{self._label_text}\n{kind_label}"

    @classmethod
    def _attachment_mime_type(cls, attachment: Optional[dict]) -> str:
        value = dict(attachment or {})
        for container_key in ("file_url", "image_url"):
            container = value.get(container_key)
            if isinstance(container, dict):
                mime = str(container.get("mime_type") or container.get("mimeType") or "").strip().lower()
                if mime:
                    return mime
                url = str(container.get("url") or "").strip()
            else:
                url = str(container or "").strip()
            if url.startswith("data:") and ";" in url:
                return url[5:url.find(";")].strip().lower()
        return ""

    @classmethod
    def attachment_file_kind(cls, label: str, attachment: Optional[dict]) -> str:
        value = dict(attachment or {})
        mime = cls._attachment_mime_type(value)
        suffix = Path(str(label or "")).suffix.lower()
        if str(value.get("type") or "") == "image_url" or mime.startswith("image/"):
            return "image"
        if mime == "application/pdf" or suffix == ".pdf":
            return "pdf"
        if mime.startswith("text/") or suffix in cls._TEXT_EXTENSIONS:
            return "text"
        if suffix in cls._CODE_EXTENSIONS:
            return "code"
        if suffix in {".doc", ".docx", ".rtf", ".odt"}:
            return "document"
        if suffix in {".xls", ".xlsx", ".ods"}:
            return "spreadsheet"
        if suffix in {".ppt", ".pptx", ".odp"}:
            return "presentation"
        if suffix in {".zip", ".rar", ".7z", ".tar", ".gz"}:
            return "archive"
        if mime in {
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }:
            return "document"
        if "spreadsheet" in mime or "excel" in mime:
            return "spreadsheet"
        if "presentation" in mime or "powerpoint" in mime:
            return "presentation"
        if "zip" in mime or "compressed" in mime:
            return "archive"
        return "unknown"

    def _file_kind(self) -> str:
        return self.attachment_file_kind(self._label_text, self._attachment)

    def _make_file_icon_pixmap(self) -> QPixmap:
        kind = self._file_kind()
        spec = {
            "text": ("TXT", QColor("#2563eb"), QColor("#dbeafe")),
            "code": ("</>", QColor("#7c3aed"), QColor("#ede9fe")),
            "pdf": ("PDF", QColor("#dc2626"), QColor("#fee2e2")),
            "document": ("DOC", QColor("#1d4ed8"), QColor("#dbeafe")),
            "spreadsheet": ("XLS", QColor("#047857"), QColor("#d1fae5")),
            "presentation": ("PPT", QColor("#c2410c"), QColor("#ffedd5")),
            "archive": ("ZIP", QColor("#9333ea"), QColor("#f3e8ff")),
            "image": ("IMG", QColor("#0891b2"), QColor("#cffafe")),
            "unknown": ("?", QColor("#64748b"), QColor("#e2e8f0")),
        }.get(kind, ("?", QColor("#64748b"), QColor("#e2e8f0")))
        label, accent, fill = spec
        pixmap = QPixmap(44, 44)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor("#cbd5e1"), 1))
            painter.setBrush(QBrush(QColor("#ffffff")))
            doc_path = QPainterPath()
            doc_path.moveTo(11, 5)
            doc_path.lineTo(27, 5)
            doc_path.lineTo(35, 13)
            doc_path.lineTo(35, 38)
            doc_path.lineTo(11, 38)
            doc_path.closeSubpath()
            painter.drawPath(doc_path)
            painter.setPen(QPen(QColor("#cbd5e1"), 1))
            painter.setBrush(QBrush(QColor("#f8fafc")))
            fold_path = QPainterPath()
            fold_path.moveTo(27, 5)
            fold_path.lineTo(35, 13)
            fold_path.lineTo(27, 13)
            fold_path.closeSubpath()
            painter.drawPath(fold_path)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(fill))
            painter.drawRoundedRect(QRectF(8, 22, 28, 14), 4, 4)
            painter.setPen(accent)
            font = QFont()
            font.setBold(True)
            font.setPixelSize(8 if len(label) > 3 else 9)
            painter.setFont(font)
            painter.drawText(QRectF(8, 22, 28, 14), Qt.AlignmentFlag.AlignCenter, label)

            painter.setPen(QPen(accent, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            if kind == "text":
                painter.drawLine(QPointF(15, 14), QPointF(29, 14))
                painter.drawLine(QPointF(15, 18), QPointF(25, 18))
            elif kind == "unknown":
                font.setPixelSize(13)
                painter.setFont(font)
                painter.drawText(QRectF(14, 9, 16, 15), Qt.AlignmentFlag.AlignCenter, "?")
            else:
                painter.drawLine(QPointF(15, 16), QPointF(27, 16))
        finally:
            painter.end()
        return pixmap


def _refresh_attachment_preview_bar_if_available(panel: object) -> None:
    refresh = getattr(panel, "_refresh_attachment_preview_bar", None)
    if callable(refresh):
        refresh()
