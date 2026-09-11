import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

if sys.platform.startswith("win") and sys.version_info >= (3, 13):
    pytest.skip("Python 3.13 + PyQt6 on Windows may crash during QApplication teardown", allow_module_level=True)

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton
except Exception:
    pytest.skip("缺少 PyQt6，跳过 AI 历史侧栏测试", allow_module_level=True)

from deepcat.ui.post_capture_actions import (
    AIChatHistorySidebar,
    OcrGenericMenuPopup,
    _AI_CHAT_BATCH_SELECTED_ROLE,
    _AI_CHAT_RECORD_ROLE,
)


class _FakeHistoryStore:
    def __init__(self, total: int = 200) -> None:
        self.total = total
        self.calls: list[tuple[int, int]] = []
        self.query_kwargs: list[dict] = []
        self.deleted_record_ids: list[int] = []

    def get_records(self, limit=50, offset=0, **kwargs):
        self.query_kwargs.append(dict(kwargs))
        return self._records(limit, offset)

    def delete_records(self, record_ids):
        self.deleted_record_ids = [int(record_id) for record_id in record_ids]
        return len(set(self.deleted_record_ids))

    def _records(self, limit: int, offset: int):
        self.calls.append((limit, offset))
        if offset >= self.total:
            return []
        count = min(limit, self.total - offset)
        return [
            {
                "id": offset + index + 1,
                "source_text": f"会话 {offset + index + 1}",
                "result_text": "回答内容",
                "task_type": "qa",
                "created_at": "2026-06-17 10:00:00",
                "title": "",
                "is_pinned": 0,
                "prompt_text": "",
            }
            for index in range(count)
        ]


class _SummaryOnlyHistoryStore(_FakeHistoryStore):
    def __init__(self, total: int = 200) -> None:
        super().__init__(total)
        self.summary_calls: list[tuple[int, int]] = []
        self.summary_query_kwargs: list[dict] = []

    def get_records(self, *args, **kwargs):
        raise AssertionError("AI 历史侧栏不应加载完整历史记录")

    def get_record_summaries(self, limit=50, offset=0, **kwargs):
        self.summary_calls.append((limit, offset))
        self.summary_query_kwargs.append(dict(kwargs))
        return self._records(limit, offset)


def _app() -> QApplication:
    return QApplication.instance() or QApplication(sys.argv)


def test_ai_chat_history_title_collapses_multiline_source_to_single_line():
    title = AIChatHistorySidebar._display_title(
        {
            "source_text": "群谈\n📎 [附件: demo.pdf]\n这是第二行问题",
            "title": "",
        }
    )

    assert title == "群谈 📎 [附件: demo.pdf] 这是第二行问题"
    assert "\n" not in title


def test_ai_chat_history_title_marks_translate_records_only():
    translate_title = AIChatHistorySidebar._display_title(
        {
            "task_type": "translate",
            "source_text": "Hello",
            "title": "",
        }
    )
    qa_title = AIChatHistorySidebar._display_title(
        {
            "task_type": "qa",
            "source_text": "怎么配置问答模型？",
            "title": "",
        }
    )
    renamed_translate_title = AIChatHistorySidebar._display_title(
        {
            "task_type": "translate",
            "source_text": "Hello",
            "title": "[翻译]自定义标题",
        }
    )

    assert translate_title == "[翻译]Hello"
    assert qa_title == "怎么配置问答模型？"
    assert renamed_translate_title == "[翻译]自定义标题"


def test_ai_chat_history_group_title_only_separates_pinned():
    assert AIChatHistorySidebar._record_group_title({"is_pinned": 1, "is_starred": 1}) == "置顶"
    assert AIChatHistorySidebar._record_group_title({
        "is_pinned": 0,
        "is_starred": 1,
        "created_at": "2000-01-01 00:00:00",
    }) == "更早"


def test_ai_chat_history_search_snippet_prefers_matching_body_text():
    sidebar = type(
        "SidebarSnippetProbe",
        (),
        {
            "_search_text": "卡片盒",
            "_compact_text": staticmethod(AIChatHistorySidebar._compact_text),
        },
    )()
    record = {
        "source_text": "普通标题",
        "result_text": "前置内容很多很多很多 卡片盒 命中的正文片段 后续内容",
        "prompt_text": "",
    }

    snippet = AIChatHistorySidebar._search_snippet(sidebar, record, limit=24)

    assert "卡片盒" in snippet
    assert "命中的正文片段" in snippet


