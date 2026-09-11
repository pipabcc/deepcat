from __future__ import annotations

import ctypes
import os
import shutil
import tempfile
import weakref
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from PyQt6.QtCore import Qt, QSize, QTimer, QUrl, QMimeData
from PyQt6.QtGui import QGuiApplication, QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QFrame,
)
from deepcat.settings_store import get_image_output_dir, get_pdf_output_dir
from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND, MAIN_WINDOW_BACKGROUND_COLORREF


def _apply_main_window_caption_color(window: QWidget) -> None:
    try:
        if os.name != "nt":
            return
        hwnd = int(window.winId())
        if not hwnd:
            return
        dwm_caption_color = 35
        color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(dwm_caption_color),
            ctypes.byref(color),
            ctypes.sizeof(color),
        )
    except Exception:
        pass


def _set_native_topmost(window: QWidget, topmost: bool) -> None:
    try:
        if os.name != "nt":
            return
        hwnd = int(window.winId())
        if not hwnd:
            return
        hwnd_topmost = -1
        hwnd_notopmost = -2
        swp_nosize = 0x0001
        swp_nomove = 0x0002
        swp_noactivate = 0x0010
        ctypes.windll.user32.SetWindowPos(
            ctypes.c_void_p(hwnd),
            ctypes.c_void_p(hwnd_topmost if topmost else hwnd_notopmost),
            0,
            0,
            0,
            0,
            swp_nosize | swp_nomove | swp_noactivate,
        )
    except Exception:
        pass


def _force_native_foreground(window: QWidget) -> bool:
    if os.name != "nt":
        return False
    try:
        hwnd = int(window.winId())
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        sw_restore = 9
        user32.ShowWindow(ctypes.c_void_p(hwnd), sw_restore)
        user32.BringWindowToTop(ctypes.c_void_p(hwnd))
        activated = bool(user32.SetForegroundWindow(ctypes.c_void_p(hwnd)))
        return activated or int(user32.GetForegroundWindow() or 0) == hwnd
    except Exception:
        return False


def _foreground_belongs_to_window_process(window: QWidget) -> bool:
    if os.name != "nt":
        return True
    try:
        hwnd = int(window.winId())
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        foreground_hwnd = int(user32.GetForegroundWindow() or 0)
        if not foreground_hwnd or foreground_hwnd == hwnd:
            return True
        target_process_id = ctypes.c_ulong()
        foreground_process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(
            ctypes.c_void_p(hwnd), ctypes.byref(target_process_id)
        )
        user32.GetWindowThreadProcessId(
            ctypes.c_void_p(foreground_hwnd), ctypes.byref(foreground_process_id)
        )
        return bool(
            target_process_id.value
            and target_process_id.value == foreground_process_id.value
        )
    except Exception:
        return False


def _restore_and_activate_window(window: QWidget) -> None:
    can_activate = _foreground_belongs_to_window_process(window)
    show_without_activating = False
    try:
        if not can_activate:
            show_without_activating = bool(
                window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            )
            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        window.showNormal()
        window.raise_()
    except RuntimeError:
        return
    except Exception:
        pass
    finally:
        if not can_activate and not show_without_activating:
            try:
                window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
            except (AttributeError, RuntimeError):
                pass

    if not can_activate:
        return

    try:
        window.activateWindow()
    except RuntimeError:
        return
    except Exception:
        pass

    try:
        handle = window.windowHandle()
        if handle is not None:
            handle.requestActivate()
    except RuntimeError:
        return
    except Exception:
        pass
    _force_native_foreground(window)


def _present_window(window: QWidget) -> None:
    _restore_and_activate_window(window)
    _set_native_topmost(window, True)
    _apply_main_window_caption_color(window)
    QTimer.singleShot(80, lambda: _set_native_topmost(window, True))
    QTimer.singleShot(600, lambda: _set_native_topmost(window, False))


