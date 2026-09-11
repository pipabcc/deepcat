from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

from deepcat.utils.image_utils import bgr_to_rgb
from deepcat.utils.paths import get_files_dir


def bgr_to_qimage(image_bgr: np.ndarray) -> QImage:
    rgb = bgr_to_rgb(image_bgr)
    h, w, _ = rgb.shape
    rgb = np.ascontiguousarray(rgb)
    return QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


class PreviewWindow(QMainWindow):
    def __init__(
        self,
        image_bgr: np.ndarray,
        on_retake: Optional[Callable[[], None]] = None,
        on_toast: Optional[Callable[[str, str, int], None]] = None,
        default_format: str = "png",
        jpg_quality: int = 95,
    ) -> None:
        super().__init__(None)
        self.setWindowTitle("预览")
        self._image_bgr = image_bgr
        self._on_retake = on_retake
        self._on_toast = on_toast
        self._default_format = default_format
        self._jpg_quality = int(jpg_quality)

        qimg = bgr_to_qimage(image_bgr)
        pix = QPixmap.fromImage(qimg)

        img_label = QLabel()
        img_label.setPixmap(pix)
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_label.setFixedSize(pix.size())
        img_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(img_label)

        info = QLabel(f"尺寸：{qimg.width()} x {qimg.height()}")

        btn_save = QPushButton("保存")
        btn_copy = QPushButton("复制到剪贴板")
        btn_retake = QPushButton("重新截取")
        btn_close = QPushButton("关闭")

        btn_save.clicked.connect(self._save)
        btn_copy.clicked.connect(self._copy)
        btn_close.clicked.connect(self.close)
        btn_retake.clicked.connect(self._retake)
        if self._on_retake is None:
            btn_retake.setEnabled(False)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(10)
        bar.addWidget(btn_save)
        bar.addWidget(btn_copy)
        bar.addWidget(btn_retake)
        bar.addStretch(1)
        bar.addWidget(btn_close)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(scroll, 1)
        layout.addWidget(info)
        layout.addLayout(bar)

        self.setCentralWidget(root)
        self.setMinimumSize(520, 420)
        self.resize(900, 700)

    def _show_feedback(self, *, success: bool, title: str, summary: str, detail: str = "") -> None:
        if self._on_toast is not None:
            message = str(summary or "").strip()
            if str(detail or "").strip():
                message = f"{message}\n{str(detail).strip()}" if message else str(detail).strip()
            try:
                self._on_toast(str(title), message, 1800 if bool(success) else 3600)
                return
            except Exception:
                pass
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information if bool(success) else QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(summary)
        if str(detail or "").strip():
            box.setInformativeText(str(detail).strip())
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    @staticmethod
    def _friendly_error(exc: object, default_message: str) -> str:
        text = str(exc or "").strip()
        lower = text.lower()
        if isinstance(exc, PermissionError) or "permission" in lower or "access denied" in lower or "winerror 5" in lower or "拒绝访问" in text:
            return "权限不足，请检查保存目录或剪贴板权限。"
        if any(token in lower for token in ("invalid path", "invalid filename", "no such file", "not a directory")) or any(token in text for token in ("路径无效", "文件名", "目录不存在")):
            return "保存路径无效，请重新选择可用路径。"
        if "no space" in lower or "disk full" in lower or "磁盘空间" in text:
            return "磁盘空间不足，请清理空间后重试。"
        if text:
            return f"{default_message}：{text[:160]}"
        return default_message

    def _copy(self) -> None:
        try:
            qimg = bgr_to_qimage(self._image_bgr)
            QGuiApplication.clipboard().setImage(qimg)
            self._show_feedback(success=True, title="复制成功", summary="截图已复制到剪贴板。")
        except Exception as e:
            self._show_feedback(success=False, title="复制失败", summary=self._friendly_error(e, "截图复制失败"))

    def _save(self) -> None:
        default_ext = (self._default_format or "png").lower()
        default_name = str(get_files_dir() / f"screenshot.{default_ext}")
        filters = "PNG (*.png);;JPG (*.jpg);;PDF (*.pdf)"
        path, _ = QFileDialog.getSaveFileName(self, "保存", default_name, filters)
        if not path:
            return
        try:
            self._save_to_path(path)
            self._show_feedback(success=True, title="保存成功", summary="截图已保存。", detail=f"路径：{path}")
        except Exception as e:
            self._show_feedback(success=False, title="保存失败", summary=self._friendly_error(e, "截图保存失败"))

    def _save_to_path(self, path: str) -> None:
        try:
            from PIL import Image
        except Exception as e:
            raise RuntimeError("未安装 pillow") from e

        suffix = Path(path).suffix.lower().lstrip(".")
        if suffix == "jpeg":
            suffix = "jpg"
        if suffix not in {"png", "jpg", "pdf"}:
            raise ValueError("文件后缀需为 .png / .jpg / .pdf")

        rgb = bgr_to_rgb(self._image_bgr)
        img = Image.fromarray(rgb)
        if suffix == "png":
            img.save(path, format="PNG")
        elif suffix == "jpg":
            img.save(path, format="JPEG", quality=self._jpg_quality, optimize=True)
        else:
            img.save(path, format="PDF")

    def _retake(self) -> None:
        if self._on_retake is not None:
            self.close()
            self._on_retake()
