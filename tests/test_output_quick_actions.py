from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _isolate_ai_panel_persistence(monkeypatch):
    try:
        from deepcat.ui.post_capture_actions import OcrTextPanel
    except Exception:
        yield
        return

    monkeypatch.setattr(OcrTextPanel, "_save_current_chat_draft", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(OcrTextPanel, "_restore_chat_draft", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(OcrTextPanel, "_save_last_pos_to_settings", staticmethod(lambda: None))
    monkeypatch.setattr(
        OcrTextPanel,
        "_save_history_sidebar_open_to_settings",
        staticmethod(lambda _opened: None),
    )
    yield


def test_output_quick_action_bar_emits_expected_actions():
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtWidgets import QToolButton
        from deepcat.ui.post_capture_actions import OutputQuickActionBar
    except Exception:
        pytest.skip("缺少 PyQt6，跳过输出区快捷图标测试")

    app = QApplication.instance() or QApplication([])
    seen: list[str] = []
    asset_dir = Path(__file__).resolve().parents[1] / "deepcat" / "ui" / "assets"
    bar = OutputQuickActionBar(seen.append, asset_dir)

    try:
        buttons = bar.findChildren(QToolButton)
        action_ids = [str(button.property("quickActionId") or "") for button in buttons]

        assert action_ids == [
            "add_chat_card",
            "switch_model",
            "clipboard_window",
            "later_read_window",
            "region_capture",
            "new_todo",
            "rest_todo",
            "drive_cleaner",
            "main_window",
        ]

        for button in buttons:
            button.click()

        app.processEvents()
        assert seen == action_ids
    finally:
        bar.deleteLater()


def test_main_window_output_quick_action_dispatches_to_existing_entrypoints():
    from deepcat.ui.main_window import MainWindow

    calls: list[tuple[str, object]] = []
    dummy = SimpleNamespace(
        _open_compact_list_window=lambda page_type: calls.append(("compact", page_type)),
        _start_capture_clicked=lambda **kwargs: calls.append(("capture", kwargs)),
        _open_todo_dialog=lambda: calls.append(("todo", None)),
        _open_todo_resource_window=lambda: calls.append(("rest", None)),
        _open_drive_cleaner_window=lambda: calls.append(("cleaner", None)),
        _show_from_tray=lambda: calls.append(("main", None)),
        _send_tray_notification=lambda title, body, timeout: calls.append(("toast", (title, body, timeout))),
    )

    for action_id in [
        "clipboard_window",
        "later_read_window",
        "region_capture",
        "new_todo",
        "rest_todo",
        "drive_cleaner",
        "main_window",
        "unknown",
    ]:
        MainWindow._handle_output_quick_action(dummy, action_id)

    assert calls == [
        ("compact", "clipboard"),
        ("compact", "later_read"),
        ("capture", {"from_tray": False, "mode_override": "框选截图"}),
        ("todo", None),
        ("rest", None),
        ("cleaner", None),
        ("main", None),
        ("toast", ("快捷动作", "暂不支持该快捷动作。", 1800)),
    ]


def test_region_capture_from_output_quick_action_tracks_source_ai_panel():
    from deepcat.ui.main_window import MainWindow

    calls: list[tuple[str, object]] = []

    class FakePanel:
        def _minimize_panel(self):
            calls.append(("minimize", None))

        def width(self):
            return 640

        def submit_ocr_text_for_qa(self, text: str) -> bool:
            calls.append(("submit", text))
            return True

    panel = FakePanel()
    dummy = SimpleNamespace(
        _start_capture_clicked=lambda **kwargs: calls.append(("capture", kwargs)),
        _send_tray_notification=lambda title, body, timeout: calls.append(("toast", (title, body, timeout))),
    )

    MainWindow._handle_output_quick_action(dummy, "region_capture", panel)

    assert getattr(dummy, "_pending_output_quick_ocr_panel") is panel
    assert calls == [
        ("minimize", None),
        ("capture", {"from_tray": False, "mode_override": "框选截图"}),
    ]

    assert MainWindow._handle_output_quick_ocr_text_result(dummy, "识别文本", 1.2) is True
    assert getattr(dummy, "_pending_output_quick_ocr_panel") is None
    assert calls[-2:] == [
        ("submit", "识别文本"),
        ("toast", ("OCR识别完成", "识别结果已填入当前AI对话输入框。", 1800)),
    ]


def test_submit_ocr_text_keeps_content_in_adaptive_input_without_sending():
    from PyQt6.QtCore import QRect
    from deepcat.ui.post_capture_actions import OcrTextPanel

    calls = []

    dummy = SimpleNamespace(
        _region=QRect(10, 20, 300, 120),
        set_text_and_reposition=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert OcrTextPanel.submit_ocr_text_for_qa(dummy, "OCR识别文本") is True
    assert calls[0][0][0] == "OCR识别文本"
    assert calls[0][1]["force_initial_view"] is True
    assert calls[0][1]["ocr_input_auto_height"] is True
    assert calls[0][1]["input_origin"] == "ocr"


def test_ocr_input_window_height_is_content_adaptive_and_capped_at_800():
    from deepcat.ui.post_capture_actions import OcrTextPanel

    bound = OcrTextPanel._bounded_ocr_input_window_height
    assert bound(180, 120, 40, 800, 1200) == 180
    assert bound(180, 120, 520, 800, 1200) == 580
    assert bound(180, 120, 1400, 800, 1200) == 800
    assert bound(180, 120, 1400, 800, 640) == 640


def test_standalone_ocr_success_stays_in_adaptive_ai_input_area():
    from PyQt6.QtCore import QRect
    from PyQt6.QtWidgets import QApplication
    from deepcat.ui.post_capture_actions import OcrTextPanel

    app = QApplication.instance() or QApplication([])
    panel = OcrTextPanel(restore_history_sidebar=False)
    try:
        panel.set_text_and_reposition(
            "短文本",
            QRect(100, 100, 500, 300),
            elapsed=1.25,
            force_initial_view=True,
            ocr_input_auto_height=True,
            input_origin="ocr",
        )
        app.processEvents()
        short_height = panel.height()

        ocr_text = "\n".join(f"第 {index} 行 OCR 内容" for index in range(120))
        panel.set_text_and_reposition(
            ocr_text,
            QRect(100, 100, 500, 300),
            elapsed=1.25,
            force_initial_view=True,
            ocr_input_auto_height=True,
            input_origin="ocr",
        )
        app.processEvents()
        app.processEvents()

        assert panel._editor.toPlainText() == ocr_text
        assert panel._chat_history == []
        assert not panel._bubble_view.isVisible()
        assert panel.height() > short_height
        assert panel.height() <= 800

        panel._clear_ocr_input_auto_height()
        panel._chat_history = [{"role": "user", "content": ocr_text, "task_type": "qa"}]
        panel._is_chatting = True
        panel._bubble_view.show()
        panel._reposition()
        app.processEvents()

        assert panel._ocr_input_auto_height_cap == 0
        assert panel._editor_container.height() == 120
    finally:
        panel.close()
        app.processEvents()


def test_output_quick_action_bar_only_shows_for_empty_preserved_output_area():
    try:
        from deepcat.ui.post_capture_actions import OcrTextPanel
    except Exception:
        pytest.skip("缺少 PyQt6，跳过输出区快捷图标可见性测试")

    class FakeBar:
        def __init__(self):
            self.visible = None

        def setVisible(self, value):
            self.visible = bool(value)

    class FakeBubbleView:
        def __init__(self, visible: bool, empty: bool):
            self._visible = visible
            self._empty = empty

        def isVisible(self):
            return self._visible

        def is_empty(self):
            return self._empty

    dummy = SimpleNamespace(
        _output_quick_action_bar=FakeBar(),
        _quick_action_handler=lambda action: None,
        _bubble_view=FakeBubbleView(False, True),
        _chat_history=[],
        _is_chatting=False,
        _history_showing=False,
        _preserve_cleared_output_area=False,
        _history_forced_output_area=False,
    )

    OcrTextPanel._sync_output_quick_action_bar_visibility(dummy)
    assert dummy._output_quick_action_bar.visible is False

    dummy._bubble_view = FakeBubbleView(True, True)
    dummy._preserve_cleared_output_area = True
    OcrTextPanel._sync_output_quick_action_bar_visibility(dummy)
    assert dummy._output_quick_action_bar.visible is True

    dummy._chat_history = [{"role": "assistant", "content": "回答"}]
    OcrTextPanel._sync_output_quick_action_bar_visibility(dummy)
    assert dummy._output_quick_action_bar.visible is False

    dummy._chat_history = []
    dummy._is_chatting = True
    OcrTextPanel._sync_output_quick_action_bar_visibility(dummy)
    assert dummy._output_quick_action_bar.visible is False

    dummy._is_chatting = False
    dummy._history_showing = True
    dummy._history_forced_output_area = True
    dummy._preserve_cleared_output_area = False
    OcrTextPanel._sync_output_quick_action_bar_visibility(dummy)
    assert dummy._output_quick_action_bar.visible is True

    dummy._history_showing = False
    dummy._history_forced_output_area = False
    dummy._bubble_view = FakeBubbleView(False, True)
    OcrTextPanel._sync_output_quick_action_bar_visibility(dummy)
    assert dummy._output_quick_action_bar.visible is False


def test_prompt_settings_button_hover_only_shows_tooltip():
    try:
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QApplication
        from deepcat.ui.post_capture_actions import OcrTextPanel
    except Exception:
        pytest.skip("缺少 PyQt6，跳过快捷按钮悬浮测试")

    app = QApplication.instance() or QApplication([])
    panel = OcrTextPanel(on_toast=None, restore_history_sidebar=False)
    calls: list[str] = []
    panel._show_prompt_settings_popup = lambda: calls.append("popup")
    try:
        panel.eventFilter(panel._btn_prompt_settings, QEvent(QEvent.Type.Enter))
        app.processEvents()
        assert calls == []
    finally:
        panel.deleteLater()


def test_remembered_history_sidebar_ai_panel_shows_quick_action_bar(monkeypatch):
    try:
        from PyQt6.QtCore import QRect
        from PyQt6.QtWidgets import QApplication
        from deepcat.ui.post_capture_actions import OcrTextPanel
    except Exception:
        pytest.skip("缺少 PyQt6，跳过历史侧栏快捷工具条测试")

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        OcrTextPanel,
        "_load_history_sidebar_open_from_settings",
        staticmethod(lambda: True),
    )
    panel = OcrTextPanel(
        on_toast=None,
        quick_action_handler=lambda action_id, source_panel=None: None,
    )
    try:
        panel.set_text_and_reposition("", QRect(20, 20, 1, 1))
        app.processEvents()
        app.processEvents()
        assert panel._history_showing is True
        assert panel._output_quick_action_bar.isVisible() is True

        panel._hide_history(persist_state=False)
        app.processEvents()
        app.processEvents()
        assert panel._history_showing is False
        assert panel._output_quick_action_bar.isVisible() is False
    finally:
        panel.close()
        panel.deleteLater()
