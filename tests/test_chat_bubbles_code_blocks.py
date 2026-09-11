import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

if sys.platform.startswith("win") and sys.version_info >= (3, 13):
    pytest.skip("Python 3.13 + PyQt6 on Windows may crash during QApplication teardown", allow_module_level=True)

try:
    from PyQt6.QtCore import QEvent, QEventLoop, QPoint, QTimer, Qt
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QApplication, QToolButton
except Exception:
    pytest.skip("缺少 PyQt6，跳过代码块 UI 测试", allow_module_level=True)

from deepcat.ui.chat_bubbles import BubbleListView, ChatBubble, ChatImageWidget, CodeBlockWidget


def _app() -> QApplication:
    return QApplication.instance() or QApplication(sys.argv)


def test_streaming_code_block_renders_directly():
    _app()
    bubble = ChatBubble("assistant")

    bubble.set_content("```html\n<div>Hello</div>\n", is_markdown=True, streaming=True)

    code_widget = bubble.findChild(CodeBlockWidget)
    assert code_widget is not None
    assert "代码块正在生成" not in bubble._label.text()

    bubble.set_content("```html\n<div>Hello</div>\n```", is_markdown=True, streaming=False)

    code_widget = bubble.findChild(CodeBlockWidget)
    assert code_widget is not None


def test_streaming_code_block_updates_real_widget():
    _app()
    bubble = ChatBubble("assistant")

    bubble.set_content("```python\nprint(1)\n", is_markdown=True, streaming=True)
    code_widget = bubble.findChild(CodeBlockWidget)
    assert code_widget is not None

    bubble.set_content("```python\nprint(1)\nprint(2)\n", is_markdown=True, streaming=True)

    code_widget = bubble.findChild(CodeBlockWidget)
    assert code_widget is not None
    assert "print(2)" in code_widget._code_text


def test_streaming_code_block_appends_without_full_plain_text_reset(monkeypatch):
    _app()
    widget = CodeBlockWidget("print(1)\n", "python", streaming=True)
    calls = []

    monkeypatch.setattr(widget._editor, "setPlainText", lambda text: calls.append(text))

    widget.update_code("print(1)\nprint(2)\n", "python", streaming=True)

    assert calls == []
    assert "print(2)" in widget._editor.toPlainText()


def test_code_block_editor_uses_custom_text_context_menu():
    _app()
    bubble = ChatBubble("assistant")

    bubble.set_content("```python\nprint(1)\n```", is_markdown=True)

    code_widget = bubble.findChild(CodeBlockWidget)
    assert code_widget is not None
    assert code_widget._editor.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    assert bool(code_widget._editor.property("deepcatCustomContextMenu"))


def test_plain_image_preview_link_emits_preview_signal():
    _app()
    bubble = ChatBubble("user")
    captured = []
    bubble.image_preview_requested.connect(captured.append)

    bubble.set_content("请看\n📎 [图片: demo.png](deepcat-image-preview:img_123)", is_markdown=False)

    assert 'href="deepcat-image-preview:img_123"' in bubble._label.text()
    assert "text-decoration:none" in bubble._label.text()
    assert "┄" not in bubble._label.text()
    assert bubble._label.contentsMargins().bottom() >= 3
    bubble._handle_link_activated("deepcat-image-preview:img_123")
    assert captured == ["img_123"]


def test_markdown_data_image_uses_real_image_widget_instead_of_rich_text_image():
    _app()
    bubble = ChatBubble("assistant")
    data_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAFgwJ/lZ7x2wAAAABJRU5ErkJggg=="
    )

    bubble.set_content(f"前文\n![generated image]({data_url})\n后文", is_markdown=True)

    image_widget = bubble.findChild(ChatImageWidget)
    assert image_widget is not None
    assert data_url not in bubble._label.text()
    assert "前文" in bubble._label.text()


def test_markdown_image_counts_height_before_bubble_is_shown():
    _app()
    bubble = ChatBubble("assistant")
    data_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAFgwJ/lZ7x2wAAAABJRU5ErkJggg=="
    )

    bubble.set_content(f"![generated image]({data_url})", is_markdown=True)
    image_widget = bubble.findChild(ChatImageWidget)

    assert image_widget is not None
    assert not image_widget.isHidden()
    assert not image_widget.isVisible()
    assert bubble.heightForWidth(420) >= image_widget.sizeHint().height() + 20


