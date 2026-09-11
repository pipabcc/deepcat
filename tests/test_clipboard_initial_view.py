"""冷启动首屏、后台预取和标签页切换的回归验证。"""

from __future__ import annotations

import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QEventLoop
from PyQt6.QtWidgets import QApplication, QLabel

from deepcat.clipboard_history.clipboard_database import ClipboardDatabase, SCHEMA_SQL
from deepcat.clipboard_history.clipboard_history_page import ClipboardHistoryPage
from deepcat.clipboard_history.query_worker import ClipboardQuery
from deepcat.ui.main_window.window_content_pages import WindowContentPagesMixin


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "记录 # 首屏.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executemany(
            "INSERT INTO clipboard_records(content, timestamp, is_pinned) VALUES (?, ?, ?)",
            [(f"内容 {index}", f"2026-09-10 01:00:{index:02d}", int(index < 3)) for index in range(20)],
        )
        conn.executemany(
            "INSERT INTO clipboard_stats VALUES (?, ?)",
            [
                ("records_total_count", "20"),
                ("records_data_size_all_bytes", "2048"),
            ],
        )
    return path


def test_snapshot_reads_real_first_page_without_migrating_database(database_path):
    before = database_path.read_bytes()
    with patch.object(ClipboardDatabase, "init_database", side_effect=AssertionError("首屏不应初始化数据库")):
        snapshot = ClipboardDatabase.read_initial_snapshot(str(database_path), limit=5)
    assert snapshot is not None
    assert [record[1] for record in snapshot["records"]] == [f"内容 {index}" for index in range(19, 14, -1)]
    assert len(snapshot["pinned"]) == 3
    assert snapshot["has_more"]
    assert snapshot["statistics"] == (20, "2.0 KB")
    assert database_path.read_bytes() == before


def test_missing_database_has_a_real_empty_state_without_creating_files(tmp_path):
    path = tmp_path / "不存在" / "records.db"
    snapshot = ClipboardDatabase.read_initial_snapshot(str(path))
    assert snapshot["records"] == [] and snapshot["pinned"] == []
    assert snapshot["statistics"][0] == 0
    assert not path.parent.exists()


def test_missing_statistics_does_not_hide_existing_records(database_path):
    with sqlite3.connect(database_path) as conn:
        conn.execute("DROP TABLE clipboard_stats")
    snapshot = ClipboardDatabase.read_initial_snapshot(str(database_path))
    assert len(snapshot["records"]) == 17
    assert snapshot["statistics"][0] is None
    assert snapshot["statistics"][1] == "统计中…"


def test_locked_database_does_not_block_first_paint(database_path):
    with sqlite3.connect(database_path) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        assert ClipboardDatabase.read_initial_snapshot(str(database_path)) is None
        assert time.monotonic() - started < 1.0


def test_page_contains_records_before_first_background_query(app, database_path):
    snapshot = ClipboardDatabase.read_initial_snapshot(str(database_path), limit=5)
    with patch.object(ClipboardDatabase, "read_initial_snapshot", return_value=snapshot):
        page = ClipboardHistoryPage()
    try:
        assert page._database is None
        assert page._record_list.record_count() == 8
        assert "20" in page._statistics_bar._label.text()
        first_widget = page._record_list.itemWidget(page._record_list.item(0))
        page._on_query_completed(ClipboardQuery(1), snapshot)
        assert page._record_list.itemWidget(page._record_list.item(0)) is first_widget
    finally:
        page.cleanup()
        page.deleteLater()


@pytest.mark.parametrize(
    "snapshot,expected_text",
    [
        (None, "正在读取复制记录"),
        ({"records": [], "pinned": [], "statistics": (0, "0 B"), "has_more": False}, "还没有复制记录"),
    ],
)
def test_pending_or_empty_database_does_not_paint_a_blank_list(app, snapshot, expected_text):
    with patch.object(ClipboardDatabase, "read_initial_snapshot", return_value=snapshot):
        page = ClipboardHistoryPage()
    try:
        assert page._record_list.count() == 1
        labels = page._record_list.findChildren(QLabel)
        assert any(label.text() == expected_text for label in labels)
    finally:
        page.cleanup()
        page.deleteLater()


def test_hidden_page_finishes_query_and_keeps_records_while_waiting(app, database_path):
    snapshot = ClipboardDatabase.read_initial_snapshot(str(database_path), limit=5)
    with patch.object(ClipboardDatabase, "read_initial_snapshot", return_value=snapshot):
        page = ClipboardHistoryPage()
    entered, release = threading.Event(), threading.Event()
    completed = []

    class SlowDatabase:
        def get_records(self, *args, **kwargs):
            entered.set()
            release.wait(2)
            return snapshot["records"]

        def get_pinned_records(self):
            return snapshot["pinned"]

        def get_statistics(self, content_type):
            return snapshot["statistics"]

    page._database = SlowDatabase()
    page._initialized = True
    page._query_controller.completed.connect(lambda *_: completed.append(True))
    try:
        page.on_page_shown()
        assert entered.wait(1)
        assert page._record_list.record_count() == 8
        page.on_page_hidden()
        release.set()
        deadline = time.monotonic() + 2
        while not completed and time.monotonic() < deadline:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents)
            time.sleep(0.005)
        assert completed
        assert page._record_list.record_count() == 8
    finally:
        release.set()
        page._query_controller.cancel(wait_ms=2000)
        page.cleanup()
        page.deleteLater()


def test_startup_prefetches_clipboard_even_when_another_tab_is_open():
    owner = SimpleNamespace(
        _feature_enabled=lambda name: True,
        _clipboard_history_page=MagicMock(),
        _stack=MagicMock(),
        _refresh_hover_cursor=MagicMock(),
    )
    owner._stack.currentIndex.return_value = 0
    WindowContentPagesMixin._initialize_clipboard_history_background(owner)
    owner._clipboard_history_page.initialize.assert_called_once_with(refresh=True)