def test_ai_chat_history_model_name_is_compacted_for_display():
    model_name = AIChatHistorySidebar._display_model_name(
        {
            "model_name": "  gpt-4.1-mini   | 备用信息  ",
        }
    )

    assert model_name == "gpt-4.1-mini"


def test_ai_chat_history_meta_shows_body_without_model_by_default():
    sidebar = type(
        "SidebarMetaProbe",
        (),
        {
            "_search_text": "",
            "_compact_text": staticmethod(AIChatHistorySidebar._compact_text),
            "_display_model_name": staticmethod(AIChatHistorySidebar._display_model_name),
            "_search_snippet": AIChatHistorySidebar._search_snippet,
        },
    )()
    record = {
        "created_at": "2026-06-29 12:01:00",
        "model_name": "gemini-3.5-flash-thinking",
        "result_text": "这是正文片段",
        "source_text": "问题标题",
        "prompt_text": "",
    }

    meta_text = AIChatHistorySidebar._record_meta_text(sidebar, record)

    assert meta_text == "06-29 12:01  这是正文片段"
    assert "模型:" not in meta_text


def test_ai_chat_history_meta_shows_model_when_searching():
    sidebar = type(
        "SidebarSearchMetaProbe",
        (),
        {
            "_search_text": "正文片段",
            "_compact_text": staticmethod(AIChatHistorySidebar._compact_text),
            "_display_model_name": staticmethod(AIChatHistorySidebar._display_model_name),
            "_search_snippet": AIChatHistorySidebar._search_snippet,
        },
    )()
    record = {
        "created_at": "2026-06-29 12:01:00",
        "model_name": "gemini-3.5-flash-thinking | extra",
        "result_text": "这是正文片段",
        "source_text": "问题标题",
        "prompt_text": "",
    }

    meta_text = AIChatHistorySidebar._record_meta_text(sidebar, record)

    assert meta_text == "06-29 12:01  模型: gemini-3.5-flash-thinking  这是正文片段"


def test_ai_chat_history_sidebar_search_activation_emits_query():
    _app()
    store = _FakeHistoryStore(total=1)
    sidebar = AIChatHistorySidebar(store)
    captured: list[tuple] = []
    try:
        sidebar.refresh()
        sidebar.record_selected.connect(lambda record_id: captured.append(("plain", record_id)))
        sidebar.record_search_selected.connect(
            lambda record_id, query: captured.append(("search", record_id, query))
        )

        sidebar._selected_record_id = 1
        sidebar._search_text = "回答内容"
        sidebar._activate_record(1)

        assert captured == [("search", 1, "回答内容")]
    finally:
        sidebar.deleteLater()


def test_ai_chat_history_sidebar_uses_delegate_items_instead_of_row_widgets():
    _app()
    store = _FakeHistoryStore(total=120)
    sidebar = AIChatHistorySidebar(store)
    try:
        sidebar.resize(280, 500)
        sidebar.show()
        sidebar.refresh()
        QApplication.processEvents()

        assert sidebar._list.count() == 60
        for row in range(sidebar._list.count()):
            assert sidebar._list.itemWidget(sidebar._list.item(row)) is None

        sidebar._set_batch_mode(True)
        sidebar._set_record_selected(2, True)
        selected_item = sidebar._item_for_record(2)
        assert selected_item is not None
        assert selected_item.data(_AI_CHAT_BATCH_SELECTED_ROLE) is True
    finally:
        sidebar.deleteLater()


def test_ai_chat_history_sidebar_context_menu_uses_custom_popup():
    _app()
    store = _FakeHistoryStore(total=1)
    sidebar = AIChatHistorySidebar(store)
    try:
        sidebar.refresh()
        QApplication.processEvents()
        record = sidebar._record_from_item(sidebar._list.item(0))

        sidebar._show_record_menu(record, sidebar.mapToGlobal(sidebar.rect().center()))
        QApplication.processEvents()

        popup = sidebar.findChild(OcrGenericMenuPopup)
        assert popup is not None
        assert popup.isVisible()
        button_texts = [button.text() for button in popup.findChildren(QPushButton) if button.text()]
        assert button_texts[:6] == ["重命名", "置顶", "收藏", "删除", "删除上下5条", "批量删除"]
        popup.close()
    finally:
        sidebar.deleteLater()


