import sys

from PyQt6.QtWidgets import QApplication

from deepcat.ui.post_capture_actions.text_widgets import PremiumTextEdit, RoundedTextEditContainer


def test_ai_input_does_not_create_expand_button():
    app = QApplication.instance() or QApplication(sys.argv)
    editor = PremiumTextEdit()
    container = RoundedTextEditContainer(editor)
    container.resize(320, 80)
    container.show()
    editor.setPlainText("很多内容\n" * 100)
    app.processEvents()

    assert container._expand_btn is None
    assert editor.verticalScrollBar().maximum() > 0
    container.close()
