import unittest

from PyQt6.QtCore import QMimeData, Qt
from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from deepcat.ui.main_window import _NotesEditor, _note_auto_link_parts
from deepcat.ui.settings_dialog import SettingsDialog


class NotesEditorLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_plain_text_without_link_returns_empty_parts(self):
        self.assertEqual(_note_auto_link_parts("普通笔记文本"), [])

    def test_https_link_strips_sentence_punctuation(self):
        self.assertEqual(
            _note_auto_link_parts("查看 https://example.com/path。"),
            [
                ("text", "查看 ", ""),
                ("link", "https://example.com/path", "https://example.com/path"),
                ("text", "。", ""),
            ],
        )

    def test_www_link_gets_https_href(self):
        self.assertEqual(
            _note_auto_link_parts("www.example.com"),
            [("link", "www.example.com", "https://www.example.com")],
        )

    def test_multiple_links_keep_plain_text_between_them(self):
        self.assertEqual(
            _note_auto_link_parts("a https://a.test\nb ftp://b.test/file"),
            [
                ("text", "a ", ""),
                ("link", "https://a.test", "https://a.test"),
                ("text", "\nb ", ""),
                ("link", "ftp://b.test/file", "ftp://b.test/file"),
            ],
        )

    def test_notes_editor_keeps_own_context_menu_after_global_menu_install(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        editor = _NotesEditor()
        layout.addWidget(editor)

        try:
            SettingsDialog._install_custom_text_context_menus(root, root)

            self.assertEqual(editor.contextMenuPolicy(), Qt.ContextMenuPolicy.DefaultContextMenu)
            self.assertFalse(bool(editor.property("deepcatCustomContextMenu")))
        finally:
            root.deleteLater()

    def test_notes_editor_renders_raw_markdown_on_set_html(self):
        editor = _NotesEditor()
        try:
            editor.setHtml("# 决策专家\n\n## 角色定义\n\n1. **先诊断**，后开方")

            plain_text = editor.toPlainText()
            self.assertIn("决策专家", plain_text)
            self.assertIn("角色定义", plain_text)
            self.assertIn("先诊断", plain_text)
            self.assertNotIn("# 决策专家", plain_text)
            self.assertNotIn("**先诊断**", plain_text)
            self.assertIn("<h1", editor.toHtml().lower())
        finally:
            editor.deleteLater()

    def test_notes_editor_renders_markdown_saved_as_plain_qt_html(self):
        raw_markdown = "# 营销策划专家\n\n## 核心工作原则\n\n---\n\n- **目标先于创意**"
        doc = QTextDocument()
        doc.setPlainText(raw_markdown)

        editor = _NotesEditor()
        try:
            editor.setHtml(doc.toHtml())

            plain_text = editor.toPlainText()
            self.assertIn("营销策划专家", plain_text)
            self.assertIn("核心工作原则", plain_text)
            self.assertIn("目标先于创意", plain_text)
            self.assertNotIn("# 营销策划专家", plain_text)
            self.assertNotIn("**目标先于创意**", plain_text)
            self.assertIn("<h1", editor.toHtml().lower())
        finally:
            editor.deleteLater()

    def test_notes_editor_pastes_plain_markdown_as_rich_text(self):
        editor = _NotesEditor()
        mime = QMimeData()
        mime.setText("# 标题\n\n正文 **重点**")
        try:
            editor.insertFromMimeData(mime)

            self.assertIn("标题", editor.toPlainText())
            self.assertNotIn("# 标题", editor.toPlainText())
            self.assertNotIn("**重点**", editor.toPlainText())
            self.assertIn("<h1", editor.toHtml().lower())
        finally:
            editor.deleteLater()

    def test_notes_editor_pastes_markdown_even_when_clipboard_has_html(self):
        editor = _NotesEditor()
        mime = QMimeData()
        mime.setText("# 标题\n\n正文 **重点**")
        mime.setHtml("<p># 标题</p><p>正文 **重点**</p>")
        try:
            editor.insertFromMimeData(mime)

            plain_text = editor.toPlainText()
            self.assertIn("标题", plain_text)
            self.assertIn("正文", plain_text)
            self.assertNotIn("# 标题", plain_text)
            self.assertNotIn("**重点**", plain_text)
            self.assertIn("<h1", editor.toHtml().lower())
        finally:
            editor.deleteLater()

    def test_notes_editor_keeps_rendered_html_with_markdown_like_text(self):
        editor = _NotesEditor()
        try:
            editor.setHtml(
                "<h1>已渲染标题</h1>"
                "<p># 这里是普通正文，不是待解析标题</p>"
                "<p>正文 <span style='font-weight:700;'>重点</span></p>"
            )

            plain_text = editor.toPlainText()
            self.assertIn("已渲染标题", plain_text)
            self.assertIn("# 这里是普通正文，不是待解析标题", plain_text)
            self.assertIn("重点", plain_text)
            self.assertIn("<h1", editor.toHtml().lower())
        finally:
            editor.deleteLater()


if __name__ == "__main__":
    unittest.main()
