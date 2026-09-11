"""验证置顶分页、中文子串和后台查询的取消与限流。"""

import threading
import time
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from deepcat.clipboard_history import clipboard_database as module
from deepcat.clipboard_history.query_worker import ClipboardQueryController


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "load_settings", lambda: SimpleNamespace(ui={}))
    return module.ClipboardDatabase(str(tmp_path / "clipboard.db"), auto_cleanup_enabled=False)


def test_pinned_match_does_not_truncate_pagination(database):
    pinned_id = database.add_record("测试命中 pinned", is_pinned=1)
    expected = {database.add_record(f"测试命中 {i}") for i in range(60)}
    first = database.search_records("测试命中", limit=50, include_pinned=False)
    second = database.search_records("测试命中", limit=50, offset=50, include_pinned=False)
    assert len(first) == 50 and len(second) == 10
    assert {row[0] for row in first + second} == expected
    assert pinned_id not in expected


@pytest.mark.parametrize("keyword", ["人工", "工", "智能工具", "inside", "中间", "%.py", "two words"])
def test_index_keeps_legacy_search_semantics(database, keyword):
    for text in [
        "人工智能工具",
        "prefixinsideword",
        "段落中间文本",
        "example.py",
        "two words together",
        "two distant words",
    ]:
        database.add_record(text)
    with_index = database.search_records(keyword)
    database._substring_index_available = False
    fallback = database.search_records(keyword)
    assert with_index == fallback


def test_substring_index_tracks_edits_and_deletes(database):
    record_id = database.add_record("before_unique_phrase")
    assert database.search_records("unique_phrase")
    assert database.update_record(record_id, "after_replaced_value")
    assert not database.search_records("unique_phrase")
    assert database.search_records("replaced_value")
    assert database.delete_record(record_id)
    assert not database.search_records("replaced_value")


def test_database_closes_its_connections(database, monkeypatch):
    original = database._connect
    connections = []

    def connect():
        connection = original()
        connections.append(connection)
        return connection

    monkeypatch.setattr(database, "_connect", connect)
    database.add_record("test")
    database.get_records()
    database.search_records("test")
    database.get_statistics()
    import sqlite3

    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_new_query_replaces_pending_query_without_blocking_qt():
    app = QApplication.instance() or QApplication([])
    entered = threading.Event()
    release = threading.Event()
    running = []
    calls = []
    maximum = 0

    class Database:
        def search_records(self, keyword, *args, cancelled=None, **kwargs):
            nonlocal maximum
            calls.append(keyword)
            running.append(keyword)
            maximum = max(maximum, len(running))
            if keyword == "old":
                entered.set()
                release.wait(2)
            running.remove(keyword)
            return [(1, keyword)]

        def get_pinned_records(self):
            return []

        def get_statistics(self, content_type):
            return 1, "10 B"

    controller = ClipboardQueryController()
    results = []
    controller.completed.connect(lambda query, payload: results.append(query.keyword))
    ticks = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    try:
        database = Database()
        controller.submit(database, keyword="old")
        assert entered.wait(1)
        controller.submit(database, keyword="intermediate")
        controller.submit(database, keyword="latest")
        for _ in range(10):
            app.processEvents()
            time.sleep(0.003)
        assert ticks and calls == ["old"]
        release.set()
        deadline = time.monotonic() + 2
        while (controller.busy or not results) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.003)
        assert results == ["latest"]
        assert calls == ["old", "latest"]
        assert maximum == 1
    finally:
        release.set()
        timer.stop()
        controller.cancel(wait_ms=2000)
        app.processEvents()
        controller.close()


def test_clipboard_history_page_reinitializes_after_cleanup():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    assert app is not None
    from deepcat.clipboard_history.clipboard_history_page import ClipboardHistoryPage

    page = ClipboardHistoryPage()
    try:
        # 1. 首次初始化
        page.initialize(refresh=False)
        assert page._initialized is True
        assert page._monitor is not None
        assert page._query_controller._closed is False

        # 2. 模拟关闭功能：调用 cleanup
        page.cleanup()
        assert page._initialized is False
        assert page._monitor is None
        assert page._query_controller._closed is True

        # 3. 模拟重新打开功能：调用 initialize
        page.initialize(refresh=False)
        assert page._initialized is True
        assert page._monitor is not None
        assert page._query_controller._closed is False

        # 4. 再次关闭并重新打开，确保多次反复开关稳定自愈
        page.cleanup()
        assert page._initialized is False
        page.initialize(refresh=False)
        assert page._initialized is True
        assert page._monitor is not None
        assert page._query_controller._closed is False
    finally:
        page.cleanup()
        page.deleteLater()