def test_ai_chat_history_sidebar_search_input_uses_custom_context_menu():
    _app()
    store = _FakeHistoryStore(total=1)
    sidebar = AIChatHistorySidebar(store)
    try:
        assert sidebar._search_input.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
        assert bool(sidebar._search_input.property("deepcatCustomContextMenu"))
    finally:
        sidebar.deleteLater()


def test_ai_chat_history_sidebar_hover_dots_open_same_custom_popup():
    _app()
    store = _FakeHistoryStore(total=1)
    sidebar = AIChatHistorySidebar(store)
    try:
        sidebar.resize(280, 500)
        sidebar.show()
        sidebar.refresh()
        QApplication.processEvents()

        item = sidebar._list.item(0)
        row_rect = sidebar._row_rect_for_item(item)
        menu_pos = sidebar._menu_rect_for_row_rect(row_rect).center()

        assert sidebar._handle_viewport_click(menu_pos)
        QApplication.processEvents()

        popup = sidebar.findChild(OcrGenericMenuPopup)
        assert popup is not None
        assert popup.isVisible()
        popup.close()
    finally:
        sidebar.deleteLater()


def test_ai_chat_history_sidebar_loads_one_page_per_bottom_scroll():
    _app()
    store = _FakeHistoryStore(total=180)
    sidebar = AIChatHistorySidebar(store)
    try:
        sidebar.resize(280, 500)
        sidebar.show()
        sidebar.refresh()
        QApplication.processEvents()

        bar = sidebar._list.verticalScrollBar()
        bar.setValue(bar.maximum())
        QApplication.processEvents()

        assert store.calls == [(60, 0), (60, 60)]
        assert sidebar._list.count() == 120
    finally:
        sidebar.deleteLater()


def test_ai_chat_history_sidebar_prefers_lightweight_summaries():
    _app()
    store = _SummaryOnlyHistoryStore(total=60)
    sidebar = AIChatHistorySidebar(store)
    try:
        sidebar.refresh()
        QApplication.processEvents()

        assert store.summary_calls == [(60, 0)]
        assert store.summary_query_kwargs[0]["task_type_filter"] is None
        item = sidebar._list.item(0)
        assert item is not None
        assert sidebar._record_from_item(item)["prompt_text"] == ""
    finally:
        sidebar.deleteLater()


def test_ai_chat_history_sidebar_surrounding_delete_range_loads_next_page(monkeypatch):
    _app()
    store = _FakeHistoryStore(total=75)
    sidebar = AIChatHistorySidebar(store)
    try:
        sidebar.refresh()
        QApplication.processEvents()

        assert sidebar._list.count() == 60
        assert sidebar._surrounding_record_ids(55, radius=5) == list(range(50, 61))
        assert sidebar._list.count() == 75
        assert store.calls == [(60, 0), (60, 60)]

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )
        record = sidebar._record_from_item(sidebar._item_for_record(55))
        sidebar._delete_surrounding_records(record)

        assert store.deleted_record_ids == list(range(50, 61))
    finally:
        sidebar.deleteLater()


def test_ai_chat_history_sidebar_surrounding_delete_range_clamps_at_edges():
    _app()
    store = _FakeHistoryStore(total=30)
    sidebar = AIChatHistorySidebar(store)
    try:
        sidebar.refresh()
        QApplication.processEvents()

        assert sidebar._surrounding_record_ids(1, radius=5) == list(range(1, 7))
        assert sidebar._surrounding_record_ids(30, radius=5) == list(range(25, 31))
    finally:
        sidebar.deleteLater()


def test_ai_chat_history_sidebar_surrounding_delete_skips_pinned_records():
    _app()
    store = _FakeHistoryStore(total=30)
    sidebar = AIChatHistorySidebar(store)
    try:
        sidebar.refresh()
        QApplication.processEvents()

        for record_id in (3, 5, 8, 10):
            item = sidebar._item_for_record(record_id)
            record = dict(sidebar._record_from_item(item))
            record["is_pinned"] = 1
            item.setData(_AI_CHAT_RECORD_ROLE, record)

        assert sidebar._surrounding_record_ids(7, radius=5) == [1, 2, 4, 6, 7, 9, 11, 12, 13, 14, 15]
        assert sidebar._surrounding_record_ids(5, radius=5) == []
    finally:
        sidebar.deleteLater()
