import unittest
from types import SimpleNamespace


class TestLaterReadActions(unittest.TestCase):
    def test_later_read_sort_places_pinned_first_by_time(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过稍后阅读动作测试")

        dummy = SimpleNamespace(
            _later_read_item_is_pinned=mw.MainWindow._later_read_item_is_pinned,
            _later_read_time_value=lambda item: int(item.get("ts", 0)),
        )
        items = [
            {"id": "normal-old", "ts": 10},
            {"id": "pinned-old", "ts": 20, "is_pinned": True},
            {"id": "normal-new", "ts": 30},
            {"id": "pinned-new", "ts": 40, "is_pinned": True},
        ]

        sorted_items = mw.MainWindow._sort_later_read_items_for_display(dummy, items, "time")

        self.assertEqual(
            [item["id"] for item in sorted_items],
            ["pinned-new", "pinned-old", "normal-new", "normal-old"],
        )

    def test_later_read_pin_update_saves_is_pinned_field(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过稍后阅读动作测试")

        items = [
            {"id": "read-1", "title": "A", "url": "https://a.example"},
            {"id": "read-2", "title": "B", "url": "https://b.example", "pinned": True},
        ]
        captured: dict[str, object] = {}

        def save_items(next_items, *, refresh=True) -> None:
            captured["items"] = [dict(item) for item in next_items]

        dummy = SimpleNamespace(
            _current_later_read_items=lambda: items,
            _save_later_read_items=save_items,
            _show_later_read_status=lambda text, *, tone: captured.update(status=(text, tone)),
        )

        mw.MainWindow._set_later_read_item_pinned(dummy, "read-2", False)

        saved_items = captured["items"]
        self.assertFalse(saved_items[1]["is_pinned"])
        self.assertNotIn("pinned", saved_items[1])
        self.assertEqual(captured["status"], ("已取消置顶该稍后阅读。", "success"))


if __name__ == "__main__":
    unittest.main()