def test_streaming_markdown_image_renders_real_image_widget():
    _app()
    bubble = ChatBubble("assistant")
    data_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAFgwJ/lZ7x2wAAAABJRU5ErkJggg=="
    )

    bubble.set_content(f"前文\n![generated image]({data_url})", is_markdown=True, streaming=True)

    assert bubble.findChild(ChatImageWidget) is not None
    assert "图片生成中" not in bubble._label.text()


def test_streaming_markdown_table_renders_directly():
    _app()
    bubble = ChatBubble("assistant")
    table = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"

    bubble.set_content(f"前文\n{table}", is_markdown=True, streaming=True)

    assert "表格正在生成" not in bubble._label.text()

    bubble.set_content(f"前文\n{table}", is_markdown=True, streaming=False)

    assert "<table" in bubble._label.text()


def test_stream_update_does_not_force_bottom_after_user_scrolls_up():
    app = _app()
    view = BubbleListView()
    view.resize(420, 180)
    view.show()
    view.render_messages([
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "旧回答\n" * 80},
    ], initial_scroll_to_bottom=True)
    app.processEvents()
    view.scroll_to_bottom_now()
    app.processEvents()
    bar = view.verticalScrollBar()
    bar.setValue(0)

    view.update_last_ai("新回答\n" * 120, streaming=True)
    app.processEvents()

    assert bar.value() < bar.maximum()


def test_streaming_updates_are_coalesced_until_flush():
    _app()
    view = BubbleListView()
    view.show_single("旧回答", True)

    view.update_last_ai("新回答 1", streaming=True)
    view.update_last_ai("新回答 2", streaming=True)

    assert view._last_ai.raw_text() == "旧回答"
    assert view.stream_perf_snapshot()["delta_per_sec"] > 0

    view._flush_pending_stream_updates()

    assert view._last_ai.raw_text() == "新回答 2"
    assert view.stream_perf_snapshot()["flushes"] >= 1


def test_finalize_message_flushes_pending_stream_text():
    _app()
    view = BubbleListView()
    view.render_messages([
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "旧回答"},
    ])

    assert view.update_message_at(1, "最终回答", streaming=True)
    assert view._bubbles[1].raw_text() == "旧回答"

    assert view.finalize_message_at(1, model_name="demo")

    assert view._bubbles[1].raw_text() == "最终回答"
    assert not view._bubbles[1].is_streaming()


def test_long_answer_does_not_collapse():
    _app()
    bubble = ChatBubble("assistant")
    text = "段落内容\n" * 3000

    bubble.set_content(text, is_markdown=True, streaming=False)

    assert "回答较长" not in bubble._label.text()
    assert bubble._expand_answer_btn is None


def test_markdown_file_image_uses_real_image_widget(tmp_path):
    _app()
    bubble = ChatBubble("assistant")
    image_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfc\xcf"
        b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    image_path = tmp_path / "generated.png"
    image_path.write_bytes(image_bytes)
    image_url = image_path.as_uri()

    bubble.set_content(f"前文\n![generated image]({image_url})\n后文", is_markdown=True)

    image_widget = bubble.findChild(ChatImageWidget)
    assert image_widget is not None
    assert image_widget._image_bytes == image_bytes
    assert image_url not in bubble._label.text()


def test_markdown_file_image_link_without_bang_uses_real_image_widget(tmp_path):
    _app()
    bubble = ChatBubble("assistant")
    image_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfc\xcf"
        b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    image_path = tmp_path / "generated with space.png"
    image_path.write_bytes(image_bytes)
    image_url = image_path.as_uri()

    bubble.set_content(f"[generated image]({image_url})", is_markdown=True)

    image_widget = bubble.findChild(ChatImageWidget)
    assert image_widget is not None
    assert image_widget._image_bytes == image_bytes
    assert image_widget.sizeHint().width() <= ChatImageWidget.THUMBNAIL_MAX_WIDTH


