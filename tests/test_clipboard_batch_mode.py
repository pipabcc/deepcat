"""复制记录在首屏缓存和异步刷新下的批量选择回归。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest

from deepcat.clipboard_history import clipboard_history_page as clipboard_page
from deepcat.clipboard_history.query_worker import ClipboardQuery


def _record(record_id: int, *, pinned: bool = False) -> tuple:
    return (record_id, f"测试记录 {record_id}", "text", "", "", 0, "", "2026-09-12 08:00:00", 0, 0, "", int(pinned))


def _snapshot(records, pinned=()) -> dict:
    return {
        "records": list(records),
        "pinned": list(pinned),
        "statistics": (len(records) + len(pinned), "1 KB"),
        "has_more": False,
    }


@pytest.fixture
def page(qt_application, monkeypatch):
    snapshot = _snapshot([_record(1), _record(2), _record(3)], [_record(99, pinned=True)])
    monkeypatch.setattr(clipboard_page.ClipboardDatabase, "read_initial_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        clipboard_page,
        "load_settings",
        lambda: SimpleNamespace(ui={"clipboard_history": {"filter_keywords": []}}),
    )
    widget = clipboard_page.ClipboardHistoryPage()
    widget._database = MagicMock()
    widget._database.get_record_by_id.return_value = None
    monkeypatch.setattr(widget._query_controller, "submit", MagicMock())
    widget.resize(820, 480)
    widget.show()
    qt_application.processEvents()
    yield widget
    widget.cleanup()
    widget.close()
    widget.deleteLater()
    qt_application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_application.processEvents()


def _enter_batch_mode(page) -> None:
    QTest.mouseDClick(page._statistics_bar, Qt.MouseButton.LeftButton)
    assert page._batch_mode


def test_double_click_shows_checkboxes_on_existing_cached_rows(page):
    original_rows = dict(page._record_list._record_widgets)
    _enter_batch_mode(page)

    assert page._record_list._record_widgets == original_rows
    for row in original_rows.values():
        assert row._checkbox is not None
        assert row._checkbox.isVisible()
        assert not row._action_box.isVisible()


def test_select_all_checks_unpinned_records_and_updates_count(page):
    _enter_batch_mode(page)
    page._batch_select_all.click()

    assert set(page._record_list.selected_record_ids()) == {1, 2, 3}
    assert not page._record_list._record_widgets[99].isChecked()
    assert page._batch_sel_count_label.text() == "3 条"
    assert page._batch_select_all.isChecked()
    assert page._batch_delete.isEnabled()

    page._batch_select_all.click()
    assert page._record_list.selected_record_ids() == []
    assert page._batch_sel_count_label.text() == "0 条"
    assert not page._batch_delete.isEnabled()


def test_individual_checkboxes_keep_select_all_in_sync(page):
    _enter_batch_mode(page)
    rows = page._record_list._record_widgets
    for record_id in (1, 2, 3):
        rows[record_id]._checkbox.click()
    assert page._batch_select_all.isChecked()

    rows[2]._checkbox.click()
    assert not page._batch_select_all.isChecked()
    assert page._batch_sel_count_label.text() == "2 条"

    page._batch_select_all.click()
    assert set(page._record_list.selected_record_ids()) == {1, 2, 3}


def test_done_hides_checkboxes_and_clears_selection_without_replacing_rows(page):
    original_rows = dict(page._record_list._record_widgets)
    _enter_batch_mode(page)
    page._batch_select_all.click()
    page._batch_done.click()

    assert not page._batch_mode
    assert page._record_list._record_widgets == original_rows
    assert page._record_list.selected_record_ids() == []
    for row in original_rows.values():
        assert not row._batch_mode
        assert not row._checkbox.isVisible()
        assert not row.isChecked()

    _enter_batch_mode(page)
    assert page._batch_sel_count_label.text() == "0 条"
    assert not page._batch_select_all.isChecked()


def test_unchanged_query_preserves_batch_selection_and_cached_rows(page):
    _enter_batch_mode(page)
    page._batch_select_all.click()
    original_rows = dict(page._record_list._record_widgets)
    payload = _snapshot(page._record_list._loaded_records, page._record_list._loaded_pinned)
    page._on_query_completed(ClipboardQuery(1), payload)

    assert page._record_list._record_widgets == original_rows
    assert set(page._record_list.selected_record_ids()) == {1, 2, 3}
    assert page._batch_select_all.isChecked()
    assert all(row._checkbox.isVisible() for row in original_rows.values())


def test_pagination_creates_checkboxes_and_reconciles_select_all(page):
    _enter_batch_mode(page)
    page._batch_select_all.click()
    page._on_query_completed(ClipboardQuery(2, offset=3), _snapshot([_record(4), _record(5)]))

    assert page._record_list._record_widgets[4]._checkbox.isVisible()
    assert set(page._record_list.selected_record_ids()) == {1, 2, 3}
    assert page._batch_sel_count_label.text() == "3 条"
    assert not page._batch_select_all.isChecked()

    page._batch_select_all.click()
    assert set(page._record_list.selected_record_ids()) == {1, 2, 3, 4, 5}


def test_changed_query_clears_stale_selection_controls(page):
    _enter_batch_mode(page)
    page._batch_select_all.click()
    page._on_query_completed(ClipboardQuery(3, keyword="新的筛选"), _snapshot([_record(4)]))

    assert page._record_list._record_widgets[4]._checkbox.isVisible()
    assert page._record_list.selected_record_ids() == []
    assert page._batch_sel_count_label.text() == "0 条"
    assert not page._batch_select_all.isChecked()
    assert not page._batch_delete.isEnabled()


def test_record_title_toggles_selection_in_batch_mode(page):
    _enter_batch_mode(page)
    page._record_list._record_widgets[2]._title_btn.click()

    assert page._record_list.selected_record_ids() == [2]
    assert page._batch_sel_count_label.text() == "1 条"
    page._database.get_record_by_id.assert_not_called()

    page._batch_done.click()
    page._record_list._record_widgets[2]._title_btn.click()
    page._database.get_record_by_id.assert_called_once_with(2)


def test_batch_delete_receives_the_checked_records(page, monkeypatch):
    from deepcat.ui.main_window import compact
    from PyQt6.QtWidgets import QMessageBox

    confirmation = MagicMock()
    confirmation.button.return_value = None
    confirmation.exec.return_value = QMessageBox.StandardButton.Yes
    monkeypatch.setattr(compact, "StyledMessageBox", lambda parent: confirmation)
    _enter_batch_mode(page)
    page._batch_select_all.click()
    page._batch_delete.click()

    page._database.delete_records.assert_called_once_with([1, 2, 3])
    assert not page._batch_mode
    assert page._record_list.selected_record_ids() == []


def test_empty_batch_mode_disables_selection_and_delete(page):
    page._on_query_completed(ClipboardQuery(4), _snapshot([]))
    _enter_batch_mode(page)

    assert page._batch_sel_count_label.text() == "0 条"
    assert not page._batch_select_all.isEnabled()
    assert not page._batch_delete.isEnabled()
    assert page._batch_done.isEnabled()


def test_status_auto_hide_is_cancelled_when_label_is_destroyed(page):
    from PyQt6 import sip

    page._show_inline_status("已删除测试记录", auto_hide_ms=10)
    label = page._inline_status_label
    assert label.isVisible()
    sip.delete(label)
    QTest.qWait(30)
