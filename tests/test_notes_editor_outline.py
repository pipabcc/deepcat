import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QApplication, QTreeWidget, QWidget

from deepcat.ui.main_window.notes import NotesMixin, _NoteOutlineWindowEventFilter, _NotesEditor


class _IndexStub:
    def __init__(self, index: int) -> None:
        self._index = index

    def currentIndex(self) -> int:
        return self._index


class _OutlineDummy(NotesMixin):
    def __init__(self, editor: _NotesEditor | None = None) -> None:
        self._notes_editor = editor
        self._note_outline_tree = QTreeWidget() if editor is not None else None
        self._note_outline_heading_positions: list[int] = []
        self._note_outline_items_by_position = {}
        self._note_outline_manual_visibility = None
        self._maximized = True
        self._stack = _IndexStub(self._NOTE_OUTLINE_PAGE_INDEX)
        self._table_notes_stack = _IndexStub(1)

    def windowState(self):
        return Qt.WindowState.WindowMaximized if self._maximized else Qt.WindowState.WindowNoState

    def isMaximized(self) -> bool:
        return self._maximized

    def _schedule_note_outline_refresh(self) -> None:
        pass


class NotesEditorOutlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_extracts_semantic_headings_with_levels_and_positions(self):
        editor = _NotesEditor()
        try:
            editor.setHtml(
                "<h1>项目总览</h1>"
                "<p>普通正文</p>"
                "<h3>实施细节</h3>"
                "<h2>验收标准</h2>"
            )

            outline = NotesMixin._extract_note_outline(editor.document())

            self.assertEqual([(level, text) for level, text, _position in outline], [
                (1, "项目总览"),
                (3, "实施细节"),
                (2, "验收标准"),
            ])
            self.assertEqual([position for _level, _text, position in outline], sorted(
                position for _level, _text, position in outline
            ))
        finally:
            editor.deleteLater()

    def test_extracts_legacy_visual_heading(self):
        editor = _NotesEditor()
        try:
            editor.setPlainText("旧版一级标题\n普通正文")
            cursor = QTextCursor(editor.document().begin())
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            heading_format = QTextCharFormat()
            heading_format.setFontPointSize(20)
            heading_format.setFontWeight(800)
            cursor.mergeCharFormat(heading_format)

            outline = NotesMixin._extract_note_outline(editor.document())

            self.assertEqual([(level, text) for level, text, _position in outline], [(1, "旧版一级标题")])
        finally:
            editor.deleteLater()

    def test_title_style_sets_and_body_style_clears_semantic_heading(self):
        editor = _NotesEditor()
        dummy = _OutlineDummy(editor)
        try:
            editor.setPlainText("可跳转标题")

            NotesMixin._apply_note_block_style(dummy, "标题 1")
            self.assertEqual(editor.document().begin().blockFormat().headingLevel(), 1)
            self.assertIn("<h1", editor.toHtml().lower())

            NotesMixin._apply_note_block_style(dummy, "正文")
            self.assertEqual(editor.document().begin().blockFormat().headingLevel(), 0)
            self.assertNotIn("<h1", editor.toHtml().lower())
        finally:
            dummy._note_outline_tree.deleteLater()
            editor.deleteLater()

    def test_outline_tree_preserves_heading_hierarchy_and_click_jumps(self):
        editor = _NotesEditor()
        dummy = _OutlineDummy(editor)
        try:
            editor.setHtml("<h1>第一章</h1><h2>第一节</h2><p>正文</p><h1>第二章</h1>")

            NotesMixin._refresh_note_outline(dummy)

            tree = dummy._note_outline_tree
            self.assertEqual(tree.topLevelItemCount(), 2)
            self.assertEqual(tree.topLevelItem(0).text(0), "第一章")
            self.assertEqual(tree.topLevelItem(0).childCount(), 1)
            self.assertEqual(tree.topLevelItem(0).child(0).text(0), "第一节")

            second_chapter = tree.topLevelItem(1)
            NotesMixin._jump_to_note_outline_item(dummy, second_chapter)
            self.assertEqual(editor.textCursor().block().text(), "第二章")
        finally:
            dummy._note_outline_tree.deleteLater()
            editor.deleteLater()

    def test_hidden_outline_only_marks_dirty_without_scanning_document(self):
        editor = _NotesEditor()
        dummy = _OutlineDummy(editor)
        dummy._maximized = False
        dummy._note_outline_dirty = False
        timer_calls = []
        extract_calls = []

        class _Timer:
            @staticmethod
            def start(delay_ms):
                timer_calls.append(("start", delay_ms))

            @staticmethod
            def stop():
                timer_calls.append(("stop",))

        dummy._note_outline_refresh_timer = _Timer()
        dummy._extract_note_outline = lambda _document: extract_calls.append("extract") or []
        try:
            NotesMixin._schedule_note_outline_refresh(dummy)

            self.assertTrue(dummy._note_outline_dirty)
            self.assertEqual(timer_calls, [("stop",)])
            self.assertEqual(extract_calls, [])
        finally:
            dummy._note_outline_tree.deleteLater()
            editor.deleteLater()

    def test_showing_dirty_outline_rebuilds_once(self):
        editor = _NotesEditor()
        dummy = _OutlineDummy(editor)
        dummy._note_outline_dirty = True
        extract_calls = []

        class _Panel:
            def __init__(self) -> None:
                self.visible = False

            def isVisible(self) -> bool:
                return self.visible

            def setVisible(self, visible: bool) -> None:
                self.visible = visible

        dummy._note_outline_panel = _Panel()
        dummy._extract_note_outline = lambda _document: extract_calls.append("extract") or []
        try:
            NotesMixin._sync_note_outline_visibility(dummy)
            NotesMixin._sync_note_outline_visibility(dummy)

            self.assertTrue(dummy._note_outline_panel.visible)
            self.assertFalse(dummy._note_outline_dirty)
            self.assertEqual(extract_calls, ["extract"])
        finally:
            dummy._note_outline_tree.deleteLater()
            editor.deleteLater()

    def test_outline_is_visible_only_for_maximized_notebook_page(self):
        dummy = _OutlineDummy()
        self.assertTrue(NotesMixin._should_show_note_outline(dummy))

        dummy._maximized = False
        self.assertFalse(NotesMixin._should_show_note_outline(dummy))

        dummy._maximized = True
        dummy._table_notes_stack = _IndexStub(0)
        self.assertFalse(NotesMixin._should_show_note_outline(dummy))

        dummy._table_notes_stack = _IndexStub(1)
        dummy._stack = _IndexStub(0)
        self.assertFalse(NotesMixin._should_show_note_outline(dummy))

    def test_manual_outline_visibility_overrides_window_size(self):
        dummy = _OutlineDummy()
        dummy._maximized = False
        self.assertEqual(NotesMixin._note_outline_menu_label(dummy), "显示目录")

        NotesMixin._toggle_note_outline_visibility(dummy)
        self.assertTrue(NotesMixin._should_show_note_outline(dummy))
        self.assertEqual(NotesMixin._note_outline_menu_label(dummy), "隐藏目录")

        dummy._maximized = True
        NotesMixin._toggle_note_outline_visibility(dummy)
        self.assertFalse(NotesMixin._should_show_note_outline(dummy))
        self.assertEqual(NotesMixin._note_outline_menu_label(dummy), "显示目录")

    def test_window_state_change_restores_automatic_outline_and_active_tab(self):
        dummy = _OutlineDummy()
        dummy._maximized = False
        scroll_calls = []
        timer_starts = []

        class _Timer:
            @staticmethod
            def start(delay_ms):
                timer_starts.append(delay_ms)

        dummy._scroll_active_tab_into_view = lambda tab_type: scroll_calls.append(tab_type)
        dummy._note_window_layout_timer = _Timer()

        NotesMixin._toggle_note_outline_visibility(dummy)
        self.assertTrue(NotesMixin._should_show_note_outline(dummy))

        dummy._maximized = True
        NotesMixin._handle_note_window_state_change(dummy)
        self.assertIsNone(dummy._note_outline_manual_visibility)
        self.assertTrue(NotesMixin._should_show_note_outline(dummy))

        NotesMixin._toggle_note_outline_visibility(dummy)
        self.assertFalse(NotesMixin._should_show_note_outline(dummy))

        dummy._maximized = False
        NotesMixin._handle_note_window_state_change(dummy)
        self.assertIsNone(dummy._note_outline_manual_visibility)
        self.assertFalse(NotesMixin._should_show_note_outline(dummy))
        self.assertEqual(scroll_calls, ["note", "note"])
        self.assertEqual(timer_starts, [160, 160])

        dummy._maximized = True
        NotesMixin._handle_note_window_state_change(dummy)
        self.assertTrue(NotesMixin._should_show_note_outline(dummy))

    def test_active_table_notes_tab_visibility_tracks_current_mode(self):
        dummy = _OutlineDummy()
        scroll_calls = []
        dummy._scroll_active_tab_into_view = lambda tab_type: scroll_calls.append(tab_type)

        NotesMixin._ensure_active_table_notes_tab_visible(dummy)
        dummy._table_notes_stack._index = 0
        NotesMixin._ensure_active_table_notes_tab_visible(dummy)
        dummy._stack._index = 0
        NotesMixin._ensure_active_table_notes_tab_visible(dummy)

        self.assertEqual(scroll_calls, ["note", "table"])

    def test_active_table_notes_tab_visibility_is_scheduled_after_layout(self):
        dummy = _OutlineDummy()
        scroll_calls = []
        scheduled = []
        dummy._scroll_active_tab_into_view = lambda tab_type: scroll_calls.append(tab_type)

        with patch(
            "deepcat.ui.main_window.notes.QTimer.singleShot",
            side_effect=lambda delay, callback: scheduled.append((delay, callback)),
        ):
            NotesMixin._schedule_active_table_notes_tab_visibility(dummy)

        self.assertEqual([delay for delay, _callback in scheduled], [0, 160])
        for _delay, callback in scheduled:
            callback()
        self.assertEqual(scroll_calls, ["note", "note"])

    def test_switching_to_table_notes_schedules_active_tab_visibility(self):
        from types import SimpleNamespace

        from deepcat.ui.main_window.window_navigation import WindowNavigationMixin

        calls = []

        class _Page:
            @staticmethod
            def setFocus() -> None:
                calls.append("focus")

        class _Stack:
            def __init__(self) -> None:
                self._index = 2

            def currentIndex(self) -> int:
                return self._index

            @staticmethod
            def count() -> int:
                return 10

            def setCurrentIndex(self, index: int) -> None:
                self._index = index

            @staticmethod
            def currentWidget():
                return _Page()

        dummy = SimpleNamespace(
            _stack=_Stack(),
            _SEARCH_PAGE_INDEX=7,
            _USAGE_GUIDE_PAGE_INDEX=9,
            _ABOUT_PAGE_INDEX=6,
            _NOTE_OUTLINE_PAGE_INDEX=5,
            _nav_buttons=[],
            _remember_current_resizable_page_size=lambda _index: None,
            isVisible=lambda: False,
            updatesEnabled=lambda: True,
            _set_window_resize_mode=lambda _resizable: None,
            _is_resizable_page_index=lambda _index: True,
            _apply_remembered_resizable_page_size=lambda _index: None,
            _sync_page_right_edge_guard=lambda: None,
            _schedule_hover_cursor_refresh=lambda: None,
            _schedule_active_table_notes_tab_visibility=lambda: calls.append("schedule-tabs"),
        )

        with patch("deepcat.ui.main_window.window_navigation.QTimer.singleShot"):
            WindowNavigationMixin._switch_page(dummy, 5)

        self.assertEqual(dummy._stack.currentIndex(), 5)
        self.assertEqual(calls, ["focus", "schedule-tabs"])

    def test_editor_context_menu_places_outline_toggle_before_pin(self):
        import deepcat.ui.post_capture_actions as post_capture_actions

        dummy = _OutlineDummy()
        dummy._maximized = False
        dummy._active_note_tab = 0
        dummy._pin_tab_content = lambda *_args: None
        captured_items = []

        class _Popup:
            def __init__(self, items, parent=None):
                captured_items.append(items)

            def show_at_pos(self, _pos):
                pass

        class _ContextMenuEvent:
            @staticmethod
            def pos():
                return QPoint(20, 20)

            @staticmethod
            def globalPos():
                return QPoint(20, 20)

        editor = _NotesEditor(dummy)
        old_popup = post_capture_actions.OcrGenericMenuPopup
        post_capture_actions.OcrGenericMenuPopup = _Popup
        try:
            editor.contextMenuEvent(_ContextMenuEvent())
            first_menu = captured_items[-1]
            self.assertEqual(
                [first_menu[-2][0], first_menu[-1][0]],
                ["🧭 显示目录", "📌 置顶记事本"],
            )

            first_menu[-2][1]()
            editor.contextMenuEvent(_ContextMenuEvent())
            self.assertEqual(captured_items[-1][-2][0], "🧭 隐藏目录")
        finally:
            post_capture_actions.OcrGenericMenuPopup = old_popup
            editor.deleteLater()

    def test_window_state_event_schedules_outline_visibility_sync(self):
        class _Owner(QWidget):
            def __init__(self) -> None:
                super().__init__()
                self.sync_count = 0

            def _sync_note_outline_visibility(self) -> None:
                self.sync_count += 1

        owner = _Owner()
        event_filter = _NoteOutlineWindowEventFilter(owner)
        try:
            handled = event_filter.eventFilter(owner, QEvent(QEvent.Type.WindowStateChange))
            self.app.processEvents()

            self.assertFalse(handled)
            self.assertEqual(owner.sync_count, 1)
        finally:
            owner.deleteLater()

    def test_window_event_filter_tolerates_cleared_owner(self):
        owner = QWidget()
        event_filter = _NoteOutlineWindowEventFilter(owner)
        try:
            del event_filter._owner
            handled = event_filter.eventFilter(owner, QEvent(QEvent.Type.WindowStateChange))
            self.assertFalse(handled)
        finally:
            owner.deleteLater()

    def test_window_outline_callback_is_cancelled_when_owner_is_destroyed(self):
        from PyQt6 import sip

        calls = []

        class _Owner(QWidget):
            def _sync_note_outline_visibility(self):
                calls.append("sync")

        owner = _Owner()
        event_filter = _NoteOutlineWindowEventFilter(owner)
        event_filter.eventFilter(owner, QEvent(QEvent.Type.WindowStateChange))
        sip.delete(owner)
        self.app.processEvents()
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