def test_update_last_ai_file_image_stabilizes_bubble_height(tmp_path):
    app = _app()
    image_path = tmp_path / "generated-large.png"
    image = QImage(640, 480, QImage.Format.Format_RGB32)
    image.fill(QColor("#58a66c"))
    assert image.save(str(image_path))

    view = BubbleListView()
    view.resize(520, 260)
    view.show()
    view.show_single("正在思考...", True)

    view.update_last_ai(f"![generated image]({image_path.as_uri()})")

    loop = QEventLoop()
    QTimer.singleShot(220, loop.quit)
    loop.exec()
    app.processEvents()

    image_widget = view.findChild(ChatImageWidget)
    bubble = view._last_ai
    assert image_widget is not None
    assert bubble is not None
    assert image_widget.height() > 100
    assert bubble.minimumHeight() >= image_widget.sizeHint().height() + 20
    assert bubble.heightForWidth(bubble._measure_width or 420) >= image_widget.sizeHint().height() + 20


def test_file_image_layout_retries_after_stream_render_block(tmp_path):
    app = _app()
    image_path = tmp_path / "generated-after-stream.png"
    image = QImage(640, 640, QImage.Format.Format_RGB32)
    image.fill(QColor("#4f7fbf"))
    assert image.save(str(image_path))

    view = BubbleListView()
    view.resize(520, 260)
    view.show()
    view.show_single("正在生成图片...", True)
    view._block_refresh = True
    successful_refreshes = []
    original_refresh_layout = view.refresh_layout

    def tracked_refresh_layout(*, keep_bottom=False):
        if not view._block_refresh:
            successful_refreshes.append(bool(keep_bottom))
        return original_refresh_layout(keep_bottom=keep_bottom)

    view.refresh_layout = tracked_refresh_layout
    view.update_last_ai(f"![generated image]({image_path.as_uri()})")
    QTimer.singleShot(180, lambda: setattr(view, "_block_refresh", False))

    loop = QEventLoop()
    QTimer.singleShot(650, loop.quit)
    loop.exec()
    app.processEvents()

    image_widget = view.findChild(ChatImageWidget)
    bubble = view._last_ai
    assert successful_refreshes
    assert image_widget is not None
    assert bubble is not None
    assert bubble.minimumHeight() >= image_widget.sizeHint().height() + 20


def test_markdown_avif_data_image_shows_downloadable_placeholder():
    _app()
    bubble = ChatBubble("assistant")
    data_url = "data:image/avif;base64,AAAAIGZ0eXBhdmlmAAAAAA=="

    bubble.set_content(f"![generated image]({data_url})", is_markdown=True)

    image_widget = bubble.findChild(ChatImageWidget)
    assert image_widget is not None
    assert image_widget._mime_type == "image/avif"
    assert "下载原图" in image_widget._label.text()
    assert data_url not in bubble._label.text()


def test_chat_image_placeholder_size_hint_uses_calculated_height():
    _app()
    widget = ChatImageWidget("data:image/png;base64,not-valid-image-data", "broken")

    assert widget.sizeHint().height() == widget.height()
    assert widget.minimumSizeHint().height() == widget.height()
    assert widget.sizeHint().height() >= 34


def test_chat_image_widget_downloads_original_bytes(tmp_path, monkeypatch):
    _app()
    data_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAFgwJ/lZ7x2wAAAABJRU5ErkJggg=="
    )
    widget = ChatImageWidget(data_url, "demo")
    target = tmp_path / "downloaded.png"

    from deepcat.ui import chat_bubbles

    monkeypatch.setattr(
        chat_bubbles.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(target), "PNG"),
    )

    widget._download_image()

    assert target.read_bytes() == widget._image_bytes


def test_chat_image_overlay_buttons_show_only_while_hovering():
    app = _app()
    data_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAFgwJ/lZ7x2wAAAABJRU5ErkJggg=="
    )
    widget = ChatImageWidget(data_url, "demo")

    assert widget._overlay.isHidden()

    widget.eventFilter(widget._label, QEvent(QEvent.Type.Enter))
    assert widget._overlay.isVisible()

    widget.eventFilter(widget._label, QEvent(QEvent.Type.Leave))
    app.processEvents()

    assert widget._overlay.isHidden()


def test_chat_image_event_filter_tolerates_partial_initialization():
    _app()
    data_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAFgwJ/lZ7x2wAAAABJRU5ErkJggg=="
    )
    widget = ChatImageWidget(data_url, "demo")
    overlay = widget._overlay
    copy_btn = widget._copy_btn
    download_btn = widget._download_btn

    delattr(widget, "_overlay")
    delattr(widget, "_copy_btn")
    delattr(widget, "_download_btn")

    widget.eventFilter(widget._label, QEvent(QEvent.Type.Enter))

    widget._overlay = overlay
    widget._copy_btn = copy_btn
    widget._download_btn = download_btn


