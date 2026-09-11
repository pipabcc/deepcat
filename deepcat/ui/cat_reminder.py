from __future__ import annotations

import sys
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from deepcat.utils.logger import get_logger


def _todo_combo_popup_view_style() -> str:
    return """
        QAbstractItemView#TodoFilterPopup {
            background: #ffffff;
            color: #111827;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            outline: none;
            padding: 4px;
            selection-background-color: #f1f5f9;
            selection-color: #0f172a;
            font-size: 13px;
            font-weight: 700;
        }
        QAbstractItemView#TodoFilterPopup::item {
            min-height: 26px;
            padding: 4px 8px;
            border-radius: 6px;
        }
        QAbstractItemView#TodoFilterPopup::item:hover,
        QAbstractItemView#TodoFilterPopup::item:selected {
            background: #f1f5f9;
            color: #0f172a;
        }
    """


def _style_todo_combo_popup_view(combo: QComboBox) -> None:
    try:
        view = combo.view()
        view.setObjectName("TodoFilterPopup")
        view.setStyleSheet(_todo_combo_popup_view_style())
    except Exception:
        pass


class _TodoComboPopup(QWidget):
    optionSelected = pyqtSignal(int)

    def __init__(self, options: list[tuple[str, object]], current_index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("TodoComboPopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        card = QFrame(self)
        card.setObjectName("TodoComboPopupCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(4, 4, 4, 4)
        card_layout.setSpacing(0)

        self._list = QListWidget(card)
        self._list.setObjectName("TodoFilterPopup")
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._list.viewport().setAutoFillBackground(False)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for index, (label, value) in enumerate(options):
            item = QListWidgetItem(str(label))
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setData(Qt.ItemDataRole.UserRole + 1, value)
            item.setSizeHint(QSize(90, 34))
            self._list.addItem(item)
            if index == int(current_index):
                self._list.setCurrentItem(item)
        self._list.itemClicked.connect(self._on_item_clicked)
        card_layout.addWidget(self._list)
        layout.addWidget(card)
        self.setStyleSheet(
            f"""
            QWidget#TodoComboPopup {{
                background: transparent;
                border: none;
            }}
            QFrame#TodoComboPopupCard {{
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
            }}
            {_todo_combo_popup_view_style()}
            QAbstractItemView#TodoFilterPopup {{
                background: transparent;
                border: none;
                padding: 0;
            }}
            """
        )

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self.optionSelected.emit(int(item.data(Qt.ItemDataRole.UserRole) or 0))
        self.close()

    def show_below(self, anchor: QWidget, *, width: int) -> None:
        row_height = 34
        visible_rows = max(1, min(self._list.count(), 8))
        self.setFixedSize(max(72, int(width)), visible_rows * row_height + 8)
        pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        try:
            screen = QGuiApplication.screenAt(pos)
            geo = screen.availableGeometry() if screen is not None else QGuiApplication.primaryScreen().availableGeometry()
            x = min(max(pos.x(), geo.left() + 4), geo.right() - self.width() - 4)
            y = pos.y()
            if y + self.height() > geo.bottom() - 4:
                y = anchor.mapToGlobal(QPoint(0, -self.height() - 4)).y()
            self.move(int(x), int(max(geo.top() + 4, y)))
        except Exception:
            self.move(pos)
        self.show()
        self.raise_()


class _TodoPopupComboBox(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_popup: _TodoComboPopup | None = None

    def showPopup(self) -> None:
        options = [(self.itemText(i), self.itemData(i)) for i in range(self.count())]
        if not options:
            return
        try:
            if self._todo_popup is not None:
                self._todo_popup.close()
        except Exception:
            pass
        popup = _TodoComboPopup(options, self.currentIndex(), self)
        self._todo_popup = popup

        def select_index(index: int) -> None:
            if 0 <= int(index) < self.count():
                self.setCurrentIndex(int(index))

        popup.optionSelected.connect(select_index)
        popup.destroyed.connect(lambda *_: setattr(self, "_todo_popup", None))
        popup.show_below(self, width=max(self.width(), self.minimumSizeHint().width()))

    def hidePopup(self) -> None:
        if self._todo_popup is not None:
            self._todo_popup.close()


# Interaction inspired by Cat Gatekeeper / Panda Gatekeeper. Their assets are
# reserved, so DeepCat uses its own icon and local system speech instead.
def play_cat_voice_reminder(message: str) -> None:
    """Use local Windows speech when available; no third-party audio assets are bundled."""

    def run() -> None:
        if sys.platform.startswith("win"):
            pythoncom = None
            try:
                import pythoncom as _pythoncom  # type: ignore
                import win32com.client  # type: ignore

                pythoncom = _pythoncom
                pythoncom.CoInitialize()
                speaker = win32com.client.Dispatch("SAPI.SpVoice")
                speaker.Speak(str(message or "休息时间到了。"), 0)
                return
            except Exception as exc:
                get_logger().debug("系统语音提醒不可用，改用提示音: %s", exc)
            finally:
                try:
                    if pythoncom is not None:
                        pythoncom.CoUninitialize()
                except Exception:
                    pass
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                return
            except Exception:
                pass

    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        pass


class _CatReminderWindow(QWidget):
    exitRequested = pyqtSignal()
    countdownFinished = pyqtSignal()
    snoozeRequested = pyqtSignal(int)
    dismissRequested = pyqtSignal()

    def __init__(
        self,
        screen,
        *,
        duration_seconds: int,
        title: str = "休息提醒",
        message: str,
        icon_path: Path,
        exit_enabled: bool = True,
        countdown_enabled: bool = True,
        snooze_enabled: bool = False,
        snooze_minutes: int = 10,
    ) -> None:
        super().__init__(None)
        self._countdown_enabled = bool(countdown_enabled)
        self._snooze_enabled = bool(snooze_enabled)
        self._duration = max(5, int(duration_seconds))
        self._remaining = int(self._duration)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setObjectName("CatReminderOverlay")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(screen.geometry())

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 24)
        root.setSpacing(16)
        root.addStretch(1)

        card = QFrame()
        card.setObjectName("CatReminderCard")
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        card.setMinimumWidth(420)
        card.setMaximumWidth(520)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(30, 26, 30, 26)
        card_layout.setSpacing(12)

        cat = QLabel()
        cat.setObjectName("CatReminderCat")
        cat.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if icon_path.exists():
            cat.setPixmap(QPixmap(str(icon_path)).scaled(76, 76, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        card_layout.addWidget(cat)

        title_label = QLabel(str(title or "休息提醒"))
        title_label.setObjectName("CatReminderTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(title_label)

        body = QLabel(str(message or "休息一下吧。"))
        body.setObjectName("CatReminderBody")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(body)

        if self._countdown_enabled:
            self._countdown = QLabel()
            self._countdown.setObjectName("CatReminderCountdown")
            self._countdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_layout.addWidget(self._countdown)

            self._progress = QProgressBar()
            self._progress.setObjectName("CatReminderProgress")
            self._progress.setRange(0, self._duration)
            self._progress.setTextVisible(False)
            card_layout.addWidget(self._progress)
        else:
            self._countdown = None
            self._progress = None

        if self._snooze_enabled:
            actions = QHBoxLayout()
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(8)
            self._snooze = _TodoPopupComboBox()
            self._snooze.setObjectName("TodoFilterCombo")
            for text, minutes in [("5 分钟后", 5), ("10 分钟后", 10), ("15 分钟后", 15), ("30 分钟后", 30), ("1 小时后", 60)]:
                self._snooze.addItem(text, minutes)
            for i in range(self._snooze.count()):
                if int(self._snooze.itemData(i) or 0) == int(snooze_minutes):
                    self._snooze.setCurrentIndex(i)
                    break
            later = QPushButton("再次提醒")
            later.setObjectName("CatReminderSnooze")
            later.setCursor(Qt.CursorShape.PointingHandCursor)
            done = QPushButton("知道了")
            done.setObjectName("CatReminderDismiss")
            done.setCursor(Qt.CursorShape.PointingHandCursor)
            later.clicked.connect(self._on_snooze)
            done.clicked.connect(self._on_dismiss)
            actions.addWidget(self._snooze, 1)
            actions.addWidget(later)
            actions.addWidget(done)
            card_layout.addLayout(actions)

        root.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)
        root.addStretch(1)

        if bool(exit_enabled) and not self._snooze_enabled:
            bottom = QHBoxLayout()
            bottom.setContentsMargins(0, 0, 0, 0)
            bottom.addStretch(1)
            exit_btn = QPushButton("退出提醒")
            exit_btn.setObjectName("CatReminderExit")
            exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            exit_btn.clicked.connect(self.exitRequested.emit)
            bottom.addWidget(exit_btn)
            root.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._raise_timer = QTimer(self)
        self._raise_timer.setInterval(700)
        self._raise_timer.timeout.connect(self._keep_on_top)
        self._refresh()

        arrow_url = str((Path(__file__).resolve().parent / "assets" / "icon_combo_arrow.svg").as_posix())
        self.setStyleSheet(
            f"""
            QWidget#CatReminderOverlay {{
                background: rgba(15, 23, 42, 210);
            }}
            QFrame#CatReminderCard {{
                background: rgba(255, 255, 255, 0.97);
                border: 1px solid rgba(191, 219, 254, 0.96);
                border-radius: 18px;
            }}
            QLabel#CatReminderTitle {{
                color: #1f2937;
                font-size: 24px;
                font-weight: 900;
            }}
            QLabel#CatReminderBody {{
                color: #475569;
                font-size: 14px;
                line-height: 1.45;
            }}
            QLabel#CatReminderCountdown {{
                color: #2f3d56;
                font-size: 46px;
                font-weight: 900;
            }}
            QProgressBar#CatReminderProgress {{
                height: 8px;
                border: none;
                border-radius: 4px;
                background: #e5eaf2;
            }}
            QProgressBar#CatReminderProgress::chunk {{
                border-radius: 4px;
                background: #2f3d56;
            }}
            QPushButton#CatReminderExit {{
                background: rgba(255, 255, 255, 0.96);
                color: #2f3d56;
                border: 1px solid rgba(203, 213, 225, 0.9);
                border-radius: 10px;
                padding: 9px 16px;
                font-weight: 800;
            }}
            QPushButton#CatReminderExit:hover {{
                background: #f8fafc;
                border-color: rgba(148, 163, 184, 0.96);
            }}
            QComboBox {{
                min-height: 28px;
                border: 1px solid #d8e0ec;
                border-radius: 7px;
                padding: 3px 28px 3px 8px;
                color: #1f2937;
                background: white;
            }}
            QComboBox::drop-down {{
                width: 24px;
                border: none;
                background: transparent;
            }}
            QComboBox::down-arrow {{
                image: url({arrow_url});
                width: 12px;
                height: 12px;
            }}
            QComboBox QAbstractItemView {{
                background: #ffffff;
                color: #111827;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                outline: none;
                padding: 4px;
                selection-background-color: #f1f5f9;
                selection-color: #0f172a;
                font-size: 13px;
                font-weight: 700;
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 26px;
                padding: 4px 8px;
                border-radius: 6px;
            }}
            QComboBox QAbstractItemView::item:hover,
            QComboBox QAbstractItemView::item:selected {{
                background: #f1f5f9;
                color: #0f172a;
            }}
            QPushButton#CatReminderSnooze {{
                color: #22324c;
                background: #f2f5f9;
                border: 1px solid #d8e0ec;
                border-radius: 7px;
                padding: 9px 16px;
                font-weight: 800;
            }}
            QPushButton#CatReminderDismiss {{
                color: white;
                background: #2f3d56;
                border: none;
                border-radius: 7px;
                padding: 9px 16px;
                font-weight: 800;
            }}
            """
        )
        if self._snooze_enabled:
            _style_todo_combo_popup_view(self._snooze)

    def start(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        if self._countdown_enabled:
            self._timer.start()
        self._raise_timer.start()

    def _keep_on_top(self) -> None:
        try:
            popup = QApplication.activePopupWidget()
            if popup is not None:
                return
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def _refresh(self) -> None:
        if self._countdown is not None:
            self._countdown.setText(f"{max(0, int(self._remaining))}s")
        if self._progress is not None:
            elapsed = max(0, int(self._duration) - int(self._remaining))
            self._progress.setValue(elapsed)

    def _tick(self) -> None:
        if not self._countdown_enabled:
            return
        self._remaining -= 1
        self._refresh()
        if self._remaining <= 0:
            self.countdownFinished.emit()

    def _on_snooze(self) -> None:
        minutes = int(self._snooze.currentData() or 10)
        self.snoozeRequested.emit(minutes)

    def _on_dismiss(self) -> None:
        self.dismissRequested.emit()

    def keyPressEvent(self, event) -> None:
        event.accept()

    def mousePressEvent(self, event) -> None:
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        event.accept()


class CatReminderSession(QObject):
    finished = pyqtSignal()
    snoozeRequested = pyqtSignal(int)
    dismissRequested = pyqtSignal()

    def __init__(
        self,
        *,
        duration_seconds: int,
        title: str = "休息提醒",
        message: str,
        voice_enabled: bool,
        icon_path: Path,
        exit_enabled: bool = True,
        countdown_enabled: bool = True,
        snooze_enabled: bool = False,
        snooze_minutes: int = 10,
    ) -> None:
        super().__init__(None)
        self._duration_seconds = max(5, int(duration_seconds))
        self._title = str(title or "休息提醒")
        self._message = str(message or "休息一下吧。")
        self._voice_enabled = bool(voice_enabled)
        self._exit_enabled = bool(exit_enabled)
        self._countdown_enabled = bool(countdown_enabled)
        self._snooze_enabled = bool(snooze_enabled)
        self._snooze_minutes = int(snooze_minutes)
        self._icon_path = Path(icon_path)
        self._windows: list[_CatReminderWindow] = []
        self._closed = False

    def start(self) -> None:
        screens = list(QGuiApplication.screens() or [])
        if not screens and QGuiApplication.primaryScreen() is not None:
            screens = [QGuiApplication.primaryScreen()]
        for screen in screens:
            w = _CatReminderWindow(
                screen,
                duration_seconds=self._duration_seconds,
                title=self._title,
                message=self._message,
                icon_path=self._icon_path,
                exit_enabled=self._exit_enabled,
                countdown_enabled=self._countdown_enabled,
                snooze_enabled=self._snooze_enabled,
                snooze_minutes=self._snooze_minutes,
            )
            w.exitRequested.connect(self.close)
            w.countdownFinished.connect(self.close)
            w.snoozeRequested.connect(self._on_snooze)
            w.dismissRequested.connect(self._on_dismiss)
            self._windows.append(w)
            w.start()
        if self._voice_enabled:
            play_cat_voice_reminder(self._message)

    def _on_snooze(self, minutes: int) -> None:
        self.close()
        self.snoozeRequested.emit(minutes)

    def _on_dismiss(self) -> None:
        self.close()
        self.dismissRequested.emit()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for w in list(self._windows):
            try:
                w.close()
            except Exception:
                pass
        self._windows.clear()
        self.finished.emit()
