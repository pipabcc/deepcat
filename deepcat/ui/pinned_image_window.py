
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QLineEdit

import numpy as np
import weakref
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QPropertyAnimation, QTimer
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QFileDialog, QLabel, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QHBoxLayout, QPushButton, QStyle, QStyleOptionSlider, QWidget, QScrollArea

from deepcat.output.qt_clipboard import copy_bgr_image
from deepcat.output.scroll_saver import save_full_image_to_path
from deepcat.output.naming import unique_output_path
from deepcat.ui.image_save_worker import start_image_save
from deepcat.ui.popup_behavior import set_disable_global_tooltip
from deepcat.ui.timer_scope import single_shot_scoped


_PINNED_ALIVE: dict[int, "PinnedImageWindow"] = {}


def _valid_device_pixel_ratio(value: float) -> float:
    try:
        dpr = float(value)
        if dpr > 0:
            return dpr
    except Exception:
        pass
    return 1.0


def restore_all_pinned() -> None:
    for win in list(_PINNED_ALIVE.values()):
        try:
            if win is None:
                continue
            win.show()
            win.raise_()
            win.activateWindow()
        except Exception:
            pass


def hide_all_pinned() -> None:
    for win in list(_PINNED_ALIVE.values()):
        try:
            if win is None:
                continue
            win.hide()
        except Exception:
            pass


def has_visible_pinned() -> bool:
    for win in list(_PINNED_ALIVE.values()):
        try:
            if win is not None and win.isVisible():
                return True
        except Exception:
            pass
    return False


def toggle_all_pinned() -> bool:
    if has_visible_pinned():
        hide_all_pinned()
        return False
    restore_all_pinned()
    return True