def test_chat_image_context_menu_uses_input_area_popup_style(monkeypatch):
    _app()
    widget = ChatImageWidget("data:image/png;base64,QUJDRA==", "demo")
    captured = {}

    class _FakePopup:
        def __init__(self, items, parent=None, **kwargs):
            captured["items"] = items
            captured["parent"] = parent
            captured["kwargs"] = kwargs

        def show_at_pos(self, pos):
            captured["pos"] = pos

    import deepcat.ui.post_capture_actions as post_capture_actions

    monkeypatch.setattr(post_capture_actions, "OcrGenericMenuPopup", _FakePopup)

    widget._show_context_menu(QPoint(3, 4))

    assert captured["parent"] is widget
    assert captured["items"][0][0] == "下载图片"
    assert captured["items"][0][2] is True
    assert callable(captured["items"][0][1])
    assert captured["pos"] == widget._label.mapToGlobal(QPoint(3, 4))


def test_user_bubble_context_menu_uses_custom_popup(monkeypatch):
    _app()
    bubble = ChatBubble("user")
    bubble.set_msg_index(0)
    bubble.set_content("这是问题", is_markdown=False)
    captured = {}

    class _FakePopup:
        def __init__(self, items, parent=None, **kwargs):
            captured["items"] = items
            captured["parent"] = parent
            captured["kwargs"] = kwargs

        def show_at_pos(self, pos):
            captured["pos"] = pos

    import deepcat.ui.post_capture_actions as post_capture_actions

    monkeypatch.setattr(post_capture_actions, "OcrGenericMenuPopup", _FakePopup)

    menu_pos = QPoint(11, 12)
    bubble._show_bubble_context_menu_at(menu_pos)

    assert captured["parent"] is bubble
    assert [item[0] for item in captured["items"]] == [
        "复制问题",
        "编辑问题从这里重发",
        "新建分支会话",
        "固定到上下文",
        "删除问题",
    ]
    assert all(item[2] is True for item in captured["items"])
    assert captured["pos"] == menu_pos


def test_assistant_bubble_context_menu_uses_custom_popup(monkeypatch):
    _app()
    bubble = ChatBubble("assistant")
    bubble.set_msg_index(1)
    bubble.set_content("这是回答", is_markdown=False)
    captured = {}

    class _FakePopup:
        def __init__(self, items, parent=None, **kwargs):
            captured["items"] = items
            captured["parent"] = parent
            captured["kwargs"] = kwargs

        def show_at_pos(self, pos):
            captured["pos"] = pos

    import deepcat.ui.post_capture_actions as post_capture_actions

    monkeypatch.setattr(post_capture_actions, "OcrGenericMenuPopup", _FakePopup)

    menu_pos = QPoint(13, 14)
    bubble._show_bubble_context_menu_at(menu_pos)

    assert captured["parent"] is bubble
    assert [item[0] for item in captured["items"]] == [
        "复制回答",
        "重新生成",
        "新建分支会话",
        "固定到上下文",
        "加入笔记",
        "删除回答",
    ]
    assert all(item[2] is True for item in captured["items"])
    assert captured["pos"] == menu_pos


def test_failed_assistant_bubble_context_menu_shows_recovery_actions(monkeypatch):
    _app()
    bubble = ChatBubble("assistant")
    bubble.set_msg_index(1)
    bubble.set_content("回答失败：网络超时", is_markdown=False)
    captured = {}

    class _FakePopup:
        def __init__(self, items, parent=None, **kwargs):
            captured["items"] = items
            captured["parent"] = parent
            captured["kwargs"] = kwargs

        def show_at_pos(self, pos):
            captured["pos"] = pos

    import deepcat.ui.post_capture_actions as post_capture_actions

    monkeypatch.setattr(post_capture_actions, "OcrGenericMenuPopup", _FakePopup)

    menu_pos = QPoint(15, 16)
    bubble._show_bubble_context_menu_at(menu_pos)

    assert captured["parent"] is bubble
    assert [item[0] for item in captured["items"]] == [
        "重试",
        "复制错误",
        "切换模型重试",
        "保留原问题新开一轮",
        "新建分支会话",
        "固定到上下文",
        "删除回答",
    ]
    assert all(item[2] is True for item in captured["items"])
    assert captured["pos"] == menu_pos


