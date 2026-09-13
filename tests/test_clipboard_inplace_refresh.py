"""复制记录列表「原地复用行控件」回归测试。

搜索框逐字输入时，如果每次都重建 50 个记录控件，主线程会被阻塞数百毫秒。
这里锁定原地刷新路径的正确性，以及退回整表重建的边界条件。
"""

from __future__ import annotations

import sys
import unittest

from PyQt6.QtWidgets import QApplication

from deepcat.clipboard_history.clipboard_history_page import RecordListWidget, _RecordItemWidget


def _record(
    record_id: int,
    content: str,
    content_type: str = "text",
    file_path: str = "",
    is_pinned: int = 0,
) -> tuple:
    return (
        record_id, content, content_type, "", file_path, 0, "Code",
        "2026-09-12 08:24:00", 0, "", 0, is_pinned, "", "",
    )


_HTML = "<!DOCTYPE html><html><body><script>a=1</script></body></html>"
_TEXT = "一条普通文本记录"


class TestClipboardInPlaceRefresh(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _make_list(self):
        widget = RecordListWidget()
        self.addCleanup(widget.close)
        widget.resize(760, 560)
        return widget

    @staticmethod
    def _ids(widget: RecordListWidget) -> list[int]:
        ids = []
        for index in range(widget.count()):
            item_widget = widget.itemWidget(widget.item(index))
            if isinstance(item_widget, _RecordItemWidget):
                ids.append(item_widget.record_id())
        return ids

    def test_same_row_count_refreshes_in_place(self) -> None:
        """行数不变时应复用控件，并把每行的标题、类型、时间刷新为新记录。"""
        widget = self._make_list()
        first = [_record(i, f"第一组内容 {i}") for i in range(1, 6)]
        widget.load_records(first, [])
        widgets_before = [widget.itemWidget(widget.item(i)) for i in range(5)]

        second = [_record(i, f"第二组内容 {i}") for i in range(101, 106)]
        widget.load_records(second, [])

        self.assertEqual(self._ids(widget), [101, 102, 103, 104, 105])
        self.assertEqual(
            [widget.itemWidget(widget.item(i)) for i in range(5)],
            widgets_before,
            "行数不变时不应重建控件",
        )
        self.assertIn("第二组内容 101", widget.itemWidget(widget.item(0))._title_btn.text())

    def test_row_count_change_falls_back_to_rebuild(self) -> None:
        widget = self._make_list()
        widget.load_records([_record(i, f"内容 {i}") for i in range(1, 6)], [])

        smaller = [_record(i, f"内容 {i}") for i in range(201, 204)]
        widget.load_records(smaller, [])
        self.assertEqual(self._ids(widget), [201, 202, 203])
        self.assertEqual(widget.record_count(), 3)

    def test_pinned_fold_structure_change_falls_back(self) -> None:
        """置顶条数跨越 3 条阈值会增删折叠控件，必须退回整表重建。"""
        widget = self._make_list()
        pinned = [_record(i, f"置顶 {i}", is_pinned=1) for i in range(1, 6)]
        widget.load_records([_record(i, f"普通 {i}") for i in range(10, 15)], pinned)
        self.assertIsNotNone(widget._toggle_item)
        self.assertEqual(self._ids(widget), [1, 2, 3, 4, 5, 10, 11, 12, 13, 14])

        single_pin = [_record(9, "只剩一条置顶", is_pinned=1)]
        widget.load_records([_record(i, f"普通 {i}") for i in range(20, 25)], single_pin)
        self.assertIsNone(widget._toggle_item)
        self.assertEqual(self._ids(widget), [9, 20, 21, 22, 23, 24])

    def test_empty_result_shows_empty_state(self) -> None:
        widget = self._make_list()
        widget.load_records([_record(i, f"内容 {i}") for i in range(1, 6)], [])
        widget.load_records([], [])
        self.assertIsNotNone(widget._empty_item)
        self.assertEqual(widget.record_count(), 0)

        widget.load_records([_record(7, "恢复的记录")], [])
        self.assertIsNone(widget._empty_item)
        self.assertEqual(self._ids(widget), [7])

    def test_run_button_is_added_and_removed_in_place(self) -> None:
        """可运行预览按钮的有无会随搜索结果变化，必须原地增删而不是重建整行。"""
        widget = self._make_list()
        plain = _record(1, _TEXT)
        widget.load_records([plain], [])
        row = widget.itemWidget(widget.item(0))
        self.assertFalse(row._is_runnable)
        self.assertIsNone(row._run_btn)
        self.assertEqual(row._action_box.width(), 100)

        widget.load_records([_record(1, _HTML)], [])
        row = widget.itemWidget(widget.item(0))
        self.assertTrue(row._is_runnable)
        self.assertIsNotNone(row._run_btn)
        self.assertEqual(row._action_box.width(), 126)

        widget.load_records([plain], [])
        row = widget.itemWidget(widget.item(0))
        self.assertFalse(row._is_runnable)
        self.assertIsNone(row._run_btn)
        self.assertEqual(row._action_box.width(), 100)

    def test_batch_mode_selection_reset_on_in_place_refresh(self) -> None:
        widget = self._make_list()
        widget.load_records([_record(i, f"内容 {i}") for i in range(1, 4)], [])
        widget.set_batch_mode(True)
        for index in range(3):
            widget.itemWidget(widget.item(index)).setChecked(True)
        self.assertEqual(len(widget.selected_record_ids()), 3)

        widget.load_records([_record(i, f"新内容 {i}") for i in range(11, 14)], [])
        self.assertEqual(self._ids(widget), [11, 12, 13])
        self.assertEqual(widget.selected_record_ids(), [])

    def test_selection_signal_reports_current_record_after_reuse(self) -> None:
        """复用控件后，按钮回调必须指向新的记录 id。"""
        widget = self._make_list()
        widget.load_records([_record(1, "旧记录")], [])
        row = widget.itemWidget(widget.item(0))

        widget.load_records([_record(42, "新记录")], [])
        copied: list[int] = []
        widget.copy_requested.connect(copied.append)
        row._set_actions_visible(True)  # 模拟鼠标悬停后操作按钮可用
        row._copy_btn.click()
        self.assertEqual(copied, [42])


if __name__ == "__main__":
    unittest.main()