class _ScrollHandleHintBubble(QWidget):
    """滚动长图首次出现时的轻提示气泡。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._text = "按住滑块拖动，查看完整长图"
        self._font = QFont("Microsoft YaHei UI", 9)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def show_at(self, target: QPoint, duration_ms: int = 5200) -> None:
        metrics = QFontMetrics(self._font)
        arrow_w = 9
        bubble_w = max(196, int(metrics.horizontalAdvance(self._text)) + 28 + arrow_w)
        bubble_h = 38
        parent = self.parentWidget()
        parent_rect = parent.rect() if parent is not None else QRect(0, 0, 1200, 800)
        x = int(target.x() - bubble_w - 10)
        y = int(target.y() - bubble_h / 2)
        x = max(8, min(x, max(8, int(parent_rect.width()) - bubble_w - 8)))
        y = max(8, min(y, max(8, int(parent_rect.height()) - bubble_h - 8)))
        self.setGeometry(x, y, bubble_w, bubble_h)
        self.show()
        self.raise_()
        self._hide_timer.start(max(1000, int(duration_ms)))
        self.update()

    def hide(self) -> None:
        self._hide_timer.stop()
        super().hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            arrow_w = 9
            body = QRectF(1, 1, max(1, self.width() - arrow_w - 2), max(1, self.height() - 2))
            path = QPainterPath()
            path.addRoundedRect(body, 9, 9)
            mid_y = float(body.center().y())
            path.moveTo(float(body.right()) - 1, mid_y - 6)
            path.lineTo(float(self.width()) - 1, mid_y)
            path.lineTo(float(body.right()) - 1, mid_y + 6)
            path.closeSubpath()
            painter.setPen(QPen(QColor(15, 23, 42, 36), 1))
            painter.setBrush(QColor(255, 255, 255, 248))
            painter.drawPath(path)
            painter.setFont(self._font)
            painter.setPen(QColor(51, 65, 85))
            text_rect = body.adjusted(12, 0, -10, 0)
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self._text)
        finally:
            if painter.isActive():
                painter.end()


class PinnedImageWindow(QWidget):
    OCR_MAX_IMAGE_HEIGHT = 10000

    def __init__(
        self,
        image: QImage | QPixmap,
        image_bgr: np.ndarray,
        default_dir: str,
        default_format: str,
        jpg_quality: int,
        anchor_rect: Optional[QRect] = None,
        capture_frames: int = 0,
        blur_mode: bool = False,
        on_image_changed: Optional[Callable[[np.ndarray], None]] = None,
        on_toast: Optional[Callable[[str, str, int], None]] = None,
    ) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if isinstance(image, QPixmap):
            self._orig_pix = QPixmap(image)
            self._image_dpr = _valid_device_pixel_ratio(self._orig_pix.devicePixelRatio())
        else:
            qimg = QImage(image)
            self._image_dpr = _valid_device_pixel_ratio(qimg.devicePixelRatio())
            self._orig_pix = QPixmap.fromImage(qimg)
            self._orig_pix.setDevicePixelRatio(self._image_dpr)
        self._scale = 1.0
        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        self._image_bgr = image_bgr
        self._on_image_changed = on_image_changed
        self._on_toast = on_toast
        self._blur_mode = bool(blur_mode)
        self._painting_blur = False
        self._last_blur_pos = None
        self._brush_size_px = 18
        self._default_dir = str(default_dir)
        self._default_format = (default_format or "png").lower()
        self._jpg_quality = int(jpg_quality)
        self._capture_frames = int(capture_frames)
        self._did_initial_show_adjust = False
        self._crop_overlay = None
        self._scroll_shadow: Optional[QGraphicsDropShadowEffect] = None
        self._scroll_hint_bubble: Optional[_ScrollHandleHintBubble] = None
        self._scroll_hint_shown = False

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._scroll_area.setViewportMargins(0, 0, 0, 0)
        self._apply_scroll_area_style()

        self._label = QLabel(self._scroll_area)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._label.setStyleSheet("background: rgba(255,255,255,0.0);")
        self._scroll_area.setWidget(self._label)
        self._scroll_area.verticalScrollBar().valueChanged.connect(self._update_edit_actions_pos)
        self._scroll_area.horizontalScrollBar().valueChanged.connect(self._update_edit_actions_pos)
        self._scroll_area.verticalScrollBar().valueChanged.connect(self._update_toolbar_pos)
        self._scroll_area.horizontalScrollBar().valueChanged.connect(self._update_toolbar_pos)
        self._scroll_area.verticalScrollBar().valueChanged.connect(self._sync_scroll_hint_bubble_pos)

        eff = QGraphicsDropShadowEffect(self._scroll_area)
        eff.setBlurRadius(34)
        eff.setOffset(0, 10)
        eff.setColor(QColor(15, 23, 42, 72))
        self._scroll_area.setGraphicsEffect(eff)
        self._scroll_shadow = eff
        self._label.setMouseTracking(True)
        self._scroll_area.setMouseTracking(True)
        self._scroll_area.viewport().setMouseTracking(True)
        self._scale_badge = QLabel(self)
        self._scale_badge_visible = False
        self._scale_badge.setObjectName("PinnedImageScaleBadge")
        self._scale_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scale_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._scale_badge.setStyleSheet(
            "QLabel#PinnedImageScaleBadge {"
            " background: rgba(15, 23, 42, 0.74);"
            " color: #ffffff;"
            " border: 1px solid rgba(255, 255, 255, 0.20);"
            " border-radius: 8px;"
            " padding: 2px 8px;"
            " font-size: 11px;"
            " font-weight: 700;"
            " font-family: 'Microsoft YaHei', 'Segoe UI', system-ui;"
            "}"
        )
        self._scale_badge.hide()
        self._apply_scale()
        if bool(self._blur_mode):
            self._apply_blur_cursor()

        self._toolbar: Optional[QWidget] = None
        self._toolbar_opacity: Optional[QGraphicsOpacityEffect] = None
        self._toolbar_anim: Optional[QPropertyAnimation] = None
        self._toolbar_tooltip = None
        self._ocr_busy_tooltip_active = False
        self._toolbar_ocr_button: Optional[QPushButton] = None
        self._toolbar_buttons: list[QPushButton] = []
        self._edit_actions: Optional[QWidget] = None
        self._setup_toolbar()
        self._scroll_hint_bubble = _ScrollHandleHintBubble(self)
        self._label.installEventFilter(self)
        self._scroll_area.installEventFilter(self)
        self._scroll_area.viewport().installEventFilter(self)

        _PINNED_ALIVE[id(self)] = self
        try:
            owner_id = id(self)
            self.destroyed.connect(lambda *_, owner_id=owner_id: _PINNED_ALIVE.pop(owner_id, None))
        except Exception:
            pass

        try:
            if anchor_rect is not None:
                x0 = int(anchor_rect.left())
                y0 = int(anchor_rect.top())
                screen = QGuiApplication.screenAt(anchor_rect.center()) or QGuiApplication.primaryScreen()
            else:
                p = QCursor.pos()
                x0 = int(p.x() - self.width() // 2)
                y0 = int(p.y() - self.height() // 2)
                screen = QGuiApplication.screenAt(p) or QGuiApplication.primaryScreen()

            geo = screen.availableGeometry() if screen is not None else None
            if geo is not None:
                force_center = False
                try:
                    img_h = int(self._image_bgr.shape[0])
                    if int(self._capture_frames) >= 50:
                        force_center = True
                    elif img_h >= int(geo.height() * 2):
                        force_center = True
                    elif anchor_rect is not None:
                        est_frames = float(self._image_bgr.shape[0]) / float(max(1, int(anchor_rect.height())))
                        if est_frames >= 50.0:
                            force_center = True
                    elif int(self.height()) >= int(geo.height() * 0.85):
                        force_center = True

                    if bool(force_center):
                        y0 = int(geo.y() + (geo.height() - self.height()) // 2)
                except Exception:
                    pass
                x0 = max(int(geo.x()), min(int(x0), int(geo.x() + geo.width() - self.width())))
                if not bool(force_center):
                    y0 = max(int(geo.y()), min(int(y0), int(geo.y() + geo.height() - self.height())))
            self.move(int(x0), int(y0))
        except Exception:
            pass

        self.setStyleSheet("""
            QToolTip {
                background-color: #ffffff;
                color: #1e293b;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
                font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
            }
        """)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._edit_actions is not None:
            try:
                self._edit_actions.show()
                self._update_edit_actions_pos()
            except Exception:
                pass
        single_shot_scoped(260, self, self._maybe_show_scroll_handle_hint)
        if bool(self._did_initial_show_adjust):
            return
        self._did_initial_show_adjust = True
        try:
            screen = self.screen() or QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen is not None else None
            if geo is None:
                return
            force_center = bool(int(self._capture_frames) >= 50) or bool(int(self._image_bgr.shape[0]) >= int(geo.height() * 2))
            if not bool(force_center):
                return
            x0 = max(int(geo.x()), min(int(self.x()), int(geo.x() + geo.width() - self.width())))
            y0 = int(geo.y() + (geo.height() - self.height()) // 2)
            self.move(int(x0), int(y0))
        except Exception:
            pass

    def _setup_toolbar(self) -> None:
        assets_dir = Path(__file__).resolve().parent / "assets"

        def _icon(name: str) -> QIcon:
            p = assets_dir / name
            return QIcon(str(p)) if p.exists() else QIcon()

        toolbar = QWidget(self)
        toolbar.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        toolbar.hide()

        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        def _register_toolbar_button(button: QPushButton) -> None:
            self._toolbar_buttons.append(button)
            set_disable_global_tooltip(button)
            button.installEventFilter(self)

        # 1. 展开/折叠按钮
        self._btn_expand = QPushButton("‹")
        self._btn_expand.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        self._btn_expand.setFixedSize(28, 28)
        self._btn_expand.setToolTip("展开绘制工具条")
        self._btn_expand.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_expand.setFlat(True)
        self._btn_expand.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 4px; color: #64748b; padding: 0px 4px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.06); color: #2196F3; }"
            "QPushButton:pressed { background: rgba(0,0,0,0.10); }"
        )
        self._btn_expand.clicked.connect(self._toggle_expand_toolbar)
        _register_toolbar_button(self._btn_expand)
        layout.addWidget(self._btn_expand)

        # 将百分比图标作为子元素放入工具栏 layout 中，位置排在展开折叠按钮的右侧，样式与按钮统一
        self._scale_badge.setParent(toolbar)
        self._scale_badge.setFixedHeight(28)
        self._scale_badge.setStyleSheet(
            "QLabel#PinnedImageScaleBadge {"
            " background: transparent;"
            " color: #64748b;"
            " border: none;"
            " border-radius: 4px;"
            " padding: 0px 2px 0px 4px;"
            " font-size: 11px;"
            " font-weight: 700;"
            " font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;"
            "}"
        )
        layout.addWidget(self._scale_badge)

        # 2. 绘制标注按钮组折叠面板 (默认隐藏)
        self._expand_panel = QWidget(toolbar)
        self._expand_panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._expand_panel.setStyleSheet("background: transparent; border: none;")
        self._expand_panel.hide()

        panel_layout = QHBoxLayout(self._expand_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(2)

        self._tool_buttons = {}
        annotation_actions = [
            ("ocr", _icon("icon_action_ocr.svg"), "文字识别"),
            ("blur", _icon("icon_action_blur.svg"), "模糊"),
            ("rect", _icon("icon_action_rect.svg"), "框选"),
            ("arrow", _icon("icon_action_arrow.svg"), "箭头"),
            ("pen", _icon("icon_action_pen.svg"), "画笔"),
            ("marker", _icon("icon_action_marker.svg"), "记号"),
            ("number", _icon("icon_action_number.svg"), "序号"),
            ("text", _icon("icon_action_text.svg"), "文本"),
            ("eraser", _icon("icon_action_eraser.svg"), "擦除"),
            ("undo", _icon("icon_action_undo.svg"), "撤销"),
            ("redo", _icon("icon_action_redo.svg"), "还原"),
        ]

        for tool_name, icon, tip in annotation_actions:
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setIconSize(QSize(16, 16))
            btn.setIcon(icon)
            btn.setToolTip(str(tip))
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setFlat(True)
            btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; border-radius: 4px; padding: 4px; }"
                "QPushButton:hover { background: rgba(0,0,0,0.06); }"
            )
            btn.clicked.connect(lambda _checked, t=tool_name: self._trigger_annotation_tool(t))
            _register_toolbar_button(btn)
            panel_layout.addWidget(btn)
            if tool_name == "ocr":
                self._toolbar_ocr_button = btn
            if tool_name not in {"undo", "redo", "ocr"}:
                self._tool_buttons[tool_name] = btn

        layout.addWidget(self._expand_panel)

        # 3. 裁剪按钮
        self._btn_crop = QPushButton()
        self._btn_crop.setFixedSize(28, 28)
        self._btn_crop.setIconSize(QSize(16, 16))
        self._btn_crop.setIcon(_icon("icon_crop.svg"))
        self._btn_crop.setToolTip("裁剪")
        self._btn_crop.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_crop.setFlat(True)
        self._btn_crop.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 4px; padding: 4px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.06); }"
            "QPushButton:pressed { background: rgba(0,0,0,0.10); }"
        )
        self._btn_crop.clicked.connect(self._toggle_crop_mode)
        _register_toolbar_button(self._btn_crop)
        layout.addWidget(self._btn_crop)

        # 4. 另存为按钮
        self._btn_save_as = QPushButton()
        self._btn_save_as.setFixedSize(28, 28)
        self._btn_save_as.setIconSize(QSize(16, 16))
        self._btn_save_as.setIcon(_icon("icon_pin_save_as.svg"))
        self._btn_save_as.setToolTip("另存为…")
        self._btn_save_as.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_save_as.setFlat(True)
        self._btn_save_as.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 4px; padding: 4px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.06); }"
            "QPushButton:pressed { background: rgba(0,0,0,0.10); }"
        )
        self._btn_save_as.clicked.connect(self._do_save_as)
        _register_toolbar_button(self._btn_save_as)
        layout.addWidget(self._btn_save_as)

        # 5. 复制按钮
        self._btn_copy = QPushButton()
        self._btn_copy.setFixedSize(28, 28)
        self._btn_copy.setIconSize(QSize(16, 16))
        self._btn_copy.setIcon(_icon("icon_action_copy.svg"))
        self._btn_copy.setToolTip("复制")
        self._btn_copy.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_copy.setFlat(True)
        self._btn_copy.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 4px; padding: 4px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.06); }"
            "QPushButton:pressed { background: rgba(0,0,0,0.10); }"
        )
        self._btn_copy.clicked.connect(self._do_copy)
        _register_toolbar_button(self._btn_copy)
        layout.addWidget(self._btn_copy)

        # 6. 销毁（不保存）按钮
        self._btn_destroy = QPushButton()
        self._btn_destroy.setFixedSize(28, 28)
        self._btn_destroy.setIconSize(QSize(16, 16))
        self._btn_destroy.setIcon(_icon("icon_pin_destroy.svg"))
        self._btn_destroy.setToolTip("销毁（不保存）")
        self._btn_destroy.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_destroy.setFlat(True)
        self._btn_destroy.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 4px; padding: 4px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.06); }"
            "QPushButton:pressed { background: rgba(0,0,0,0.10); }"
        )
        self._btn_destroy.clicked.connect(self._do_destroy)
        _register_toolbar_button(self._btn_destroy)
        layout.addWidget(self._btn_destroy)

        # 6. 最小化按钮
        self._btn_minimize = QPushButton()
        self._btn_minimize.setFixedSize(28, 28)
        self._btn_minimize.setIconSize(QSize(16, 16))
        self._btn_minimize.setIcon(_icon("icon_pin_minimize.svg"))
        self._btn_minimize.setToolTip("最小化")
        self._btn_minimize.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_minimize.setFlat(True)
        self._btn_minimize.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 4px; padding: 4px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.06); }"
            "QPushButton:pressed { background: rgba(0,0,0,0.10); }"
        )
        self._btn_minimize.clicked.connect(self._do_minimize)
        _register_toolbar_button(self._btn_minimize)
        layout.addWidget(self._btn_minimize)

        # 7. 关闭按钮
        self._btn_close = QPushButton()
        self._btn_close.setFixedSize(28, 28)
        self._btn_close.setIconSize(QSize(16, 16))
        self._btn_close.setIcon(_icon("icon_action_close.svg"))
        self._btn_close.setToolTip("关闭（保存）")
        self._btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_close.setFlat(True)
        self._btn_close.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 4px; padding: 4px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.06); }"
            "QPushButton:pressed { background: rgba(0,0,0,0.10); }"
        )
        self._btn_close.clicked.connect(self._do_close_save)
        _register_toolbar_button(self._btn_close)
        layout.addWidget(self._btn_close)

        toolbar.setObjectName("pinned_toolbar")
        toolbar.setStyleSheet("""
            QWidget#pinned_toolbar {
                background: rgba(255, 255, 255, 0.96);
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 8px;
            }
            QToolTip {
                background-color: #ffffff;
                color: #1e293b;
                border: 1px solid rgba(0, 0, 0, 0.08);
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
                font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
            }
        """)
        toolbar.adjustSize()

        self._toolbar_opacity = QGraphicsOpacityEffect(toolbar)
        self._toolbar_opacity.setOpacity(0.0)
        toolbar.setGraphicsEffect(self._toolbar_opacity)

        self._toolbar = toolbar
        self._toolbar.installEventFilter(self)
        self._refresh_ocr_availability()
        self._apply_scale()

    def _update_toolbar_pos(self) -> None:
        if self._toolbar is None:
            return
        tw = int(self._toolbar.width())
        th = int(self._toolbar.height())
        gap = 2

        if hasattr(self, "_scroll_area") and self._scroll_area.isVisible():
            scroll_geo = self._scroll_area.geometry()
            img_x = int(scroll_geo.x())
            img_y = int(scroll_geo.y())
            img_w = int(scroll_geo.width())
            img_h = int(scroll_geo.height())
        else:
            label_geo = self._label.geometry()
            img_x = int(label_geo.x())
            img_y = int(label_geo.y())
            img_w = int(label_geo.width())
            img_h = int(label_geo.height())

        # 默认：上方外侧右对齐
        x = img_x + img_w - tw

        # 避开垂直滚动条：如果右侧滚动条可见，额外向左移动 15 像素以防止遮挡
        try:
            if hasattr(self, "_scroll_area") and self._scroll_area.verticalScrollBar().isVisible():
                x -= 15
        except Exception:
            pass

        y = img_y - th - gap

        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            win_x = int(self.x())

            # 右对齐后右侧超出屏幕 -> 左对齐
            if win_x + x + tw > geo.right():
                x = img_x

            # 左对齐后左侧超出屏幕 -> 右对齐
            if win_x + x < geo.left():
                x = img_x + img_w - tw

            # 如果右对齐仍然超出右侧，贴右侧边界
            if win_x + x + tw > geo.right():
                x = geo.right() - win_x - tw

        # 工具栏始终位于图片上边线外侧；窗口顶部透明留白负责容纳它。
        if y < 0:
            y = 0

        max_x = max(0, int(self.width()) - tw)
        x = max(0, min(int(x), max_x))

        self._toolbar.move(x, y)

    def _toolbar_top_reserve(self) -> int:
        th = 34
        try:
            if self._toolbar is not None:
                self._toolbar.adjustSize()
                th = max(th, int(self._toolbar.height()))
        except Exception:
            pass
        return int(th + 6)

    def _toolbar_width_reserve(self) -> int:
        tw = 0
        try:
            if self._toolbar is not None:
                self._toolbar.adjustSize()
                tw = max(tw, int(self._toolbar.width()))
        except Exception:
            pass
        return int(tw + 12)

    def _image_height_px(self) -> int:
        try:
            shape = getattr(self._image_bgr, "shape", None)
            if shape is not None and len(shape) >= 1:
                return int(shape[0])
        except Exception:
            pass
        return 0

    def _ocr_disabled_reason(self) -> str:
        height = self._image_height_px()
        limit = int(self.OCR_MAX_IMAGE_HEIGHT)
        if height > limit:
            return f"当前图片高度 {height}px，超过 {limit}px，文字识别不可用"
        return ""

    def _refresh_ocr_availability(self) -> None:
        btn = self._toolbar_ocr_button
        if btn is None:
            return
        try:
            reason = self._ocr_disabled_reason()
            if reason:
                btn.setEnabled(False)
                btn.setToolTip(reason)
                btn.setCursor(QCursor(Qt.CursorShape.ForbiddenCursor))
            else:
                btn.setEnabled(True)
                btn.setToolTip("文字识别")
                btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; border-radius: 4px; padding: 4px; }"
                "QPushButton:hover { background: rgba(0,0,0,0.06); }"
                "QPushButton:disabled { background: transparent; color: #cbd5e1; }"
            )
        except Exception:
            pass

    def _ocr_busy_tooltip_anchor(self) -> QPoint:
        btn = self._toolbar_ocr_button
        if btn is not None:
            return btn.mapToGlobal(QPoint(int(btn.width() / 2), -4))
        return self.mapToGlobal(QPoint(int(self.width() / 2), 0))

    def _animate_toolbar(self, target_opacity: float) -> None:
        if self._toolbar is None:
            return
        anim = QPropertyAnimation(self._toolbar_opacity, b"opacity", self)
        anim.setDuration(120)
        anim.setStartValue(float(self._toolbar_opacity.opacity()))
        anim.setEndValue(float(target_opacity))
        anim.finished.connect(self._on_fade_finished)
        anim.start()
        self._toolbar_anim = anim

    def _on_fade_finished(self) -> None:
        if self._toolbar is not None and self._toolbar_opacity.opacity() <= 0.01:
            self._toolbar.hide()

    def _show_toolbar(self) -> None:
        if self._toolbar is None:
            return
        if not self._toolbar.isVisible():
            self._toolbar.show()
            self._toolbar.raise_()
            self._update_toolbar_pos()
            self._toolbar_opacity.setOpacity(0.0)
        self._animate_toolbar(1.0)
        self._show_scale_badge()

    def _hide_toolbar(self) -> None:
        if self._toolbar is None:
            return
        self._hide_toolbar_tooltip()
        self._animate_toolbar(0.0)
        self._hide_scale_badge()

    def _show_scale_badge(self) -> None:
        self._scale_badge_visible = True
        self._update_scale_badge()

    def _hide_scale_badge(self) -> None:
        self._scale_badge_visible = False
        try:
            self._scale_badge.hide()
        except Exception:
            pass

    def _show_toolbar_tooltip(self, button: QPushButton, text_override: Optional[str] = None) -> None:
        text = str(text_override if text_override is not None else button.toolTip() or "").strip()
        if not text:
            self._hide_toolbar_tooltip()
            return
        try:
            from deepcat.ui.post_capture_actions import SmoothToolTip
            if self._toolbar_tooltip is None:
                self._toolbar_tooltip = SmoothToolTip()
            pos = button.mapToGlobal(QPoint(int(button.width() / 2), int(button.height() + 4)))
            self._toolbar_tooltip.show_text(text, pos, padding=21)
        except Exception:
            pass

    def _show_ocr_busy_tooltip(self, text: str) -> None:
        self._ocr_busy_tooltip_active = True
        btn = self._toolbar_ocr_button
        if btn is not None:
            self._show_toolbar_tooltip(btn, text_override=text)

    def _hide_ocr_busy_tooltip(self) -> None:
        self._ocr_busy_tooltip_active = False
        self._hide_toolbar_tooltip()

    def _hide_toolbar_tooltip(self) -> None:
        try:
            if self._toolbar_tooltip is not None:
                self._toolbar_tooltip.hide()
        except Exception:
            pass

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent, QPoint
        from PyQt6.QtGui import QMouseEvent

        if obj in getattr(self, "_toolbar_buttons", []):
            if event.type() == QEvent.Type.Enter:
                self._show_toolbar()
                try:
                    if bool(getattr(self, "_ocr_busy_tooltip_active", False)) and obj is self._toolbar_ocr_button:
                        self._show_toolbar_tooltip(obj, text_override="识别中")
                    else:
                        self._show_toolbar_tooltip(obj)
                except Exception:
                    pass
            elif event.type() == QEvent.Type.Leave:
                self._hide_toolbar_tooltip()
            elif event.type() == QEvent.Type.ToolTip:
                try:
                    self._show_toolbar_tooltip(obj)
                except Exception:
                    pass
                return True

        is_scroll_obj = False
        try:
            if hasattr(self, "_scroll_area") and self._scroll_area is not None:
                if obj is self._scroll_area or obj is self._scroll_area.viewport():
                    is_scroll_obj = True
        except Exception:
            pass

        if is_scroll_obj and event.type() == QEvent.Type.Wheel:
            try:
                scrollbar = self._scroll_area.verticalScrollBar()
                if scrollbar.isVisible():
                    pos_in_scroll = self._scroll_area.mapFromGlobal(obj.mapToGlobal(event.position().toPoint()))
                    if scrollbar.geometry().contains(pos_in_scroll):
                        return False
                self.wheelEvent(event)
                event.accept()
                return True
            except Exception:
                pass

        if (obj is self._label or is_scroll_obj) and event.type() == QEvent.Type.Enter:
            self._show_toolbar()
        if (obj is self._label or is_scroll_obj) and event.type() == QEvent.Type.Leave:
            try:
                pos_g = QCursor.pos()
                pos_l = self.mapFromGlobal(pos_g)
                in_scroll = hasattr(self, "_scroll_area") and self._scroll_area.geometry().contains(pos_l)
                # 为工具栏几何区域向外扩展 8 像素进行安全边界检测，以规避高 DPI 舍入误差造成的误判
                in_toolbar = self._toolbar is not None and self._toolbar.geometry().adjusted(-8, -8, 8, 8).contains(pos_l)
                if not in_scroll and not in_toolbar:
                    self._hide_toolbar()
            except Exception:
                self._hide_toolbar()
        if (obj is self._label or is_scroll_obj) and event.type() == QEvent.Type.MouseMove:
            self._get_active_annotation_overlay()

        if obj is self._toolbar and event.type() == QEvent.Type.Enter:
            self._show_toolbar()
        if obj is self._toolbar and event.type() == QEvent.Type.Leave:
            try:
                pos_g = QCursor.pos()
                pos_l = self.mapFromGlobal(pos_g)
                # 为工具栏几何区域向外扩展 8 像素进行安全边界检测，以规避高 DPI 舍入误差造成的误判
                in_toolbar = self._toolbar is not None and self._toolbar.geometry().adjusted(-8, -8, 8, 8).contains(pos_l)
                if not in_toolbar:
                    self._hide_toolbar()
            except Exception:
                self._hide_toolbar()

        if (obj is self._label or is_scroll_obj) and event.type() in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick
        }:
            overlay = self._get_active_annotation_overlay()
            is_editing = False
            if overlay is not None or self._crop_overlay is not None:
                is_editing = True
            if self._edit_actions is not None:
                try:
                    if bool(self._edit_actions._annotation_mode):
                        is_editing = True
                except Exception:
                    pass
            if bool(self._blur_mode):
                is_editing = True

            if is_editing:
                try:
                    if event.type() == QEvent.Type.MouseButtonPress:
                        evt = QMouseEvent(
                            event.type(),
                            self.mapFromGlobal(event.globalPosition().toPoint()),
                            event.globalPosition(),
                            event.button(),
                            event.buttons(),
                            event.modifiers()
                        )
                        self.mousePressEvent(evt)
                        event.accept()
                        return True
                    elif event.type() == QEvent.Type.MouseMove:
                        evt = QMouseEvent(
                            event.type(),
                            self.mapFromGlobal(event.globalPosition().toPoint()),
                            event.globalPosition(),
                            event.button(),
                            event.buttons(),
                            event.modifiers()
                        )
                        self.mouseMoveEvent(evt)
                        event.accept()
                        return True
                    elif event.type() == QEvent.Type.MouseButtonRelease:
                        evt = QMouseEvent(
                            event.type(),
                            self.mapFromGlobal(event.globalPosition().toPoint()),
                            event.globalPosition(),
                            event.button(),
                            event.buttons(),
                            event.modifiers()
                        )
                        self.mouseReleaseEvent(evt)
                        event.accept()
                        return True
                    elif event.type() == QEvent.Type.MouseButtonDblClick:
                        evt = QMouseEvent(
                            event.type(),
                            self.mapFromGlobal(event.globalPosition().toPoint()),
                            event.globalPosition(),
                            event.button(),
                            event.buttons(),
                            event.modifiers()
                        )
                        self.mouseDoubleClickEvent(evt)
                        event.accept()
                        return True
                except Exception:
                    pass

        return super().eventFilter(obj, event)

    def _do_minimize(self) -> None:
        self._finalize_overlay_annotations()
        try:
            import time
            pin_dir = Path(self._default_dir) / "pin"
            pin_dir.mkdir(parents=True, exist_ok=True)
            ext = self._default_format if self._default_format in {"png", "jpg", "pdf"} else "png"
            name = f"pin_{time.strftime('%Y%m%d_%H%M%S', time.localtime())}_{time.time_ns() % 1000000}.{ext}"
            p = str(pin_dir / name)
            # 长图编码耗时较长，落盘放到后台线程；失败时通过 toast 提示
            start_image_save(
                self._image_bgr, p, ext, int(self._jpg_quality),
                saver=save_full_image_to_path,
                on_saved=self._on_pin_background_save_finished,
                on_failed=self._on_pin_background_save_failed,
            )
        except Exception:
            pass
        if self._on_toast is not None:
            self._on_toast("提示", "贴图已隐藏，右键托盘图标显示", 3000)
        self.hide()

    def _on_pin_background_save_finished(self, _tag: object, saved_path: str) -> None:
        try:
            if self._on_toast is not None:
                self._on_toast("提示", f"已保存到：{saved_path}", 3000)
        except RuntimeError:
            # 窗口可能在后台保存完成前被销毁
            pass

    def _on_pin_background_save_failed(self, _tag: object, error: str) -> None:
        try:
            if self._on_toast is not None:
                self._on_toast("保存失败", error, 3500)
        except RuntimeError:
            pass

    def _do_close_save(self) -> None:
        if bool(getattr(self, "_save_in_progress", False)):
            return
        self._finalize_overlay_annotations()
        fmt = self._default_format if self._default_format in {"png", "jpg", "pdf"} else "png"
        try:
            target = unique_output_path(self._default_dir, fmt)
        except Exception as error:
            if self._on_toast is not None:
                self._on_toast("保存失败", str(error), 3500)
            return
        self._save_in_progress = True
        start_image_save(
            self._image_bgr, str(target), fmt, int(self._jpg_quality),
            saver=save_full_image_to_path,
            on_saved=self._on_close_save_finished,
            on_failed=self._on_close_save_failed,
        )

    def _on_close_save_finished(self, _tag: object, saved_path: str) -> None:
        self._save_in_progress = False
        try:
            if self._on_toast is not None:
                self._on_toast("提示", f"已保存到：{saved_path}", 3000)
            self.close()
        except RuntimeError:
            # 窗口可能在后台保存完成前被销毁
            pass

    def _on_close_save_failed(self, _tag: object, error: str) -> None:
        self._save_in_progress = False
        try:
            if self._on_toast is not None:
                self._on_toast("保存失败", error, 3500)
        except RuntimeError:
            pass

    def _do_destroy(self) -> None:
        self.close()

    def _do_save_as(self) -> None:
        if bool(getattr(self, "_save_in_progress", False)):
            return
        self._finalize_overlay_annotations()
        import time
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        fmt = self._default_format if self._default_format in {"png", "jpg", "pdf"} else "png"
        init = str(Path(self._default_dir) / f"screenshot_{ts}.{fmt}")
        filters = "PNG (*.png);;JPG (*.jpg);;PDF (*.pdf)"
        path, _ = QFileDialog.getSaveFileName(self, "另存为", init, filters)
        if not path:
            return
        ext = Path(path).suffix.lower().lstrip(".")
        fmt2 = ext or fmt
        self._save_in_progress = True
        start_image_save(
            self._image_bgr, path, fmt2, int(self._jpg_quality),
            saver=save_full_image_to_path,
            on_saved=self._on_close_save_finished,
            on_failed=self._on_close_save_failed,
        )

    def _do_copy(self) -> None:
        self._finalize_overlay_annotations()
        try:
            copy_bgr_image(self._image_bgr)
        except Exception:
            return

    def _toggle_crop_mode(self) -> None:
        if self._crop_overlay is not None:
            self._crop_overlay.cancel_crop()
            return
        if self._edit_actions is not None:
            try:
                self._finalize_overlay_annotations()
            except Exception:
                pass
        self._crop_overlay = PinnedCropOverlay(self._label)
        self._crop_overlay.setGeometry(0, 0, self._label.width(), self._label.height())
        self._crop_overlay.show()
        self._crop_overlay.raise_()
        self._crop_overlay.setFocus()

    def _toggle_expand_toolbar(self) -> None:
        if self._expand_panel is None:
            return
        if self._expand_panel.isHidden():
            self._expand_panel.show()
            self._btn_expand.setText("›")
            self._btn_expand.setToolTip("收起绘制工具条")
            self._get_or_create_edit_actions()
        else:
            self._expand_panel.hide()
            self._btn_expand.setText("‹")
            self._btn_expand.setToolTip("展开绘制工具条")

        if self._toolbar is not None:
            self._toolbar.adjustSize()
            self._apply_scale()
            self._update_toolbar_pos()

    def _get_or_create_edit_actions(self) -> Optional[QWidget]:
        if self._edit_actions is None:
            try:
                from deepcat.ui.post_capture_actions import PostCaptureActions

                label_pos = self._label.mapToGlobal(QPoint(0, 0))
                region = QRect(label_pos, self._label.size())

                def on_close() -> None:
                    self._finalize_overlay_annotations()
                    self._edit_actions = None

                actions = PostCaptureActions(
                    region_rect=region,
                    image_bgr=self._image_bgr,
                    default_dir=self._default_dir,
                    default_format=self._default_format,
                    jpg_quality=int(self._jpg_quality),
                    capture_frames=int(self._capture_frames),
                    region_px=(0, 0, int(self._image_bgr.shape[1]), int(self._image_bgr.shape[0])),
                    on_close=on_close,
                    on_toast=self._on_toast,
                    save_button_auto=False,
                    button_style="icon",
                    on_image_annotated=lambda img: self.update_image(img, from_annotation=True),
                    annotation_parent=self._label,
                    ocr_busy_tooltip_anchor=self._ocr_busy_tooltip_anchor,
                    ocr_busy_tooltip_direction="above",
                    ocr_busy_tooltip_show=self._show_ocr_busy_tooltip,
                    ocr_busy_tooltip_hide=self._hide_ocr_busy_tooltip,
                )
                try:
                    actions._btn_record.setEnabled(False)
                    actions._btn_record.setToolTip("贴图模式下禁用录屏")
                except Exception:
                    pass
                self._edit_actions = actions
                self_ref = weakref.ref(self)
                actions.destroyed.connect(lambda *_: setattr(self_ref(), "_edit_actions", None) if self_ref() else None)
                actions.hide() # 静默隐藏！
            except Exception:
                self._edit_actions = None
        return self._edit_actions

    def _trigger_annotation_tool(self, tool: str) -> None:
        if str(tool) == "ocr":
            reason = self._ocr_disabled_reason()
            if reason:
                self._refresh_ocr_availability()
                try:
                    btn = self._toolbar_ocr_button
                    if btn is not None:
                        self._show_toolbar_tooltip(btn, text_override=reason)
                except Exception:
                    pass
                try:
                    if self._on_toast is not None:
                        self._on_toast("提示", reason, 1800)
                except Exception:
                    pass
                return
        actions = self._get_or_create_edit_actions()
        if actions is not None:
            try:
                if tool == "ocr":
                    actions._ocr_extract()
                elif tool == "undo":
                    actions._annotation_undo()
                elif tool == "redo":
                    actions._annotation_redo()
                else:
                    actions._toggle_annotation_mode(tool)
                self._update_edit_actions_pos()
            except Exception:
                pass

        # 联动更新选中高亮状态
        if tool not in {"undo", "redo", "ocr"}:
            active_tool = ""
            if actions is not None:
                active_tool = str(actions._annotation_mode)
            for btn_tool, btn in self._tool_buttons.items():
                if btn_tool == active_tool:
                    btn.setStyleSheet(
                        "QPushButton { background: rgba(33, 150, 243, 0.15); border: 1px solid rgba(33, 150, 243, 0.3); border-radius: 4px; padding: 4px; }"
                        "QPushButton:hover { background: rgba(33, 150, 243, 0.25); }"
                    )
                else:
                    btn.setStyleSheet(
                        "QPushButton { background: transparent; border: none; border-radius: 4px; padding: 4px; }"
                        "QPushButton:hover { background: rgba(0,0,0,0.06); }"
                    )

    def _open_edit_toolbar(self) -> None:
        try:
            if self._edit_actions is not None and self._edit_actions.isVisible():
                self._edit_actions.raise_()
                return
        except Exception:
            self._edit_actions = None
        try:
            from deepcat.ui.post_capture_actions import PostCaptureActions

            label_pos = self._label.mapToGlobal(QPoint(0, 0))
            region = QRect(label_pos, self._label.size())

            def on_close() -> None:
                self._finalize_overlay_annotations()
                self._edit_actions = None

            actions = PostCaptureActions(
                region_rect=region,
                image_bgr=self._image_bgr,
                default_dir=self._default_dir,
                default_format=self._default_format,
                jpg_quality=int(self._jpg_quality),
                capture_frames=int(self._capture_frames),
                region_px=(0, 0, int(self._image_bgr.shape[1]), int(self._image_bgr.shape[0])),
                on_close=on_close,
                on_toast=self._on_toast,
                save_button_auto=False,
                button_style="icon",
                on_image_annotated=lambda img: self.update_image(img, from_annotation=True),
                annotation_parent=self._label,
                ocr_busy_tooltip_anchor=self._ocr_busy_tooltip_anchor,
                ocr_busy_tooltip_direction="above",
                ocr_busy_tooltip_show=self._show_ocr_busy_tooltip,
                ocr_busy_tooltip_hide=self._hide_ocr_busy_tooltip,
            )
            # 置顶图贴图模式下，禁用录屏按钮并添加悬停提示
            # 置顶图贴图模式下，禁用录屏按钮并添加悬停提示
            try:
                actions._btn_record.setEnabled(False)
                actions._btn_record.setToolTip("贴图模式下禁用录屏")
            except Exception:
                pass
            self._edit_actions = actions
            self_ref = weakref.ref(self)
            actions.destroyed.connect(lambda *_: setattr(self_ref(), "_edit_actions", None) if self_ref() else None)
            actions.show()
            actions.raise_()
        except Exception:
            self._edit_actions = None

    def update_image(self, image_bgr: np.ndarray, *, from_annotation: bool = False) -> None:
        dim_changed = True
        if hasattr(self, "_image_bgr") and self._image_bgr is not None:
            if self._image_bgr.shape[0] == image_bgr.shape[0] and self._image_bgr.shape[1] == image_bgr.shape[1]:
                dim_changed = False

        self._image_bgr = image_bgr.copy()
        if self._edit_actions is not None:
            try:
                self._edit_actions._image_bgr = self._image_bgr.copy()
                if not bool(from_annotation):
                    self._edit_actions._base_image_bgr = self._image_bgr.copy()
                if hasattr(self._edit_actions, "_refresh_ocr_availability"):
                    self._edit_actions._refresh_ocr_availability()
            except Exception:
                pass
        self._refresh_ocr_availability()
        self._rebuild_orig_pixmap_from_bgr()

        if dim_changed:
            self._apply_scale()
        else:
            self._label.setPixmap(self._display_pixmap())

        if self._on_image_changed is not None:
            try:
                self._on_image_changed(self._image_bgr)
            except Exception:
                pass

    def _update_edit_actions_pos(self) -> None:
        if self._edit_actions is not None:
            try:
                if hasattr(self, "_scroll_area") and self._scroll_area.isVisible():
                    container_pos = self._scroll_area.mapToGlobal(QPoint(0, 0))
                    visible_region = QRect(container_pos, self._scroll_area.size())
                else:
                    label_pos = self._label.mapToGlobal(QPoint(0, 0))
                    visible_region = QRect(label_pos, self._label.size())

                self._edit_actions.set_region(visible_region)

                if self._edit_actions._annotation_overlay is not None:
                    overlay = self._edit_actions._annotation_overlay
                    overlay.setGeometry(0, 0, self._label.width(), self._label.height())
                    overlay.set_region(QRect(0, 0, self._label.width(), self._label.height()), self._image_bgr, clear=False)
            except Exception:
                pass

        if self._crop_overlay is not None:
            try:
                self._crop_overlay.update_buttons_position()
            except Exception:
                pass

    def _apply_scroll_area_style(self) -> None:
        self._scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                border: none;
                background: #f8fafc;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(100, 116, 139, 0.25);
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(100, 116, 139, 0.45);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
                background: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: #f8fafc;
            }
        """)

    def _set_scroll_shadow_enabled(self, enabled: bool) -> None:
        effect = getattr(self, "_scroll_shadow", None)
        if effect is None:
            return
        try:
            effect.setEnabled(bool(enabled))
        except Exception:
            pass

    def _scrollbar_slider_center(self) -> Optional[QPoint]:
        try:
            scrollbar = self._scroll_area.verticalScrollBar()
            if scrollbar is None or not scrollbar.isVisible() or int(scrollbar.maximum()) <= 0:
                return None
            option = QStyleOptionSlider()
            scrollbar.initStyleOption(option)
            slider_rect = scrollbar.style().subControlRect(
                QStyle.ComplexControl.CC_ScrollBar,
                option,
                QStyle.SubControl.SC_ScrollBarSlider,
                scrollbar,
            )
            if slider_rect.isNull():
                return None
            return scrollbar.mapTo(self, slider_rect.center())
        except Exception:
            try:
                scrollbar = self._scroll_area.verticalScrollBar()
                ratio = float(scrollbar.value() - scrollbar.minimum()) / float(max(1, scrollbar.maximum() - scrollbar.minimum()))
                y = int(12 + ratio * max(1, scrollbar.height() - 24))
                return scrollbar.mapTo(self, QPoint(int(scrollbar.width() / 2), y))
            except Exception:
                return None

    def _sync_scroll_hint_bubble_pos(self) -> None:
        bubble = getattr(self, "_scroll_hint_bubble", None)
        if bubble is None or not bubble.isVisible():
            return
        center = self._scrollbar_slider_center()
        if center is not None:
            bubble.show_at(center, duration_ms=2200)

    def _maybe_show_scroll_handle_hint(self) -> None:
        if bool(getattr(self, "_scroll_hint_shown", False)):
            return
        if int(getattr(self, "_capture_frames", 0) or 0) <= 0:
            return
        bubble = getattr(self, "_scroll_hint_bubble", None)
        if bubble is None:
            return
        center = self._scrollbar_slider_center()
        if center is None:
            return
        self._scroll_hint_shown = True
        bubble.show_at(center)

    def _apply_scale(self) -> None:
        pix = self._display_pixmap()
        pad = 18
        top_pad = max(pad, self._toolbar_top_reserve())
        self._label.setPixmap(pix)
        try:
            di = pix.deviceIndependentSize()
            disp_w = int(di.width())
            disp_h = int(di.height())
        except Exception:
            disp_w = int(pix.width())
            disp_h = int(pix.height())
        self._label.resize(disp_w, disp_h)

        # Calculate max height allowed based on screen
        try:
            screen = self.screen() or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen is not None else None
        except Exception:
            geo = None

        if geo is not None:
            max_allowed_h = int(geo.height() - 60)
        else:
            max_allowed_h = 900

        if disp_h + top_pad + pad > max_allowed_h:
            use_scroll = True
            win_h = max_allowed_h
            scrollbar_w = 8
            image_area_w = int(disp_w + scrollbar_w)
            image_area_h = int(max(80, win_h - top_pad - pad))
        else:
            use_scroll = False
            image_area_w = int(disp_w)
            image_area_h = int(disp_h)
            win_h = image_area_h + top_pad + pad
        win_w = max(image_area_w + pad * 2, self._toolbar_width_reserve() + pad * 2)

        self.resize(win_w, win_h)
        self._set_scroll_shadow_enabled(True)

        self._apply_scroll_area_style()

        if use_scroll:
            scroll_x = max(pad, int((win_w - image_area_w) / 2))
            self._scroll_area.setGeometry(scroll_x, top_pad, image_area_w, image_area_h)
            self._scroll_area.show()
            self._label.move(0, 0)
            self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            scroll_x = max(pad, int((win_w - image_area_w) / 2))
            self._scroll_area.setGeometry(scroll_x, top_pad, image_area_w, image_area_h)
            self._scroll_area.show()
            self._label.move(0, 0)
            self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.update()

        self._update_scale_badge()
        if bool(use_scroll) and self.isVisible():
            single_shot_scoped(220, self, self._maybe_show_scroll_handle_hint)

    def _update_scale_badge(self) -> None:
        badge = getattr(self, "_scale_badge", None)
        if badge is None:
            return
        try:
            new_text = f"{int(round(float(self._scale) * 100.0))}%"
            new_visible = bool(getattr(self, "_scale_badge_visible", False))

            old_text = badge.text()
            old_visible = not badge.isHidden()

            # 如果文本与显示状态都未发生改变，说明不需要重新排布和定位工具栏，以防止引发按钮栏重定位产生的抖动
            changed = (new_text != old_text) or (new_visible != old_visible)

            badge.setText(new_text)
            badge.adjustSize()
            badge.updateGeometry()  # 显式通知 Layout 重新计算尺寸
            if new_visible:
                badge.show()
            else:
                badge.hide()

            # 百分比变动会改变工具栏的实际宽度，仅当文本或可见性确实发生改变时才触发重排定位
            if changed and getattr(self, "_toolbar", None) is not None:
                self._toolbar.adjustSize()
                self._update_toolbar_pos()
        except Exception:
            pass

    def _rebuild_orig_pixmap_from_bgr(self) -> None:
        rgb = self._image_bgr[:, :, ::-1].copy()
        qimg = QImage(
            rgb.data,
            int(rgb.shape[1]),
            int(rgb.shape[0]),
            int(rgb.strides[0]),
            QImage.Format.Format_RGB888,
        ).copy()
        qimg.setDevicePixelRatio(self._image_dpr)
        self._orig_pix = QPixmap.fromImage(qimg)
        self._orig_pix.setDevicePixelRatio(self._image_dpr)

    def _display_pixmap(self) -> QPixmap:
        if self._scale == 1.0:
            return self._orig_pix
        pix = self._orig_pix.scaled(
            int(max(1, self._orig_pix.width() * self._scale)),
            int(max(1, self._orig_pix.height() * self._scale)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pix.setDevicePixelRatio(self._image_dpr)
        return pix

    def _set_blur_mode(self, enabled: bool) -> None:
        self._blur_mode = bool(enabled)
        if bool(enabled):
            self._apply_blur_cursor()
        else:
            self.unsetCursor()

    def _apply_blur_cursor(self) -> None:
        s = int(max(8, int(self._brush_size_px)))
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor(0, 0, 0))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawRect(0, 0, s - 1, s - 1)
        p.end()
        self.setCursor(QCursor(pm, s // 2, s // 2))

    def _to_image_xy(self, pos: Optional[QPoint] = None) -> tuple[int, int] | None:
        if pos is None:
            return None
        lp = self._label.mapFromGlobal(self.mapToGlobal(pos))
        disp_w = int(self._label.width())
        disp_h = int(self._label.height())
        if disp_w <= 0 or disp_h <= 0:
            return None
        lx = int(lp.x())
        ly = int(lp.y())
        if lx < 0 or ly < 0 or lx >= disp_w or ly >= disp_h:
            return None
        src_h, src_w = int(self._image_bgr.shape[0]), int(self._image_bgr.shape[1])
        if src_w <= 0 or src_h <= 0:
            return None
        x = int(lx * src_w / disp_w)
        y = int(ly * src_h / disp_h)
        x = max(0, min(src_w - 1, x))
        y = max(0, min(src_h - 1, y))
        return (x, y)

    def _apply_blur_at(self, pos: Optional[QPoint] = None, skip_pixmap_rebuild: bool = False) -> None:
        p = self._to_image_xy(pos)
        if p is None:
            return
        x, y = p
        h, w = int(self._image_bgr.shape[0]), int(self._image_bgr.shape[1])
        disp_w = max(1, int(self._label.width()))
        disp_h = max(1, int(self._label.height()))

        # 笔刷大小
        brush_size = max(8, int(self._brush_size_px))

        side_x = int(brush_size * w / disp_w)
        side_y = int(brush_size * h / disp_h)
        side = max(6, int((side_x + side_y) / 2))
        if side % 2 != 0:
            side += 1
        half = side // 2

        x0, x1 = max(0, x - half), min(w, x + half)
        y0, y1 = max(0, y - half), min(h, y + half)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return

        roi = self._image_bgr[y0:y1, x0:x1]
        k = 15
        if k > min(x1 - x0, y1 - y0):
            k = max(3, min(x1 - x0, y1 - y0) // 2 * 2 + 1)

        import cv2
        blurred = cv2.GaussianBlur(roi, (k, k), 0)
        self._image_bgr[y0:y1, x0:x1] = blurred

        if self._edit_actions is not None:
            try:
                self._edit_actions._base_image_bgr = self._image_bgr.copy()
            except Exception:
                pass

        if skip_pixmap_rebuild:
            # 局部更新已有的 label pixmap，瞬间极速响应！
            blurred_rgb = blurred[:, :, ::-1].copy()
            local_qimg = QImage(
                blurred_rgb.data,
                int(blurred_rgb.shape[1]),
                int(blurred_rgb.shape[0]),
                int(blurred_rgb.strides[0]),
                QImage.Format.Format_RGB888
            ).copy()

            lx0 = int(x0 * disp_w / w)
            ly0 = int(y0 * disp_h / h)
            lx1 = int(x1 * disp_w / w)
            ly1 = int(y1 * disp_h / h)
            lw = lx1 - lx0
            lh = ly1 - ly0

            if lw > 0 and lh > 0:
                pix = self._label.pixmap()
                if pix is not None and not pix.isNull():
                    painter = QPainter(pix)
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    painter.drawImage(QRect(lx0, ly0, lw, lh), local_qimg)
                    painter.end()
                    self._label.setPixmap(pix)
                    self._label.update(QRect(lx0, ly0, lw, lh))
        else:
            self._rebuild_orig_pixmap_from_bgr()
            self._apply_scale()
            if self._on_image_changed is not None:
                try:
                    self._on_image_changed(self._image_bgr)
                except Exception:
                    pass

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        finally:
            if painter.isActive():
                painter.end()

    def _get_active_annotation_overlay(self) -> Optional[QWidget]:
        if self._edit_actions is not None:
            try:
                overlay = self._edit_actions._annotation_overlay
                mode = self._edit_actions._annotation_mode
                if mode != "text" and hasattr(self, "_text_editor") and self._text_editor is not None:
                    self._commit_text_editor()
                if overlay is not None and bool(mode):
                    if mode == "blur":
                        self.setCursor(Qt.CursorShape.CrossCursor)
                        return None
                    self.setCursor(overlay.cursor())
                    return overlay
            except Exception:
                pass
        if not bool(self._blur_mode):
            self.unsetCursor()
        return None

    def _get_current_style_color(self) -> str:
        try:
            from deepcat.settings_store import load_settings
            style = load_settings().ui.get("annotation_style", {})
            return str(style.get("text_color", "#E53935"))
        except Exception:
            return "#E53935"

    def _begin_text_editor(self, p: QPoint) -> None:
        self._commit_text_editor()
        from PyQt6.QtWidgets import QLineEdit
        label_pos = self._label.mapFromGlobal(self.mapToGlobal(p))
        editor = QLineEdit(self._label)
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        color_hex = self._get_current_style_color()
        font = QFont()
        font.setPixelSize(max(18, int(min(max(1, self._label.width()), max(1, self._label.height())) * 0.035)))
        metrics = QFontMetrics(font)
        editor_height = max(34, int(metrics.height()) + 12)
        self._text_editor_anchor = QPoint(label_pos)
        editor.setFont(font)
        editor.setStyleSheet(
            f"QLineEdit {{ background: rgba(255,255,255,0.96); border: 1px solid {color_hex}80;"
            f" border-radius: 4px; padding: 4px 6px; font-size: {font.pixelSize()}px; color: {color_hex}; }}"
        )
        editor.resize(180, editor_height)
        editor.show()
        editor.raise_()
        editor.setFocus()
        self._move_text_editor_caret_to_point(editor, label_pos, editor_height)
        editor.returnPressed.connect(self._commit_text_editor)
        editor.editingFinished.connect(self._commit_text_editor)
        self._text_editor = editor

    def _move_text_editor_caret_to_point(self, editor: QLineEdit, p: QPoint, editor_height: int) -> None:
        try:
            cursor_rect = editor.cursorRect()
            caret_top_right = cursor_rect.topRight()
            target_y = int(p.y() - round(float(cursor_rect.height()) / 2.0))
            editor.move(int(p.x() - caret_top_right.x()), int(target_y - caret_top_right.y()))
        except Exception:
            try:
                metrics = QFontMetrics(editor.font())
                visual_offset = QPoint(6, max(0, int(round((editor_height - int(metrics.height())) / 2.0))))
                editor.move(int(p.x() - visual_offset.x()), int(p.y() - visual_offset.y() - round(float(metrics.height()) / 2.0)))
            except Exception:
                editor.move(int(p.x()), int(p.y()))

    def _text_editor_text_top_left(self, editor: QLineEdit, text: str) -> QPoint:
        try:
            current_rect = editor.cursorRect()
            original_pos = int(editor.cursorPosition())
            editor.setCursorPosition(0)
            start_rect = editor.cursorRect()
            editor.setCursorPosition(original_pos)
            x = int(editor.x() + start_rect.x() + round(float(start_rect.width()) / 2.0))
            y = int(editor.y() + current_rect.y())
            return QPoint(x, y)
        except Exception:
            return QPoint(getattr(self, "_text_editor_anchor", None) or editor.pos())

    def _commit_text_editor(self) -> None:
        if not hasattr(self, "_text_editor") or self._text_editor is None:
            return
        editor = self._text_editor
        self._text_editor = None
        try:
            text = str(editor.text()).strip()
            if text:
                lp = self._text_editor_text_top_left(editor, text)
            else:
                lp = QPoint(getattr(self, "_text_editor_anchor", None) or editor.pos())
            editor.close()
        except Exception:
            text = ""
            lp = QPoint(0, 0)
        self._text_editor_anchor = None
        if not text:
            return
        w = max(1, int(self._label.width()))
        h = max(1, int(self._label.height()))
        norm_x = float(max(0, min(w, int(lp.x())))) / float(w)
        norm_y = float(max(0, min(h, int(lp.y())))) / float(h)
        if self._edit_actions is not None:
            try:
                overlay = self._edit_actions._annotation_overlay
                if overlay is not None:
                    overlay._commands.append({"type": "text", "pos": (norm_x, norm_y), "text": text})
                    overlay._redo.clear()
                    overlay.update()
                    overlay.changed.emit()
            except Exception:
                pass

    def _finalize_overlay_annotations(self) -> None:
        """在输出大图前，一次性固化合成所有标注，并清空 overlay"""
        overlay = self._get_active_annotation_overlay()
        if overlay is not None:
            try:
                base_bgr = self._image_bgr
                if self._edit_actions is not None and getattr(self._edit_actions, "_base_image_bgr", None) is not None:
                    try:
                        base_bgr = self._edit_actions._base_image_bgr
                    except Exception:
                        base_bgr = self._image_bgr
                final_bgr = overlay.render_to_bgr(base_bgr, commit_text=True)
                self._image_bgr = final_bgr.copy()
                old_block = False
                try:
                    old_block = bool(overlay.blockSignals(True))
                    overlay.clear()
                finally:
                    try:
                        overlay.blockSignals(old_block)
                    except Exception:
                        pass

                if self._edit_actions is not None:
                    try:
                        self._edit_actions._base_image_bgr = self._image_bgr.copy()
                        self._edit_actions._image_bgr = self._image_bgr.copy()
                        if hasattr(self._edit_actions, "_update_annotation_history"):
                            self._edit_actions._update_annotation_history(False, False)
                    except Exception:
                        pass

                self._rebuild_orig_pixmap_from_bgr()
                self._label.setPixmap(self._display_pixmap())
                if self._on_image_changed is not None:
                    try:
                        self._on_image_changed(self._image_bgr)
                    except Exception:
                        pass
                self._refresh_ocr_availability()
            except Exception:
                pass

    def mousePressEvent(self, event) -> None:
        overlay = self._get_active_annotation_overlay()
        is_toolbar_blur = False
        is_toolbar_text = False
        if self._edit_actions is not None:
            try:
                mode = self._edit_actions._annotation_mode
                if mode == "blur":
                    is_toolbar_blur = True
                elif mode == "text":
                    is_toolbar_text = True
            except Exception:
                pass

        if overlay is not None:
            try:
                from PyQt6.QtCore import QPointF
                from PyQt6.QtGui import QMouseEvent
                local_pos = QPointF(self._label.mapFromGlobal(event.globalPosition().toPoint()))
                new_event = QMouseEvent(
                    event.type(),
                    local_pos,
                    event.globalPosition(),
                    event.button(),
                    event.buttons(),
                    event.modifiers()
                )
                overlay.mousePressEvent(new_event)
                event.accept()
                return
            except Exception:
                pass

        if event.button() == Qt.MouseButton.LeftButton:
            if is_toolbar_text:
                self._begin_text_editor(event.position().toPoint())
                event.accept()
                return
            if bool(self._blur_mode) or is_toolbar_blur:
                self._painting_blur = True
                self._last_blur_pos = event.position().toPoint()
                self._apply_blur_at(self._last_blur_pos)
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                event.accept()
                return
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        overlay = self._get_active_annotation_overlay()
        is_toolbar_blur = False
        if self._edit_actions is not None:
            try:
                if self._edit_actions._annotation_mode == "blur":
                    is_toolbar_blur = True
            except Exception:
                pass

        if overlay is not None:
            try:
                from PyQt6.QtCore import QPointF
                from PyQt6.QtGui import QMouseEvent
                local_pos = QPointF(self._label.mapFromGlobal(event.globalPosition().toPoint()))
                new_event = QMouseEvent(
                    event.type(),
                    local_pos,
                    event.globalPosition(),
                    event.button(),
                    event.buttons(),
                    event.modifiers()
                )
                overlay.mouseMoveEvent(new_event)
                event.accept()
                return
            except Exception:
                pass

        if (bool(self._blur_mode) or is_toolbar_blur) and bool(self._painting_blur):
            p0 = self._last_blur_pos
            p1 = event.position().toPoint()
            if p0 is not None:
                dist = ((p1.x() - p0.x())**2 + (p1.y() - p0.y())**2)**0.5
                if dist > 3:
                    steps = int(dist / 3)
                    for i in range(1, steps + 1):
                        t = float(i) / steps
                        pt = QPoint(int(p0.x() + (p1.x() - p0.x()) * t), int(p0.y() + (p1.y() - p0.y()) * t))
                        self._apply_blur_at(pt, skip_pixmap_rebuild=True)
                else:
                    self._apply_blur_at(p1, skip_pixmap_rebuild=True)
            else:
                self._apply_blur_at(p1, skip_pixmap_rebuild=True)
            self._last_blur_pos = p1
            event.accept()
            return
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._update_toolbar_pos()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        overlay = self._get_active_annotation_overlay()
        is_toolbar_blur = False
        if self._edit_actions is not None:
            try:
                if self._edit_actions._annotation_mode == "blur":
                    is_toolbar_blur = True
            except Exception:
                pass

        if overlay is not None:
            try:
                from PyQt6.QtCore import QPointF
                from PyQt6.QtGui import QMouseEvent
                local_pos = QPointF(self._label.mapFromGlobal(event.globalPosition().toPoint()))
                new_event = QMouseEvent(
                    event.type(),
                    local_pos,
                    event.globalPosition(),
                    event.button(),
                    event.buttons(),
                    event.modifiers()
                )
                overlay.mouseReleaseEvent(new_event)
                event.accept()
                return
            except Exception:
                pass

        if event.button() == Qt.MouseButton.LeftButton:
            if bool(self._blur_mode) or is_toolbar_blur:
                self._painting_blur = False
                self._last_blur_pos = None

                self._rebuild_orig_pixmap_from_bgr()
                self._label.setPixmap(self._display_pixmap())
                if self._on_image_changed is not None:
                    try:
                        self._on_image_changed(self._image_bgr)
                    except Exception:
                        pass
                event.accept()
                return
            self._dragging = False
            event.accept()

    def wheelEvent(self, event) -> None:
        if self._crop_overlay is not None:
            event.accept()
            return
        try:
            d = int(event.angleDelta().y())
        except Exception:
            d = 0
        if d == 0:
            return
        step = 0.08 if abs(d) < 240 else 0.14
        self._scale = float(max(0.25, min(3.0, self._scale + (step if d > 0 else -step))))
        self._apply_scale()

    def contextMenuEvent(self, event) -> None:
        self._show_context_popup(event.globalPos())
        event.accept()

    def _show_context_popup(self, global_pos: QPoint) -> None:
        from deepcat.ui.post_capture_actions import OcrGenericMenuPopup

        items = [
            ("裁剪", self._toggle_crop_mode, True),
            ("最小化", self._do_minimize, True),
            ("-", lambda: None, False),
            ("关闭（保存）", self._do_close_save, True),
            ("另存为...", self._do_save_as, True),
            ("复制", self._do_copy, True),
            ("-", lambda: None, False),
            ("销毁（不保存）", self._do_destroy, True),
        ]
        popup = OcrGenericMenuPopup(items, parent=self, match_parent_width=False, active_indicator="background")
        self._context_popup = popup
        popup.show_at_pos(QPoint(global_pos))

    def mouseDoubleClickEvent(self, event) -> None:
        if self._crop_overlay is not None:
            event.accept()
            return
        overlay = self._get_active_annotation_overlay()
        if overlay is not None:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.close()
            event.accept()

    def keyPressEvent(self, event) -> None:
        key = int(event.key())
        if key not in {
            int(Qt.Key.Key_Left),
            int(Qt.Key.Key_Right),
            int(Qt.Key.Key_Up),
            int(Qt.Key.Key_Down),
        }:
            super().keyPressEvent(event)
            return
        step = 10
        mods = event.modifiers()
        if bool(mods & Qt.KeyboardModifier.ShiftModifier):
            step = 30
        x0, y0 = int(self.x()), int(self.y())
        if key == int(Qt.Key.Key_Left):
            x0 -= step
        elif key == int(Qt.Key.Key_Right):
            x0 += step
        elif key == int(Qt.Key.Key_Up):
            y0 -= step
        elif key == int(Qt.Key.Key_Down):
            y0 += step
        self.move(int(x0), int(y0))
        event.accept()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._update_edit_actions_pos()
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._crop_overlay is not None:
            try:
                self._crop_overlay.setGeometry(0, 0, self._label.width(), self._label.height())
            except Exception:
                pass
        self._update_edit_actions_pos()
        self._sync_scroll_hint_bubble_pos()
        self._update_scale_badge()
        event.accept()

    def closeEvent(self, event) -> None:
        self._commit_text_editor()
        try:
            if self._toolbar_tooltip is not None:
                self._toolbar_tooltip.close()
                self._toolbar_tooltip = None
        except Exception:
            pass
        try:
            if self._scroll_hint_bubble is not None:
                self._scroll_hint_bubble.hide()
        except Exception:
            pass
        if self._edit_actions is not None:
            try:
                self._edit_actions.close()
            except Exception:
                pass
        super().closeEvent(event)

    def hideEvent(self, event) -> None:
        self._commit_text_editor()
        self._hide_toolbar_tooltip()
        try:
            if self._scroll_hint_bubble is not None:
                self._scroll_hint_bubble.hide()
        except Exception:
            pass
        if self._edit_actions is not None:
            try:
                self._edit_actions.hide()
            except Exception:
                pass
        super().hideEvent(event)


class PinnedCropOverlay(QWidget):
    def pimg_win(self):
        p = self.parent()
        from deepcat.ui.pinned_image_window import PinnedImageWindow
        while p is not None and not isinstance(p, PinnedImageWindow):
            p = p.parent()
        return p

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        pimg = self.pimg_win()
        w, h = pimg._label.width(), pimg._label.height()
        self._crop_rect = QRect(12, 12, max(40, w - 24), max(40, h - 24))

        self._drag_handle = None
        self._drag_start_global = None
        self._drag_start_rect = None

        # 精细控制参数
        self._dot_r = 4       # 圆点半径
        self._dot_hit = 12    # 圆点热区大小
        self._edge_grip = 10  # 边缘热区大小
        self._min_w = 30      # 最小裁剪宽度
        self._min_h = 30      # 最小裁剪高度

        # 创建确认与取消按钮
        self._btn_confirm = QPushButton("确定", self)
        self._btn_cancel = QPushButton("取消", self)

        self._btn_confirm.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        # 设置软件主按钮风格的确认按钮和灰色风格取消按钮
        self._btn_confirm.setStyleSheet(
            "QPushButton { background-color: #1e293b; color: white; border: none; border-radius: 4px; padding: 4px 10px; font-size: 11px; font-weight: bold; }"
            "QPushButton:hover { background-color: #334155; }"
            "QPushButton:pressed { background-color: #0f172a; }"
        )
        self._btn_cancel.setStyleSheet(
            "QPushButton { background-color: #E0E0E0; color: #333333; border: none; border-radius: 4px; padding: 4px 10px; font-size: 11px; }"
            "QPushButton:hover { background-color: #D5D5D5; }"
            "QPushButton:pressed { background-color: #BDBDBD; }"
        )

        self._btn_confirm.clicked.connect(self.confirm_crop)
        self._btn_cancel.clicked.connect(self.cancel_crop)

        self.update_buttons_position()

    def update_buttons_position(self) -> None:
        pimg = self.pimg_win()
        if pimg is None or not hasattr(pimg, "_scroll_area") or pimg._scroll_area is None:
            r = self._crop_rect
            btn_w = 55
            btn_h = 24
            gap = 6
            x = r.right() - (btn_w * 2 + gap)
            y = r.bottom() + 6
            if y + btn_h > self.height():
                y = r.bottom() - btn_h - 6
                x = r.right() - (btn_w * 2 + gap) - 6
            x = max(6, min(self.width() - (btn_w * 2 + gap) - 6, x))
            y = max(6, min(self.height() - btn_h - 6, y))
            self._btn_confirm.setGeometry(x, y, btn_w, btn_h)
            self._btn_cancel.setGeometry(x + btn_w + gap, y, btn_w, btn_h)
            return

        r = self._crop_rect
        btn_w = 55
        btn_h = 24
        gap = 6

        # 获取当前 QScrollArea 的可视 Y/X 轴范围（在 self 的坐标系中）
        y_min = pimg._scroll_area.verticalScrollBar().value()
        y_max = y_min + pimg._scroll_area.viewport().height()
        x_min = pimg._scroll_area.horizontalScrollBar().value()
        x_max = x_min + pimg._scroll_area.viewport().width()

        # 计算裁剪框在当前可视区内的可见部分
        vis_left = max(r.left(), x_min)
        vis_right = min(r.right(), x_max)
        vis_top = max(r.top(), y_min)
        vis_bottom = min(r.bottom(), y_max)

        # 默认放置在可见裁剪框的右下角内侧
        x = vis_right - (btn_w * 2 + gap) - 6
        y = vis_bottom - btn_h - 6

        # 如果裁剪框完全在可视区域外侧上方（即用户滚上去了）
        if r.bottom() < y_min:
            y = y_min + 6
            x = r.right() - (btn_w * 2 + gap) - 6
        # 如果裁剪框完全在可视区域外侧下方（即用户滚下去了）
        elif r.top() > y_max:
            y = y_max - btn_h - 6
            x = r.right() - (btn_w * 2 + gap) - 6
        else:
            # 裁剪框部分可见或全部可见。如果底边缘可见，且下方放得下，优先放底边缘外侧
            if r.bottom() + btn_h + 6 <= y_max:
                y = r.bottom() + 6
                x = r.right() - (btn_w * 2 + gap)
            else:
                y = vis_bottom - btn_h - 6
                x = vis_right - (btn_w * 2 + gap) - 6

        # 保证 x, y 不越界
        x = max(6, min(self.width() - (btn_w * 2 + gap) - 6, x))
        y = max(6, min(self.height() - btn_h - 6, y))

        self._btn_confirm.setGeometry(x, y, btn_w, btn_h)
        self._btn_cancel.setGeometry(x + btn_w + gap, y, btn_w, btn_h)

    def _hit_handle(self, pos: QPoint) -> Optional[str]:
        r = self._crop_rect
        x = pos.x()
        y = pos.y()

        x_left = r.left()
        x_right = r.right()
        x_center = r.left() + r.width() // 2
        y_top = r.top()
        y_bottom = r.bottom()
        y_center = r.top() + r.height() // 2

        points = {
            "top_left": (x_left, y_top),
            "top": (x_center, y_top),
            "top_right": (x_right, y_top),
            "right": (x_right, y_center),
            "bottom_right": (x_right, y_bottom),
            "bottom": (x_center, y_bottom),
            "bottom_left": (x_left, y_bottom),
            "left": (x_left, y_center),
        }
        for name, (px, py) in points.items():
            if abs(x - px) <= self._dot_hit and abs(y - py) <= self._dot_hit:
                return name

        on_l = abs(x - x_left) <= self._edge_grip
        on_r = abs(x - x_right) <= self._edge_grip
        on_t = abs(y - y_top) <= self._edge_grip
        on_b = abs(y - y_bottom) <= self._edge_grip

        if on_l and on_t: return "top_left"
        if on_r and on_t: return "top_right"
        if on_l and on_b: return "bottom_left"
        if on_r and on_b: return "bottom_right"
        if on_t and x_left - self._edge_grip <= x <= x_right + self._edge_grip: return "top"
        if on_b and x_left - self._edge_grip <= x <= x_right + self._edge_grip: return "bottom"
        if on_l and y_top - self._edge_grip <= y <= y_bottom + self._edge_grip: return "left"
        if on_r and y_top - self._edge_grip <= y <= y_bottom + self._edge_grip: return "right"

        return None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

            # 1. 绘制外部半透明黑色遮罩
            path = QPainterPath()
            path.addRect(QRectF(self.rect()))
            path.addRect(QRectF(self._crop_rect))
            painter.setBrush(QColor(0, 0, 0, 120))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)

            # 2. 绘制裁剪边框 (红色线框)
            border_color = QColor("#E53935")
            pen = QPen(border_color)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._crop_rect)

            # 3. 尺寸大小文字常驻显示
            pimg = self.pimg_win()
            src_w = pimg._image_bgr.shape[1]
            src_h = pimg._image_bgr.shape[0]
            disp_w = max(1, self.width())
            disp_h = max(1, self.height())

            crop_w_px = int(round(self._crop_rect.width() * src_w / disp_w))
            crop_h_px = int(round(self._crop_rect.height() * src_h / disp_h))

            text = f" 裁剪：{crop_w_px} x {crop_h_px} "
            font = QFont()
            font.setPixelSize(11)
            painter.setFont(font)

            label_h = 20
            # 动态计算文字宽度，避免显示不全
            fm = painter.fontMetrics()
            label_w = fm.horizontalAdvance(text) + 12

            # 优先放在选框左上角的正上方，如果太靠顶，则放在选框内侧左上角
            label_y = self._crop_rect.top() - label_h - 2
            if label_y < 2:
                label_y = self._crop_rect.top() + 4
            label_x = max(2, self._crop_rect.left())

            label_rect = QRectF(label_x, label_y, label_w, label_h)
            painter.fillRect(label_rect, QColor(0, 0, 0, 160))
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, text)

            # 4. 绘制 8 个调整手柄圆点
            r = self._crop_rect
            x_left = r.left()
            x_right = r.right()
            x_center = r.left() + r.width() // 2
            y_top = r.top()
            y_bottom = r.bottom()
            y_center = r.top() + r.height() // 2

            pts = [
                (x_left, y_top),
                (x_center, y_top),
                (x_right, y_top),
                (x_right, y_center),
                (x_right, y_bottom),
                (x_center, y_bottom),
                (x_left, y_bottom),
                (x_left, y_center),
            ]

            d = self._dot_r * 2
            for px, py in pts:
                # 绘制白底外圈
                painter.setBrush(QColor(255, 255, 255, 230))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QRectF(px - self._dot_r - 1.0, py - self._dot_r - 1.0, d + 2.0, d + 2.0))
                # 绘制红心内圈
                painter.setBrush(border_color)
                painter.drawEllipse(QRectF(px - self._dot_r, py - self._dot_r, d, d))

        finally:
            if painter.isActive():
                painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        p = event.position().toPoint()
        h = self._hit_handle(p)
        if h is None and self._crop_rect.contains(p):
            h = "move"
        if h is None:
            return
        self._drag_handle = h
        self._drag_start_global = event.globalPosition().toPoint()
        self._drag_start_rect = QRect(self._crop_rect)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_handle is None:
            p = event.position().toPoint()
            h = self._hit_handle(p)
            if h is None and self._crop_rect.contains(p):
                h = "move"

            if h in {"top", "bottom"}:
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif h in {"left", "right"}:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif h in {"top_left", "bottom_right"}:
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif h in {"top_right", "bottom_left"}:
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            elif h == "move":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.unsetCursor()
            return

        delta = event.globalPosition().toPoint() - self._drag_start_global
        g0 = self._drag_start_rect
        x, y, w, h = g0.x(), g0.y(), g0.width(), g0.height()

        overlay_w = self.width()
        overlay_h = self.height()

        if self._drag_handle == "top":
            y2 = max(0, min(y + h - self._min_h, y + delta.y()))
            h = h + (y - y2)
            y = y2
        elif self._drag_handle == "bottom":
            h = max(self._min_h, min(overlay_h - y, h + delta.y()))
        elif self._drag_handle == "left":
            x2 = max(0, min(x + w - self._min_w, x + delta.x()))
            w = w + (x - x2)
            x = x2
        elif self._drag_handle == "right":
            w = max(self._min_w, min(overlay_w - x, w + delta.x()))
        elif self._drag_handle == "top_left":
            x2 = max(0, min(x + w - self._min_w, x + delta.x()))
            y2 = max(0, min(y + h - self._min_h, y + delta.y()))
            w = w + (x - x2)
            h = h + (y - y2)
            x = x2
            y = y2
        elif self._drag_handle == "top_right":
            y2 = max(0, min(y + h - self._min_h, y + delta.y()))
            h = h + (y - y2)
            y = y2
            w = max(self._min_w, min(overlay_w - x, w + delta.x()))
        elif self._drag_handle == "bottom_left":
            x2 = max(0, min(x + w - self._min_w, x + delta.x()))
            w = w + (x - x2)
            x = x2
            h = max(self._min_h, min(overlay_h - y, h + delta.y()))
        elif self._drag_handle == "bottom_right":
            w = max(self._min_w, min(overlay_w - x, w + delta.x()))
            h = max(self._min_h, min(overlay_h - y, h + delta.y()))
        elif self._drag_handle == "move":
            x = max(0, min(overlay_w - w, x + delta.x()))
            y = max(0, min(overlay_h - h, y + delta.y()))

        self._crop_rect = QRect(x, y, w, h)
        self.update_buttons_position()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_handle = None
            self._drag_start_global = None
            self._drag_start_rect = None
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            p = event.position().toPoint()
            if self._crop_rect.contains(p):
                self.confirm_crop()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            self.confirm_crop()
            event.accept()
        elif key == Qt.Key.Key_Escape:
            self.cancel_crop()
            event.accept()
        else:
            super().keyPressEvent(event)

    def confirm_crop(self) -> None:
        try:
            parent = self.pimg_win()
            if parent is None:
                return
            src_h, src_w = parent._image_bgr.shape[0], parent._image_bgr.shape[1]
            disp_w = max(1, self.width())
            disp_h = max(1, self.height())

            # 高精度映射原始像素坐标
            x0 = int(round(self._crop_rect.left() * src_w / disp_w))
            y0 = int(round(self._crop_rect.top() * src_h / disp_h))
            x1 = int(round(self._crop_rect.right() * src_w / disp_w))
            y1 = int(round(self._crop_rect.bottom() * src_h / disp_h))

            # 防越界越小容错
            x0 = max(0, min(src_w - 2, x0))
            y0 = max(0, min(src_h - 2, y0))
            x1 = max(x0 + 2, min(src_w, x1))
            y1 = max(y0 + 2, min(src_h, y1))

            # 切片裁剪 BGR 图像
            cropped_bgr = parent._image_bgr[y0:y1, x0:x1].copy()

            # 在 parent 中应用裁剪图像并安全更新图幅与窗口
            parent.update_image(cropped_bgr)
            if parent._edit_actions is not None:
                try:
                    parent._edit_actions._base_image_bgr = cropped_bgr.copy()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self.cancel_crop()

    def cancel_crop(self) -> None:
        try:
            parent = self.pimg_win()
            if parent is not None:
                parent._crop_overlay = None
        except Exception:
            pass
        self.close()
