"""复制记录标题省略渲染回归测试。"""

from __future__ import annotations

import sys
import unittest

from PyQt6.QtGui import QFontMetrics
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QStackedWidget, QWidget

from deepcat.clipboard_history.clipboard_history_page import RecordListWidget, _RecordItemWidget


class TestClipboardRecordTitleElide(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_long_path_is_elided_before_first_paint(self) -> None:
        """长路径标题必须在构建时就省略，避免切换标签页时先显示完整内容再变成省略号。"""
        long_path = (
            r"D:\Example Projects\sample-workspace\deepcat-demo"
            r"\generated-images\some-very-long-folder-name\capture_2026_09_09_204600.png"
        )
        records = [(1, long_path, "image", "", long_path, "", "", "2026-09-09T20:46:00", 0, 0, "", 0)]

        widget = RecordListWidget()
        self.addCleanup(widget.close)
        widget.resize(760, 500)
        widget.load_records(records, [])

        item_widget = widget.itemWidget(widget.item(0))
        self.assertIsInstance(item_widget, _RecordItemWidget)
        title_text = item_widget._title_btn.text()
        self.assertTrue(title_text)
        self.assertNotEqual(title_text, long_path)
        self.assertLess(len(title_text), len(long_path))

    def test_title_is_stable_from_first_paint_after_tab_switch(self) -> None:
        stack = QStackedWidget()
        self.addCleanup(stack.close)
        other_page = QWidget()
        records = RecordListWidget()
        stack.addWidget(other_page)
        stack.addWidget(records)
        stack.resize(760, 300)
        stack.show()
        self.app.processEvents()
        long_title = "这是一条用于检查首次绘制和标签页切换的长记录。" * 15

        for width in (760, 540, 900):
            with self.subTest(width=width):
                stack.setCurrentWidget(other_page)
                stack.resize(width, 300)
                records.load_records([(1, long_title, "text", "", "", "", "", "2026-09-09 12:46:00", 0, 0, "", 0)], [])
                stack.setCurrentWidget(records)
                title = records.itemWidget(records.item(0))._title_btn
                first_frame = title.grab().toImage()
                first_text = title.text()
                self.assertTrue(first_text)
                self.assertNotEqual(first_text, long_title)
                self.assertLessEqual(QFontMetrics(title.font()).horizontalAdvance(first_text), title.width())
                QTest.qWait(180)
                self.assertEqual(title.text(), first_text)
                self.assertEqual(title.grab().toImage(), first_frame)

    def test_narrow_title_never_keeps_wider_text(self) -> None:
        widget = RecordListWidget()
        self.addCleanup(widget.close)
        widget.load_records([(1, "一条需要省略的很长记录" * 10, "text", "", "", "", "", "", 0, 0, "", 0)], [])
        title = widget.itemWidget(widget.item(0))._title_btn
        title.setFixedWidth(24)
        title.grab()
        self.assertLessEqual(QFontMetrics(title.font()).horizontalAdvance(title.text()), 24)

    def test_font_change_updates_elision_before_paint(self) -> None:
        widget = RecordListWidget()
        self.addCleanup(widget.close)
        widget.load_records([(1, "字体变大后仍需正确省略的记录" * 15, "text", "", "", "", "", "", 0, 0, "", 0)], [])
        title = widget.itemWidget(widget.item(0))._title_btn
        title.setFixedWidth(260)
        title.grab()
        first_text = title.text()
        title.setStyleSheet("font-size: 24px;")
        title.grab()
        self.assertEqual(title.font().pixelSize(), 24)
        self.assertLess(len(title.text()), len(first_text))
        self.assertLessEqual(QFontMetrics(title.font()).horizontalAdvance(title.text()), title.width())


if __name__ == "__main__":
    unittest.main()
