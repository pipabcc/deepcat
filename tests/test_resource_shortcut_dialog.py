import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog, QWidget

from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
from deepcat.ui.main_window.resource_shortcuts import _ResourceShortcutDialog


def test_resource_shortcut_dialog_is_prepared_before_show_and_keeps_caption_background():
    app = QApplication.instance() or QApplication(sys.argv)
    parent = QWidget()
    base_style = "QDialog { background: #f8fafc; } QPushButton { padding: 4px; }"

    dialog = _ResourceShortcutDialog(parent, base_style)

    assert dialog.objectName() == "ResourceShortcutDialog"
    assert dialog._layout_prepared is True
    assert dialog.layout().geometry().height() > 0
    assert f"background-color: {MAIN_WINDOW_BACKGROUND}" in dialog.styleSheet()
    assert dialog.styleSheet().rfind("QDialog#ResourceShortcutDialog") > dialog.styleSheet().find("QDialog {")
    assert dialog.height() == dialog.sizeHint().height()
    dialog.close()
    parent.close()


class _ParentWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.statuses: list[tuple[str, str]] = []

    def _show_resource_status(self, text: str, *, tone: str = "info", auto_hide_ms: int = 3000) -> None:
        self.statuses.append((text, tone))


def test_browse_app_target_uses_non_native_dialog():
    """回归：logs/crash.log 2026-09-06 主线程在原生文件对话框内因 0x8001010e 崩溃。"""
    app = QApplication.instance() or QApplication(sys.argv)
    parent = _ParentWidget()
    dialog = _ResourceShortcutDialog(parent)
    seen: dict[str, object] = {}

    def fake_exec(self, *args, **kwargs):
        seen["non_native"] = bool(self.testOption(QFileDialog.Option.DontUseNativeDialog))
        seen["mode"] = self.fileMode()
        return QFileDialog.DialogCode.Accepted

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(QFileDialog, "exec", fake_exec)
        monkey.setattr(QFileDialog, "selectedFiles", lambda self: [r"C:\Tools\tool.lnk"])
        dialog._target.clear()
        dialog._title.clear()
        dialog._browse_app_target()
    finally:
        monkey.undo()

    assert seen["non_native"] is True
    assert seen["mode"] == QFileDialog.FileMode.ExistingFile
    assert dialog._target.text() == r"C:\Tools\tool.lnk"
    assert dialog._title.text() == "tool"
    dialog.close()
    parent.close()


def test_browse_app_target_cancel_keeps_fields():
    app = QApplication.instance() or QApplication(sys.argv)
    parent = _ParentWidget()
    dialog = _ResourceShortcutDialog(parent)

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(QFileDialog, "exec", lambda self, *a, **k: QFileDialog.DialogCode.Rejected)
        dialog._target.clear()
        dialog._browse_app_target()
    finally:
        monkey.undo()

    assert dialog._target.text() == ""
    dialog.close()
    parent.close()


def test_browse_app_target_reports_error_without_crash():
    app = QApplication.instance() or QApplication(sys.argv)
    parent = _ParentWidget()
    dialog = _ResourceShortcutDialog(parent)

    def broken_exec(self, *args, **kwargs):
        raise RuntimeError("dialog backend failed")

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(QFileDialog, "exec", broken_exec)
        dialog._target.clear()
        dialog._browse_app_target()
    finally:
        monkey.undo()

    assert dialog._target.text() == ""
    assert parent.statuses and "选择文件失败" in parent.statuses[0][0]
    assert parent.statuses[0][1] == "error"
    dialog.close()
    parent.close()
