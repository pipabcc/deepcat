"""使用 Qt 输入法事件复现预编辑、上屏、快捷键和输入刷新合并。"""

from types import SimpleNamespace

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QTimer, Qt
from PyQt6.QtGui import QFocusEvent, QImage, QInputMethodEvent, QKeyEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from deepcat.settings_store import AppSettings
from deepcat.ui.post_capture_actions.text_panel import OcrTextPanel
from deepcat.ui.post_capture_actions.text_widgets import PremiumTextEdit


def _preedit(editor, text):
    QApplication.sendEvent(editor, QInputMethodEvent(text, []))


def _commit(editor, text):
    event = QInputMethodEvent()
    event.setCommitString(text)
    QApplication.sendEvent(editor, event)


@pytest.fixture
def panel(monkeypatch):
    from deepcat.ui.post_capture_actions import text_panel, text_panel_context, text_panel_history, text_panel_window

    settings = AppSettings(6, False, False, "", "", "", {})
    for module in (text_panel, text_panel_context, text_panel_history, text_panel_window):
        monkeypatch.setattr(module, "load_settings", lambda **_kwargs: settings)
        monkeypatch.setattr(module, "update_ui_settings", lambda **_kwargs: settings)
    monkeypatch.setattr(text_panel, "TranslationHistoryStore", lambda: None)
    monkeypatch.setattr(OcrTextPanel, "_restore_chat_draft", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(OcrTextPanel, "_save_current_chat_draft", lambda *_args, **_kwargs: None)
    widget = OcrTextPanel(restore_history_sidebar=False)
    QTest.qWait(120)
    widget._is_chatting = True
    yield widget
    widget.close()
    if not sip.isdeleted(widget):
        sip.delete(widget)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_preedit_is_protected_until_commit_finishes():
    editor = PremiumTextEdit()
    states, during_commit = [], []
    editor.composition_changed.connect(states.append)
    editor.textChanged.connect(lambda: during_commit.append(editor.is_composing()))
    _preedit(editor, "cai")
    _preedit(editor, "caishen")
    assert editor.is_composing()
    assert editor.toPlainText() == ""
    _commit(editor, "财神")
    assert editor.toPlainText() == "财神"
    assert not editor.is_composing()
    assert states == [True, False]
    assert during_commit and all(during_commit)
    sip.delete(editor)


def test_commit_without_preedit_also_defers_text_change_work():
    editor = PremiumTextEdit()
    states, during_commit = [], []
    editor.composition_changed.connect(states.append)
    editor.textChanged.connect(lambda: during_commit.append(editor.is_composing()))
    _commit(editor, "直接上屏")
    assert states == [True, False]
    assert during_commit == [True]
    assert not editor.is_composing()
    sip.delete(editor)


def test_focus_loss_clears_composition_state():
    editor = PremiumTextEdit()
    _preedit(editor, "nihao")
    QApplication.sendEvent(editor, QFocusEvent(QEvent.Type.FocusOut))
    assert not editor.is_composing()
    sip.delete(editor)


def test_preedit_does_not_trigger_panel_send_translate_or_escape(panel, monkeypatch):
    actions = []
    monkeypatch.setattr(panel, "_answer_question", lambda: actions.append("send"))
    monkeypatch.setattr(panel, "_translate_text", lambda: actions.append("translate"))
    monkeypatch.setattr(panel, "close", lambda: actions.append("close"))
    _preedit(panel._editor, "caishen")
    assert not panel._esc_shortcut.isEnabled()
    for key, modifiers in [
        (Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier),
        (Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier),
        (Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
    ]:
        override = QKeyEvent(QEvent.Type.ShortcutOverride, key, modifiers)
        assert panel.eventFilter(panel._editor, override)
        event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers)
        assert not panel.eventFilter(panel._editor, event)
    panel._close_from_escape()
    assert actions == []
    _commit(panel._editor, "财神输入法")
    assert panel._esc_shortcut.isEnabled()
    panel.eventFilter(panel._editor, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    panel.eventFilter(
        panel._editor, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    )
    panel._close_from_escape()
    assert actions == ["send", "translate", "close"]


def test_focus_reset_does_not_treat_preedit_as_empty_input():
    editor = PremiumTextEdit()
    _preedit(editor, "正在选词")
    fake = SimpleNamespace(
        _editor=editor,
        _bubble_view=SimpleNamespace(isVisible=lambda: True),
        isVisible=lambda: True,
    )
    assert editor.toPlainText() == ""
    assert not OcrTextPanel._can_start_question_editor_ime_focus_reset(fake)
    assert not OcrTextPanel._question_editor_ready_for_ime_refocus(fake)
    sip.delete(editor)


def test_continuous_input_coalesces_attachment_refresh_without_saving_draft(panel, monkeypatch):
    refreshes, drafts = [], []
    monkeypatch.setattr(
        panel, "_sync_pending_attachments_from_editor_text", lambda: refreshes.append(panel._editor.toPlainText())
    )
    monkeypatch.setattr(panel, "_save_current_chat_draft", lambda **_kwargs: drafts.append(True))
    for _ in range(40):
        panel._editor.insertPlainText("输入")
    assert refreshes == []
    assert drafts == []
    QTest.qWait(140)
    assert refreshes == ["输入" * 40]
    assert drafts == []


def test_refresh_waits_for_composition_to_end_and_keeps_latest_text(panel, monkeypatch):
    refreshes = []
    monkeypatch.setattr(
        panel, "_sync_pending_attachments_from_editor_text", lambda: refreshes.append(panel._editor.toPlainText())
    )
    panel._editor.insertPlainText("已上屏")
    _preedit(panel._editor, "houxu")
    QTest.qWait(140)
    assert refreshes == []
    _commit(panel._editor, "后续")
    assert refreshes == []
    QTest.qWait(140)
    assert refreshes == ["已上屏后续"]


def test_escape_in_image_preview_keeps_chat_window_open(panel, tmp_path):
    from deepcat.ui.chat_bubbles import ChatImageWidget

    path = tmp_path / "preview.png"
    image = QImage(320, 240, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    assert image.save(str(path))
    widget = ChatImageWidget(path.as_uri(), parent=panel)
    panel.show()
    closed_by_escape = []

    def dismiss_preview():
        dialog = QApplication.activeModalWidget()
        assert dialog is not None
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        closed_by_escape.append(sip.isdeleted(dialog) or not dialog.isVisible())
        if not sip.isdeleted(dialog) and dialog.isVisible():
            dialog.reject()

    QTimer.singleShot(40, dismiss_preview)
    widget._open_original_image()
    assert closed_by_escape == [True]
    assert not sip.isdeleted(panel)
    assert panel.isVisible()
    from deepcat.ui.main_window import ImagePreviewDialog

    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not panel.findChildren(ImagePreviewDialog)