def test_failed_assistant_bubble_inline_actions_are_buttons_only():
    _app()
    bubble = ChatBubble("assistant")
    bubble.set_msg_index(1)
    bubble.set_content("回答失败：网络超时", is_markdown=False)

    bar = bubble._failure_action_bar
    assert bar is not None
    assert "background:transparent" in bar.styleSheet()
    assert "border:none" in bar.styleSheet()

    buttons = [child.text() for child in bar.findChildren(QToolButton)]
    assert buttons == ["重试", "换模型", "新开一轮"]
    assert "复制错误" not in buttons


def test_bubble_list_relays_image_preview_signal():
    _app()
    view = BubbleListView()
    captured = []
    view.image_preview_requested.connect(captured.append)

    view.render_messages([
        {"role": "user", "display_content": "📎 [图片: demo.png](deepcat-image-preview:img_456)"}
    ])

    assert view._bubbles
    view._bubbles[0]._handle_link_activated("deepcat-image-preview:img_456")
    assert captured == ["img_456"]


def test_bubble_list_recomputes_minimum_height_after_width_policy():
    app = _app()
    view = BubbleListView()
    view.resize(800, 500)
    view.show()

    view.render_messages(
        [
            {
                "role": "user",
                "display_content": (
                    "参考附图生成质感画风一样的图片，但是服装、场景、面貌要不同。 9：16\n"
                    "📎 [图片: pasted_image_1782356164.png](deepcat-image-preview:img_1)"
                ),
            },
            {
                "role": "assistant",
                "display_content": "回答失败：ChatGPT Web 生图完成，但未能解析到原图。（image_result_missing）",
            },
        ]
    )
    app.processEvents()

    assert len(view._bubbles) == 2
    for bubble in view._bubbles:
        assert bubble.minimumHeight() <= bubble.sizeHint().height() + 8
        assert bubble.height() <= bubble.sizeHint().height() + 16


def test_markdown_follow_up_label_emits_query():
    _app()
    bubble = ChatBubble("assistant")
    captured = []
    bubble.follow_up_requested.connect(captured.append)
    query = "详细分析一下 SpaceX 600亿美元收购 Cursor 的背后深意，以及这对 AI 编程市场有什么影响？"

    bubble.set_content(
        "对今天的哪条新闻最感兴趣？我们可以进一步聊聊：\n"
        f"深入了解 SpaceX 收购 Cursor 的技术与商业意图：{query}",
        is_markdown=True,
    )

    html = bubble._label.text()
    assert "deepcat-follow-up:" in html
    assert "深入了解 SpaceX 收购 Cursor 的技术与商业意图" in html
    assert query in html
    href = re.search(r'href="(deepcat-follow-up:[^"]+)"', html).group(1)

    bubble._handle_link_activated(href)
    assert captured == [query]


def test_markdown_follow_up_does_not_link_regular_colon_line():
    _app()
    bubble = ChatBubble("assistant")

    bubble.set_content("结论：这是普通回答，不应该变成追问链接。", is_markdown=True)

    assert "deepcat-follow-up:" not in bubble._label.text()


def test_bubble_list_relays_follow_up_signal():
    _app()
    view = BubbleListView()
    captured = []
    view.follow_up_requested.connect(captured.append)
    query = "今天《自然》杂志发表的医疗 AI 模型具体是怎么运作的？"

    view.render_messages([
        {
            "role": "assistant",
            "content": f"了解《自然》杂志发布的医疗 AI 模型细节：{query}",
        }
    ])

    assert view._bubbles
    href = re.search(r'href="(deepcat-follow-up:[^"]+)"', view._bubbles[0]._label.text()).group(1)
    view._bubbles[0]._handle_link_activated(href)
    assert captured == [query]


def test_clear_invalidates_pending_scroll_to_bottom():
    _app()
    view = BubbleListView()

    view.scroll_to_bottom()
    assert view._scroll_to_bottom_pending
    generation = view._scroll_generation

    view.clear()

    loop = QEventLoop()
    QTimer.singleShot(50, loop.quit)
    loop.exec()

    assert view._scroll_generation == generation + 1
    assert not view._scroll_to_bottom_pending