class _StashedCapturesDialog(QDialog):
    def __init__(
        self,
        items: list[dict[str, Any]],
        *,
        default_format: str,
        jpg_quality: int,
        on_pin: Callable[[Any, float, int], None],
        on_toast: Optional[Callable[..., None]] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("暂存前图")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumSize(520, 360)
        self.resize(560, 420)
        self._items = list(items)
        self._preview_dialogs: list[QDialog] = []
        self._clipboard_temp_dirs: list[Path] = []
        self._selected_row_indices: set[int] = set()
        self._selection_syncing = False
        self._default_format = str(default_format or "png").strip().lower()
        self._jpg_quality = int(jpg_quality)
        self._on_pin = on_pin
        self._on_toast = on_toast

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = QLabel(f"已暂存 {len(self._items)} 张截图")
        header.setObjectName("StashedCapturesHeader")
        layout.addWidget(header)

        self._list = QListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setObjectName("StashedCapturesList")
        self._list.itemDoubleClicked.connect(lambda item: self._show_preview(int(item.data(Qt.ItemDataRole.UserRole))))
        self._list.itemSelectionChanged.connect(self._sync_selected_row_indices_from_list)
        for index, item in enumerate(self._items):
            label = QListWidgetItem()
            label.setData(Qt.ItemDataRole.UserRole, index)
            label.setSizeHint(QSize(0, 64))
            self._list.addItem(label)
            self._list.setItemWidget(label, self._make_row_widget(index, item))
            self._set_row_selected(index, True)
        layout.addWidget(self._list, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        select_all = QPushButton("全选", self)
        copy_button = QPushButton("复制选中", self)
        save_button = QPushButton("保存选中", self)
        pin_button = QPushButton("置顶选中", self)
        merge_button = QPushButton("合成长图", self)
        close_button = QPushButton("关闭", self)
        for button in (select_all, copy_button, save_button, pin_button, merge_button, close_button):
            button.setMinimumHeight(32)
            actions.addWidget(button)
        layout.addLayout(actions)

        select_all.clicked.connect(self._select_all)
        copy_button.clicked.connect(self._copy_selected)
        save_button.clicked.connect(self._save_selected)
        pin_button.clicked.connect(self._pin_selected)
        merge_button.clicked.connect(self._pin_merged)
        close_button.clicked.connect(self.close)

        self.setStyleSheet("""
            QDialog {
                background: __MAIN_WINDOW_BACKGROUND__;
            }
            QLabel#StashedCapturesHeader {
                color: #111827;
                font-size: 15px;
                font-weight: 600;
            }
            QListWidget#StashedCapturesList {
                background: #ffffff;
                border: 1px solid #d9e2ec;
                border-radius: 8px;
                padding: 4px;
                color: #1f2937;
                outline: none;
            }
            QListWidget#StashedCapturesList::item {
                min-height: 58px;
                border-radius: 6px;
                padding: 4px 8px;
            }
            QListWidget#StashedCapturesList::item:selected {
                background: #e7f0ff;
                color: #111827;
            }
            QWidget#StashedCaptureRow {
                background: transparent;
            }
            QLabel#StashedCaptureText {
                color: #102033;
                font-size: 13px;
                font-weight: 500;
            }
            QLabel#StashedCaptureThumbnail {
                background: #ffffff;
                border: 1px solid #c7d4e4;
                border-radius: 6px;
            }
            QLabel#StashedCaptureThumbnail:hover {
                border-color: #6b9dfc;
                background: #f8fbff;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #d8e0ea;
                border-radius: 7px;
                padding: 0 10px;
                color: #263244;
                font-size: 13px;
            }
            QPushButton:hover {
                background: #eef5ff;
                border-color: #b8c8dc;
            }
            QPushButton:pressed {
                background: #ddeafb;
            }
        """.replace("__MAIN_WINDOW_BACKGROUND__", MAIN_WINDOW_BACKGROUND))

    def _apply_caption_color(self) -> None:
        _apply_main_window_caption_color(self)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()

    def present(self) -> None:
        _present_window(self)

    def _item_text(self, index: int, item: dict[str, Any]) -> str:
        image_bgr = item.get("image_bgr")
        shape = getattr(image_bgr, "shape", None)
        if shape is not None and len(shape) >= 2:
            size_text = f"{int(shape[1])} x {int(shape[0])}"
        else:
            size_text = "未知尺寸"
        dpr = float(item.get("dpr") or 1.0)
        return f"第 {index + 1} 张 · {size_text} · DPR {dpr:.2f}"

    def _qimage_from_bgr(self, image_bgr, *, dpr: float = 1.0) -> Optional[QImage]:
        try:
            rgb = image_bgr[:, :, ::-1].copy()
            h, w = int(rgb.shape[0]), int(rgb.shape[1])
            qimg = QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()
            ratio = float(dpr or 1.0)
            if 0.25 <= ratio <= 8.0:
                qimg.setDevicePixelRatio(ratio)
            return qimg
        except Exception:
            return None

    def _make_thumbnail_pixmap(self, item: dict[str, Any], size: QSize = QSize(96, 52)) -> QPixmap:
        qimg = self._qimage_from_bgr(item.get("image_bgr"), dpr=1.0)
        if qimg is None:
            pix = QPixmap(size)
            pix.fill(QColor("#eef3f8"))
            return pix
        pix = QPixmap.fromImage(qimg)
        return pix.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def _make_row_widget(self, index: int, item: dict[str, Any]) -> QWidget:
        row = QWidget(self._list)
        row.setObjectName("StashedCaptureRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 4, 8, 4)
        layout.setSpacing(8)

        text = QLabel(self._item_text(index, item), row)
        text.setObjectName("StashedCaptureText")
        text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(text, 1)

        thumb = QLabel(row)
        thumb.setObjectName("StashedCaptureThumbnail")
        thumb.setFixedSize(104, 54)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        thumb.setToolTip("点击查看大图")
        thumb.setPixmap(self._make_thumbnail_pixmap(item))
        thumb.mousePressEvent = lambda event, row_index=index: self._show_preview(row_index)
        layout.addWidget(thumb, 0)

        def select_row(event, row_index=index) -> None:
            self._select_row_from_widget(event, row_index)

        row.mousePressEvent = select_row
        return row

    def _sync_selected_row_indices_from_list(self) -> None:
        if bool(getattr(self, "_selection_syncing", False)):
            return
        rows: set[int] = set()
        for item in self._list.selectedItems():
            try:
                row = int(item.data(Qt.ItemDataRole.UserRole))
            except Exception:
                continue
            if 0 <= row < len(self._items):
                rows.add(row)
        self._selected_row_indices = rows

    def _set_row_selected(self, row_index: int, selected: bool, *, exclusive: bool = False) -> None:
        if row_index < 0 or row_index >= len(self._items):
            return
        self._selection_syncing = True
        try:
            if bool(exclusive):
                self._selected_row_indices.clear()
                for row in range(self._list.count()):
                    item = self._list.item(row)
                    if item is not None:
                        item.setSelected(False)
            item = self._list.item(row_index)
            if item is not None:
                item.setSelected(bool(selected))
            if bool(selected):
                self._selected_row_indices.add(row_index)
            else:
                self._selected_row_indices.discard(row_index)
        finally:
            self._selection_syncing = False

    def _select_row_from_widget(self, event, row_index: int) -> None:
        try:
            modifiers = event.modifiers()
        except Exception:
            modifiers = Qt.KeyboardModifier.NoModifier
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            self._set_row_selected(row_index, row_index not in self._selected_row_indices)
            return
        self._set_row_selected(row_index, True)

    def _show_preview(self, index: int) -> None:
        if index < 0 or index >= len(self._items):
            return
        item = self._items[index]
        image_bgr = item.get("image_bgr")
        if image_bgr is None:
            self._toast("预览失败", "暂存截图已释放。", 1600)
            return
        qimg = self._qimage_from_bgr(image_bgr, dpr=float(item.get("dpr") or 1.0))
        if qimg is None:
            self._toast("预览失败", "暂存截图数据不可用。", 1600)
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"暂存前图 - 第 {index + 1} 张")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.resize(820, 560)
        dialog.setStyleSheet(self.styleSheet())

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(False)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        label = QLabel(scroll)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = QPixmap.fromImage(qimg)
        label.setPixmap(pix)
        try:
            di = pix.deviceIndependentSize()
            label.resize(int(di.width()), int(di.height()))
        except Exception:
            label.resize(pix.size())
        scroll.setWidget(label)
        layout.addWidget(scroll, 1)

        close_button = QPushButton("关闭", dialog)
        close_button.setMinimumHeight(32)
        close_button.clicked.connect(dialog.close)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)

        def remove_preview(*_, owner_ref=weakref.ref(self), dialog_ref=weakref.ref(dialog)) -> None:
            owner = owner_ref()
            preview = dialog_ref()
            if owner is None or preview is None:
                return
            try:
                owner._preview_dialogs.remove(preview)
            except ValueError:
                pass

        dialog.destroyed.connect(remove_preview)
        self._preview_dialogs.append(dialog)
        _present_window(dialog)

    def _selected_rows(self) -> list[int]:
        self._sync_selected_row_indices_from_list()
        rows = [
            int(row)
            for row in getattr(self, "_selected_row_indices", set())
            if 0 <= int(row) < len(self._items)
        ]
        return sorted(set(rows))

    def _selected_items(self) -> list[dict[str, Any]]:
        rows = self._selected_rows()
        return [self._items[row] for row in rows]

    def _select_all(self) -> None:
        self._selection_syncing = True
        try:
            self._selected_row_indices = set(range(len(self._items)))
            for row in range(self._list.count()):
                item = self._list.item(row)
                if item is not None:
                    item.setSelected(True)
        finally:
            self._selection_syncing = False

    def _toast(self, title: str, message: str, timeout: int = 1800, *, open_dir: str | None = None) -> None:
        if self._on_toast is None:
            return
        try:
            self._on_toast(title, message, timeout, open_dir=open_dir)
        except TypeError:
            self._on_toast(title, message, timeout)
        except Exception:
            pass

    def _first_dpr(self, items: list[dict[str, Any]]) -> float:
        try:
            dpr = float(items[0].get("dpr") or 1.0)
            if 0.25 <= dpr <= 8.0:
                return dpr
        except Exception:
            pass
        return 1.0

    def _compose_items(self, items: list[dict[str, Any]]):
        import numpy as np

        images = []
        for item in items:
            image_bgr = item.get("image_bgr")
            shape = getattr(image_bgr, "shape", None)
            if shape is None or len(shape) < 3:
                continue
            images.append(image_bgr)
        if not images:
            return None
        max_width = max(int(img.shape[1]) for img in images)
        padded = []
        for img in images:
            width = int(img.shape[1])
            if width >= max_width:
                padded.append(img)
                continue
            height = int(img.shape[0])
            pad_width = max_width - width
            try:
                edge_color = np.median(img[:, max(0, width - 1):width, :].reshape(-1, 3), axis=0)
                fill = np.array([int(v) for v in edge_color], dtype=img.dtype)
            except Exception:
                fill = np.array([255, 255, 255], dtype=img.dtype)
            pad = np.empty((height, pad_width, 3), dtype=img.dtype)
            pad[:, :] = fill
            padded.append(np.hstack([img, pad]))
        return np.vstack(padded)

    def _save_one(self, image_bgr, sequence: int) -> str:
        from deepcat.output.saver import save_image_to_path

        fmt = self._default_format
        if fmt == "jpeg":
            fmt = "jpg"
        if fmt not in {"png", "jpg", "pdf"}:
            fmt = "png"
        if fmt == "pdf":
            output_dir = Path(str(get_pdf_output_dir(validate_writable=True)))
        else:
            output_dir = Path(str(get_image_output_dir(validate_writable=True)))
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = output_dir / f"screenshot_stash_{ts}_{sequence:02d}.{fmt}"
        return save_image_to_path(image_bgr, str(path), fmt, int(self._jpg_quality))

    def _copy_selected(self) -> None:
        items = self._selected_items()
        if not items:
            self._toast("未选择截图", "请先选择要复制的暂存截图。", 1600)
            return
        valid_items = [item for item in items if item.get("image_bgr") is not None]
        if not valid_items:
            self._toast("复制失败", "暂存截图数据不可用。", 1800)
            return
        if len(valid_items) == 1:
            try:
                self._cleanup_clipboard_temp_dirs()
                from deepcat.output.qt_clipboard import copy_bgr_image

                ok = bool(copy_bgr_image(valid_items[0].get("image_bgr")))
            except Exception:
                ok = False
            self._toast("已复制暂存图" if ok else "复制失败", "已复制到剪贴板。" if ok else "无法写入剪贴板。", 1600)
            return
        ok = self._copy_multiple_images(valid_items)
        self._toast(
            "已复制暂存图" if ok else "复制失败",
            f"已复制 {len(valid_items)} 张暂存图。" if ok else "无法写入剪贴板。",
            1800,
        )

    def _copy_multiple_images(self, items: list[dict[str, Any]]) -> bool:
        temp_dir: Optional[Path] = None
        try:
            from deepcat.output.saver import save_image_to_path

            self._cleanup_clipboard_temp_dirs()
            temp_dir = Path(tempfile.mkdtemp(prefix="deepcat_stash_clip_"))
            paths: list[Path] = []
            for index, item in enumerate(items, start=1):
                image_bgr = item.get("image_bgr")
                if image_bgr is None:
                    continue
                path = temp_dir / f"stashed_{index:02d}.png"
                save_image_to_path(image_bgr, str(path), "png", int(self._jpg_quality))
                paths.append(path)
            if not paths:
                return False

            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
            mime.setText("\n".join(str(path) for path in paths))
            merged = self._compose_items(items)
            qimg = self._qimage_from_bgr(merged, dpr=self._first_dpr(items)) if merged is not None else None
            if qimg is not None:
                mime.setImageData(qimg)
            QGuiApplication.clipboard().setMimeData(mime)
            self._clipboard_temp_dirs.append(temp_dir)
            return True
        except Exception:
            if temp_dir is not None:
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass
            return False

    def _cleanup_clipboard_temp_dirs(self) -> None:
        for temp_dir in list(getattr(self, "_clipboard_temp_dirs", [])):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
        self._clipboard_temp_dirs = []

    def _save_selected(self) -> None:
        items = self._selected_items()
        if not items:
            self._toast("未选择截图", "请先选择要保存的暂存截图。", 1600)
            return
        saved_paths: list[str] = []
        try:
            for index, item in enumerate(items, start=1):
                image_bgr = item.get("image_bgr")
                if image_bgr is None:
                    continue
                saved_paths.append(self._save_one(image_bgr, index))
        except Exception as exc:
            self._toast("保存失败", str(exc), 2400)
            return
        open_dir = str(Path(saved_paths[0]).parent) if saved_paths else None
        self._toast("已保存暂存图", f"已保存 {len(saved_paths)} 张截图。", 1800, open_dir=open_dir)

    def _pin_selected(self) -> None:
        items = self._selected_items()
        if not items:
            self._toast("未选择截图", "请先选择要置顶的暂存截图。", 1600)
            return
        for item in items:
            image_bgr = item.get("image_bgr")
            if image_bgr is None:
                continue
            self._on_pin(image_bgr, float(item.get("dpr") or 1.0), 1)
        self._toast("已置顶暂存图", f"已置顶 {len(items)} 张截图。", 1600)

    def _pin_merged(self) -> None:
        items = self._selected_items()
        if not items:
            self._toast("未选择截图", "请先选择要合成的暂存截图。", 1600)
            return
        merged = self._compose_items(items)
        if merged is None:
            self._toast("合成失败", "暂存截图数据不可用。", 1800)
            return
        self._on_pin(merged, self._first_dpr(items), len(items))
        self._toast("已合成长图", "已置顶合成后的长图。", 1600)

    def _release_cached_images(self) -> None:
        for dialog in list(getattr(self, "_preview_dialogs", [])):
            try:
                dialog.close()
            except Exception:
                pass
        self._preview_dialogs = []
        self._cleanup_clipboard_temp_dirs()
        try:
            self._list.clear()
        except Exception:
            pass
        for item in list(getattr(self, "_items", [])):
            try:
                item["image_bgr"] = None
            except Exception:
                pass
        self._items = []

    def closeEvent(self, event) -> None:
        self._release_cached_images()
        super().closeEvent(event)
