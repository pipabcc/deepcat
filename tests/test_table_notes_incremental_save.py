from __future__ import annotations

from typing import Any

from deepcat.ui.main_window.notes import NotesMixin


class _IncrementalStore:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.failed_note_indices: set[int] = set()

    @staticmethod
    def has_table_tabs() -> bool:
        return True

    @staticmethod
    def has_note_tabs() -> bool:
        return True

    def save_table_tab(self, index: int, tab: dict[str, Any]) -> None:
        self.calls.append(("save_table", index, tab["name"]))

    def save_note_tab(self, index: int, tab: dict[str, Any]) -> None:
        self.calls.append(("save_note", index, tab["name"]))
        if index in self.failed_note_indices:
            raise OSError("磁盘写入失败")

    def replace_table_tabs(self, tabs: list[dict[str, Any]]) -> None:
        self.calls.append(("replace_tables", len(tabs)))

    def replace_note_tabs(self, tabs: list[dict[str, Any]]) -> None:
        self.calls.append(("replace_notes", len(tabs)))

    def set_active_table_tab(self, index: int) -> None:
        self.calls.append(("active_table", index))

    def set_active_note_tab(self, index: int) -> None:
        self.calls.append(("active_note", index))

    def save_all_table_tabs(self, *_args: Any) -> None:
        raise AssertionError("增量保存不应调用 save_all_table_tabs")

    def save_all_note_tabs(self, *_args: Any) -> None:
        raise AssertionError("增量保存不应调用 save_all_note_tabs")


class _IncrementalSaveDummy(NotesMixin):
    def __init__(self) -> None:
        self._table_notes_store = _IncrementalStore()
        self._table_tabs = [
            {"name": "表格 A", "data": [], "column_widths": []},
            {"name": "表格 B", "data": [], "column_widths": []},
        ]
        self._note_tabs = [
            {"name": "笔记 A", "html": "a"},
            {"name": "笔记 B", "html": "b"},
            {"name": "笔记 C", "html": "c"},
        ]
        self._active_table_tab = 0
        self._active_note_tab = 0
        self._loading_table_notes = False
        self.statuses: list[tuple[str, str]] = []
        self._reset_table_notes_save_state()

    def _save_current_table_tab_data(self) -> None:
        pass

    def _save_current_note_tab_data(self) -> None:
        pass

    def _show_table_notes_status(self, text: str, *, tone: str = "info", **_kwargs: Any) -> None:
        self.statuses.append((text, tone))


def test_only_dirty_tab_is_saved_incrementally() -> None:
    dummy = _IncrementalSaveDummy()
    dummy._note_tabs[1]["html"] = "changed"
    dummy._mark_table_notes_tab_dirty("note", 1)

    dummy._save_table_notes_settings()

    assert dummy._table_notes_store.calls == [("save_note", 1, "笔记 B")]
    assert dummy._dirty_note_tab_indices == set()


def test_active_index_is_saved_without_rewriting_tabs() -> None:
    dummy = _IncrementalSaveDummy()
    dummy._active_note_tab = 2

    dummy._save_table_notes_settings()

    assert dummy._table_notes_store.calls == [("active_note", 2)]
    assert dummy._active_note_index_dirty is False


def test_failed_tab_save_keeps_dirty_state_and_reports_error() -> None:
    dummy = _IncrementalSaveDummy()
    dummy._note_tabs[1]["html"] = "changed"
    dummy._mark_table_notes_tab_dirty("note", 1)
    dummy._table_notes_store.failed_note_indices.add(1)

    dummy._save_table_notes_settings()

    assert dummy._dirty_note_tab_indices == {1}
    assert dummy.statuses and dummy.statuses[-1][1] == "error"
    assert "仍会保留" in dummy.statuses[-1][0]

    dummy._table_notes_store.failed_note_indices.clear()
    dummy._save_table_notes_settings()
    assert dummy._dirty_note_tab_indices == set()


def test_deleted_tab_uses_structure_transaction_and_resets_dirty_indices() -> None:
    dummy = _IncrementalSaveDummy()
    dummy._mark_table_notes_tab_dirty("note", 2)
    del dummy._note_tabs[0]

    dummy._save_table_notes_settings()

    assert dummy._table_notes_store.calls == [("replace_notes", 2)]
    assert dummy._dirty_note_tab_indices == set()
    assert dummy._persisted_note_tab_ids == [id(tab) for tab in dummy._note_tabs]


def test_removing_unsaved_appended_tab_discards_stale_dirty_index() -> None:
    dummy = _IncrementalSaveDummy()
    dummy._note_tabs.append({"name": "临时笔记", "html": "temporary"})
    dummy._mark_table_notes_tab_dirty("note", 3)
    dummy._note_tabs.pop()

    dummy._save_table_notes_settings()

    assert dummy._table_notes_store.calls == []
    assert dummy._dirty_note_tab_indices == set()
