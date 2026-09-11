import sys
import unittest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
import deepcat.clipboard_history.clipboard_history_page as clipboard_page
from deepcat.clipboard_history.clipboard_history_page import RecordListWidget, _PinnedFoldToggleWidget, _RecordItemWidget

class TestClipboardPinnedFold(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create a single QApplication instance for the class
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(sys.argv)

    def test_pinned_folding_logic(self):
        widget = RecordListWidget()

        # Scenario 1: <= 3 pinned items (e.g., 3 pinned items)
        pinned_3 = [
            (i, f"content_{i}", "text", "", f"file_{i}", "", "", "2026-06-06T12:00:00", 0, 0, "", 1)
            for i in range(1, 4)
        ]
        records = [
            (i, f"rec_{i}", "text", "", "", "", "", "2026-06-06T11:00:00", 0, 0, "", 0)
            for i in range(10, 15)
        ]

        widget.load_records(records, pinned_3)

        # Check total items in the list widget: 3 pinned + 5 normal = 8 items
        self.assertEqual(widget.count(), 8)

        # Verify no toggle widget is present
        for i in range(widget.count()):
            item_widget = widget.itemWidget(widget.item(i))
            self.assertNotIsInstance(item_widget, _PinnedFoldToggleWidget)

        # Scenario 2: > 3 pinned items (e.g., 6 pinned items)
        pinned_6 = [
            (i, f"content_{i}", "text", "", f"file_{i}", "", "", "2026-06-06T12:00:00", 0, 0, "", 1)
            for i in range(1, 7)
        ]

        widget.load_records(records, pinned_6)

        # All items are added to the list: 6 pinned + 1 toggle + 5 normal = 12 items
        self.assertEqual(widget.count(), 12)
        self.assertTrue(widget._pinned_folded)

        # Verify initial folding:
        # Row 0, 1, 2 are visible
        self.assertFalse(widget.isRowHidden(0))
        self.assertFalse(widget.isRowHidden(1))
        self.assertFalse(widget.isRowHidden(2))

        # Row 3, 4, 5 (excess pinned items) are hidden
        self.assertTrue(widget.isRowHidden(3))
        self.assertTrue(widget.isRowHidden(4))
        self.assertTrue(widget.isRowHidden(5))

        # Row 6 (toggle widget) is visible
        self.assertFalse(widget.isRowHidden(6))

        # Verify toggle widget content
        toggle_widget = widget.itemWidget(widget.item(6))
        self.assertIsInstance(toggle_widget, _PinnedFoldToggleWidget)
        self.assertEqual(toggle_widget._label.text(), "展开其余 3 项置顶记录... ∨")

        # Simulate click on the toggle widget to expand
        toggle_widget.clicked.emit()

        # Now it should be unfolded.
        self.assertFalse(widget._pinned_folded)

        # All rows should be visible
        for i in range(12):
            self.assertFalse(widget.isRowHidden(i))

        # Verify toggle widget text changed
        self.assertEqual(toggle_widget._label.text(), "折叠多余置顶记录... ∧")

        # Simulate click again to collapse
        toggle_widget.clicked.emit()

        # Should collapse back
        self.assertTrue(widget._pinned_folded)
        self.assertTrue(widget.isRowHidden(3))
        self.assertTrue(widget.isRowHidden(4))
        self.assertTrue(widget.isRowHidden(5))
        self.assertEqual(toggle_widget._label.text(), "展开其余 3 项置顶记录... ∨")

        widget.close()

    def test_record_context_menu_uses_modern_popup(self):
        widget = RecordListWidget()
        widget.resize(420, 240)
        widget.load_records([
            (1, "hello", "text", "", "", "", "", "2026-06-06T11:00:00", 0, 0, "", 0)
        ], [])
        widget.show()
        self.app.processEvents()

        captured = {}
        calls = []

        class FakePopup:
            def __init__(self, items, parent=None):
                captured["items"] = items
                captured["parent"] = parent

            def show_at_pos(self, pos):
                captured["pos"] = pos

        old_popup = clipboard_page.OcrGenericMenuPopup
        clipboard_page.OcrGenericMenuPopup = FakePopup
        try:
            widget.copy_requested.connect(lambda record_id: calls.append(("copy", record_id)))
            widget.edit_requested.connect(lambda record_id: calls.append(("edit", record_id)))
            widget.pin_requested.connect(lambda record_id, pinned: calls.append(("pin", record_id, pinned)))
            widget.delete_requested.connect(lambda record_id: calls.append(("delete", record_id)))

            row_pos = widget.visualItemRect(widget.item(0)).center()
            widget._show_context_menu(row_pos)
        finally:
            clipboard_page.OcrGenericMenuPopup = old_popup
            widget.close()

        menu_items = captured["items"]
        self.assertIs(captured["parent"], widget)
        self.assertEqual([item[0] for item in menu_items], ["复制", "编辑", "-", "置顶", "-", "删除"])

        menu_items[0][1]()
        menu_items[1][1]()
        menu_items[3][1]()
        menu_items[5][1]()
        self.assertEqual(calls, [("copy", 1), ("edit", 1), ("pin", 1, True), ("delete", 1)])

    def test_empty_state_is_not_counted_as_record(self):
        widget = RecordListWidget()
        widget.load_records([], [])

        self.assertEqual(widget.count(), 1)
        self.assertEqual(widget.record_count(), 0)

        widget.load_records([
            (1, "hello", "text", "", "", "", "", "2026-06-06T11:00:00", 0, 0, "", 0)
        ], [])
        self.assertEqual(widget.record_count(), 1)
        widget.close()

    def test_empty_state_action_hides_widget_before_refresh(self):
        calls = []
        widget = clipboard_page._ClipboardEmptyStateWidget(
            "空", "说明", "刷新列表", lambda: calls.append("refresh")
        )
        button = widget.findChild(clipboard_page.QPushButton, "ClipboardEmptyAction")
        self.assertIsNotNone(button)

        button.click()
        self.assertFalse(widget.isVisible())
        self.assertFalse(button.isEnabled())
        self.app.processEvents()
        self.assertEqual(calls, ["refresh"])
        widget.close()

    def test_empty_state_without_action_does_not_show_button(self):
        widget = clipboard_page._ClipboardEmptyStateWidget("空", "说明", "", None)
        button = widget.findChild(clipboard_page.QPushButton, "ClipboardEmptyAction")

        self.assertIsNotNone(button)
        self.assertFalse(button.isVisible())
        widget.close()

if __name__ == "__main__":
    unittest.main()
