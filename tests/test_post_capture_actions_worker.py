import json
import time
from types import SimpleNamespace

import numpy as np
from PyQt6.QtCore import QPoint, QRect, QSize, Qt

from deepcat.settings_store import AppSettings
from deepcat.ui import main_window
from deepcat.ui import chat_bubbles
from deepcat.ui import post_capture_actions
from deepcat.ui.post_capture_actions import OcrTextPanel, OcrTranslationWorker, _group_translator_model_menu_items, _translator_model_search_text
from deepcat.ui.selection_border_overlay import SelectionBorderOverlay


def _worker(**kwargs):
    cfg = {
        "model_type": "glm",
        "base_url": "http://127.0.0.1:8081",
        "model_name": "gemini-3.5-flash-thinking",
        "api_key": "123456",
    }
    cfg.update(kwargs.pop("model_config", {}))
    return OcrTranslationWorker(
        "",
        "自动检测",
        "双向",
        cfg,
        task="qa",
        messages=[{"role": "user", "content": "测试"}],
        **kwargs,
    )


def test_annotation_tool_cursors_are_custom_and_cached():
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication([])
    overlay = post_capture_actions.AnnotationCanvasOverlay.__new__(
        post_capture_actions.AnnotationCanvasOverlay
    )
    overlay._tool_cursor_cache = {}

    modes = ("rect", "pen", "marker", "eraser", "arrow", "number")
    cursors = {
        mode: post_capture_actions.AnnotationCanvasOverlay._tool_cursor(overlay, mode)
        for mode in modes
    }

    assert app is not None
    assert set(overlay._tool_cursor_cache) == set(modes)
    assert all(cursor.shape() == Qt.CursorShape.BitmapCursor for cursor in cursors.values())
    assert post_capture_actions.AnnotationCanvasOverlay._tool_cursor(overlay, "pen") is cursors["pen"]
    assert cursors["number"].hotSpot() == QPoint(16, 16)


def test_post_capture_annotation_buttons_use_hand_cursor():
    try:
        from PyQt6.QtWidgets import QApplication, QPushButton
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication([])
    dummy = SimpleNamespace(
        _btn_rect=QPushButton(),
        _btn_arrow=QPushButton(),
        _btn_pen=QPushButton(),
        _btn_marker=QPushButton(),
        _btn_eraser=QPushButton(),
    )
    plain_button = QPushButton()
    tool_buttons = (
        dummy._btn_rect,
        dummy._btn_arrow,
        dummy._btn_pen,
        dummy._btn_marker,
        dummy._btn_eraser,
    )

    assert app is not None
    for button in tool_buttons:
        cursor = post_capture_actions.PostCaptureActions._cursor_for_action_button(dummy, button)
        assert cursor == Qt.CursorShape.PointingHandCursor

    assert (
        post_capture_actions.PostCaptureActions._cursor_for_action_button(dummy, plain_button)
        == Qt.CursorShape.PointingHandCursor
    )


def test_text_annotation_editor_caret_stays_on_click_point():
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication([])
    image = SimpleNamespace(shape=(240, 320, 3))
    overlay = post_capture_actions.AnnotationCanvasOverlay(QRect(0, 0, 320, 240), image)
    click_point = QPoint(86, 94)

    post_capture_actions.AnnotationCanvasOverlay._begin_text_editor(overlay, click_point)
    app.processEvents()

    editor = overlay._text_editor
    assert editor is not None
    initial_caret_top_left = editor.mapTo(overlay, editor.cursorRect().topLeft())
    initial_caret_top_right = editor.mapTo(overlay, editor.cursorRect().topRight())
    expected_top_y = int(click_point.y() - round(float(editor.cursorRect().height()) / 2.0))
    assert abs(initial_caret_top_right.x() - click_point.x()) <= 1
    assert abs(initial_caret_top_right.y() - expected_top_y) <= 1

    editor.setText("Hi")
    editor.setCursorPosition(0)
    start_rect = editor.cursorRect()
    text_start_before_commit = editor.mapTo(
        overlay,
        QPoint(
            int(start_rect.x() + round(float(start_rect.width()) / 2.0)),
            int(start_rect.y()),
        ),
    )
    editor.setCursorPosition(len(editor.text()))
    post_capture_actions.AnnotationCanvasOverlay._commit_text_editor(overlay)

    assert len(overlay._commands) == 1
    saved_point = post_capture_actions.AnnotationCanvasOverlay._to_point(
        overlay,
        overlay._commands[0]["pos"],
        overlay.width(),
        overlay.height(),
    )
    assert abs(saved_point.x() - text_start_before_commit.x()) <= 1
    assert abs(saved_point.y() - text_start_before_commit.y()) <= 1
    overlay.close()


def test_selection_border_text_editor_caret_stays_on_click_point():
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication([])
    overlay = SelectionBorderOverlay(QRect(0, 0, 320, 240), interactive=True)
    click_point = QPoint(96, 104)

    SelectionBorderOverlay._begin_text_editor(overlay, click_point)
    app.processEvents()

    editor = overlay._text_editor
    assert editor is not None
    caret_top_left = editor.mapTo(overlay, editor.cursorRect().topLeft())
    caret_top_right = editor.mapTo(overlay, editor.cursorRect().topRight())
    expected_top_y = int(click_point.y() - round(float(editor.cursorRect().height()) / 2.0))
    assert abs(caret_top_right.x() - click_point.x()) <= 1
    assert abs(caret_top_right.y() - expected_top_y) <= 1

    editor.setText("Hi")
    editor.setCursorPosition(0)
    start_rect = editor.cursorRect()
    text_start_before_commit = editor.mapTo(
        overlay,
        QPoint(
            int(start_rect.x() + round(float(start_rect.width()) / 2.0)),
            int(start_rect.y()),
        ),
    )
    editor.setCursorPosition(len(editor.text()))
    SelectionBorderOverlay._commit_text_editor(overlay)

    assert len(overlay._annotation_commands) == 1
    saved_point = SelectionBorderOverlay._annotation_point(
        overlay,
        overlay._annotation_commands[0]["pos"],
        overlay._annotation_rect(),
    )
    assert abs(saved_point.x() - text_start_before_commit.x()) <= 1
    assert abs(saved_point.y() - text_start_before_commit.y()) <= 1
    overlay.close()


def test_selection_border_annotation_tools_use_shared_tool_cursors():
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication([])
    assert app is not None
    overlay = SelectionBorderOverlay(QRect(0, 0, 120, 80), interactive=True)
    try:
        for mode in ("rect", "pen", "marker", "eraser", "arrow"):
            overlay.set_annotation_mode(mode)
            assert overlay.cursor().shape() == Qt.CursorShape.BitmapCursor
            assert overlay.cursor().hotSpot() == QPoint(13, 13)
        overlay.set_annotation_mode("number")
        assert overlay.cursor().shape() == Qt.CursorShape.BitmapCursor
        assert overlay.cursor().hotSpot() == QPoint(16, 16)
    finally:
        overlay.close()


def test_selection_border_coalesces_rect_changed_while_dragging():
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication([])
    assert app is not None
    overlay = SelectionBorderOverlay(QRect(0, 0, 120, 80), interactive=True)
    emitted: list[QRect] = []
    overlay.rect_changed.connect(lambda rect: emitted.append(QRect(rect)))

    try:
        overlay.set_selection_rect(QRect(1, 2, 120, 80), emit_change=True)
        assert emitted == [QRect(1, 2, 120, 80)]

        overlay._drag_handle = "right"
        overlay.set_selection_rect(QRect(1, 2, 130, 80), emit_change=True)
        overlay.set_selection_rect(QRect(1, 2, 140, 80), emit_change=True)

        assert emitted == [QRect(1, 2, 120, 80)]

        overlay._flush_pending_rect_changed()
        assert emitted[-1] == QRect(1, 2, 140, 80)
        assert len(emitted) == 2
    finally:
        overlay.close()


def test_qa_falls_back_to_non_stream_when_glm_stream_is_empty():
    worker = _worker()
    calls = []

    def empty_stream(messages):
        calls.append(("stream", messages))
        return ""

    def non_stream(messages):
        calls.append(("non_stream", messages))
        return "OK"

    worker._stream_with_glm_messages = empty_stream  # type: ignore[method-assign]
    worker._chat_with_glm_messages = non_stream  # type: ignore[method-assign]

    assert worker._run_qa() == "OK"
    assert [name for name, _ in calls] == ["stream", "non_stream"]


def test_output_image_bgr_returns_none_when_image_refs_released():
    dummy = SimpleNamespace(
        _image_pending=False,
        _annotation_overlay=None,
        _image_bgr=None,
        _base_image_bgr=None,
        _on_render_annotations=lambda _image: (_ for _ in ()).throw(AssertionError("should not render without base image")),
    )

    assert post_capture_actions.PostCaptureActions._output_image_bgr(dummy) is None


def test_glm_payload_and_response_text_support_non_stream_chat_response():
    worker = _worker()

    payload = worker._glm_messages_payload([{"role": "user", "content": "测试"}], stream=False)
    assert payload["model"] == "gemini-3.5-flash-thinking"
    assert "stream" not in payload

    text = worker._extract_glm_choice_text({"choices": [{"message": {"content": "可用"}}]})
    assert text == "可用"


def test_chatgpt_web2api_payload_adds_image_friendly_upstream_timeouts():
    worker = _worker(
        model_config={
            "model_type": "chatgpt_web",
            "base_url": "http://127.0.0.1:8082",
            "model_name": "auto",
        }
    )

    payload = worker._glm_messages_payload([{"role": "user", "content": "测试"}], stream=True)

    assert payload["stream"] is True
    assert payload["upstream_bootstrap_timeout_sec"] == 10
    assert payload["upstream_warmup_timeout_sec"] == 10
    assert payload["upstream_connect_timeout_sec"] == 6
    assert payload["upstream_timeout_sec"] == 420
    assert payload["handoff_stream_timeout_sec"] == 180
    assert payload["handoff_parallel_poll_delay_sec"] == 15
    assert payload["fallback_fetch_attempts"] == 84
    assert payload["fallback_fetch_interval_sec"] == 5
    assert payload["fallback_fetch_stable_after_text_attempts"] == 3
    assert payload["localize_generated_images"] is True
    assert payload["enable_conversation_append"] is False
    assert worker._chat_timeout(60) == 420


def test_chatgpt_web2api_base_url_defaults_to_local_service():
    worker = _worker(
        model_config={
            "model_type": "chatgpt_web",
            "base_url": "",
            "model_name": "gpt-5-5-thinking",
        }
    )

    assert worker._chat_completions_base_url() == "http://127.0.0.1:8082/v1"


def test_chatgpt_web2api_start_failure_is_not_swallowed(monkeypatch):
    worker = _worker(
        model_config={
            "model_type": "chatgpt_web",
            "base_url": "http://127.0.0.1:8082",
            "model_name": "gpt-5-5-thinking",
        }
    )

    def fail_start(_cfg):
        raise RuntimeError("端口被占用")

    monkeypatch.setattr("deepcat.local_chatgpt_web2api_server.ensure_server", fail_start)

    try:
        worker._ensure_local_chatgpt_web2api_server()
    except Exception as exc:
        assert "启动本地 ChatGPT Web2API 服务失败" in str(exc)
        assert "端口被占用" in str(exc)
    else:
        raise AssertionError("本地 ChatGPT Web2API 启动失败时不应继续请求")


def test_chatgpt_web2api_network_stream_error_does_not_retry_non_stream():
    worker = _worker(
        model_config={
            "model_type": "chatgpt_web",
            "base_url": "http://127.0.0.1:8082",
            "model_name": "auto",
        }
    )
    calls = []

    def stream_fail(messages):
        calls.append("stream")
        raise RuntimeError("HTTPConnectionPool(host='127.0.0.1', port=8082): Read timed out.")

    def non_stream(messages):
        calls.append("non_stream")
        return "不应调用"

    worker._stream_with_glm_messages = stream_fail  # type: ignore[method-assign]
    worker._chat_with_glm_messages = non_stream  # type: ignore[method-assign]

    try:
        worker._run_qa()
    except RuntimeError:
        pass

    assert calls == ["stream"]


def test_openai_images_worker_forwards_message_image_attachments():
    sample_data_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN4QAAAAASUVORK5CYII="
    )
    generated_b64 = sample_data_url.split(",", 1)[1]
    worker = OcrTranslationWorker(
        "",
        "自动检测",
        "双向",
        {
            "model_type": "openai_images",
            "base_url": "http://127.0.0.1:8082",
            "model_name": "gpt-image-2",
            "api_key": "test-key",
        },
        task="qa",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "参考附件生成图片"},
                    {"type": "image_url", "image_url": {"url": sample_data_url}},
                ],
            }
        ],
    )
    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = json.dumps({"data": [{"b64_json": generated_b64}]})
        content = text.encode("utf-8")

        def raise_for_status(self):
            return None

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return FakeResponse()

    worker._request = fake_request  # type: ignore[method-assign]
    worker._save_generated_image = lambda image_bytes, prompt: "D:\\generated.png"  # type: ignore[method-assign]

    result = worker._generate_image_openai()

    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8082/v1/images/generations"
    assert captured["json"]["prompt"] == "参考附件生成图片"
    assert captured["json"]["attachments"] == [
        {"type": "image_url", "image_url": {"url": sample_data_url}}
    ]
    assert result.startswith(OcrTranslationWorker.GENERATED_IMAGE_MARKER_PREFIX)


def test_raise_for_status_extracts_openai_error_message():
    class _Response:
        content = b'{"error":{"message":"ChatGPT Web \\u4e0a\\u6e38\\u8bf7\\u6c42\\u5931\\u8d25\\uff0cHTTP 502","code":"upstream_error"}}'
        text = content.decode("utf-8")

        def raise_for_status(self):
            raise RuntimeError("502 Server Error")

    try:
        OcrTranslationWorker._raise_for_status(_Response())
    except Exception as exc:
        assert str(exc) == "ChatGPT Web 上游请求失败，HTTP 502 (upstream_error)"


def test_non_stream_glm_chat_uses_v1_for_root_openai_relay():
    worker = _worker(model_config={"base_url": "https://www.ai8.my", "model_name": "gpt-5"})
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return object()

    worker._request = fake_request  # type: ignore[method-assign]
    worker._response_json = lambda response: {"choices": [{"message": {"content": "OK"}}]}  # type: ignore[method-assign]

    assert worker._chat_with_glm_messages([{"role": "user", "content": "测试"}]) == "OK"
    assert captured["method"] == "POST"
    assert captured["url"] == "https://www.ai8.my/v1/chat/completions"
    assert captured["json"]["model"] == "gpt-5"


def test_non_stream_anthropic_chat_uses_messages_url_without_chat_suffix():
    worker = _worker(
        model_config={
            "model_type": "anthropic",
            "base_url": "https://api.anthropic.com/v1/messages",
            "model_name": "claude-3-5-sonnet-latest",
            "api_key": "anthropic-key",
        }
    )
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")
        return object()

    worker._request = fake_request  # type: ignore[method-assign]
    worker._response_json = lambda response: {"content": [{"type": "text", "text": "OK"}]}  # type: ignore[method-assign]

    assert worker._chat_with_anthropic_messages([{"role": "user", "content": "测试"}]) == "OK"
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "anthropic-key"
    assert captured["json"]["model"] == "claude-3-5-sonnet-latest"
    assert captured["json"]["messages"] == [{"role": "user", "content": "测试"}]


def test_non_stream_openai_responses_uses_responses_endpoint():
    worker = _worker(
        model_config={
            "model_type": "openai_responses",
            "base_url": "https://api.openai.com/v1/responses",
            "model_name": "gpt-5",
            "api_key": "openai-key",
        }
    )
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")
        return object()

    worker._request = fake_request  # type: ignore[method-assign]
    worker._response_json = lambda response: {"output_text": "OK"}  # type: ignore[method-assign]

    assert worker._chat_with_openai_responses_messages([{"role": "user", "content": "测试"}]) == "OK"
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["headers"]["Authorization"] == "Bearer openai-key"
    assert captured["json"]["model"] == "gpt-5"
    assert captured["json"]["input"] == [{"role": "user", "content": "测试"}]
    assert "messages" not in captured["json"]


def test_openai_responses_stream_delta_is_extracted():
    worker = _worker(model_config={"model_type": "openai_responses"})

    assert worker._openai_responses_stream_item(
        {"type": "response.output_text.delta", "delta": "OK"}
    ) == ("text", "OK")
    assert worker._openai_responses_stream_item(
        {"type": "response.reasoning_summary_text.delta", "delta": "思考"}
    ) == ("reasoning", "思考")


def test_stream_with_glm_messages_keeps_content_when_reasoning_and_content_share_chunk():
    worker = _worker(
        model_config={
            "model_type": "chatgpt_web",
            "base_url": "http://127.0.0.1:8082",
            "model_name": "gpt-5-5-thinking",
        }
    )

    class FakeSignal:
        def __init__(self):
            self.values = []

        def emit(self, value):
            self.values.append(value)

    worker.reasoning_delta = FakeSignal()
    worker.translation_delta = FakeSignal()
    worker._ensure_local_hunyuan_server = lambda: False  # type: ignore[method-assign]
    worker._ensure_local_gemini_web2api_server = lambda: False  # type: ignore[method-assign]
    worker._ensure_local_chatgpt_web2api_server = lambda: False  # type: ignore[method-assign]
    worker._release_local_hunyuan_server = lambda active: None  # type: ignore[method-assign]
    worker._release_local_gemini_web2api_server = lambda active: None  # type: ignore[method-assign]
    worker._release_local_chatgpt_web2api_server = lambda active: None  # type: ignore[method-assign]
    worker._request = lambda *args, **kwargs: object()  # type: ignore[method-assign]
    worker._iter_sse_json = lambda response: iter(
        [
            {
                "choices": [
                    {
                        "delta": {
                            "reasoning_content": "先构思气氛",
                            "content": "在时光的渡口，"
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "reasoning_content": "再补尾句",
                            "content": "与美好温柔相逢。"
                        }
                    }
                ]
            },
        ]
    )  # type: ignore[method-assign]

    result = worker._stream_with_glm_messages([{"role": "user", "content": "写一篇优美散文"}])

    assert result == "在时光的渡口，与美好温柔相逢。"
    assert worker.reasoning_delta.values == ["先构思气氛", "再补尾句"]
    assert worker.translation_delta.values == ["在时光的渡口，", "与美好温柔相逢。"]


def test_stream_with_glm_messages_merges_snapshot_chunks_without_duplicate_append():
    worker = _worker(
        model_config={
            "model_type": "chatgpt_web",
            "base_url": "http://127.0.0.1:8082",
            "model_name": "gpt-5-5-thinking",
        }
    )

    class FakeSignal:
        def __init__(self):
            self.values = []

        def emit(self, value):
            self.values.append(value)

    worker.reasoning_delta = FakeSignal()
    worker.translation_delta = FakeSignal()
    worker._ensure_local_hunyuan_server = lambda: False  # type: ignore[method-assign]
    worker._ensure_local_gemini_web2api_server = lambda: False  # type: ignore[method-assign]
    worker._ensure_local_chatgpt_web2api_server = lambda: False  # type: ignore[method-assign]
    worker._release_local_hunyuan_server = lambda active: None  # type: ignore[method-assign]
    worker._release_local_gemini_web2api_server = lambda active: None  # type: ignore[method-assign]
    worker._release_local_chatgpt_web2api_server = lambda active: None  # type: ignore[method-assign]
    worker._request = lambda *args, **kwargs: object()  # type: ignore[method-assign]
    worker._iter_sse_json = lambda response: iter(
        [
            {"choices": [{"message": {"content": "在时光的渡口"}}]},
            {"choices": [{"message": {"content": "在时光的渡口，与美好温柔相逢。"}}]},
        ]
    )  # type: ignore[method-assign]

    result = worker._stream_with_glm_messages([{"role": "user", "content": "写一篇优美散文"}])

    assert result == "在时光的渡口，与美好温柔相逢。"
    assert worker.translation_delta.values == ["在时光的渡口", "，与美好温柔相逢。"]


def test_stream_with_glm_messages_replaces_prefix_suffix_gap_snapshot():
    worker = _worker(
        model_config={
            "model_type": "chatgpt_web",
            "base_url": "http://127.0.0.1:8082",
            "model_name": "gpt-5-5-thinking",
        }
    )

    class FakeSignal:
        def __init__(self):
            self.values = []

        def emit(self, value):
            self.values.append(value)

    partial_text = "AI时代学习的利用AI放大自身能力的人。"
    full_text = (
        "AI时代学习的核心已经从“记住知识”转变为“驾驭知识”。\n\n"
        "过去：\n学习 = 获取信息 + 记忆信息\n\n"
        "现在：\n学习 = 提出问题 + 理解原理 + 利用AI解决问题\n\n"
        "真正领先的，往往是既懂专业领域、又懂如何利用AI放大自身能力的人。"
    )

    worker.reasoning_delta = FakeSignal()
    worker.translation_delta = FakeSignal()
    worker._ensure_local_hunyuan_server = lambda: False  # type: ignore[method-assign]
    worker._ensure_local_gemini_web2api_server = lambda: False  # type: ignore[method-assign]
    worker._ensure_local_chatgpt_web2api_server = lambda: False  # type: ignore[method-assign]
    worker._release_local_hunyuan_server = lambda active: None  # type: ignore[method-assign]
    worker._release_local_gemini_web2api_server = lambda active: None  # type: ignore[method-assign]
    worker._release_local_chatgpt_web2api_server = lambda active: None  # type: ignore[method-assign]
    worker._request = lambda *args, **kwargs: object()  # type: ignore[method-assign]
    worker._iter_sse_json = lambda response: iter(
        [
            {"choices": [{"message": {"content": partial_text}}]},
            {"choices": [{"message": {"content": full_text}}]},
        ]
    )  # type: ignore[method-assign]

    result = worker._stream_with_glm_messages([{"role": "user", "content": "ai时代如何学习"}])

    assert result == full_text
    assert worker.translation_delta.values == [
        partial_text,
        OcrTranslationWorker.STREAM_REPLACE_MARKER + full_text,
    ]


def test_agnes_message_stream_preserves_repeated_markdown_and_paragraph_breaks():
    worker = _worker(
        model_config={
            "model_type": "glm",
            "base_url": "https://apihub.agnes-ai.com/v1",
            "model_name": "agnes-2.0-flash",
            "api_key": "test-key",
        }
    )

    class FakeSignal:
        def __init__(self):
            self.values = []

        def emit(self, value):
            self.values.append(value)

    deltas = ["**", "《秋巷拾光》", "**", "\n\n", "第一段。", "\n\n", "第二段。"]
    worker.reasoning_delta = FakeSignal()
    worker.translation_delta = FakeSignal()
    worker._ensure_local_hunyuan_server = lambda: False  # type: ignore[method-assign]
    worker._ensure_local_gemini_web2api_server = lambda: False  # type: ignore[method-assign]
    worker._ensure_local_chatgpt_web2api_server = lambda: False  # type: ignore[method-assign]
    worker._release_local_hunyuan_server = lambda active: None  # type: ignore[method-assign]
    worker._release_local_gemini_web2api_server = lambda active: None  # type: ignore[method-assign]
    worker._release_local_chatgpt_web2api_server = lambda active: None  # type: ignore[method-assign]
    worker._request = lambda *args, **kwargs: object()  # type: ignore[method-assign]
    worker._iter_sse_json = lambda response: iter(
        [{"choices": [{"delta": {"content": delta}}]} for delta in deltas]
    )  # type: ignore[method-assign]

    result = worker._stream_with_glm_messages([{"role": "user", "content": "写一篇散文"}])

    assert result == "**《秋巷拾光》**\n\n第一段。\n\n第二段。"
    assert worker.translation_delta.values == deltas


def test_openai_compatible_delta_stream_preserves_all_boundaries_for_any_model():
    worker = _worker(
        model_config={
            "model_type": "glm",
            "base_url": "https://example.invalid/v1",
            "model_name": "any-openai-compatible-model",
            "api_key": "test-key",
        }
    )

    class FakeSignal:
        def __init__(self):
            self.values = []

        def emit(self, value):
            self.values.append(value)

    deltas = [
        "### ", "执行提供程序", "\n\n", "- ", "完成 HTP", "Hexagon ",
        "Tensor Processor", "\n", "| 版本 | 特性 |", "\n| --- | --- |",
        "\n| 1.27.0 | WebNN EP |", "\n", "LLM", "Mistral", "  repeated  ", "text",
    ]
    worker.reasoning_delta = FakeSignal()
    worker.translation_delta = FakeSignal()
    worker._ensure_local_hunyuan_server = lambda: False  # type: ignore[method-assign]
    worker._ensure_local_gemini_web2api_server = lambda: False  # type: ignore[method-assign]
    worker._ensure_local_chatgpt_web2api_server = lambda: False  # type: ignore[method-assign]
    worker._release_local_hunyuan_server = lambda active: None  # type: ignore[method-assign]
    worker._release_local_gemini_web2api_server = lambda active: None  # type: ignore[method-assign]
    worker._release_local_chatgpt_web2api_server = lambda active: None  # type: ignore[method-assign]
    worker._request = lambda *args, **kwargs: object()  # type: ignore[method-assign]
    worker._iter_sse_json = lambda response: iter(
        [{"choices": [{"delta": {"content": delta}}]} for delta in deltas]
    )  # type: ignore[method-assign]

    result = worker._stream_with_glm_messages([{"role": "user", "content": "测试"}])

    assert result == "".join(deltas).strip()
    assert worker.translation_delta.values == deltas


def test_agnes_single_prompt_stream_preserves_repeated_spaces_and_newlines():
    worker = _worker(
        model_config={
            "model_type": "glm",
            "base_url": "https://apihub.agnes-ai.com/v1",
            "model_name": "agnes-2.0-flash",
            "api_key": "test-key",
        }
    )

    class FakeSignal:
        def __init__(self):
            self.values = []

        def emit(self, value):
            self.values.append(value)

    deltas = ["标题", "\n\n", "第一段", " ", "正文", "\n\n", "第二段"]
    worker.reasoning_delta = FakeSignal()
    worker.translation_delta = FakeSignal()
    worker._ensure_local_hunyuan_server = lambda: False  # type: ignore[method-assign]
    worker._ensure_local_gemini_web2api_server = lambda: False  # type: ignore[method-assign]
    worker._ensure_local_chatgpt_web2api_server = lambda: False  # type: ignore[method-assign]
    worker._release_local_hunyuan_server = lambda active: None  # type: ignore[method-assign]
    worker._release_local_gemini_web2api_server = lambda active: None  # type: ignore[method-assign]
    worker._release_local_chatgpt_web2api_server = lambda active: None  # type: ignore[method-assign]
    worker._request = lambda *args, **kwargs: object()  # type: ignore[method-assign]
    worker._iter_sse_json = lambda response: iter(
        [{"choices": [{"delta": {"content": delta}}]} for delta in deltas]
    )  # type: ignore[method-assign]

    result = worker._stream_with_glm("写一篇散文")

    assert result == "标题\n\n第一段 正文\n\n第二段"
    assert worker.translation_delta.values == deltas


def test_iter_sse_json_uses_small_chunk_size_for_local_sse():
    worker = _worker()

    class _Response:
        def __init__(self):
            self.requested_chunk_size = None
            self.requested_decode_unicode = None

        def raise_for_status(self):
            return None

        def iter_lines(self, chunk_size=None, decode_unicode=False):
            self.requested_chunk_size = chunk_size
            self.requested_decode_unicode = decode_unicode
            return iter(
                [
                    b": keep-alive",
                    b"data: {\"choices\": [{\"delta\": {\"content\": \"A\"}}]}",
                    b"data: [DONE]",
                ]
            )

    response = _Response()
    items = list(worker._iter_sse_json(response))

    assert items == [{"choices": [{"delta": {"content": "A"}}]}]
    assert response.requested_chunk_size == 1
    assert response.requested_decode_unicode is False


def test_iter_sse_json_falls_back_when_chunk_size_is_unsupported():
    worker = _worker()

    class _Response:
        def __init__(self):
            self.calls = []

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=False):
            self.calls.append(decode_unicode)
            return iter([b"data: {\"choices\": [{\"delta\": {\"content\": \"A\"}}]}"])

    response = _Response()
    items = list(worker._iter_sse_json(response))

    assert items == [{"choices": [{"delta": {"content": "A"}}]}]
    assert response.calls == [False]


def test_merge_stream_snapshot_replaces_marker_polluted_prefix_without_duplicate_reply():
    current = (
        "下面给你整理一份今日（6月24日）国内新闻要点汇总，尽量帮你抓重点👇 \n\n"
        "🇨🇳 今日国内新闻汇总\n"
        "1️⃣ 夏季达沃斯论坛在大连举行\n"
        "世界经济论坛新领军者年会（夏季达沃斯论坛）正在辽宁大连举行。\n\n"
        "2️⃣ 中国供应链博览会（链博会）持续引关注\n"
        "在北京举办的\uE200entity\uE202[\"event\",\"中国国际"
    )
    snapshot = (
        "下面给你整理一份今日（6月24日）国内新闻要点汇总，尽量帮你抓重点👇\n\n"
        "🇨🇳 今日国内新闻汇总\n"
        "1️⃣ 夏季达沃斯论坛在大连举行\n"
        "世界经济论坛新领军者年会（夏季达沃斯论坛）正在辽宁大连举行。\n\n"
        "2️⃣ 中国供应链博览会（链博会）持续引关注\n"
        "在北京举办的中国国际供应链促进博览会上，量子科技、人形机器人、智能科研系统等集中亮相。"
    )

    merged = OcrTranslationWorker._merge_stream_snapshot(current, snapshot)
    delta = OcrTranslationWorker._stream_delta_after_accumulated(current, merged)

    assert merged == snapshot
    assert delta == "中国国际供应链促进博览会上，量子科技、人形机器人、智能科研系统等集中亮相。"


def test_response_json_reports_non_json_preview():
    class FakeResponse:
        content = b"<html>bad gateway</html>"
        text = "<html>bad gateway</html>"
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    try:
        OcrTranslationWorker._response_json(FakeResponse())
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("non-json response should raise")

    assert "不是 JSON 数据" in message
    assert "bad gateway" in message


def test_worker_treats_zh_en_mutual_translation_as_bidirectional():
    cfg = {
        "model_type": "glm",
        "base_url": "http://127.0.0.1:8081",
        "model_name": "gemini-3.5-flash-thinking",
        "api_key": "123456",
    }

    worker = OcrTranslationWorker("Hello", "自动检测", "中英互译", cfg)
    legacy_worker = OcrTranslationWorker("Hello", "自动检测", "双向", cfg)
    chinese_worker = OcrTranslationWorker("你好，世界", "自动检测", "中英互译", cfg)

    assert worker._actual_target_language() == "中文"
    assert legacy_worker._actual_target_language() == "中文"
    assert chinese_worker._actual_target_language() == "英文"


def test_model_menu_items_are_grouped_like_settings_combos():
    translator = {
        "model_configs": {
            "Google翻译": {"provider": "Google"},
            "DeepLX": {"provider": "DeepLX"},
            "gpt-5": {"provider": "AI8"},
            "gemini-3.5-flash-thinking": {"provider": "AI8"},
            "custom": {},
        }
    }

    translate_groups = _group_translator_model_menu_items(translator, "translate")
    qa_groups = _group_translator_model_menu_items(translator, "qa")

    assert ("免费翻译", ["DeepLX", "Google翻译"]) in translate_groups
    assert ("AI8", ["gemini-3.5-flash-thinking", "gpt-5"]) in translate_groups
    assert ("其它", ["custom"]) == translate_groups[-1]
    assert all("Google翻译" not in names for _, names in qa_groups)
    assert all("DeepLX" not in names for _, names in qa_groups)
    assert ("AI8", ["gemini-3.5-flash-thinking", "gpt-5"]) in qa_groups


def test_model_menu_search_text_includes_hidden_config_fields():
    search_text = _translator_model_search_text(
        "模型别名A",
        {
            "base_url": "https://api.example.com/v1",
            "model_name": "actual-model-id",
            "api_key": "secret-key-123",
            "model_type": "glm",
        },
        "AI分组",
    )

    assert "模型别名A" in search_text
    assert "https://api.example.com/v1" in search_text
    assert "actual-model-id" in search_text
    assert "secret-key-123" in search_text
    assert "AI分组" in search_text


def test_ai_panel_ime_focus_reset_only_starts_when_editor_is_empty_and_focused():
    class FakeViewport:
        def __init__(self, focused=False):
            self._focused = bool(focused)

        def hasFocus(self):
            return self._focused

    class FakeEditor:
        def __init__(self, text="", focused=True, viewport_focused=False):
            self._text = text
            self._focused = bool(focused)
            self._viewport = FakeViewport(viewport_focused)

        def toPlainText(self):
            return self._text

        def hasFocus(self):
            return self._focused

        def viewport(self):
            return self._viewport

    class FakeBubbleView:
        def __init__(self, visible=True):
            self._visible = bool(visible)

        def isVisible(self):
            return self._visible

    class FakePanel:
        _history_showing = False
        _panel_collapsed = False
        _is_collapsed = False

        def __init__(self, *, visible=True, editor=None, bubble_view=None):
            self._visible = bool(visible)
            self._editor = editor if editor is not None else FakeEditor()
            self._bubble_view = bubble_view if bubble_view is not None else FakeBubbleView()

        def isVisible(self):
            return self._visible

    assert OcrTextPanel._can_start_question_editor_ime_focus_reset(FakePanel())
    assert OcrTextPanel._can_start_question_editor_ime_focus_reset(
        FakePanel(editor=FakeEditor(focused=False, viewport_focused=True))
    )
    assert not OcrTextPanel._can_start_question_editor_ime_focus_reset(
        FakePanel(editor=FakeEditor(text="已经开始输入", focused=True))
    )
    assert not OcrTextPanel._can_start_question_editor_ime_focus_reset(
        FakePanel(editor=FakeEditor(focused=False, viewport_focused=False))
    )
    assert not OcrTextPanel._can_start_question_editor_ime_focus_reset(
        FakePanel(bubble_view=FakeBubbleView(visible=False))
    )
    assert not OcrTextPanel._can_start_question_editor_ime_focus_reset(FakePanel(visible=False))


def test_ai_panel_input_watermark_only_shows_for_empty_active_editor():
    class FakeEditor:
        def __init__(self, text=""):
            self._text = text

        def toPlainText(self):
            return self._text

    class FakePanel:
        def __init__(self, text="", *, history=False, panel_collapsed=False, collapsed=False):
            self._history_showing = bool(history)
            self._panel_collapsed = bool(panel_collapsed)
            self._is_collapsed = bool(collapsed)
            self._editor = FakeEditor(text)

    assert OcrTextPanel._should_show_input_watermark(FakePanel())
    assert not OcrTextPanel._should_show_input_watermark(FakePanel("随便聊聊"))
    assert not OcrTextPanel._should_show_input_watermark(FakePanel(" "))
    assert OcrTextPanel._should_show_input_watermark(FakePanel(history=True))
    assert not OcrTextPanel._should_show_input_watermark(FakePanel(panel_collapsed=True))
    assert not OcrTextPanel._should_show_input_watermark(FakePanel(collapsed=True))


def test_ai_dialog_runtime_config_uses_translate_and_qa_models_separately(monkeypatch):
    settings = AppSettings(
        version=6,
        autostart=False,
        auto_save=False,
        image_output_dir="",
        pdf_output_dir="",
        hotkey="",
        ui={
            "translator": {
                "translate_model": "translate-relay",
                "qa_model": "qa-relay",
                "current_model": "translate-relay",
                "model_configs": {
                    "translate-relay": {
                        "base_url": "https://translate.example.com",
                        "model_name": "translate-model-id",
                        "api_key": "t-key",
                        "model_type": "glm",
                    },
                    "qa-relay": {
                        "base_url": "https://qa.example.com",
                        "model_name": "qa-model-id",
                        "api_key": "q-key",
                        "model_type": "glm",
                    },
                },
            }
        },
    )
    monkeypatch.setattr(post_capture_actions.text_panel, "load_settings", lambda: settings)
    panel = type("DummyPanel", (), {})()

    translate_cfg, _, _ = OcrTextPanel._translator_runtime_config(panel, "translate")
    qa_cfg, _, _ = OcrTextPanel._translator_runtime_config(panel, "qa")

    assert translate_cfg["display_name"] == "translate-relay"
    assert translate_cfg["model_name"] == "translate-model-id"
    assert qa_cfg["display_name"] == "qa-relay"
    assert qa_cfg["model_name"] == "qa-model-id"


def test_ai_history_sidebar_open_state_uses_ui_settings(monkeypatch):
    settings = AppSettings(
        version=6,
        autostart=False,
        auto_save=False,
        image_output_dir="",
        pdf_output_dir="",
        hotkey="",
        ui={"ai_history_sidebar_open": True},
    )
    saved = []
    monkeypatch.setattr(post_capture_actions.text_panel, "load_settings", lambda: settings)
    monkeypatch.setattr(post_capture_actions.text_panel, "update_ui_settings", lambda **kwargs: saved.append(kwargs))

    assert OcrTextPanel._load_history_sidebar_open_from_settings() is True

    OcrTextPanel._save_history_sidebar_open_to_settings(False)

    assert saved == [{"ai_history_sidebar_open": False}]


def test_ai_panel_position_restores_only_explicit_user_drag(monkeypatch):
    settings = AppSettings(
        version=6,
        autostart=False,
        auto_save=False,
        image_output_dir="",
        pdf_output_dir="",
        hotkey="",
        ui={"qa_window_pos": [120, 160], "qa_window_pos_user_moved": False},
    )
    monkeypatch.setattr(post_capture_actions.text_panel, "load_settings", lambda: settings)

    assert OcrTextPanel._load_last_pos_from_settings() is None

    settings.ui["qa_window_pos_user_moved"] = True
    assert OcrTextPanel._load_last_pos_from_settings() == QPoint(120, 160)


def test_ai_panel_position_save_clears_untrusted_automatic_position(monkeypatch):
    saved = []
    previous_last_pos = OcrTextPanel._last_pos
    previous_user_moved = OcrTextPanel._last_pos_user_moved
    monkeypatch.setattr(post_capture_actions.text_panel, "update_ui_settings", lambda **kwargs: saved.append(kwargs))
    try:
        OcrTextPanel._last_pos = QPoint(120, 160)
        OcrTextPanel._last_pos_user_moved = False
        OcrTextPanel._save_last_pos_to_settings()

        OcrTextPanel._last_pos_user_moved = True
        OcrTextPanel._save_last_pos_to_settings()
    finally:
        OcrTextPanel._last_pos = previous_last_pos
        OcrTextPanel._last_pos_user_moved = previous_user_moved

    assert saved == [
        {"qa_window_pos": None, "qa_window_pos_user_moved": False},
        {"qa_window_pos": [120, 160], "qa_window_pos_user_moved": True},
    ]


def test_unsent_ocr_input_does_not_overwrite_existing_manual_draft():
    written = []
    panel = SimpleNamespace(
        _editor=SimpleNamespace(toPlainText=lambda: "OCR识别结果"),
        _current_input_origin="ocr",
        _pending_attachments=[],
        _current_draft_key=lambda: "new",
        _load_chat_drafts=lambda: {"new": "原有手动草稿"},
        _write_chat_drafts=lambda drafts: written.append(dict(drafts)),
    )

    OcrTextPanel._save_current_chat_draft(panel, show_feedback=False)

    assert written == []


def test_manual_input_keeps_existing_draft_behavior():
    written = []
    panel = SimpleNamespace(
        _editor=SimpleNamespace(toPlainText=lambda: "新的手动草稿"),
        _current_input_origin="manual",
        _pending_attachments=[],
        _current_draft_key=lambda: "new",
        _load_chat_drafts=lambda: {"new": "原草稿"},
        _write_chat_drafts=lambda drafts: written.append(dict(drafts)),
    )

    OcrTextPanel._save_current_chat_draft(panel, show_feedback=False)

    assert written == [{"new": "新的手动草稿"}]


def test_ai_history_sidebar_restore_opens_without_resaving(monkeypatch):
    monkeypatch.setattr(OcrTextPanel, "_load_history_sidebar_open_from_settings", staticmethod(lambda: True))
    calls = []
    panel = type("DummyPanel", (), {})()
    panel._history_only_mode = False
    panel._history_showing = False
    panel._history_sidebar = object()
    panel._show_history = lambda **kwargs: calls.append(kwargs)

    OcrTextPanel._restore_history_sidebar_state_from_settings(panel)

    assert calls == [{"persist_state": False}]

    calls.clear()
    panel._history_only_mode = True
    OcrTextPanel._restore_history_sidebar_state_from_settings(panel)
    assert calls == []


def test_ai_history_sidebar_restore_can_be_disabled_for_ocr_panel(monkeypatch):
    monkeypatch.setattr(OcrTextPanel, "_load_history_sidebar_open_from_settings", staticmethod(lambda: True))
    calls = []
    panel = type("DummyPanel", (), {})()
    panel._restore_history_sidebar_on_init = False
    panel._history_only_mode = False
    panel._history_showing = False
    panel._history_sidebar = object()
    panel._show_history = lambda **kwargs: calls.append(kwargs)

    OcrTextPanel._restore_history_sidebar_state_from_settings(panel)

    assert calls == []


def test_prepare_history_sidebar_for_initial_show_sets_full_history_state():
    calls = []

    class DummySidebar:
        def show(self):
            calls.append("sidebar_show")

        def refresh(self):
            calls.append("sidebar_refresh")

        def select_record(self, record_id):
            calls.append(("select", record_id))

    class DummyWidget:
        def hide(self):
            calls.append("hide_widget")

    class DummyButton:
        def setText(self, text):
            calls.append(("text", text))

        def setToolTip(self, text):
            calls.append(("tip", text))

    panel = type("DummyPanel", (), {})()
    panel._history_sidebar = DummySidebar()
    panel._history_showing = False
    panel._current_session_record_id = 12
    panel._watermark = DummyWidget()
    panel._translation_info = DummyWidget()
    panel._btn_history = DummyButton()
    panel._sync_history_layout_edges = lambda: calls.append("sync_edges")
    panel._ensure_history_output_area = lambda: calls.append("ensure_output")
    panel._reposition = lambda: calls.append("reposition")
    panel._sync_output_quick_action_bar_visibility = lambda: calls.append("sync_quick_actions")
    panel.setUpdatesEnabled = lambda value: calls.append(("updates", value))
    panel.update = lambda: calls.append("update")
    panel.isVisible = lambda: True
    panel.geometry = lambda: QRect(20, 30, 640, 520)

    OcrTextPanel._show_history(panel, persist_state=False)

    assert panel._history_showing is True
    assert calls == [
        ("updates", False),
        "sync_edges",
        "hide_widget",
        "hide_widget",
        "ensure_output",
        "sidebar_show",
        "sidebar_refresh",
        ("select", 12),
        "reposition",
        "sync_quick_actions",
        ("text", "历史"),
        ("tip", "收起历史记录"),
        ("updates", True),
        "update",
    ]


def test_manual_ai_panel_opens_directly_with_history_sidebar(monkeypatch):
    calls = []

    class DummySignal:
        def connect(self, callback):
            calls.append(("connect", callable(callback)))

    class FakePanel:
        destroyed = DummySignal()

        def __init__(self, *args, **kwargs):
            calls.append(("init", kwargs))
            self._restore_main_window_visibility_after_close = None

        def set_text_and_reposition(self, *args, **kwargs):
            calls.append(("set_text", args, kwargs))

        def _activate_for_text_input(self):
            calls.append("activate")

    monkeypatch.setattr(post_capture_actions, "OcrTextPanel", FakePanel)

    dummy = SimpleNamespace(
        _selection_translate_panel=None,
        _send_tray_notification=lambda *args: None,
        _install_ai_panel_close_guard=lambda panel: calls.append("guard"),
        _refresh_selection_popup_blocked=lambda: calls.append("refresh_blocked"),
    )

    main_window.MainWindow._open_selection_text_panel(dummy, "", 10, 20, "manual")

    assert ("init", {"on_toast": dummy._send_tray_notification}) in calls
    set_text_calls = [item for item in calls if item[0] == "set_text"]
    assert set_text_calls
    assert set_text_calls[0][2]["center_on_first_show"] is True
    assert dummy._selection_translate_panel is not None


def test_reset_to_initial_input_view_hides_history_and_output_area():
    calls = []

    class DummyWidget:
        def __init__(self, name):
            self.name = name
            self.hidden = False
            self.cleared = False
            self.min_h = None
            self.max_h = None

        def hide(self):
            self.hidden = True
            calls.append((self.name, "hide"))

        def clear(self):
            self.cleared = True
            calls.append((self.name, "clear"))

        def setMinimumHeight(self, value):
            self.min_h = int(value)

        def setMaximumHeight(self, value):
            self.max_h = int(value)

    class DummyButton:
        def __init__(self):
            self.text = ""
            self.tooltip = ""

        def setText(self, text):
            self.text = str(text)

        def setToolTip(self, text):
            self.tooltip = str(text)

    panel = type("DummyPanel", (), {})()
    panel._history_showing = True
    panel._history_forced_output_area = True
    panel._preserve_cleared_output_area = True
    panel._history_sidebar = DummyWidget("sidebar")
    panel._bubble_view = DummyWidget("bubble")
    panel._editor_container = DummyWidget("editor")
    panel._btn_history = DummyButton()
    panel._sync_history_layout_edges = lambda: calls.append("sync_edges")
    panel._sync_input_watermark_visibility = lambda: calls.append("sync_watermark")
    panel._sync_output_quick_action_bar_visibility = lambda: None

    OcrTextPanel._reset_to_initial_input_view(panel)

    assert panel._history_showing is False
    assert panel._history_forced_output_area is False
    assert panel._preserve_cleared_output_area is False
    assert panel._history_sidebar.hidden is True
    assert panel._bubble_view.hidden is True
    assert panel._bubble_view.cleared is True
    assert panel._bubble_view.min_h == 0
    assert panel._bubble_view.max_h == 16777215
    assert panel._editor_container.min_h == 0
    assert panel._editor_container.max_h == 16777215
    assert panel._btn_history.text == "历史"
    assert panel._btn_history.tooltip == "查看翻译/问答历史记录"
    assert "sync_edges" in calls
    assert "sync_watermark" in calls


def test_ai_dialog_translate_run_uses_translate_runtime_config():
    panel = type("DummyPanel", (), {})()
    calls = []
    current_geometry = QRect(30, 40, 640, 520)

    panel._external_reuse_action_blocked = lambda: False
    panel._translation_worker = None
    panel._translator_runtime_config = lambda purpose: calls.append(purpose) or (
        {
            "display_name": "translate-relay" if purpose == "translate" else "qa-relay",
            "base_url": "https://example.com",
            "model_name": f"{purpose}-model-id",
            "api_key": "key",
            "model_type": "glm",
        },
        False,
        "",
    )
    panel._translator_required_fields = lambda cfg: ["base_url", "model_name", "api_key"]
    panel._qa_required_fields = lambda cfg: ["base_url", "model_name", "api_key"]
    panel._set_worker_buttons_busy = lambda *args, **kwargs: None
    panel._translation_info = type("Info", (), {"hide": lambda self: None, "clear": lambda self: None})()
    panel._thinking_card = type("Thinking", (), {"start_thinking": lambda *a, **kw: None, "add_thinking_delta": lambda self, *_: None})()
    panel._source_lang = type("Combo", (), {"currentText": lambda self: "自动检测"})()
    panel._target_lang = type("Combo", (), {"currentText": lambda self: "双向"})()
    panel._chat_history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": ""}]
    panel._stream_markdown_text = ""
    panel._show_translation_error = lambda message: (_ for _ in ()).throw(AssertionError(message))
    panel._show_qa_error = lambda message: (_ for _ in ()).throw(AssertionError(message))
    panel._on_translation_delta = lambda *args, **kwargs: None
    panel._on_translation_finished = lambda *args, **kwargs: None

    class FakeSignal:
        def connect(self, callback):
            return None

    class FakeWorker:
        reasoning_delta = FakeSignal()
        translation_delta = FakeSignal()
        translation_finished = FakeSignal()
        finished = FakeSignal()

        def __init__(self, text, source_lang, target_lang, cfg, **kwargs):
            self.text = text
            self.cfg = cfg
            self.kwargs = kwargs

        def deleteLater(self):
            return None

        def start(self):
            return None

    original_worker = post_capture_actions.text_panel.OcrTranslationWorker
    post_capture_actions.text_panel.OcrTranslationWorker = FakeWorker
    try:
        OcrTextPanel._run_qa_prompt(panel, "hello", "正在翻译...", button_task="translate", result_label="翻译结果")
    finally:
        post_capture_actions.text_panel.OcrTranslationWorker = original_worker

    assert calls == ["translate"]
    assert panel._translation_task == "translate"
    assert panel._translation_worker.cfg["display_name"] == "translate-relay"
    assert panel._translation_worker.kwargs["task"] == "translate"
    assert panel._translation_worker.kwargs["messages"] is None
    assert panel._translation_worker.kwargs["enable_conversation_append"] is False


def test_ai_dialog_qa_run_enables_conversation_append():
    panel = type("DummyPanel", (), {})()
    calls = []

    panel._external_reuse_action_blocked = lambda: False
    panel._translation_worker = None
    panel._translator_runtime_config = lambda purpose: calls.append(purpose) or (
        {
            "display_name": "qa-relay",
            "base_url": "https://example.com",
            "model_name": "qa-model-id",
            "api_key": "key",
            "model_type": "glm",
        },
        False,
        "",
    )
    panel._translator_required_fields = lambda cfg: ["base_url", "model_name", "api_key"]
    panel._qa_required_fields = lambda cfg: ["base_url", "model_name", "api_key"]
    panel._set_worker_buttons_busy = lambda *args, **kwargs: None
    panel._translation_info = type("Info", (), {"hide": lambda self: None, "clear": lambda self: None})()
    panel._thinking_card = type("Thinking", (), {"start_thinking": lambda *a, **kw: None, "add_thinking_delta": lambda self, *_: None})()
    panel._source_lang = type("Combo", (), {"currentText": lambda self: "自动检测"})()
    panel._target_lang = type("Combo", (), {"currentText": lambda self: "双向"})()
    panel._chat_history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": ""}]
    panel._stream_markdown_text = ""
    panel._show_translation_error = lambda message: (_ for _ in ()).throw(AssertionError(message))
    panel._show_qa_error = lambda message: (_ for _ in ()).throw(AssertionError(message))
    panel._on_translation_delta = lambda *args, **kwargs: None
    panel._on_translation_finished = lambda *args, **kwargs: None

    class FakeSignal:
        def connect(self, callback):
            return None

    class FakeWorker:
        reasoning_delta = FakeSignal()
        translation_delta = FakeSignal()
        translation_finished = FakeSignal()
        finished = FakeSignal()

        def __init__(self, text, source_lang, target_lang, cfg, **kwargs):
            self.text = text
            self.cfg = cfg
            self.kwargs = kwargs

        def deleteLater(self):
            return None

        def start(self):
            return None

    original_worker = post_capture_actions.text_panel.OcrTranslationWorker
    post_capture_actions.text_panel.OcrTranslationWorker = FakeWorker
    try:
        OcrTextPanel._run_qa_prompt(panel, "hello", "正在回答...", button_task="qa", result_label="问答结果")
    finally:
        post_capture_actions.text_panel.OcrTranslationWorker = original_worker

    assert calls == ["qa"]
    assert panel._translation_task == "qa"
    assert panel._translation_worker.cfg["display_name"] == "qa-relay"
    assert panel._translation_worker.kwargs["task"] == "qa"
    assert panel._translation_worker.kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert panel._translation_worker.kwargs["enable_conversation_append"] is True


def test_free_google_translate_does_not_require_api_key_or_base_url():
    panel = type("DummyPanel", (), {})()
    assert OcrTextPanel._translator_required_fields(panel, {"model_type": "microsoft_free"}) == []
    assert OcrTextPanel._translator_required_fields(panel, {"model_type": "google_free"}) == []
    panel._external_reuse_action_blocked = lambda: False
    panel._translation_worker = None
    panel._translator_runtime_config = lambda purpose: (
        {"display_name": "Google翻译", "model_type": "google_free"},
        False,
        "",
    )
    panel._translator_required_fields = lambda cfg: OcrTextPanel._translator_required_fields(panel, cfg)
    panel._qa_required_fields = lambda cfg: OcrTextPanel._qa_required_fields(panel, cfg)
    panel._set_worker_buttons_busy = lambda *args, **kwargs: None
    panel._translation_info = type("Info", (), {"hide": lambda self: None, "clear": lambda self: None})()
    panel._thinking_card = type("Thinking", (), {"start_thinking": lambda *a, **kw: None, "add_thinking_delta": lambda self, *_: None})()
    panel._source_lang = type("Combo", (), {"currentText": lambda self: "自动检测"})()
    panel._target_lang = type("Combo", (), {"currentText": lambda self: "中文"})()
    panel._chat_history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": ""}]
    panel._stream_markdown_text = ""
    panel._show_translation_error = lambda message: (_ for _ in ()).throw(AssertionError(message))
    panel._show_qa_error = lambda message: (_ for _ in ()).throw(AssertionError(message))
    panel._on_translation_delta = lambda *args, **kwargs: None
    panel._on_translation_finished = lambda *args, **kwargs: None

    class FakeSignal:
        def connect(self, callback):
            return None

    class FakeWorker:
        reasoning_delta = FakeSignal()
        translation_delta = FakeSignal()
        translation_finished = FakeSignal()
        finished = FakeSignal()

        def __init__(self, text, source_lang, target_lang, cfg, **kwargs):
            self.text = text
            self.cfg = cfg
            self.kwargs = kwargs

        def deleteLater(self):
            return None

        def start(self):
            return None

    original_worker = post_capture_actions.text_panel.OcrTranslationWorker
    post_capture_actions.text_panel.OcrTranslationWorker = FakeWorker
    try:
        OcrTextPanel._run_qa_prompt(panel, "hello", "正在翻译...", button_task="translate", result_label="翻译结果")
    finally:
        post_capture_actions.text_panel.OcrTranslationWorker = original_worker

    assert panel._translation_worker.cfg["model_type"] == "google_free"
    assert panel._translation_worker.kwargs["task"] == "translate"


def test_qa_missing_config_stops_thinking_card():
    panel = type("DummyPanel", (), {})()
    calls = []
    panel._external_reuse_action_blocked = lambda: False
    panel._translation_worker = None
    panel._translator_runtime_config = lambda purpose: ({}, False, "")
    panel._qa_required_fields = lambda cfg: ["__qa_model__"]
    panel._translator_required_fields = lambda cfg: ["base_url"]
    panel._stream_flush_timer = type("Timer", (), {"stop": lambda self: calls.append("timer_stop")})()
    panel._thinking_card = type(
        "Thinking",
        (),
        {
            "stop_thinking": lambda self: calls.append("think_stop"),
            "hide": lambda self: calls.append("think_hide"),
        },
    )()
    panel._stop_dots_animation = lambda: calls.append("dots_stop")
    panel._set_worker_buttons_busy = lambda busy, **kwargs: calls.append(("busy", busy, kwargs))
    panel._show_qa_error = lambda message: calls.append(("qa_error", message))
    panel._show_translation_error = lambda message: calls.append(("translation_error", message))

    OcrTextPanel._run_qa_prompt(panel, "hello", "正在回答...", button_task="qa", result_label="问答结果")

    assert "think_stop" in calls
    assert "think_hide" in calls
    assert ("busy", False, {"task": "qa"}) in calls
    assert any(call[0] == "qa_error" and "问答模型配置错误" in call[1] for call in calls if isinstance(call, tuple))
    assert panel._translation_worker is None


def test_qa_config_read_error_stops_thinking_card():
    panel = type("DummyPanel", (), {})()
    calls = []
    panel._external_reuse_action_blocked = lambda: False
    panel._translation_worker = None

    def raise_config(_purpose):
        raise RuntimeError("settings broken")

    panel._translator_runtime_config = raise_config
    panel._stream_flush_timer = type("Timer", (), {"stop": lambda self: calls.append("timer_stop")})()
    panel._thinking_card = type(
        "Thinking",
        (),
        {
            "stop_thinking": lambda self: calls.append("think_stop"),
            "hide": lambda self: calls.append("think_hide"),
        },
    )()
    panel._stop_dots_animation = lambda: calls.append("dots_stop")
    panel._set_worker_buttons_busy = lambda busy, **kwargs: calls.append(("busy", busy, kwargs))
    panel._show_qa_error = lambda message: calls.append(("qa_error", message))
    panel._show_translation_error = lambda message: calls.append(("translation_error", message))

    OcrTextPanel._run_qa_prompt(panel, "hello", "正在回答...", button_task="qa", result_label="问答结果")

    assert "think_stop" in calls
    assert "think_hide" in calls
    assert ("busy", False, {"task": "qa"}) in calls
    assert any(call[0] == "qa_error" and "读取问答设置失败" in call[1] for call in calls if isinstance(call, tuple))
    assert panel._translation_worker is None


def test_qa_worker_start_error_stops_thinking_card(monkeypatch):
    panel = type("DummyPanel", (), {})()
    calls = []
    panel._external_reuse_action_blocked = lambda: False
    panel._translation_worker = None
    panel._translator_runtime_config = lambda purpose: (
        {
            "display_name": "qa-relay",
            "base_url": "https://example.com",
            "model_name": "qa-model-id",
            "api_key": "key",
            "model_type": "glm",
        },
        False,
        "",
    )
    panel._translator_required_fields = lambda cfg: ["base_url", "model_name", "api_key"]
    panel._qa_required_fields = lambda cfg: ["base_url", "model_name", "api_key"]
    panel._set_worker_buttons_busy = lambda busy, **kwargs: calls.append(("busy", busy, kwargs))
    panel._translation_info = type("Info", (), {"hide": lambda self: None, "clear": lambda self: None})()
    panel._stream_flush_timer = type("Timer", (), {"stop": lambda self: calls.append("timer_stop")})()
    panel._thinking_card = type(
        "Thinking",
        (),
        {
            "start_thinking": lambda self, **kwargs: calls.append(("think_start", kwargs)),
            "stop_thinking": lambda self: calls.append("think_stop"),
            "hide": lambda self: calls.append("think_hide"),
            "add_thinking_delta": lambda self, *_: None,
        },
    )()
    panel._stop_dots_animation = lambda: calls.append("dots_stop")
    panel._start_dots_animation = lambda: calls.append("dots_start")
    panel._source_lang = type("Combo", (), {"currentText": lambda self: "自动检测"})()
    panel._target_lang = type("Combo", (), {"currentText": lambda self: "双向"})()
    panel._chat_history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": ""}]
    panel._stream_markdown_text = ""
    panel._show_translation_error = lambda message: calls.append(("translation_error", message))
    panel._show_qa_error = lambda message: calls.append(("qa_error", message))
    panel._on_translation_delta = lambda *args, **kwargs: None
    panel._on_translation_finished = lambda *args, **kwargs: None

    class FakeSignal:
        def connect(self, callback):
            return None

    class FakeWorker:
        reasoning_delta = FakeSignal()
        translation_delta = FakeSignal()
        translation_finished = FakeSignal()
        finished = FakeSignal()

        def __init__(self, *args, **kwargs):
            return None

        def deleteLater(self):
            calls.append("delete_later")

        def start(self):
            raise RuntimeError("worker boom")

    monkeypatch.setattr(post_capture_actions.text_panel, "OcrTranslationWorker", FakeWorker)

    OcrTextPanel._run_qa_prompt(panel, "hello", "正在回答...", button_task="qa", result_label="问答结果")

    assert "think_stop" in calls
    assert "think_hide" in calls
    assert ("busy", False, {"task": "qa"}) in calls
    assert "delete_later" in calls
    assert any(call[0] == "qa_error" and "问答启动失败：worker boom" in call[1] for call in calls if isinstance(call, tuple))
    assert panel._translation_worker is None


def test_qa_error_updates_panel_without_message_box(monkeypatch):
    panel = type("DummyPanel", (), {})()
    messages = []
    panel._set_translation_message = lambda message: messages.append(message)

    def fail_warning(*args, **kwargs):
        raise AssertionError("问答错误不应弹出 QMessageBox")

    monkeypatch.setattr(post_capture_actions.QMessageBox, "warning", fail_warning)

    OcrTextPanel._show_qa_error(panel, "问答模型配置错误，请检查模型管理中的问答模型是否已正确选择并配置。")

    assert messages == ["问答模型配置错误，请检查模型管理中的问答模型是否已正确选择并配置。"]


def test_qa_error_preserves_question_bubble_for_retry():
    panel = type("DummyPanel", (), {})()
    panel._is_chatting = True
    panel._chat_history = [
        {"role": "user", "content": "怎么配置问答模型？", "display_content": "怎么配置问答模型？"},
        {"role": "assistant", "content": ""},
    ]
    render_calls = []
    panel._render_chat_history = lambda is_streaming=False: render_calls.append(is_streaming)
    panel._set_translation_message = lambda message: (_ for _ in ()).throw(AssertionError(message))

    OcrTextPanel._show_qa_error(panel, "问答模型配置错误，请检查模型管理中的问答模型是否已正确选择并配置。")

    assert panel._chat_history[0]["role"] == "user"
    assert panel._chat_history[0]["display_content"] == "怎么配置问答模型？"
    assert panel._chat_history[1] == {
        "role": "assistant",
        "content": "问答模型配置错误，请检查模型管理中的问答模型是否已正确选择并配置。",
        "error_message": "问答模型配置错误，请检查模型管理中的问答模型是否已正确选择并配置。",
        "failed": True,
    }
    assert render_calls == [False]


def test_translation_error_updates_panel_without_message_box(monkeypatch):
    panel = type("DummyPanel", (), {})()
    messages = []
    panel._set_translation_message = lambda message: messages.append(message)

    def fail_warning(*args, **kwargs):
        raise AssertionError("翻译错误不应弹出 QMessageBox")

    monkeypatch.setattr(post_capture_actions.QMessageBox, "warning", fail_warning)

    OcrTextPanel._show_translation_error(panel, "翻译模型配置错误，请检查模型管理中的翻译模型是否已正确选择并配置。")

    assert messages == ["翻译模型配置错误，请检查模型管理中的翻译模型是否已正确选择并配置。"]


def test_translation_error_preserves_source_bubble_for_retry():
    panel = type("DummyPanel", (), {})()
    panel._is_chatting = True
    panel._chat_history = [
        {"role": "user", "content": "hello", "display_content": "hello"},
        {"role": "assistant", "content": ""},
    ]
    render_calls = []
    panel._render_chat_history = lambda is_streaming=False: render_calls.append(is_streaming)
    panel._set_translation_message = lambda message: (_ for _ in ()).throw(AssertionError(message))

    OcrTextPanel._show_translation_error(panel, "翻译模型配置错误，请检查模型管理中的翻译模型是否已正确选择并配置。")

    assert panel._chat_history[0]["role"] == "user"
    assert panel._chat_history[0]["display_content"] == "hello"
    assert panel._chat_history[1] == {
        "role": "assistant",
        "content": "翻译模型配置错误，请检查模型管理中的翻译模型是否已正确选择并配置。",
        "error_message": "翻译模型配置错误，请检查模型管理中的翻译模型是否已正确选择并配置。",
        "failed": True,
    }
    assert render_calls == [False]


def test_translate_question_regenerate_uses_translate_runtime():
    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {
            "role": "user",
            "content": "请将以下文本翻译成中文：\nhello",
            "display_content": "hello",
            "regenerate_text": "hello",
            "task_type": "translate",
        },
        {"role": "assistant", "content": "你好", "task_type": "translate"},
    ]
    calls = []
    panel._translation_model_display = "translate-model"
    panel._render_chat_history = lambda is_streaming=False: calls.append(("render", is_streaming))
    panel._thinking_card = type("DummyThinkingCard", (), {"start_thinking": lambda *a, **kw: calls.append(("think", kw))})()
    panel._reposition = lambda: calls.append("reposition")
    panel._run_qa_prompt = lambda text, loading_text, **kwargs: calls.append(("run", text, loading_text, kwargs))
    panel._on_toast = lambda *args: calls.append(("toast", args))

    OcrTextPanel._on_bubble_regenerate(panel, 0)

    assert panel._chat_history[-1] == {
        "role": "assistant",
        "content": "",
        "task_type": "translate",
        "status_text": "正在翻译...",
    }
    assert calls[-1] == ("run", "hello", "正在翻译...", {"button_task": "translate", "result_label": "翻译结果"})


def test_qa_question_regenerate_keeps_qa_runtime():
    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "怎么配置问答模型？", "display_content": "怎么配置问答模型？", "task_type": "qa"},
        {"role": "assistant", "content": "去设置里配置", "task_type": "qa"},
    ]
    calls = []
    panel._translation_model_display = "qa-model"
    panel._render_chat_history = lambda is_streaming=False: calls.append(("render", is_streaming))
    panel._thinking_card = type("DummyThinkingCard", (), {"start_thinking": lambda *a, **kw: calls.append(("think", kw))})()
    panel._reposition = lambda: calls.append("reposition")
    panel._run_qa_prompt = lambda text, loading_text, **kwargs: calls.append(("run", text, loading_text, kwargs))
    panel._on_toast = lambda *args: calls.append(("toast", args))

    OcrTextPanel._on_bubble_regenerate(panel, 0)

    assert panel._chat_history[-1] == {
        "role": "assistant",
        "content": "",
        "task_type": "qa",
        "status_text": "正在生成回复...",
    }
    assert calls[-1] == ("run", "怎么配置问答模型？", "正在回答...", {"button_task": "qa", "result_label": "问答结果"})


def test_failed_bubble_copy_error_uses_plain_text(monkeypatch):
    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "测试", "task_type": "qa"},
        {"role": "assistant", "content": "回答失败：网络超时", "task_type": "qa"},
    ]
    copied = []
    feedback = []
    monkeypatch.setattr(post_capture_actions.text_panel, "copy_plain_text_to_clipboard", lambda text: copied.append(text) or True)
    panel._show_light_feedback = lambda *args, **kwargs: feedback.append((args, kwargs))

    OcrTextPanel._on_bubble_copy_error(panel, 1)

    assert copied == ["回答失败：网络超时"]
    assert feedback[0][0][0] == "错误信息已复制到剪贴板。"


def test_failed_bubble_new_round_keeps_failure_and_reuses_question():
    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "怎么配置问答模型？", "display_content": "怎么配置问答模型？", "task_type": "qa"},
        {"role": "assistant", "content": "回答失败：网络超时", "task_type": "qa"},
    ]
    calls = []
    panel._translation_worker = None
    panel._translation_model_display = "qa-model"
    panel._render_chat_history = lambda is_streaming=False: calls.append(("render", is_streaming))
    panel._thinking_card = type("DummyThinkingCard", (), {"start_thinking": lambda *a, **kw: calls.append(("think", kw))})()
    panel._reposition = lambda: calls.append("reposition")
    panel._run_qa_prompt = lambda text, loading_text, **kwargs: calls.append(("run", text, loading_text, kwargs))
    panel._show_panel_status = lambda *args, **kwargs: calls.append(("status", args, kwargs))

    OcrTextPanel._on_bubble_new_round_from_error(panel, 1)

    assert panel._chat_history[1]["content"] == "回答失败：网络超时"
    assert panel._chat_history[2] == {
        "role": "user",
        "content": "怎么配置问答模型？",
        "display_content": "怎么配置问答模型？",
        "task_type": "qa",
    }
    assert panel._chat_history[3]["role"] == "assistant"
    assert panel._chat_history[3]["content"] == ""
    assert panel._chat_history[3]["task_type"] == "qa"
    assert calls[-1] == ("run", "怎么配置问答模型？", "正在回答...", {"button_task": "qa", "result_label": "问答结果"})


def test_edit_from_user_loads_message_and_truncates_tail():
    class DummyEditor:
        def __init__(self):
            self.text = ""
            self.focused = False

        def setPlainText(self, text):
            self.text = str(text)

        def toPlainText(self):
            return self.text

        def setFocus(self):
            self.focused = True

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "问题一", "display_content": "问题一", "task_type": "qa"},
        {"role": "assistant", "content": "回答一", "task_type": "qa"},
        {
            "role": "user",
            "content": "问题二",
            "display_content": "问题二\n📎 [附件: demo.txt]",
            "task_type": "qa",
        },
        {"role": "assistant", "content": "回答二", "task_type": "qa"},
    ]
    panel._translation_worker = None
    panel._ima_note_search_worker = None
    panel._editor = DummyEditor()
    panel._loading_text = False
    panel._pending_attachments = []
    panel._pending_attachment_previews = []
    panel._pending_attachment_labels = []
    panel._context_warning_bar = None
    panel._context_actions_row = None
    panel._context_expand_btn = None
    panel._context_details_label = None
    panel._bubble_view = None
    panel._content_stack = None
    calls = []
    panel._save_current_chat_draft = lambda **kwargs: calls.append(("save_draft", kwargs))
    panel._sync_input_watermark_visibility = lambda: calls.append("watermark")
    panel._render_chat_history = lambda is_streaming=False: calls.append(("render", is_streaming))
    panel._reposition = lambda: calls.append("reposition")
    panel._show_panel_status = lambda *args, **kwargs: calls.append(("status", args, kwargs))

    OcrTextPanel._on_bubble_edit_from(panel, 2)

    assert panel._chat_history == [
        {"role": "user", "content": "问题一", "display_content": "问题一", "task_type": "qa"},
        {"role": "assistant", "content": "回答一", "task_type": "qa"},
    ]
    assert panel._editor.text == "问题二"
    assert panel._editor.focused is True
    assert panel._is_chatting is True


def test_branch_from_message_starts_new_session():
    class DummyHistorySidebar:
        def __init__(self):
            self.selected = "old"

        def select_record(self, record_id):
            self.selected = record_id

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "问题一", "task_type": "qa"},
        {"role": "assistant", "content": "回答一", "task_type": "qa"},
        {"role": "user", "content": "问题二", "task_type": "qa"},
        {"role": "assistant", "content": "回答二", "task_type": "qa"},
    ]
    panel._current_session_record_id = 42
    panel._translation_worker = None
    panel._ima_note_search_worker = None
    panel._history_sidebar = DummyHistorySidebar()
    panel._context_warning_bar = None
    panel._context_actions_row = None
    panel._context_expand_btn = None
    panel._context_details_label = None
    panel._bubble_view = None
    panel._content_stack = None
    panel._btn_qa = SimpleNamespace(setText=lambda _text: None, setToolTip=lambda _text: None)
    calls = []
    panel._save_current_chat_draft = lambda **kwargs: calls.append(("save_draft", kwargs))
    panel._discard_worker_for_clear_chat = lambda: calls.append("discard")
    panel._clear_editor_silently = lambda: calls.append("clear_editor")
    panel._clear_pending_attachments = lambda: calls.append("clear_attachments")
    panel._render_chat_history = lambda is_streaming=False: calls.append(("render", is_streaming))
    panel._reposition = lambda: calls.append("reposition")
    panel._show_panel_status = lambda *args, **kwargs: calls.append(("status", args, kwargs))

    OcrTextPanel._on_bubble_branch_from(panel, 1)

    assert panel._chat_history == [
        {"role": "user", "content": "问题一", "task_type": "qa"},
        {"role": "assistant", "content": "回答一", "task_type": "qa"},
    ]
    assert panel._current_session_record_id is None
    assert panel._history_sidebar.selected is None
    assert panel._is_chatting is True


def test_toggle_pin_context_marks_message_and_updates_estimate_state():
    class DummyEditor:
        def toPlainText(self):
            return ""

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [{"role": "user", "content": "必须携带", "task_type": "qa"}]
    panel._translation_worker = None
    panel._ima_note_search_worker = None
    panel._editor = DummyEditor()
    panel._context_warning_bar = None
    panel._context_warning_label = None
    panel._context_actions_row = None
    panel._context_expand_btn = None
    panel._context_details_label = None
    panel._bubble_view = None
    panel._content_stack = None
    panel._pending_attachments = []
    panel._context_recent_round_limit = None
    panel._context_summary_text = ""
    panel._context_summary_anchor_index = 0
    calls = []
    panel._render_chat_history = lambda is_streaming=False: calls.append(("render", is_streaming))
    panel._reposition = lambda: calls.append("reposition")
    panel._show_panel_status = lambda *args, **kwargs: calls.append(("status", args, kwargs))

    OcrTextPanel._on_bubble_pin_context(panel, 0)

    assert panel._chat_history[0]["context_pinned"] is True
    snapshot = OcrTextPanel._context_management_snapshot(panel)
    assert snapshot["pinned_count"] == 1
    assert "固定 1 条" in snapshot["scope_text"]

    OcrTextPanel._on_bubble_pin_context(panel, 0)
    assert "context_pinned" not in panel._chat_history[0]


def test_bubble_delete_removes_target_without_full_render():
    class DummyBubbleView:
        def __init__(self):
            self.removed = []

        def remove_message_at(self, index, *, keep_bottom=False):
            self.removed.append((index, keep_bottom))
            return True

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "问题一"},
        {"role": "assistant", "content": "回答一"},
        {"role": "user", "content": "问题二"},
    ]
    calls = []
    panel._stream_render_signature = ("old",)
    panel._bubble_view = DummyBubbleView()
    panel._render_chat_history = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("删除单条不应全量重绘"))
    panel._clear_chat_history = lambda: calls.append("clear")
    panel._reposition = lambda: calls.append("reposition")
    panel._show_panel_status = lambda *args, **kwargs: calls.append(("status", args, kwargs))

    OcrTextPanel._on_bubble_delete(panel, 1)

    assert panel._chat_history == [
        {"role": "user", "content": "问题一"},
        {"role": "user", "content": "问题二"},
    ]
    assert panel._bubble_view.removed == [(1, False)]
    assert panel._stream_render_signature is None
    assert "reposition" in calls


def test_regenerate_user_appends_assistant_without_full_render():
    class DummyBubbleView:
        def __init__(self):
            self.appended = []

        def append_messages(self, history, *, start_index, keep_bottom=False):
            self.appended.append((list(history), start_index, keep_bottom))
            return True

        def show(self):
            pass

        def scroll_to_bottom_now(self):
            pass

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "怎么配置问答模型？", "display_content": "怎么配置问答模型？", "task_type": "qa"},
        {"role": "assistant", "content": "去设置里配置", "task_type": "qa"},
    ]
    calls = []
    panel._translation_model_display = "qa-model"
    panel._bubble_view = DummyBubbleView()
    panel._render_chat_history = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("重试问题不应全量重绘"))
    panel._sync_title_bar_visibility = lambda: calls.append("sync_title")
    panel._thinking_card = type("DummyThinkingCard", (), {"start_thinking": lambda *a, **kw: calls.append(("think", kw))})()
    panel._reposition = lambda: calls.append("reposition")
    panel._run_qa_prompt = lambda text, loading_text, **kwargs: calls.append(("run", text, loading_text, kwargs))
    panel._show_panel_status = lambda *args, **kwargs: calls.append(("status", args, kwargs))

    OcrTextPanel._on_bubble_regenerate(panel, 0)

    assert panel._bubble_view.appended[0][1:] == (2, True)
    assert panel._chat_history[-1]["role"] == "assistant"
    assert panel._chat_history[-1]["status_text"] == "正在生成回复..."
    assert calls[-1] == ("run", "怎么配置问答模型？", "正在回答...", {"button_task": "qa", "result_label": "问答结果"})


def test_regenerate_assistant_replaces_target_without_full_render():
    class DummyBubbleView:
        def __init__(self):
            self.replaced = []

        def replace_message_at(self, index, msg, *, keep_bottom=False):
            self.replaced.append((index, dict(msg), keep_bottom))
            return True

        def show(self):
            pass

        def scroll_to_bottom_now(self):
            pass

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "问题一", "task_type": "qa"},
        {"role": "assistant", "content": "回答一", "task_type": "qa"},
        {"role": "user", "content": "问题二", "task_type": "qa"},
        {"role": "assistant", "content": "回答二失败", "task_type": "qa"},
        {"role": "user", "content": "后续问题", "task_type": "qa"},
    ]
    calls = []
    panel._translation_model_display = "qa-model"
    panel._bubble_view = DummyBubbleView()
    panel._render_chat_history = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("重试回答不应全量重绘"))
    panel._sync_title_bar_visibility = lambda: calls.append("sync_title")
    panel._thinking_card = type("DummyThinkingCard", (), {"start_thinking": lambda *a, **kw: calls.append(("think", kw))})()
    panel._reposition = lambda: calls.append("reposition")
    panel._run_qa_prompt = lambda text, loading_text, **kwargs: calls.append(("run", text, loading_text, kwargs))
    panel._show_panel_status = lambda *args, **kwargs: calls.append(("status", args, kwargs))

    OcrTextPanel._on_bubble_regenerate(panel, 3)

    assert panel._bubble_view.replaced == [
        (3, {"role": "assistant", "content": "", "task_type": "qa", "status_text": "正在生成回复..."}, True)
    ]
    assert panel._chat_history == [
        {"role": "user", "content": "问题一", "task_type": "qa"},
        {"role": "assistant", "content": "回答一", "task_type": "qa"},
        {"role": "user", "content": "问题二", "task_type": "qa"},
        {"role": "assistant", "content": "", "task_type": "qa", "status_text": "正在生成回复..."},
        {"role": "user", "content": "后续问题", "task_type": "qa"},
    ]
    assert panel._active_assistant_msg_index == 3
    assert calls[-1] == ("run", "问题二", "正在回答...", {"button_task": "qa", "result_label": "问答结果"})


def test_regenerate_failed_assistant_replaces_target_without_truncating_history():
    class DummyBubbleView:
        def __init__(self):
            self.replaced = []

        def replace_message_at(self, index, msg, *, keep_bottom=False):
            self.replaced.append((index, dict(msg), keep_bottom))
            return True

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "问题一", "task_type": "qa"},
        {"role": "assistant", "content": "回答一", "task_type": "qa"},
        {"role": "user", "content": "问题二", "task_type": "qa"},
        {"role": "assistant", "content": "回答失败：网络超时", "task_type": "qa", "failed": True},
        {"role": "user", "content": "问题三", "task_type": "qa"},
    ]
    calls = []
    panel._translation_model_display = "qa-model"
    panel._bubble_view = DummyBubbleView()
    panel._render_chat_history = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("失败气泡重试不应全量重绘"))
    panel._sync_title_bar_visibility = lambda: calls.append("sync_title")
    panel._thinking_card = type("DummyThinkingCard", (), {"start_thinking": lambda *a, **kw: calls.append(("think", kw))})()
    panel._reposition = lambda: calls.append("reposition")
    panel._run_qa_prompt = lambda text, loading_text, **kwargs: calls.append(("run", text, loading_text, kwargs))
    panel._show_panel_status = lambda *args, **kwargs: calls.append(("status", args, kwargs))

    OcrTextPanel._on_bubble_regenerate(panel, 3)

    assert len(panel._chat_history) == 5
    assert panel._chat_history[2] == {"role": "user", "content": "问题二", "task_type": "qa"}
    assert panel._chat_history[3] == {
        "role": "assistant",
        "content": "",
        "task_type": "qa",
        "status_text": "正在生成回复...",
    }
    assert panel._chat_history[4] == {"role": "user", "content": "问题三", "task_type": "qa"}
    assert panel._active_assistant_msg_index == 3
    assert panel._bubble_view.replaced[0][0] == 3
    assert calls[-1] == ("run", "问题二", "正在回答...", {"button_task": "qa", "result_label": "问答结果"})


def test_failed_stream_records_partial_answer_and_keep_partial():
    class DummyThinkingCard:
        def __init__(self):
            self.hidden = False

        def stop_thinking(self):
            pass

        def hide(self):
            self.hidden = True

    class DummyBubbleView:
        def __init__(self):
            self.updated = []

        def update_message_at(self, index, text, **kwargs):
            self.updated.append((index, text, kwargs))
            return True

        def update_last_ai(self, text, **kwargs):
            self.updated.append(("last", text, kwargs))

        def show(self):
            pass

        def scroll_to_bottom(self):
            pass

    panel = type("DummyPanel", (), {})()
    panel.sender = lambda: None
    panel._aborted_worker_ids = set()
    panel._translation_worker = None
    panel._stream_flush_timer = type("Timer", (), {"stop": lambda self: None})()
    panel._geometry_frozen = False
    panel._thinking_card = DummyThinkingCard()
    panel._stop_dots_animation = lambda: None
    panel._set_worker_buttons_busy = lambda *args, **kwargs: None
    panel._translation_task = "qa"
    panel._translation_started_ts = 1000.0
    panel._translation_model_display = "qa-model"
    panel._translation_info = type("Info", (), {"hide": lambda self: None})()
    panel._is_chatting = True
    panel._chat_history = [
        {"role": "user", "content": "问题", "task_type": "qa"},
        {"role": "assistant", "content": "已有部分", "task_type": "qa"},
        {"role": "user", "content": "后续问题", "task_type": "qa"},
    ]
    panel._active_assistant_msg_index = 1
    panel._stream_markdown_text = "已有部分"
    panel._bubble_view = DummyBubbleView()
    panel._reposition = lambda: None
    panel._schedule_question_editor_ime_focus_reset = lambda: None
    panel._save_to_history = lambda *args, **kwargs: None
    panel._render_chat_history = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("部分失败应局部更新气泡"))
    panel._show_qa_error = lambda message: (_ for _ in ()).throw(AssertionError(message))

    OcrTextPanel._on_translation_finished(panel, "", False, "网络超时")

    msg = panel._chat_history[1]
    assert msg["failed"] is True
    assert msg["can_continue"] is True
    assert msg["partial_content"] == "已有部分"
    assert msg["error_message"] == "网络超时"
    assert "已接收" in msg["content"]
    assert panel._bubble_view.updated[-1][0] == 1

    statuses = []
    panel._show_panel_status = lambda *args, **kwargs: statuses.append((args, kwargs))
    OcrTextPanel._on_bubble_keep_partial(panel, 1)

    assert panel._chat_history[1]["content"] == "已有部分"
    assert "partial_content" not in panel._chat_history[1]
    assert "failed" not in panel._chat_history[1]
    assert panel._bubble_view.updated[-1][1] == "已有部分"


def test_successful_chat_finish_uses_immediate_bottom_scroll_without_extra_refresh():
    calls = []

    class DummyTimer:
        def stop(self):
            calls.append("timer_stop")

    class DummyThinkingCard:
        def __init__(self):
            self.visible = False

        def stop_thinking(self):
            calls.append("stop_thinking")

        def isVisible(self):
            return self.visible

    class DummyInfo:
        def hide(self):
            calls.append("info_hide")

        def setText(self, text):
            calls.append(("info_text", text))

        def show(self):
            calls.append("info_show")

    class DummyBubbleView:
        def __init__(self):
            self.updated = []

        def update_last_ai(self, text, **kwargs):
            self.updated.append((text, kwargs))
            calls.append(("update_last", text, kwargs))

        def show(self):
            calls.append("show")

        def refresh_layout(self, **_kwargs):
            raise AssertionError("成功完成态不应额外全量刷新气泡列表")

        def scroll_to_bottom_now(self):
            calls.append("scroll_now")

    panel = type("DummyPanel", (), {})()
    panel.sender = lambda: None
    panel._aborted_worker_ids = set()
    panel._translation_worker = object()
    panel._stream_flush_timer = DummyTimer()
    panel._geometry_frozen = False
    panel._thinking_card = DummyThinkingCard()
    panel._stop_dots_animation = lambda: calls.append("stop_dots")
    panel._set_worker_buttons_busy = lambda *args, **kwargs: calls.append(("busy", args, kwargs))
    panel._translation_task = "qa"
    panel._translation_started_ts = 1000.0
    panel._translation_model_display = "qa-model"
    panel._translation_info = DummyInfo()
    panel._is_chatting = True
    panel._history_showing = False
    panel._chat_history = [
        {"role": "user", "content": "问题", "task_type": "qa"},
        {"role": "assistant", "content": "", "task_type": "qa"},
    ]
    panel._active_assistant_msg_index = 1
    panel._bubble_view = DummyBubbleView()
    panel._is_append_action = False
    panel._translation_result_label = "问答结果"
    panel._estimate_tokens = lambda text: max(1, len(str(text or "")))
    panel._materialize_generated_images = lambda text: text
    panel._append_ai_reply_to_note = lambda *_args, **_kwargs: None
    panel._finalize_pending_context_summary = lambda *_args, **_kwargs: calls.append("finalize_context")
    panel._copy_finished_result = lambda *args, **kwargs: calls.append(("copy", args, kwargs))
    panel._save_to_history = lambda *args, **kwargs: calls.append(("save", args, kwargs))
    panel._update_translation_info_pos = lambda: calls.append("info_pos")
    panel._schedule_question_editor_ime_focus_reset = lambda: calls.append("ime_reset")
    panel._reposition = lambda: calls.append("reposition")
    panel._render_chat_history = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("完成态应局部更新气泡"))

    OcrTextPanel._on_translation_finished(panel, "最终回答", True, "")

    assert panel._bubble_view.updated[-1][0] == "最终回答"
    status_index = next(i for i, item in enumerate(calls) if isinstance(item, tuple) and item[0] == "info_text")
    update_index = next(i for i, item in enumerate(calls) if isinstance(item, tuple) and item[0] == "update_last")
    assert status_index < update_index
    assert calls.index("reposition") < calls.index("scroll_now")
    assert "ime_reset" in calls
    assert panel._translation_worker is None


def test_successful_chat_finish_skips_reposition_when_stream_text_is_final():
    calls = []

    class DummyTimer:
        def stop(self):
            pass

    class DummyThinkingCard:
        def stop_thinking(self):
            pass

        def isVisible(self):
            return False

    class DummyInfo:
        def hide(self):
            pass

        def setText(self, _text):
            pass

        def show(self):
            pass

    class DummyBubbleView:
        def finalize_message_at(self, index, **kwargs):
            calls.append(("finalize", index, kwargs))
            return True

        def update_last_ai(self, text, **kwargs):
            calls.append(("update_last", text, kwargs))

        def show(self):
            calls.append("show")

        def scroll_to_bottom_now(self):
            calls.append("scroll_now")

    panel = type("DummyPanel", (), {})()
    panel.sender = lambda: None
    panel._aborted_worker_ids = set()
    panel._translation_worker = object()
    panel._stream_flush_timer = DummyTimer()
    panel._geometry_frozen = False
    panel._thinking_card = DummyThinkingCard()
    panel._stop_dots_animation = lambda: None
    panel._set_worker_buttons_busy = lambda *args, **kwargs: None
    panel._translation_task = "qa"
    panel._translation_started_ts = 1000.0
    panel._translation_model_display = "qa-model"
    panel._translation_info = DummyInfo()
    panel._is_chatting = True
    panel._history_showing = False
    panel._chat_history = [
        {"role": "user", "content": "问题", "task_type": "qa"},
        {"role": "assistant", "content": "最终回答", "task_type": "qa"},
    ]
    panel._active_assistant_msg_index = 1
    panel._bubble_view = DummyBubbleView()
    panel._is_append_action = False
    panel._translation_result_label = "问答结果"
    panel._estimate_tokens = lambda text: max(1, len(str(text or "")))
    panel._materialize_generated_images = lambda text: text
    panel._append_ai_reply_to_note = lambda *_args, **_kwargs: None
    panel._finalize_pending_context_summary = lambda *_args, **_kwargs: None
    panel._copy_finished_result = lambda *args, **kwargs: None
    panel._save_to_history = lambda *args, **kwargs: None
    panel._update_translation_info_pos = lambda: None
    panel._schedule_question_editor_ime_focus_reset = lambda: None
    panel._reposition = lambda: calls.append("reposition")
    panel._render_chat_history = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("完成态应局部更新气泡"))

    OcrTextPanel._on_translation_finished(panel, "最终回答", True, "")

    assert any(isinstance(item, tuple) and item[0] == "finalize" and item[1] == 1 for item in calls)
    assert not any(isinstance(item, tuple) and item[0] == "update_last" for item in calls)
    assert "reposition" not in calls
    assert "scroll_now" in calls


def test_flush_stream_markdown_updates_thinking_usage_live(monkeypatch):
    calls = []

    class DummyThinkingCard:
        def isVisible(self):
            return True

        def set_model_and_elapsed(self, model, elapsed, tokens_str=""):
            calls.append(("thinking_usage", model, elapsed, tokens_str))

    class DummyInfo:
        def hide(self):
            calls.append("info_hide")

    class DummyBubbleView:
        def update_last_ai(self, text, **kwargs):
            calls.append(("update_last", text, kwargs))

    panel = type("DummyPanel", (), {})()
    panel._translation_task = "qa"
    panel._stream_markdown_text = "回答内容"
    panel._translation_started_ts = 1000.0
    panel._translation_model_display = "qa-model"
    panel._thinking_card = DummyThinkingCard()
    panel._translation_info = DummyInfo()
    panel._is_chatting = True
    panel._chat_history = [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": ""},
    ]
    panel._bubble_view = DummyBubbleView()
    panel._estimate_tokens = lambda text: len(str(text or ""))
    monkeypatch.setattr(post_capture_actions.time, "time", lambda: 1002.5)

    OcrTextPanel._flush_stream_markdown(panel)

    assert ("thinking_usage", "qa-model", 2.5, " | tokens 4/6") in calls
    assert "info_hide" in calls
    update = next(item for item in calls if isinstance(item, tuple) and item[0] == "update_last")
    assert update[2]["reply_tokens"] == 4
    assert update[2]["total_tokens"] == 6


def test_stream_flush_interval_is_fast_inside_open_code_fence():
    panel = type("DummyPanel", (), {})()
    panel._stream_markdown_text = "说明\n```python\nprint(1)"

    assert OcrTextPanel._stream_markdown_has_open_code_fence(panel._stream_markdown_text) is True
    assert OcrTextPanel._stream_flush_interval_ms(panel) == 130

    panel._stream_markdown_text = "说明\n```python\nprint(1)\n```"

    assert OcrTextPanel._stream_markdown_has_open_code_fence(panel._stream_markdown_text) is False
    assert OcrTextPanel._stream_flush_interval_ms(panel) == 140


def test_translation_delta_open_code_fence_schedules_throttled_flush(monkeypatch):
    calls = []

    class DummyThinkingCard:
        def is_thinking(self):
            return False

    class DummyTimer:
        def __init__(self):
            self.active = False

        def stop(self):
            self.active = False
            calls.append("timer_stop")

        def isActive(self):
            calls.append(("timer_active", self.active))
            return self.active

        def start(self, interval):
            calls.append(("timer_start", int(interval)))

    class DummyQTimer:
        @staticmethod
        def singleShot(delay, callback):
            calls.append(("single_shot", int(delay)))
            callback()

    panel = type("DummyPanel", (), {})()
    panel.sender = lambda: None
    panel._aborted_worker_ids = set()
    panel._translation_task = "qa"
    panel._thinking_card = DummyThinkingCard()
    panel._stream_markdown_text = ""
    panel._stream_flush_timer = DummyTimer()
    panel._flush_stream_markdown = lambda: calls.append(("flush", panel._stream_markdown_text))
    monkeypatch.setattr(post_capture_actions.text_panel, "QTimer", DummyQTimer)

    OcrTextPanel._on_translation_delta(panel, "```python\nprint(1)")

    assert "timer_stop" not in calls
    assert ("single_shot", 0) not in calls
    assert ("flush", "```python\nprint(1)") not in calls
    assert ("timer_start", 130) in calls


def test_streaming_update_last_ai_uses_pre_update_bottom_state():
    calls = []

    class DummyTimer:
        def stop(self):
            calls.append("timer_stop")

    class DummyLayout:
        def invalidate(self):
            calls.append("layout_invalidate")

        def activate(self):
            calls.append("layout_activate")

    class DummyContainer:
        def updateGeometry(self):
            calls.append("container_geometry")

    class DummyScrollBar:
        def maximum(self):
            return 320

        def setValue(self, value):
            calls.append(("scroll", int(value)))

    class DummyBubble:
        def __init__(self):
            self._is_streaming = False

        def set_content(self, text, *, is_markdown, streaming=False):
            calls.append(("set_content", text, bool(is_markdown), bool(streaming)))
            self._is_streaming = bool(streaming)
            view.at_bottom = False
            return True

        def set_metadata(self, **kwargs):
            calls.append(("metadata", kwargs))

    class DummyView:
        def __init__(self):
            self._last_ai = DummyBubble()
            self.at_bottom = True
            self._layout_flush_timer = DummyTimer()
            self._pending_keep_bottom = False
            self._vbox = DummyLayout()
            self._container = DummyContainer()
            self._scroll_bar = DummyScrollBar()
            self.updates_enabled = True

        def is_at_bottom(self, tolerance=4):
            calls.append(("is_at_bottom", int(tolerance), self.at_bottom))
            return self.at_bottom

        def _max_bubble_width(self):
            return 620

        def viewport(self):
            return None

        def updatesEnabled(self):
            return self.updates_enabled

        def setUpdatesEnabled(self, enabled):
            self.updates_enabled = bool(enabled)
            calls.append(("updates", bool(enabled)))

        def width(self):
            return 640

        def _update_bubble_stretch_and_policy(self, bubble, viewport_width, max_width):
            calls.append(("stretch", viewport_width, max_width, bubble is self._last_ai))

        def _schedule_layout_refresh(self, *, keep_bottom=False, delay_ms=0):
            calls.append(("schedule", bool(keep_bottom), int(delay_ms)))

        def _record_stream_perf(self, metric):
            calls.append(("perf", metric))

        def verticalScrollBar(self):
            return self._scroll_bar

        def _update_conversation_nav(self):
            calls.append("nav")

    view = DummyView()

    chat_bubbles.BubbleListView.update_last_ai(
        view,
        "```python\nprint(1)",
        streaming=True,
    )

    assert ("is_at_bottom", 48, True) in calls
    assert ("schedule", True, 16) not in calls
    assert ("updates", False) in calls
    assert ("updates", True) in calls
    assert "layout_activate" in calls
    assert ("scroll", 320) in calls

    calls.clear()
    view.at_bottom = True
    chat_bubbles.BubbleListView.update_last_ai(view, "最终回答", streaming=False)

    assert ("updates", False) in calls
    assert ("updates", True) in calls
    assert not any(isinstance(call, tuple) and call[0] == "schedule" for call in calls)


def test_streamed_assistant_bubble_keeps_stable_width_until_reused_as_placeholder():
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication([])
    bubble = chat_bubbles.ChatBubble("assistant")

    bubble.set_content("短回答", is_markdown=True, streaming=True)
    bubble.set_content("短回答完成", is_markdown=True, streaming=False)

    assert app is not None
    assert bubble._stream_width_locked is True

    bubble.set_placeholder("正在思考...")

    assert bubble._stream_width_locked is False


def _drag_select_from_bubble_left_padding(bubble, label, *, drag=True):
    from PyQt6.QtCore import QEvent, QPoint, QPointF
    from PyQt6.QtGui import QMouseEvent

    label_top_left = label.mapTo(bubble._bubble_box, QPoint(0, 0))
    start_local = QPoint(max(1, label_top_left.x() - 5), label_top_left.y() + max(1, label.height() // 2))
    end_local = label.mapTo(
        bubble._bubble_box,
        QPoint(min(max(12, label.width() // 2), max(12, label.width() - 1)), max(1, label.height() // 2)),
    )
    start_global = bubble._bubble_box.mapToGlobal(start_local)
    end_global = bubble._bubble_box.mapToGlobal(end_local)

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(start_local),
        QPointF(start_global),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert bubble.eventFilter(bubble._bubble_box, press) is True

    if drag:
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(end_local),
            QPointF(end_global),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        assert bubble.eventFilter(bubble._bubble_box, move) is True

    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(end_local if drag else start_local),
        QPointF(end_global if drag else start_global),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert bubble.eventFilter(bubble._bubble_box, release) is True


def test_assistant_text_can_be_selected_from_left_padding():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    bubble = chat_bubbles.ChatBubble("assistant")
    bubble.set_content("从第一个字开始选择这段回答内容", is_markdown=True)
    bubble.resize(420, bubble.sizeHint().height())
    bubble.show()
    app.processEvents()

    _drag_select_from_bubble_left_padding(bubble, bubble._label)

    assert bubble._label.selectedText().startswith("从")
    bubble.close()


def test_clicking_left_padding_without_drag_does_not_select_text():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    bubble = chat_bubbles.ChatBubble("assistant")
    bubble.set_content("空白处单击不应该选择文字", is_markdown=True)
    bubble.resize(420, bubble.sizeHint().height())
    bubble.show()
    app.processEvents()

    _drag_select_from_bubble_left_padding(bubble, bubble._label, drag=False)

    assert bubble._label.selectedText() == ""
    bubble.close()


def test_left_padding_selection_targets_matching_markdown_segment():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    bubble = chat_bubbles.ChatBubble("assistant")
    bubble.set_content(
        "第一段说明\n\n```python\nprint('demo')\n```\n\n第二段正文可以单独选择",
        is_markdown=True,
    )
    bubble.resize(420, bubble.sizeHint().height())
    bubble.show()
    app.processEvents()
    labels = [
        widget
        for kind, widget in bubble._rendered_segment_widgets
        if kind == "markdown" and isinstance(widget, chat_bubbles.SelectableAutoScrollLabel)
    ]

    assert len(labels) >= 2
    target_label = labels[-1]
    _drag_select_from_bubble_left_padding(bubble, target_label)

    assert target_label.selectedText().startswith("第二")
    assert labels[0].selectedText() == ""
    bubble.close()


def test_assistant_text_can_be_selected_from_list_outer_left_margin():
    from PyQt6.QtCore import QEvent, QPoint, QPointF
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    view = chat_bubbles.BubbleListView()
    view.resize(520, 260)
    view.render_messages([{"role": "assistant", "content": "从输出区外侧留白开始选择文字"}])
    view.show()
    app.processEvents()
    bubble = view._bubbles[0]
    label = bubble._label
    label_top = label.mapTo(view._container, QPoint(0, 0))
    bubble_left = bubble.mapTo(view._container, QPoint(0, 0)).x()
    start_container = QPoint(max(0, bubble_left - 5), label_top.y() + max(1, label.height() // 2))
    start_global = view._container.mapToGlobal(start_container)
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(start_container),
        QPointF(start_global),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    assert view.eventFilter(view._container, press) is True

    end_global = label.mapToGlobal(QPoint(min(100, max(12, label.width() - 1)), max(1, label.height() // 2)))
    end_box = bubble._bubble_box.mapFromGlobal(end_global)
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(end_box),
        QPointF(end_global),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(end_box),
        QPointF(end_global),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert bubble.eventFilter(bubble._bubble_box, move) is True
    assert bubble.eventFilter(bubble._bubble_box, release) is True
    assert label.selectedText().startswith("从")
    view.close()


def test_chat_stream_flush_bypasses_second_stream_queue():
    calls = []

    class DummyView:
        def apply_coalesced_stream_message_at(self, index, text, **kwargs):
            calls.append(("coalesced", int(index), text, kwargs))
            return True

        def update_message_at(self, *_args, **_kwargs):
            calls.append(("queued",))
            return True

    panel = type("DummyPanel", (), {"_bubble_view": DummyView()})()

    updated = OcrTextPanel._update_assistant_bubble_at(
        panel,
        3,
        "流式快照",
        streaming=True,
        model_name="demo-model",
        elapsed=1.2,
    )

    assert updated is True
    assert calls[0][0:3] == ("coalesced", 3, "流式快照")
    assert calls[0][3]["model_name"] == "demo-model"
    assert not any(call[0] == "queued" for call in calls)


def test_single_answer_stream_flush_avoids_rebuild_and_second_layout_pass():
    calls = []

    class DummyBubble:
        def role(self):
            return "assistant"

    class DummyView:
        def __init__(self):
            self._last_ai = DummyBubble()
            self._bubbles = [self._last_ai]

        def apply_coalesced_stream_last_ai(self, text, **kwargs):
            calls.append(("coalesced", text, kwargs))

        def show_single(self, *_args, **_kwargs):
            calls.append(("rebuild",))

        def show(self):
            calls.append(("show",))

        def refresh_layout(self, **kwargs):
            calls.append(("layout", kwargs))

        def scroll_to_bottom(self):
            calls.append(("scroll",))

    panel = type("DummyPanel", (), {})()
    panel._bubble_view = DummyView()
    panel._sync_title_bar_visibility = lambda: calls.append(("title",))
    panel._reposition = lambda: calls.append(("reposition",))

    OcrTextPanel._set_translation_message(
        panel,
        "持续输出",
        markdown=True,
        streaming=True,
        model_name="demo-model",
    )

    assert calls[0][0:2] == ("coalesced", "持续输出")
    assert not any(call[0] in {"rebuild", "layout", "scroll", "reposition"} for call in calls)


def test_stream_updates_defer_while_user_scrolls_history():
    calls = []

    class DummyTimer:
        def isActive(self):
            return False

        def start(self, interval):
            calls.append(("timer_start", int(interval)))

    class DummyView:
        def __init__(self):
            self._pending_stream_updates = {}
            self._last_stream_flush_at = 0.0
            self._locked_scroll_val = 42
            self._stream_flush_timer = DummyTimer()
            self.at_bottom = False

        def is_at_bottom(self, tolerance=4):
            calls.append(("is_at_bottom", int(tolerance), bool(self.at_bottom)))
            return bool(self.at_bottom)

        def _record_stream_perf(self, metric, **kwargs):
            calls.append(("perf", metric))

        def _should_defer_stream_updates(self):
            return chat_bubbles.BubbleListView._should_defer_stream_updates(self)

        def _apply_update_message_at_now(self, index, text, **kwargs):
            calls.append(("apply", int(index), text, kwargs))
            return True

    view = DummyView()

    chat_bubbles.BubbleListView._queue_stream_update(
        view,
        ("index", 1),
        "生成中",
        is_markdown=True,
        model_name="qa-model",
        elapsed=1.2,
        reply_tokens=3,
        total_tokens=8,
        start_time="07/01 18:00",
    )
    chat_bubbles.BubbleListView._flush_pending_stream_updates(view)

    assert ("timer_start", 140) not in calls
    assert not any(item[0] == "apply" for item in calls if isinstance(item, tuple))
    assert ("index", 1) in view._pending_stream_updates

    view._locked_scroll_val = None
    view.at_bottom = True
    chat_bubbles.BubbleListView._flush_pending_stream_updates(view)

    assert ("apply", 1, "生成中", {
        "is_markdown": True,
        "streaming": True,
        "model_name": "qa-model",
        "elapsed": 1.2,
        "reply_tokens": 3,
        "total_tokens": 8,
        "start_time": "07/01 18:00",
    }) in calls
    assert view._pending_stream_updates == {}


def test_stream_bottom_follow_tracks_actual_range_change_without_retry_timer(monkeypatch):
    calls = []

    class DummyScrollBar:
        def setValue(self, value):
            calls.append(("scroll", int(value)))

    view = type("DummyView", (), {})()
    view._stream_follow_bottom_until = 100.0
    view._locked_scroll_val = None
    view._update_conversation_nav = lambda: calls.append(("nav",))
    view.verticalScrollBar = lambda: DummyScrollBar()
    view._active_progressive_pin = lambda: None
    monkeypatch.setattr(chat_bubbles.time, "monotonic", lambda: 99.5)

    chat_bubbles.BubbleListView._handle_scroll_range_changed(view, 0, 480)

    assert ("scroll", 480) in calls


def test_failed_bubble_new_round_appends_tail_without_full_render():
    class DummyBubbleView:
        def __init__(self):
            self.appended = []

        def append_messages(self, history, *, start_index, keep_bottom=False):
            self.appended.append((list(history), start_index, keep_bottom))
            return True

        def show(self):
            pass

        def scroll_to_bottom_now(self):
            pass

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "怎么配置问答模型？", "display_content": "怎么配置问答模型？", "task_type": "qa"},
        {"role": "assistant", "content": "回答失败：网络超时", "task_type": "qa"},
    ]
    calls = []
    panel._translation_worker = None
    panel._translation_model_display = "qa-model"
    panel._bubble_view = DummyBubbleView()
    panel._render_chat_history = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("失败恢复不应全量重绘"))
    panel._sync_title_bar_visibility = lambda: calls.append("sync_title")
    panel._thinking_card = type("DummyThinkingCard", (), {"start_thinking": lambda *a, **kw: calls.append(("think", kw))})()
    panel._reposition = lambda: calls.append("reposition")
    panel._run_qa_prompt = lambda text, loading_text, **kwargs: calls.append(("run", text, loading_text, kwargs))
    panel._show_panel_status = lambda *args, **kwargs: calls.append(("status", args, kwargs))

    OcrTextPanel._on_bubble_new_round_from_error(panel, 1)

    assert panel._bubble_view.appended[0][1:] == (2, True)
    assert panel._chat_history[1]["content"] == "回答失败：网络超时"
    assert panel._chat_history[2]["role"] == "user"
    assert panel._chat_history[3]["status_text"] == "正在生成回复..."
    assert calls[-1] == ("run", "怎么配置问答模型？", "正在回答...", {"button_task": "qa", "result_label": "问答结果"})


def test_conversation_output_height_matches_history_sidebar_height():
    panel = type("DummyPanel", (), {"_history_sidebar_width": lambda self: 280})()
    geo = QRect(0, 0, 1440, 900)

    conversation_metrics = OcrTextPanel._stable_panel_metrics(panel, geo, "conversation", is_max=False)
    history_metrics = OcrTextPanel._stable_panel_metrics(panel, geo, "history", is_max=False)
    fullscreen_conversation_metrics = OcrTextPanel._stable_panel_metrics(panel, geo, "conversation", is_max=True)
    fullscreen_history_metrics = OcrTextPanel._stable_panel_metrics(panel, geo, "history", is_max=True)

    assert conversation_metrics["h"] == history_metrics["h"]
    assert conversation_metrics["base_h"] == history_metrics["base_h"]
    assert history_metrics["w"] > conversation_metrics["w"]
    assert fullscreen_conversation_metrics["w"] == geo.width()
    assert fullscreen_conversation_metrics["h"] == geo.height()
    assert fullscreen_history_metrics["w"] == geo.width()
    assert fullscreen_history_metrics["h"] == geo.height()


def test_reposition_does_not_override_native_maximized_geometry(monkeypatch):
    class DummyScreen:
        def availableGeometry(self):
            return QRect(0, 0, 1600, 900)

    class DummyGuiApplication:
        @staticmethod
        def screenAt(_point):
            return DummyScreen()

        @staticmethod
        def primaryScreen():
            return DummyScreen()

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._chat_history = [{"role": "user", "content": "hello"}]
    panel._history_showing = True
    panel._preserve_cleared_output_area = False
    panel._panel_collapsed = False
    panel._suppress_reposition = False
    panel._is_maximized = True
    panel._region = QRect(0, 0, 100, 100)
    panel.pos = lambda: QPoint(10, 10)
    panel._sync_title_bar_visibility = lambda: calls.append("sync_title")
    panel._panel_layout_state = lambda: "history"
    panel._sync_maximized_content_layout = lambda: calls.append("sync_maximized_layout")
    panel._update_top_seam_cover = lambda: calls.append("top_seam")
    panel._update_interaction_seam_covers = lambda: calls.append("interaction_seams")
    panel._apply_panel_geometry = lambda *_args, **_kwargs: calls.append("apply_geometry")

    monkeypatch.setattr(post_capture_actions.text_panel, "QGuiApplication", DummyGuiApplication)

    OcrTextPanel._reposition(panel)

    assert "sync_maximized_layout" in calls
    assert "top_seam" in calls
    assert "interaction_seams" in calls
    assert "apply_geometry" not in calls


def test_maximized_content_layout_releases_stale_height_constraints():
    class DummyWidget:
        def __init__(self):
            self.minimum_height_calls = []
            self.maximum_height_calls = []
            self.fixed_height_calls = []
            self.hide_count = 0

        def setMinimumHeight(self, value):
            self.minimum_height_calls.append(int(value))

        def setMaximumHeight(self, value):
            self.maximum_height_calls.append(int(value))

        def setFixedHeight(self, value):
            self.fixed_height_calls.append(int(value))

        def hide(self):
            self.hide_count += 1

    panel = type("DummyPanel", (), {})()
    calls = []
    layout_state = {"value": "conversation"}
    panel._is_maximized = True
    panel._panel_collapsed = False
    panel._editor_container = DummyWidget()
    panel._bubble_view = DummyWidget()
    panel.setMinimumSize = lambda *args: calls.append(("panel_min_size", args))
    panel.setMaximumSize = lambda *args: calls.append(("panel_max_size", args))
    panel._release_panel_size_constraints = (
        lambda **kwargs: OcrTextPanel._release_panel_size_constraints(panel, **kwargs)
    )
    panel._panel_layout_state = lambda: layout_state["value"]
    panel.height = lambda: 900
    panel._sync_compact_editor_height = lambda height: calls.append(("compact_height", int(height)))
    panel._activate_panel_layouts = lambda: calls.append("activate_layouts")

    OcrTextPanel._sync_maximized_content_layout(panel)

    assert panel._editor_container.fixed_height_calls[-1] == 120
    assert panel._bubble_view.minimum_height_calls[-1] == 0
    assert panel._bubble_view.maximum_height_calls[-1] == 16777215

    layout_state["value"] = "empty"
    OcrTextPanel._sync_maximized_content_layout(panel)

    assert panel._bubble_view.hide_count == 1
    assert ("compact_height", 900) in calls


def test_topmost_release_preserves_native_maximized_geometry(monkeypatch):
    import ctypes
    import os

    native_calls = []

    class DummyUser32:
        @staticmethod
        def SetWindowPos(*args):
            native_calls.append(args)

    class DummyWindll:
        user32 = DummyUser32()

    panel = type("DummyPanel", (), {})()
    geometry_calls = []
    panel._is_maximized = True
    panel._pending_native_maximize_restore = False
    panel.winId = lambda: 123
    panel.isMinimized = lambda: False
    panel.isMaximized = lambda: False
    panel.geometry = lambda: QRect(10, 20, 640, 520)
    panel.setGeometry = lambda geometry: geometry_calls.append(QRect(geometry))
    panel._preserve_native_maximized_geometry = (
        lambda: OcrTextPanel._preserve_native_maximized_geometry(panel)
    )

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(ctypes, "windll", DummyWindll(), raising=False)

    OcrTextPanel._set_native_topmost(panel, False)

    assert len(native_calls) == 1
    assert geometry_calls == []

    panel._is_maximized = False
    OcrTextPanel._set_native_topmost(panel, False)

    assert len(native_calls) == 2
    assert geometry_calls == [QRect(10, 20, 640, 520)]


def test_maximized_confirmation_uses_actual_geometry_when_qt_state_is_stale():
    target_geometry = QRect(0, 0, 1600, 900)
    geometry_state = {"value": QRect(0, 0, 766, 274)}
    calls = []

    panel = type("DummyPanel", (), {})()
    panel._is_maximized = True
    panel._panel_collapsed = False
    panel._pending_native_maximize_restore = True
    panel._forcing_native_maximize = False
    panel._maximized_work_area_geometry = QRect(target_geometry)
    panel.isMaximized = lambda: True
    panel.frameGeometry = lambda: QRect(geometry_state["value"])
    panel.geometry = panel.frameGeometry
    panel._target_maximized_work_area = lambda: QRect(target_geometry)
    panel._has_effective_maximized_geometry = (
        lambda tolerance=4: OcrTextPanel._has_effective_maximized_geometry(panel, tolerance)
    )
    panel._force_native_maximized_window_state = lambda: calls.append("force_native_maximize")
    panel.setWindowState = lambda state: calls.append(("window_state", state))
    panel.showNormal = lambda: calls.append("show_normal")
    panel._release_panel_size_constraints = lambda **kwargs: calls.append(("release_constraints", kwargs))

    def set_geometry(geometry):
        geometry_state["value"] = QRect(geometry)
        calls.append(("geometry", QRect(geometry)))

    panel.setGeometry = set_geometry
    panel.show = lambda: calls.append("show")
    panel._apply_maximized_work_area_fallback = (
        lambda: OcrTextPanel._apply_maximized_work_area_fallback(panel)
    )

    assert panel.isMaximized() is True
    assert panel._has_effective_maximized_geometry() is False

    OcrTextPanel._ensure_native_maximized_state(panel, allow_geometry_fallback=True)

    assert "force_native_maximize" in calls
    assert ("geometry", target_geometry) in calls
    assert panel._has_effective_maximized_geometry() is True
    assert panel._pending_native_maximize_restore is False


def test_native_maximize_confirmation_is_invalid_after_window_mode_changes(monkeypatch):
    scheduled = []
    calls = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay, callback):
            scheduled.append((int(delay), callback))

    panel = type("DummyPanel", (), {})()
    panel._window_mode_generation = 7
    panel._ensure_native_maximized_state = (
        lambda **kwargs: calls.append(kwargs)
    )

    monkeypatch.setattr(post_capture_actions.text_panel, "QTimer", FakeTimer)

    OcrTextPanel._schedule_native_maximized_state_confirmation(panel)
    panel._window_mode_generation = 8
    for _delay, callback in scheduled:
        callback()

    assert [delay for delay, _callback in scheduled] == [0, 80, 750, 1450]
    assert calls == []


def test_force_normal_window_state_restores_windows_native_state(monkeypatch):
    import ctypes
    import sys

    calls = []
    native_calls = []

    class DummyUser32:
        @staticmethod
        def ShowWindow(*args):
            native_calls.append(args)

    class DummyWindll:
        user32 = DummyUser32()

    panel = type("DummyPanel", (), {})()
    panel._pending_native_maximize_restore = True
    panel.setWindowState = lambda state: calls.append(("window_state", state))
    panel.showNormal = lambda: calls.append("show_normal")
    panel.winId = lambda: 123

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", DummyWindll(), raising=False)

    OcrTextPanel._force_normal_window_state(panel)

    assert calls == [("window_state", Qt.WindowState.WindowNoState), "show_normal"]
    assert len(native_calls) == 1
    assert native_calls[0][1] == 9
    assert panel._pending_native_maximize_restore is False
    assert panel._forcing_normal_window_state is False


def test_capsule_geometry_keeps_qt_logical_size_and_native_frame_refresh_only(monkeypatch):
    import ctypes
    import sys

    target_geometry = QRect(100, 120, 236, 62)
    calls = []
    native_calls = []

    class DummyWindowHandle:
        def devicePixelRatio(self):
            return 1.5

        def setGeometry(self, geometry):
            calls.append(("qwindow_geometry", QRect(geometry)))

    class DummyUser32:
        @staticmethod
        def SetWindowPos(*args):
            native_calls.append(args)

    class DummyWindll:
        user32 = DummyUser32()

    panel = type("DummyPanel", (), {})()
    panel.setMinimumSize = lambda *args: calls.append(("minimum", args))
    panel.setMaximumSize = lambda *args: calls.append(("maximum", args))
    panel.setFixedSize = lambda size: calls.append(("fixed", size.width(), size.height()))
    panel.setGeometry = lambda geometry: calls.append(("qwidget_geometry", QRect(geometry)))
    panel.windowHandle = lambda: DummyWindowHandle()
    panel.winId = lambda: 123

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", DummyWindll(), raising=False)

    OcrTextPanel._apply_panel_collapse_geometry(panel, target_geometry)

    assert ("fixed", 236, 62) in calls
    assert ("qwidget_geometry", target_geometry) in calls
    assert ("qwindow_geometry", target_geometry) in calls
    assert len(native_calls) == 1
    assert native_calls[0][2:6] == (0, 0, 0, 0)
    native_flags = int(native_calls[0][6])
    assert native_flags & 0x0001
    assert native_flags & 0x0002


def test_capsule_confirmation_repairs_stale_maximized_state_and_exact_geometry():
    target_geometry = QRect(100, 120, 236, 62)
    geometry_state = {"value": QRect(0, 0, 1600, 900)}
    native_state = {"maximized": True}
    calls = []

    panel = type("DummyPanel", (), {})()
    panel._window_mode_generation = 4
    panel._panel_collapsed = True
    panel._panel_collapse_target_geometry = QRect(target_geometry)
    panel.isMaximized = lambda: native_state["maximized"]
    panel.geometry = lambda: QRect(geometry_state["value"])

    def force_normal():
        native_state["maximized"] = False
        calls.append("force_normal")

    def apply_geometry(geometry):
        geometry_state["value"] = QRect(geometry)
        calls.append(("geometry", QRect(geometry)))

    panel._force_normal_window_state = force_normal
    panel._apply_panel_collapse_geometry = apply_geometry
    panel.show = lambda: calls.append("show")
    panel._refresh_panel_collapse_visual = lambda: calls.append("refresh")

    OcrTextPanel._ensure_panel_collapse_geometry(panel, 4)

    assert native_state["maximized"] is False
    assert geometry_state["value"] == target_geometry
    assert calls == ["force_normal", ("geometry", target_geometry), "show", "refresh"]

    panel._window_mode_generation = 5
    native_state["maximized"] = True
    geometry_state["value"] = QRect(0, 0, 1600, 900)
    OcrTextPanel._ensure_panel_collapse_geometry(panel, 4)

    assert calls == ["force_normal", ("geometry", target_geometry), "show", "refresh"]


def test_capsule_confirmation_refreshes_visual_when_geometry_is_already_exact():
    target_geometry = QRect(100, 120, 236, 62)
    calls = []

    panel = type("DummyPanel", (), {})()
    panel._window_mode_generation = 3
    panel._panel_collapsed = True
    panel._panel_collapse_target_geometry = QRect(target_geometry)
    panel.isMaximized = lambda: False
    panel.geometry = lambda: QRect(target_geometry)
    panel._force_normal_window_state = lambda: calls.append("unexpected_force_normal")
    panel._apply_panel_collapse_geometry = lambda _geometry: calls.append("unexpected_geometry")
    panel._refresh_panel_collapse_visual = lambda: calls.append("refresh")

    OcrTextPanel._ensure_panel_collapse_geometry(panel, 3)

    assert calls == ["refresh"]


def test_capsule_visual_refresh_rebuilds_layout_style_shadow_and_paint():
    calls = []

    class DummyLayout:
        def __init__(self, name):
            self.name = name

        def invalidate(self):
            calls.append((self.name, "invalidate"))

        def activate(self):
            calls.append((self.name, "activate"))

    class DummyStyle:
        def unpolish(self, _widget):
            calls.append("unpolish")

        def polish(self, _widget):
            calls.append("polish")

    class DummyEffect:
        def isEnabled(self):
            return True

        def setEnabled(self, enabled):
            calls.append(("effect_enabled", bool(enabled)))

        def update(self):
            calls.append("effect_update")

    class DummyWidget:
        def layout(self):
            return DummyLayout("widget_layout")

        def style(self):
            return DummyStyle()

        def graphicsEffect(self):
            return DummyEffect()

        def updateGeometry(self):
            calls.append("widget_update_geometry")

        def update(self):
            calls.append("widget_update")

        def repaint(self):
            calls.append("widget_repaint")

    panel = type("DummyPanel", (), {})()
    panel._panel_collapsed = True
    panel._collapsed_widget = DummyWidget()
    panel.layout = lambda: DummyLayout("panel_layout")
    panel.updateGeometry = lambda: calls.append("panel_update_geometry")
    panel.update = lambda: calls.append("panel_update")
    panel.repaint = lambda: calls.append("panel_repaint")

    OcrTextPanel._refresh_panel_collapse_visual(panel)

    assert ("panel_layout", "invalidate") in calls
    assert ("panel_layout", "activate") in calls
    assert ("widget_layout", "invalidate") in calls
    assert ("widget_layout", "activate") in calls
    assert ["unpolish", "polish"] == [item for item in calls if item in {"unpolish", "polish"}]
    assert ("effect_enabled", False) in calls
    assert ("effect_enabled", True) in calls
    assert "effect_update" in calls
    assert "widget_repaint" in calls
    assert "panel_repaint" in calls


def test_normal_window_collapse_keeps_normal_path_and_uses_capsule_geometry():
    calls = []
    geometry_state = {"value": QRect(80, 90, 640, 520)}

    class DummyWidget:
        def hide(self):
            calls.append("hide_widget")

        def show(self):
            calls.append("show_widget")

        def setFixedSize(self, size):
            calls.append(("widget_fixed", size.width(), size.height()))

    panel = type("DummyPanel", (), {})()
    panel._panel_collapsed = False
    panel._is_maximized = False
    panel._watermark = DummyWidget()
    panel._translation_info = DummyWidget()
    panel._view_stack = DummyWidget()
    panel._collapsed_widget = DummyWidget()
    panel._advance_window_mode_generation = (
        lambda: OcrTextPanel._advance_window_mode_generation(panel)
    )
    panel._hide_chat_transient_overlays = lambda: calls.append("hide_overlays")
    panel.pos = lambda: QPoint(geometry_state["value"].topLeft())
    panel.geometry = lambda: QRect(geometry_state["value"])
    panel.isMaximized = lambda: False
    panel._set_panel_collapsed_property = lambda value: calls.append(("collapsed", bool(value)))
    panel._set_maximized_property = lambda value: calls.append(("maximized", bool(value)))
    panel.setMinimumSize = lambda *args: calls.append(("minimum", args))
    panel.setMaximumSize = lambda *args: calls.append(("maximum", args))
    panel._collapsed_panel_size = lambda: QSize(220, 46)
    panel._collapsed_window_size = lambda: QSize(236, 62)
    panel._clamp_window_pos = lambda point, width, height: QPoint(point)
    panel._force_normal_window_state = lambda: calls.append("force_normal")

    def apply_geometry(geometry):
        geometry_state["value"] = QRect(geometry)
        calls.append(("geometry", QRect(geometry)))

    panel._apply_panel_collapse_geometry = apply_geometry
    panel.show = lambda: calls.append("show")
    panel._refresh_panel_collapse_visual = lambda: calls.append("refresh")
    panel.raise_ = lambda: calls.append("raise")
    panel._set_native_topmost = lambda value: calls.append(("topmost", bool(value)))
    panel._schedule_panel_collapse_geometry_confirmation = (
        lambda generation: calls.append(("confirm", int(generation)))
    )

    OcrTextPanel._toggle_panel_collapse(panel, True)

    assert panel._panel_collapsed is True
    assert panel._panel_collapse_restore_maximized is False
    assert geometry_state["value"] == QRect(80, 90, 236, 62)
    assert "force_normal" not in calls
    assert calls.count("refresh") == 1
    assert ("confirm", 1) in calls


def test_loaded_translate_history_marks_messages_for_regenerate():
    record = {
        "task_type": "translate",
        "source_text": "hello",
        "result_text": "你好",
        "prompt_text": '[{"role": "user", "content": "请将以下文本翻译成中文：\\nhello", "display_content": "hello"}, {"role": "assistant", "content": "你好"}]',
        "model_name": "translate-model",
        "elapsed_secs": 0.5,
        "created_at": "2026-06-19 12:00:00",
    }

    messages = OcrTextPanel._messages_from_history_record(object(), record)

    assert messages[0]["task_type"] == "translate"
    assert messages[0]["regenerate_text"] == "hello"
    assert messages[1]["task_type"] == "translate"


def test_loaded_chat_history_appends_result_text_when_prompt_json_has_no_assistant():
    result = "![generated image](file:///D:/Tencent%20Files/generated-images/chatgpt_web_file.png)"
    record = {
        "task_type": "qa",
        "source_text": "生成一个美女，全身照",
        "result_text": result,
        "prompt_text": '[{"role": "user", "content": "生成一个美女，全身照", "display_content": "生成一个美女，全身照"}]',
        "model_name": "ChatGPT Web 生图",
        "elapsed_secs": 42.3,
        "created_at": "2026-06-25 08:35:00",
    }

    messages = OcrTextPanel._messages_from_history_record(object(), record)

    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == result
    assert messages[1]["model_name"] == "ChatGPT Web 生图"


def test_loaded_chat_history_fills_empty_assistant_from_result_text():
    result = "[generated image](file:///D:/Tencent%20Files/generated-images/chatgpt_web_file.png)"
    record = {
        "task_type": "qa",
        "source_text": "生成一个美女，全身照",
        "result_text": result,
        "prompt_text": '[{"role": "user", "content": "生成一个美女，全身照"}, {"role": "assistant", "content": ""}]',
        "model_name": "ChatGPT Web 生图",
        "elapsed_secs": 42.3,
        "created_at": "2026-06-25 08:35:00",
    }

    messages = OcrTextPanel._messages_from_history_record(object(), record)

    assert len(messages) == 2
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == result
    assert messages[1]["display_content"] == result


def test_loaded_chat_history_keeps_nonempty_assistant_when_display_content_is_empty():
    result = "![generated image](file:///D:/Tencent%20Files/generated-images/chatgpt_web_file.png)"
    record = {
        "task_type": "qa",
        "source_text": "生成一个美女，全身照",
        "result_text": result,
        "prompt_text": '[{"role": "user", "content": "生成一个美女，全身照"}, {"role": "assistant", "content": "![generated image](file:///D:/Tencent%20Files/generated-images/chatgpt_web_file.png)", "display_content": ""}]',
        "model_name": "ChatGPT Web 生图",
        "elapsed_secs": 42.3,
        "created_at": "2026-06-25 08:35:00",
    }

    messages = OcrTextPanel._messages_from_history_record(object(), record)

    assert messages[1]["content"] == result
    assert messages[1]["display_content"] == ""


def test_history_search_match_message_index_finds_body_and_record_model():
    messages = [
        {"role": "user", "content": "问题标题"},
        {"role": "assistant", "content": "这里包含唯一命中正文"},
    ]
    record = {"model_name": "special-model", "source_text": "问题标题", "result_text": "回答"}

    assert OcrTextPanel._history_search_match_message_index(object(), messages, "唯一命中") == 1
    assert OcrTextPanel._history_search_match_message_index(object(), messages, "special-model", record) == 1


def test_bubble_list_scroll_to_message_index_centers_matching_bubble():
    calls = []

    class DummyBar:
        def __init__(self):
            self.value = None

        def minimum(self):
            return 0

        def maximum(self):
            return 1000

        def setValue(self, value):
            self.value = value

    class DummyViewport:
        def height(self):
            return 200

    class DummyBubble:
        def __init__(self, msg_index, top, height):
            self._msg_index = msg_index
            self._top = top
            self._height = height

        def y(self):
            return self._top

        def height(self):
            return self._height

    bar = DummyBar()
    view = chat_bubbles.BubbleListView.__new__(chat_bubbles.BubbleListView)
    view._bubbles = [DummyBubble(0, 20, 40), DummyBubble(3, 500, 80)]
    view._scroll_generation = 0
    view._progressive_generation = 0
    view._progressive_state = None
    view._scroll_to_bottom_pending = True
    view._pending_keep_bottom = True
    view._layout_flush_timer = SimpleNamespace(stop=lambda: calls.append("stop"))
    view._vbox = SimpleNamespace(invalidate=lambda: calls.append("invalidate"), activate=lambda: calls.append("activate"))
    view._container = SimpleNamespace(updateGeometry=lambda: calls.append("update_geometry"))
    view.verticalScrollBar = lambda: bar
    view.viewport = lambda: DummyViewport()
    view._refresh_conversation_nav_active = lambda: calls.append("refresh_active")

    assert chat_bubbles.BubbleListView.scroll_to_message_index(view, 3)
    assert bar.value == 440
    assert view._scroll_to_bottom_pending is False
    assert "refresh_active" in calls


def test_translate_missing_config_uses_model_settings_message():
    panel = type("DummyPanel", (), {})()
    errors = []
    panel._external_reuse_action_blocked = lambda: False
    panel._translation_worker = None
    panel._translator_runtime_config = lambda purpose: ({}, False, "")
    panel._translator_required_fields = lambda cfg: ["base_url", "model_name", "api_key"]
    panel._qa_required_fields = lambda cfg: ["__qa_model__"]
    panel._show_translation_error = lambda message: errors.append(message)
    panel._show_qa_error = lambda message: (_ for _ in ()).throw(AssertionError(message))

    OcrTextPanel._run_qa_prompt(panel, "hello", "正在翻译...", button_task="translate", result_label="翻译结果")

    assert errors == ["翻译模型配置错误，请检查模型管理中的翻译模型是否已正确选择并配置。"]


def test_answer_question_defers_geometry_until_run_prompt():
    class Editor:
        def toPlainText(self):
            return "hello"

    class Frame:
        def y(self):
            return 20

        def height(self):
            return 100

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._translation_worker = None
    panel._ocr_input_auto_height_cap = 800
    panel._current_input_origin = "ocr"
    panel._editor = Editor()
    panel._sync_pending_attachments_from_editor_text = lambda: None
    panel._pending_attachments = []
    panel._pending_attachment_previews = []
    panel._pending_attachment_labels = []
    panel._associated_note_tab = None
    panel._chat_history = [{"role": "user", "content": "previous"}]
    panel._maybe_show_context_length_warning = lambda text: None
    panel._clear_editor_silently = lambda: calls.append("clear")
    panel._remove_chat_draft = lambda: None
    panel.isVisible = lambda: True
    panel.frameGeometry = lambda: Frame()
    panel._render_chat_history = lambda is_streaming=False: calls.append(("render", is_streaming))
    panel._reposition = lambda: calls.append("reposition")
    # 使用变长参数使 mock 方法完全兼容任何形式的内部传参
    panel._thinking_card = type("DummyThinkingCard", (), {"start_thinking": lambda *a, **kw: None})()
    panel._schedule_question_editor_ime_focus_reset = lambda: calls.append("focus")
    panel._run_qa_prompt = lambda text, loading_text, **kwargs: calls.append(("run", text, loading_text, kwargs))
    panel._build_ima_note_search_summary_prompt = lambda text: text

    OcrTextPanel._answer_question(panel)

    # 随着主业务逻辑更新，此处会正常触发 reposition 序列，更新断言列表以符合新逻辑轨迹
    assert calls == [
        "clear",
        ("render", True),
        "reposition",
        "focus",
        ("run", "hello", "正在回答...", {"button_task": "qa", "result_label": "问答结果"}),
    ]
    assert panel._keep_bottom_y_on_next_reposition == 120
    assert panel._ocr_input_auto_height_cap == 0
    assert panel._current_input_origin == "manual"


def test_placeholder_card_title_displays_but_prompt_is_sent():
    class Editor:
        def toPlainText(self):
            return "AI搜索"

    class Frame:
        def y(self):
            return 20

        def height(self):
            return 100

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._translation_worker = None
    panel._ima_note_search_worker = None
    panel._editor = Editor()
    panel._pending_placeholder_card_title = "AI搜索"
    panel._pending_placeholder_card_prompt = "请帮我搜索以下内容，并整理结果："
    panel._sync_pending_attachments_from_editor_text = lambda: None
    panel._pending_attachments = []
    panel._pending_attachment_previews = []
    panel._pending_attachment_labels = []
    panel._associated_note_tab = None
    panel._chat_history = [{"role": "user", "content": "previous"}]
    panel._maybe_show_context_length_warning = lambda text: None
    panel._clear_editor_silently = lambda: calls.append("clear")
    panel._remove_chat_draft = lambda: None
    panel.isVisible = lambda: True
    panel.frameGeometry = lambda: Frame()
    panel._render_chat_history = lambda is_streaming=False: calls.append(("render", is_streaming))
    panel._reposition = lambda: calls.append("reposition")
    panel._thinking_card = type("DummyThinkingCard", (), {"start_thinking": lambda *a, **kw: None})()
    panel._schedule_question_editor_ime_focus_reset = lambda: calls.append("focus")
    panel._run_qa_prompt = lambda text, loading_text, **kwargs: calls.append(("run", text, loading_text, kwargs))
    panel._build_ima_note_search_summary_prompt = lambda text: text

    OcrTextPanel._answer_question(panel)

    assert ("run", "请帮我搜索以下内容，并整理结果：", "正在回答...", {"button_task": "qa", "result_label": "问答结果"}) in calls
    user_message = panel._chat_history[-2]
    assert user_message["content"] == "请帮我搜索以下内容，并整理结果："
    assert user_message["display_content"] == "AI搜索"


def test_placeholder_card_editor_inserts_normal_space_after_title():
    try:
        from PyQt6.QtGui import QColor
        from PyQt6.QtWidgets import QApplication, QTextEdit
    except Exception:
        import pytest

        pytest.skip("缺少 PyQt6，跳过快捷卡片编辑器格式测试")

    app = QApplication.instance() or QApplication([])
    assert app is not None

    panel = type("DummyPanel", (), {})()
    panel._editor = QTextEdit()
    panel._loading_text = False

    OcrTextPanel._set_placeholder_card_editor(panel, "长文提炼", "请总结以下内容：")

    assert panel._editor.toPlainText() == "长文提炼 "
    assert panel._editor.currentCharFormat().foreground().color() == QColor("#1f2937")
    panel._editor.insertPlainText("哈哈哈")
    assert panel._editor.toPlainText() == "长文提炼 哈哈哈"


def test_quick_translate_ensures_latest_message_visible_after_layout(monkeypatch):
    class Frame:
        def y(self):
            return 20

        def height(self):
            return 100

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._chat_history = []
    panel._current_input_origin = "ocr"
    panel._translation_model_display = "translate-model"
    panel._clear_editor_silently = lambda: calls.append("clear")
    panel.isVisible = lambda: True
    panel.frameGeometry = lambda: Frame()
    panel._render_chat_history = lambda is_streaming=False: calls.append(("render", is_streaming))
    panel._reposition = lambda: calls.append("reposition")
    panel._thinking_card = type("DummyThinkingCard", (), {"start_thinking": lambda *a, **kw: calls.append("think")})()
    panel._schedule_question_editor_ime_focus_reset = lambda: calls.append("focus")
    panel._run_qa_prompt = lambda text, loading_text, **kwargs: calls.append(("run", text, loading_text, kwargs))

    monkeypatch.setattr(
        OcrTextPanel,
        "_ensure_latest_chat_visible_after_layout",
        lambda self: calls.append("ensure_latest"),
    )

    OcrTextPanel._run_quick_command(
        panel,
        "请将以下文本翻译成中文：\nhello",
        "正在翻译...",
        "translate",
        "翻译结果",
        display_text="hello",
        execution_text="hello",
    )

    assert calls == [
        "clear",
        ("render", True),
        "think",
        "reposition",
        "ensure_latest",
        "focus",
        ("run", "hello", "正在翻译...", {"button_task": "translate", "result_label": "翻译结果"}),
    ]
    assert panel._chat_history[0]["display_content"] == "hello"
    assert panel._chat_history[0]["task_type"] == "translate"
    assert panel._chat_history[1]["status_text"] == "正在翻译..."
    assert panel._keep_bottom_y_on_next_reposition == 120
    assert panel._current_input_origin == "manual"


def test_answer_question_starts_ima_note_search_worker():
    class Editor:
        def toPlainText(self):
            return "搜索笔记：排期"

    class Frame:
        def y(self):
            return 20

        def height(self):
            return 100

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._translation_worker = None
    panel._editor = Editor()
    panel._sync_pending_attachments_from_editor_text = lambda: None
    panel._pending_attachments = []
    panel._pending_attachment_previews = []
    panel._pending_attachment_labels = []
    panel._associated_note_tab = None
    panel._chat_history = []
    panel._maybe_show_context_length_warning = lambda text: None
    panel._source_is_original_text = lambda text: False
    panel._build_qa_prompt = lambda text: text
    panel._clear_editor_silently = lambda: calls.append("clear")
    panel._remove_chat_draft = lambda: None
    panel.isVisible = lambda: True
    panel.frameGeometry = lambda: Frame()
    panel._render_chat_history = lambda is_streaming=False: calls.append(("render", is_streaming))
    panel._reposition = lambda: calls.append("reposition")
    panel._thinking_card = type("DummyThinkingCard", (), {"start_thinking": lambda *a, **kw: None})()
    panel._schedule_question_editor_ime_focus_reset = lambda: calls.append("focus")
    panel._run_qa_prompt = lambda text, loading_text, **kwargs: calls.append(("run", text, loading_text, kwargs))
    panel._set_worker_buttons_busy = lambda busy, **kwargs: calls.append(("busy", busy, kwargs))
    panel._start_dots_animation = lambda: calls.append("dots")
    panel._start_ima_note_search_worker = lambda text: calls.append(("search", text))

    OcrTextPanel._answer_question(panel)

    assert panel._chat_history[0]["content"] == "搜索笔记：排期"
    assert panel._chat_history[0]["display_content"] == "搜索笔记：排期"
    assert ("search", "搜索笔记：排期") in calls
    assert ("run", "IMA笔记检索结果上下文", "正在回答...", {"button_task": "qa", "result_label": "问答结果"}) not in calls


def test_answer_question_ima_note_search_start_error_stops_thinking_card():
    class Editor:
        def toPlainText(self):
            return "搜索笔记：排期"

    class Frame:
        def y(self):
            return 20

        def height(self):
            return 100

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._translation_worker = None
    panel._ima_note_search_worker = None
    panel._editor = Editor()
    panel._sync_pending_attachments_from_editor_text = lambda: None
    panel._pending_attachments = []
    panel._pending_attachment_previews = []
    panel._pending_attachment_labels = []
    panel._associated_note_tab = None
    panel._chat_history = []
    panel._maybe_show_context_length_warning = lambda text: None
    panel._source_is_original_text = lambda text: False
    panel._build_qa_prompt = lambda text: text
    panel._clear_editor_silently = lambda: calls.append("clear")
    panel._remove_chat_draft = lambda: calls.append("draft-remove")
    panel.isVisible = lambda: True
    panel.frameGeometry = lambda: Frame()
    panel._render_chat_history = lambda is_streaming=False: calls.append(("render", is_streaming))
    panel._reposition = lambda: calls.append("reposition")
    panel._thinking_card = type(
        "DummyThinkingCard",
        (),
        {
            "start_thinking": lambda self, **kwargs: calls.append(("think_start", kwargs)),
            "stop_thinking": lambda self: calls.append("think_stop"),
            "hide": lambda self: calls.append("think_hide"),
        },
    )()
    panel._schedule_question_editor_ime_focus_reset = lambda: calls.append("focus")
    panel._set_worker_buttons_busy = lambda busy, **kwargs: calls.append(("busy", busy, kwargs))
    panel._start_dots_animation = lambda: calls.append("dots-start")
    panel._stop_dots_animation = lambda: calls.append("dots-stop")
    panel._stream_flush_timer = type("Timer", (), {"stop": lambda self: calls.append("timer-stop")})()
    panel._show_qa_error = lambda message: calls.append(("error", message))

    def fail_start(text):
        panel._ima_note_search_worker = "partial-worker"
        raise RuntimeError("search boom")

    panel._start_ima_note_search_worker = fail_start

    OcrTextPanel._answer_question(panel)

    assert panel._ima_note_search_worker is None
    assert "think_stop" in calls
    assert "think_hide" in calls
    assert ("busy", False, {"task": "qa"}) in calls
    assert ("error", "IMA 笔记搜索启动失败：search boom") in calls


def test_context_warning_threshold_requires_token_floor_with_message_count(monkeypatch):
    class DummyBar:
        def __init__(self):
            self.visible = False
            self.hidden = False

        def show(self):
            self.visible = True
            self.hidden = False

        def hide(self):
            self.hidden = True
            self.visible = False

    class DummyLabel:
        def __init__(self):
            self.text = ""
            self.visible = True

        def setText(self, text):
            self.text = str(text)

        def setVisible(self, visible):
            self.visible = bool(visible)

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [{"role": "user", "content": str(i)} for i in range(20)]
    panel._context_recent_round_limit = None
    panel._context_warning_dismissed = False
    panel._context_warning_bar = DummyBar()
    panel._context_warning_label = DummyLabel()
    panel._context_recent_btn = None
    panel._context_manager_expanded = False
    panel._context_actions_row = None
    panel._context_expand_btn = None
    panel._context_details_label = DummyLabel()
    panel._context_warning_dismissed_snapshot = None
    panel._position_context_warning_bar = lambda: None

    def snapshot(tokens):
        return {
            "tokens": tokens,
            "message_count": len(panel._chat_history),
            "has_attachments": False,
            "scope_text": "最近 20 条 / 全部上下文",
        }

    monkeypatch.setattr(
        OcrTextPanel,
        "_context_management_snapshot",
        lambda self, next_text="": snapshot(49999),
    )
    OcrTextPanel._maybe_show_context_length_warning(panel)
    assert panel._context_warning_bar.hidden is True
    assert panel._context_warning_bar.visible is False

    panel._context_warning_bar = DummyBar()
    monkeypatch.setattr(
        OcrTextPanel,
        "_context_management_snapshot",
        lambda self, next_text="": snapshot(50000),
    )
    OcrTextPanel._maybe_show_context_length_warning(panel)
    assert panel._context_warning_bar.visible is True
    assert "约 50000 tokens" in panel._context_warning_label.text
    assert "估算上下文 tokens：50000" in panel._context_details_label.text

    panel._chat_history = [{"role": "user", "content": "short"}]
    panel._context_warning_bar = DummyBar()
    monkeypatch.setattr(
        OcrTextPanel,
        "_context_management_snapshot",
        lambda self, next_text="": snapshot(100000),
    )
    OcrTextPanel._maybe_show_context_length_warning(panel)
    assert panel._context_warning_bar.visible is True


def test_context_warning_respects_dismissed_snapshot_until_significant_change(monkeypatch):
    class DummyBar:
        def __init__(self):
            self.visible = False
            self.hidden = False

        def show(self):
            self.visible = True
            self.hidden = False

        def hide(self):
            self.hidden = True
            self.visible = False

    class DummyLabel:
        def __init__(self):
            self.text = ""

        def setText(self, text):
            self.text = str(text)

        def setVisible(self, _visible):
            pass

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [{"role": "user", "content": str(i)} for i in range(22)]
    panel._context_recent_round_limit = None
    panel._context_warning_dismissed = True
    panel._context_warning_dismissed_snapshot = {
        "tokens": 100000,
        "message_count": 22,
        "has_attachments": False,
        "scope_text": "最近 22 条 / 全部上下文",
    }
    panel._context_warning_bar = DummyBar()
    panel._context_warning_label = DummyLabel()
    panel._context_details_label = DummyLabel()
    panel._context_actions_row = None
    panel._context_expand_btn = None
    panel._context_manager_expanded = False
    panel._position_context_warning_bar = lambda: None

    monkeypatch.setattr(
        OcrTextPanel,
        "_context_management_snapshot",
        lambda self, next_text="": {
            "tokens": 105000,
            "message_count": 23,
            "has_attachments": False,
            "scope_text": "最近 22 条 / 全部上下文",
        },
    )
    OcrTextPanel._maybe_show_context_length_warning(panel)
    assert panel._context_warning_bar.visible is False
    assert panel._context_warning_dismissed is True

    monkeypatch.setattr(
        OcrTextPanel,
        "_context_management_snapshot",
        lambda self, next_text="": {
            "tokens": 116000,
            "message_count": 23,
            "has_attachments": False,
            "scope_text": "最近 22 条 / 全部上下文",
        },
    )
    OcrTextPanel._maybe_show_context_length_warning(panel)
    assert panel._context_warning_bar.visible is True
    assert panel._context_warning_dismissed is False


def test_context_summary_compresses_future_worker_messages():
    panel = type("DummyPanel", (), {})()
    messages = [
        {"role": "user", "content": "旧问题1"},
        {"role": "assistant", "content": "旧回答1"},
        {"role": "user", "content": "旧问题2"},
        {"role": "assistant", "content": "旧回答2"},
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": "新回答"},
    ]
    panel._context_summary_text = "旧对话摘要"
    panel._context_summary_anchor_index = 4
    panel._context_recent_round_limit = None

    carried = OcrTextPanel._worker_messages_for_current_context(panel, messages)

    assert carried[0]["role"] == "system"
    assert "旧对话摘要" in carried[0]["content"]
    assert carried[1:] == messages[4:]


def test_worker_messages_include_pinned_context_despite_recent_limit():
    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "必须携带的旧问题", "context_pinned": True},
        {"role": "assistant", "content": "旧回答"},
        {"role": "user", "content": "会被裁掉的问题"},
        {"role": "assistant", "content": "会被裁掉的回答"},
        {"role": "user", "content": "当前问题"},
    ]
    panel._context_summary_text = ""
    panel._context_summary_anchor_index = 0
    panel._context_recent_round_limit = 1

    carried = OcrTextPanel._worker_messages_for_current_context(panel, list(panel._chat_history))

    assert [msg["content"] for msg in carried] == ["必须携带的旧问题", "当前问题"]


def test_context_estimate_includes_pinned_context_with_recent_limit():
    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "必须携带的旧问题", "context_pinned": True},
        {"role": "assistant", "content": "旧回答"},
        {"role": "user", "content": "当前问题"},
    ]
    panel._context_summary_text = ""
    panel._context_summary_anchor_index = 0
    panel._context_recent_round_limit = 1
    panel._pending_attachments = []

    carried = OcrTextPanel._context_messages_for_estimate(panel)

    assert [msg["content"] for msg in carried] == ["必须携带的旧问题", "当前问题"]


def test_finalize_context_summary_sets_future_anchor(monkeypatch):
    panel = type("DummyPanel", (), {})()
    panel._pending_context_summary_anchor_index = 4
    panel._context_summary_text = ""
    panel._context_summary_anchor_index = 0
    panel._context_recent_round_limit = 5
    panel._context_warning_dismissed = True
    panel._context_warning_dismissed_snapshot = {"tokens": 100000}
    panel._context_warning_current_snapshot = {"tokens": 100000}
    panel._context_manager_expanded = True
    panel._context_actions_row = None
    panel._context_expand_btn = None
    panel._context_details_label = None
    panel._context_warning_bar = None
    panel._bubble_view = None
    panel._content_stack = None
    panel._chat_history = [
        {"role": "user", "content": "旧问题1"},
        {"role": "assistant", "content": "旧回答1"},
        {"role": "user", "content": "旧问题2"},
        {"role": "assistant", "content": "旧回答2"},
        {"role": "user", "content": "请总结当前对话"},
        {"role": "assistant", "content": "旧对话摘要"},
    ]
    feedback = []
    panel._show_light_feedback = lambda *args, **kwargs: feedback.append((args, kwargs))
    monkeypatch.setattr(OcrTextPanel, "_maybe_show_context_length_warning", lambda self, next_text="": None)

    OcrTextPanel._finalize_pending_context_summary(panel, "旧对话摘要")

    assert panel._pending_context_summary_anchor_index is None
    assert panel._context_summary_text == "旧对话摘要"
    assert panel._context_summary_anchor_index == len(panel._chat_history)
    assert panel._context_recent_round_limit is None
    assert panel._context_warning_dismissed is False
    assert panel._context_warning_dismissed_snapshot is None
    assert feedback


def test_conversation_nav_marker_lengths_are_center_symmetric():
    nav = chat_bubbles._ConversationNavBar.__new__(chat_bubbles._ConversationNavBar)
    nav._hover_index = 3

    widths = []
    for idx in range(0, 7):
        x1, x2 = chat_bubbles._ConversationNavBar._marker_x(nav, idx)
        assert x1 + x2 == 22
        widths.append(x2 - x1)

    assert widths == [14, 16, 18, 20, 18, 16, 14]


def test_single_conversation_nav_marker_is_vertically_centered():
    class DummyNav:
        def __init__(self):
            self.items = []

        def refresh_geometry(self):
            pass

        def height(self):
            return 360

        def set_items(self, items):
            self.items = list(items)

        def update_active_from_scroll(self):
            pass

    class DummyBubble:
        def __init__(self, role, text, y=0):
            self._role = role
            self._text = text
            self._y = y
            self._meta_start_time = ""

        def role(self):
            return self._role

        def raw_text(self):
            return self._text

        def y(self):
            return self._y

    nav = DummyNav()
    view = chat_bubbles.BubbleListView.__new__(chat_bubbles.BubbleListView)
    view._conversation_nav = nav
    view._bubbles = [
        DummyBubble("user", "问题", 24),
        DummyBubble("assistant", "回答", 88),
    ]

    chat_bubbles.BubbleListView._update_conversation_nav(view)

    assert len(nav.items) == 1
    assert nav.items[0].marker_y == 180


def test_generating_placeholder_keeps_single_line_measure_width():
    class DummySizeHint:
        def width(self):
            return 86

    class DummyLabel:
        def setWordWrap(self, _value):
            pass

        def sizeHint(self):
            return DummySizeHint()

    class DummyBubble:
        def __init__(self):
            self._natural_width = None
            self._placeholder_min_width = 168
            self._placeholder_min_height = 46
            self._label = DummyLabel()
            self.measure_width = 0
            self.minimum_height = 0

        def setMaximumWidth(self, _value):
            pass

        def raw_text(self):
            return ""

        def role(self):
            return "assistant"

        def setSizePolicy(self, *_args):
            pass

        def set_measure_width(self, value):
            self.measure_width = int(value)

        def heightForWidth(self, _value):
            return max(40, self._placeholder_min_height)

        def setMinimumHeight(self, value):
            self.minimum_height = int(value)

        def updateGeometry(self):
            pass

    view = chat_bubbles.BubbleListView.__new__(chat_bubbles.BubbleListView)
    view._vbox = None
    view._max_bubble_width = lambda: 360
    view.viewport = lambda: SimpleNamespace(width=lambda: 360)
    view.width = lambda: 360
    view.window = lambda: None
    bubble = DummyBubble()

    chat_bubbles.BubbleListView._update_bubble_stretch_and_policy(view, bubble, 360, 360)

    assert bubble.measure_width == 118
    assert bubble.minimum_height == 46


def test_conversation_nav_uses_full_history_when_only_tail_is_rendered():
    class DummyBubble:
        def __init__(self, role, text, y):
            self._role = role
            self._text = text
            self._y = y
            self._meta_start_time = ""

        def role(self):
            return self._role

        def raw_text(self):
            return self._text

        def y(self):
            return self._y

    view = chat_bubbles.BubbleListView.__new__(chat_bubbles.BubbleListView)
    view._bubbles = [
        DummyBubble("user", "问题0", 100),
        DummyBubble("assistant", "回答1", 150),
        DummyBubble("user", "问题2", 200),
        DummyBubble("assistant", "回答3", 250),
    ]

    items = chat_bubbles.BubbleListView._conversation_nav_items(view)

    assert len(items) == 2
    assert items[0].question == "问题0"
    assert items[0].answer == "回答1"
    assert items[0].target_y == 92
    assert items[1].question == "问题2"
    assert items[1].answer == "回答3"
    assert items[1].target_y == 192


def test_conversation_nav_preview_hide_clears_old_footprint():
    calls = []

    class DummyRect:
        def adjusted(self, *values):
            calls.append(("adjusted", values))
            return self

    class DummyCard:
        def geometry(self):
            return DummyRect()

        def hide(self):
            calls.append("hide")

    class DummyViewport:
        def update(self, rect):
            calls.append(("update", rect.__class__.__name__))

        def repaint(self, rect):
            calls.append(("repaint", rect.__class__.__name__))

    nav = chat_bubbles._ConversationNavBar.__new__(chat_bubbles._ConversationNavBar)
    nav._card = DummyCard()
    nav._owner = SimpleNamespace(viewport=lambda: DummyViewport())

    chat_bubbles._ConversationNavBar._show_card(nav, -1)

    assert calls.count("hide") == 1
    assert [item[0] for item in calls if isinstance(item, tuple) and item[0] in {"update", "repaint"}] == [
        "update",
    ]


def test_conversation_nav_preview_move_clears_previous_footprint():
    calls = []

    class DummyRect:
        def __init__(self, name):
            self.name = name

        def adjusted(self, *values):
            return self

    class DummyCard:
        def __init__(self):
            self.rect = DummyRect("old")

        def geometry(self):
            return self.rect

        def move(self, x, y):
            calls.append(("move", x, y))
            self.rect = DummyRect("new")

        def show(self):
            calls.append("show")

        def raise_(self):
            calls.append("raise")

        def setFixedWidth(self, value):
            calls.append(("width", value))

        def adjustSize(self):
            calls.append("adjust")

        def sizeHint(self):
            return SimpleNamespace(height=lambda: 88)

        def setFixedHeight(self, value):
            calls.append(("height", value))

        def update(self):
            calls.append("card_update")

        def repaint(self):
            calls.append("card_repaint")

    class DummyViewport:
        def width(self):
            return 420

        def height(self):
            return 360

        def update(self, rect):
            calls.append(("update", rect.name))

        def repaint(self, rect):
            calls.append(("repaint", rect.name))

    class DummyLabel:
        def setText(self, value):
            calls.append(("text", value))

        def setVisible(self, value):
            calls.append(("visible", bool(value)))

    nav = chat_bubbles._ConversationNavBar.__new__(chat_bubbles._ConversationNavBar)
    nav._card = DummyCard()
    nav._card_title = DummyLabel()
    nav._card_meta = DummyLabel()
    nav._card_body = DummyLabel()
    nav._owner = SimpleNamespace(viewport=lambda: DummyViewport(), widget=lambda: SimpleNamespace(height=lambda: 800))
    nav._items = [
        chat_bubbles._ConversationNavItem(
            question="问题",
            answer="回答",
            target_y=100,
            marker_y=80,
            time_str="10:30",
        )
    ]
    nav.mapTo = lambda _viewport, _point: QPoint(380, 8)

    chat_bubbles._ConversationNavBar._show_card(nav, 0)

    assert ("update", "old") in calls
    assert ("update", "new") in calls


def test_ima_note_search_query_does_not_match_knowledge_prefix():
    assert OcrTextPanel._ima_note_search_query("搜索知识库：排期") == ""
    assert OcrTextPanel._ima_note_search_query("搜索笔记：排期") == "排期"


def test_ima_note_search_finished_continues_with_qa_prompt():
    panel = type("DummyPanel", (), {})()
    calls = []
    panel.sender = lambda: "worker"
    panel._ima_note_search_worker = "worker"
    panel._thinking_card = type("DummyThinkingCard", (), {"stop_thinking": lambda *a, **kw: None})()
    panel._set_worker_buttons_busy = lambda busy, **kwargs: calls.append(("busy", busy, kwargs))
    panel._show_qa_error = lambda message: calls.append(("error", message))
    panel._run_qa_prompt = lambda text, loading_text, **kwargs: calls.append(("run", text, loading_text, kwargs))
    panel._chat_history = [
        {"role": "user", "content": "搜索笔记：排期", "display_content": "搜索笔记：排期"},
        {"role": "assistant", "content": ""},
    ]

    OcrTextPanel._on_ima_note_search_finished(panel, "IMA笔记检索结果上下文", True, "")

    assert panel._ima_note_search_worker is None
    assert panel._chat_history[0]["content"] == "IMA笔记检索结果上下文"
    assert calls == [("run", "IMA笔记检索结果上下文", "正在回答...", {"button_task": "qa", "result_label": "问答结果"})]


def test_ima_note_search_finished_shows_error():
    panel = type("DummyPanel", (), {})()
    calls = []
    panel.sender = lambda: "worker"
    panel._ima_note_search_worker = "worker"
    panel._thinking_card = type("DummyThinkingCard", (), {"stop_thinking": lambda *a, **kw: calls.append("stop")})()
    panel._stop_dots_animation = lambda: calls.append("dots-stop")
    panel._set_worker_buttons_busy = lambda busy, **kwargs: calls.append(("busy", busy, kwargs))
    panel._show_qa_error = lambda message: calls.append(("error", message))

    OcrTextPanel._on_ima_note_search_finished(panel, "", False, "超时")

    assert panel._ima_note_search_worker is None
    assert calls == [
        "stop",
        "dots-stop",
        ("busy", False, {"task": "qa"}),
        ("error", "IMA 笔记搜索失败：超时"),
    ]


def test_translate_button_aborts_running_worker_instead_of_starting_new_translation():
    class RunningWorker:
        _model_config = {}

        def __init__(self):
            self.interrupted = False
            self.terminated = False
            self.wait_msecs = None

        def isRunning(self):
            return True

        def requestInterruption(self):
            self.interrupted = True

        def terminate(self):
            raise AssertionError("不应强制终止 QThread")

        def wait(self, msecs):
            self.wait_msecs = msecs
            return True

    class DummyTimer:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    class DummyThinking:
        def __init__(self):
            self.stopped = False
            self.hidden = False

        def stop_thinking(self):
            self.stopped = True

        def hide(self):
            self.hidden = True

    panel = type("DummyPanel", (), {})()
    worker = RunningWorker()
    panel._translation_worker = worker
    panel._translation_task = "translate"
    panel._is_chatting = False
    panel._aborted_worker_ids = set()
    panel._stream_flush_timer = DummyTimer()
    panel._thinking_card = DummyThinking()
    panel._set_translation_message = lambda text: setattr(panel, "message", text)
    panel._set_worker_buttons_busy = lambda busy, **kwargs: setattr(panel, "busy_state", (busy, kwargs))
    panel._abort_worker = lambda: OcrTextPanel._abort_worker(panel)
    panel._editor = type("Editor", (), {"toPlainText": lambda self: "Hello"})()
    panel._run_quick_command = lambda *args, **kwargs: setattr(panel, "started_new_translation", True)

    OcrTextPanel._translate_text(panel)

    assert panel._translation_worker is None
    assert panel.message == "翻译已中止。"
    assert panel.busy_state == (False, {"task": "translate"})
    assert worker.interrupted is True
    assert worker.terminated is False
    assert worker.wait_msecs == 80
    assert not getattr(panel, "started_new_translation", False)


def test_prompt_settings_hover_does_not_abort_running_worker():
    class RunningWorker:
        def isRunning(self):
            return True

    panel = type("DummyPanel", (), {})()
    panel._translation_worker = RunningWorker()
    panel._translation_worker_is_running = lambda: OcrTextPanel._translation_worker_is_running(panel)

    assert OcrTextPanel._can_open_prompt_settings_popup_from_hover(panel) is False

    abort_calls = []
    panel._abort_worker = lambda: abort_calls.append("abort")

    OcrTextPanel._show_prompt_settings_popup(panel)

    assert abort_calls == ["abort"]


def test_close_cancels_and_releases_running_translation_worker(monkeypatch):
    import deepcat.ui.post_capture_actions.text_panel_execution as execution

    class RunningWorker:
        pass

    class DummyTimer:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    panel = type("DummyPanel", (), {})()
    worker = RunningWorker()
    panel._translation_worker = worker
    panel._translation_request_id = 4
    panel._aborted_worker_ids = set()
    panel._stream_flush_timer = DummyTimer()
    cancel_calls = []
    monkeypatch.setattr(
        execution,
        "request_thread_cancel",
        lambda target, *, wait_ms=0: cancel_calls.append((target, wait_ms)) or False,
    )

    OcrTextPanel._cancel_translation_worker_for_close(panel)

    assert cancel_calls == [(worker, 80)]
    assert panel._translation_worker is None
    assert panel._translation_request_id == 5
    assert id(worker) in panel._aborted_worker_ids
    assert panel._stream_flush_timer.stopped is True


def test_clear_chat_discards_worker_silently():
    class RunningWorker:
        def __init__(self):
            self.interrupted = False
            self.terminated = False
            self.wait_msecs = None

        def isRunning(self):
            return True

        def requestInterruption(self):
            self.interrupted = True

        def terminate(self):
            raise AssertionError("不应强制终止 QThread")

        def wait(self, msecs):
            self.wait_msecs = msecs
            return True

    class DummyTimer:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    class DummyThinking:
        def __init__(self):
            self.stopped = False
            self.hidden = False

        def stop_thinking(self):
            self.stopped = True

        def hide(self):
            self.hidden = True

    panel = type("DummyPanel", (), {})()
    worker = RunningWorker()
    panel._translation_worker = worker
    panel._translation_task = "qa"
    panel._stream_markdown_text = "partial answer"
    panel._aborted_worker_ids = set()
    panel._stream_flush_timer = DummyTimer()
    panel._thinking_card = DummyThinking()
    panel._suppress_reposition = False
    panel._stop_dots_animation = lambda: setattr(panel, "dots_stopped", True)
    panel._set_worker_buttons_busy = lambda busy, **kwargs: setattr(panel, "busy_state", (busy, kwargs))
    panel._set_translation_message = lambda text: setattr(panel, "message", text)

    OcrTextPanel._discard_worker_for_clear_chat(panel)

    assert id(worker) in panel._aborted_worker_ids
    assert panel._translation_worker is None
    assert panel._translation_task == ""
    assert panel._stream_markdown_text == ""
    assert panel._stream_flush_timer.stopped is True
    assert panel._thinking_card.stopped is True
    assert panel._thinking_card.hidden is True
    assert panel.dots_stopped is True
    assert panel.busy_state == (False, {"task": "qa"})
    assert worker.interrupted is True
    assert worker.terminated is False
    assert worker.wait_msecs == 80
    assert not hasattr(panel, "message")


def test_clear_chat_discards_ima_note_search_worker_silently():
    class DummyTimer:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    class DummyThinking:
        def __init__(self):
            self.stopped = False
            self.hidden = False

        def stop_thinking(self):
            self.stopped = True

        def hide(self):
            self.hidden = True

    panel = type("DummyPanel", (), {})()
    panel._translation_worker = None
    panel._stream_flush_timer = DummyTimer()
    panel._thinking_card = DummyThinking()
    panel._stop_dots_animation = lambda: setattr(panel, "dots_stopped", True)
    panel._set_worker_buttons_busy = lambda busy, **kwargs: setattr(panel, "busy_state", (busy, kwargs))
    panel._abort_ima_note_search_worker = lambda: True

    OcrTextPanel._discard_worker_for_clear_chat(panel)

    assert panel._stream_flush_timer.stopped is True
    assert panel._thinking_card.stopped is True
    assert panel._thinking_card.hidden is True
    assert panel.dots_stopped is True
    assert panel.busy_state == (False, {"task": "qa"})


def test_streaming_render_cache_updates_last_ai_without_full_render():
    class DummyBubbleView:
        def __init__(self):
            self.updated = []
            self.scrolled = False

        def isVisible(self):
            return True

        def update_last_ai(self, text, **kwargs):
            self.updated.append((text, kwargs))

        def scroll_to_bottom(self):
            self.scrolled = True

        def render_messages(self, _history):
            raise AssertionError("缓存命中时不应全量重建历史气泡")

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "流式回答"},
    ]
    panel._stream_render_signature = OcrTextPanel._stream_history_signature(panel._chat_history)
    panel._bubble_view = DummyBubbleView()
    panel._sync_title_bar_visibility = lambda: None
    panel._sync_output_quick_action_bar_visibility = lambda: None

    OcrTextPanel._render_chat_history(panel, is_streaming=True)

    assert panel._bubble_view.updated == [("流式回答", {"streaming": True})]
    assert panel._bubble_view.scrolled is True


def test_streaming_render_appends_new_tail_without_full_render():
    class DummyBubbleView:
        def __init__(self):
            self._bubbles = [object(), object()]
            self.appended = []
            self.shown = False
            self.scrolled_now = False

        def isVisible(self):
            return True

        def append_messages(self, history, *, start_index, keep_bottom=False):
            self.appended.append((list(history), start_index, keep_bottom))
            self._bubbles.extend([object() for _ in history[start_index:]])
            return True

        def show(self):
            self.shown = True

        def scroll_to_bottom_now(self):
            self.scrolled_now = True

        def render_messages(self, *_args, **_kwargs):
            raise AssertionError("追加新提问时不应全量重建旧气泡")

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
        {"role": "user", "content": "新问题"},
        {"role": "assistant", "content": "", "status_text": "正在回答..."},
    ]
    panel._stream_render_signature = None
    panel._bubble_view = DummyBubbleView()
    panel._sync_title_bar_visibility = lambda: None
    panel._sync_output_quick_action_bar_visibility = lambda: None

    OcrTextPanel._render_chat_history(panel, is_streaming=True)

    assert len(panel._bubble_view.appended) == 1
    assert panel._bubble_view.appended[0][1:] == (2, True)
    assert panel._bubble_view.shown is True
    assert panel._bubble_view.scrolled_now is True
    assert panel._stream_render_signature == OcrTextPanel._stream_history_signature(panel._chat_history)


def test_latest_chat_visibility_refresh_clears_width_cache_and_scrolls():
    class DummyBubble:
        def __init__(self):
            self._natural_width = 118

    class DummyViewport:
        def __init__(self):
            self.updated = False

        def update(self):
            self.updated = True

    class DummyBubbleView:
        def __init__(self):
            self._bubbles = [DummyBubble()]
            self.refreshed = []
            self.scrolled_now = 0
            self._viewport = DummyViewport()

        def _invalidate_bubble_width_cache(self):
            for bubble in self._bubbles:
                bubble._natural_width = None

        def refresh_layout(self, *, keep_bottom=False):
            self.refreshed.append(bool(keep_bottom))

        def scroll_to_bottom_now(self):
            self.scrolled_now += 1

        def viewport(self):
            return self._viewport

    panel = type("DummyPanel", (), {})()
    panel._bubble_view = DummyBubbleView()

    OcrTextPanel._ensure_latest_chat_visible_after_layout(panel)

    assert panel._bubble_view._bubbles[0]._natural_width is None
    assert panel._bubble_view.refreshed == [True]
    assert panel._bubble_view.scrolled_now == 1
    assert panel._bubble_view._viewport.updated is True


def test_history_record_switch_scrolls_before_restoring_updates(monkeypatch):
    calls = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay, callback):
            calls.append(("timer", int(delay)))
            if int(delay) <= 32:
                callback()

    monkeypatch.setattr(post_capture_actions.text_panel, "QTimer", FakeTimer)

    class DummyViewport:
        def __init__(self):
            self._updates = True

        def updatesEnabled(self):
            return self._updates

        def setUpdatesEnabled(self, value):
            self._updates = bool(value)
            calls.append(("viewport_updates", bool(value)))

        def update(self):
            calls.append(("viewport_update", self._updates))

        def repaint(self):
            calls.append(("viewport_repaint", self._updates))

    class DummyEffect:
        def __init__(self):
            self._opacity = 1.0
            self._sets = []

        def opacity(self):
            return self._opacity

        def setOpacity(self, value):
            self._opacity = float(value)
            self._sets.append(float(value))

    class DummyBubbleView:
        def __init__(self):
            self._updates = True
            self._viewport = DummyViewport()
            self._effect = DummyEffect()

        def isVisible(self):
            return True

        def updatesEnabled(self):
            return self._updates

        def setUpdatesEnabled(self, value):
            self._updates = bool(value)
            calls.append(("view_updates", bool(value)))

        def viewport(self):
            return self._viewport

        def graphicsEffect(self):
            return self._effect

        def setGraphicsEffect(self, effect):
            self._effect = effect
            calls.append(("effect", effect is not None))

        def render_messages(self, history, *, initial_scroll_to_bottom=False):
            calls.append(("render", bool(initial_scroll_to_bottom), self._updates, self._viewport._updates, len(history)))

        def show(self):
            calls.append(("show", self._updates, self._viewport._updates))

        def scroll_to_bottom_now(self):
            calls.append(("scroll_now", self._updates, self._viewport._updates))

        def scroll_to_bottom(self):
            calls.append(("scroll", self._updates, self._viewport._updates))

        def refresh_layout(self, *, keep_bottom=False):
            calls.append(("refresh", bool(keep_bottom), self._updates, self._viewport._updates))

        def update(self):
            calls.append(("view_update", self._updates))

        def repaint(self):
            calls.append(("view_repaint", self._updates))

    panel = type("DummyPanel", (), {})()
    panel._chat_history = [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
    ]
    panel._bubble_view = DummyBubbleView()
    panel._history_record_switching = True
    panel._history_showing = True
    panel._preserve_cleared_output_area = False
    panel._geometry_frozen = False
    panel._stream_render_signature = None
    panel._translation_info = type("Info", (), {"hide": lambda self: None})()
    panel.geometry = lambda: QRect(20, 30, 640, 520)
    panel.height = lambda: 520
    panel.minimumHeight = lambda: 0
    panel.maximumHeight = lambda: 16777215
    panel.setMinimumHeight = lambda value: calls.append(("panel_min_h", value))
    panel.setMaximumHeight = lambda value: calls.append(("panel_max_h", value))
    panel.setGeometry = lambda geometry: calls.append(("panel_geometry", geometry))
    panel._sync_title_bar_visibility = lambda: calls.append(("sync_title",))
    panel._reposition = lambda: calls.append(("reposition",))
    panel._activate_panel_layouts = lambda: calls.append(("activate_layouts",))
    panel._update_top_seam_cover = lambda: calls.append(("top_seam",))
    panel._sync_output_quick_action_bar_visibility = lambda: None

    OcrTextPanel._render_chat_history(panel, is_streaming=False)

    assert ("render", True, False, False, 2) in calls
    assert ("view_updates", False) in calls
    assert ("viewport_updates", False) in calls
    assert ("view_updates", True) in calls
    assert ("viewport_updates", True) in calls
    scroll_index = calls.index(("scroll_now", False, False))
    assert calls.index(("view_updates", True)) > scroll_index
    assert calls.index(("viewport_updates", True)) > scroll_index
    assert ("refresh", True, False, False) in calls
    assert ("timer", 32) in calls
    assert ("view_repaint", True) in calls
    assert ("viewport_repaint", True) in calls
    assert panel._bubble_view.graphicsEffect() is not None
    assert 0.0 in panel._bubble_view.graphicsEffect()._sets
    assert ("reposition",) not in calls


def test_clear_chat_button_preserves_full_conversation_layout():
    class DummyBubbleView:
        def __init__(self):
            self.visible = True
            self.cleared = False
            self.hidden_count = 0
            self.shown_count = 0
            self.minimum_height_calls = []
            self.maximum_height_calls = []

        def isVisible(self):
            return self.visible

        def height(self):
            return 300

        def clear(self):
            self.cleared = True

        def show(self):
            self.visible = True
            self.shown_count += 1

        def hide(self):
            self.visible = False
            self.hidden_count += 1

        def setMinimumHeight(self, value):
            self.minimum_height_calls.append(value)

        def setMaximumHeight(self, value):
            self.maximum_height_calls.append(value)

    class DummyButton:
        def __init__(self):
            self.shown = False
            self.text = None
            self.tooltip = None

        def show(self):
            self.shown = True

        def setText(self, text):
            self.text = text

        def setToolTip(self, text):
            self.tooltip = text

    class DummyInfo:
        def __init__(self):
            self.cleared = False
            self.hidden = False

        def clear(self):
            self.cleared = True

        def hide(self):
            self.hidden = True

    class DummyThinking:
        def __init__(self):
            self.hidden = False

        def hide(self):
            self.hidden = True

    class DummyEditorContainer:
        def __init__(self):
            self.minimum_height_calls = []
            self.maximum_height_calls = []

        def setMinimumHeight(self, value):
            self.minimum_height_calls.append(value)

        def setMaximumHeight(self, value):
            self.maximum_height_calls.append(value)

    class DummyFrameGeometry:
        def y(self):
            return 100

        def height(self):
            return 520

    panel = type("DummyPanel", (), {})()
    calls = []
    current_geometry = QRect(30, 40, 640, 520)
    bubble_view = DummyBubbleView()
    panel._bubble_view = bubble_view
    panel._translation_info = DummyInfo()
    panel._thinking_card = DummyThinking()
    panel._editor_container = DummyEditorContainer()
    panel._btn_clear_chat = DummyButton()
    panel._btn_qa = DummyButton()
    panel._history_showing = False
    panel._geometry_frozen = False
    panel._chat_history = [{"role": "user", "content": "测试"}, {"role": "assistant", "content": "回答"}]
    panel._current_session_record_id = 42
    panel._pending_attachments = ["img"]
    panel._pending_attachment_previews = ["preview"]
    panel._pending_attachment_labels = ["label"]
    panel._discard_worker_for_clear_chat = lambda: calls.append("discard")
    panel._clear_editor_silently = lambda: calls.append("clear_editor")
    panel._freeze_window_height = lambda: calls.append("freeze")
    panel._thaw_window_height = lambda: calls.append("thaw")
    panel._reposition = lambda: calls.append("reposition")
    def lock_output_area():
        calls.append("lock_output_area")
        OcrTextPanel._lock_editor_container_for_output_area(panel)

    panel._lock_editor_container_for_output_area = lock_output_area
    panel._activate_for_text_input = lambda: calls.append("activate_input")
    panel._update_top_seam_cover = lambda: calls.append("top_seam")
    panel._sync_output_quick_action_bar_visibility = lambda: None
    panel.isVisible = lambda: True
    panel.frameGeometry = lambda: DummyFrameGeometry()
    panel.geometry = lambda: current_geometry
    panel.setGeometry = lambda geometry: calls.append(("set_geometry", geometry))
    panel._cleared_output_restore_geometry = QRect()

    OcrTextPanel._clear_chat_history(panel, clear_editor=False, preserve_output_area=True)

    assert panel._chat_history == []
    assert panel._current_session_record_id is None
    assert panel._is_chatting is False
    assert panel._keep_bottom_y_on_next_reposition is None
    assert bubble_view.cleared is True
    assert bubble_view.visible is True
    assert bubble_view.hidden_count == 0
    assert bubble_view.shown_count == 1
    assert bubble_view.minimum_height_calls == [300]
    assert bubble_view.maximum_height_calls == [16777215]
    assert panel._editor_container.minimum_height_calls == [120]
    assert panel._editor_container.maximum_height_calls == [120]
    assert panel._preserve_cleared_output_area is True
    assert panel._cleared_output_restore_geometry == current_geometry
    assert panel._translation_info.cleared is True
    assert panel._translation_info.hidden is True
    assert panel._thinking_card.hidden is True
    assert panel._pending_attachments == ["img"]
    assert panel._pending_attachment_previews == ["preview"]
    assert panel._pending_attachment_labels == ["label"]
    assert "clear_editor" not in calls
    assert "lock_output_area" in calls
    assert ("set_geometry", current_geometry) in calls
    assert calls == [
        "discard",
        "freeze",
        "lock_output_area",
        ("set_geometry", current_geometry),
        "activate_input",
        "top_seam",
        "thaw",
    ]


def test_top_seam_cover_tracks_content_body_join():
    class DummyCover:
        def __init__(self):
            self.geometry_args = None
            self.shown = False
            self.hidden = False
            self.raised = False

        def setGeometry(self, *args):
            self.geometry_args = args

        def show(self):
            self.shown = True
            self.hidden = False

        def hide(self):
            self.hidden = True
            self.shown = False

        def raise_(self):
            self.raised = True

    class DummyWidget:
        def __init__(self, geometry=None, visible=True):
            self._geometry = geometry or QRect()
            self._visible = visible

        def geometry(self):
            return self._geometry

        def isVisible(self):
            return self._visible

    panel = type("DummyPanel", (), {})()
    cover = DummyCover()
    panel._top_seam_cover = cover
    panel._panel_collapsed = False
    panel._history_showing = False
    panel._content_body = DummyWidget(QRect(0, 30, 640, 500))
    panel._view_stack = DummyWidget(visible=True)
    panel._bubble_view = DummyWidget(visible=True)
    panel.isVisible = lambda: True

    OcrTextPanel._update_top_seam_cover(panel)

    assert cover.geometry_args == (1, 29, 638, 10)
    assert cover.shown is True
    assert cover.hidden is False
    assert cover.raised is True

    panel._history_showing = True
    OcrTextPanel._update_top_seam_cover(panel)

    assert cover.hidden is True

    panel._history_showing = False
    panel._bubble_view = DummyWidget(visible=False)
    OcrTextPanel._update_top_seam_cover(panel)

    assert cover.hidden is True


def test_restore_title_bar_after_window_restore_rehydrates_visible_output_area():
    class DummyWidget:
        def __init__(self, *, visible=False, maximum_height=16777215):
            self.visible = bool(visible)
            self.hidden = not self.visible
            self.minimum_height_calls = []
            self.maximum_height_value = int(maximum_height)
            self.maximum_height_calls = []
            self.fixed_height_calls = []
            self.update_geometry_count = 0
            self.update_count = 0

        def show(self):
            self.visible = True
            self.hidden = False

        def hide(self):
            self.visible = False
            self.hidden = True

        def isVisible(self):
            return self.visible

        def maximumHeight(self):
            return self.maximum_height_value

        def setMaximumHeight(self, value):
            self.maximum_height_value = int(value)
            self.maximum_height_calls.append(int(value))

        def setMinimumHeight(self, value):
            self.minimum_height_calls.append(int(value))

        def setFixedHeight(self, value):
            self.fixed_height_calls.append(int(value))

        def updateGeometry(self):
            self.update_geometry_count += 1

        def update(self):
            self.update_count += 1

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._panel_collapsed = False
    panel._history_showing = False
    panel._view_stack = DummyWidget(visible=False)
    panel._collapsed_widget = DummyWidget(visible=True)
    panel._title_bar_shell = DummyWidget(visible=False, maximum_height=0)
    panel._title_bar = DummyWidget(visible=False, maximum_height=0)
    panel._should_show_title_bar = lambda: True
    panel._sync_title_bar_visibility = lambda: calls.append("sync_title")
    panel._activate_panel_layouts = lambda: calls.append("activate_layouts")
    panel._update_top_seam_cover = lambda: calls.append("top_seam")
    panel._update_interaction_seam_covers = lambda: calls.append("interaction_seams")
    panel._position_floating_window_buttons = lambda: calls.append("floating_buttons")

    OcrTextPanel._restore_title_bar_after_window_restore(panel)

    assert panel._view_stack.visible is True
    assert panel._title_bar_shell.visible is True
    assert panel._title_bar.visible is True
    assert panel._title_bar_shell.minimum_height_calls[-1] == 30
    assert panel._title_bar.minimum_height_calls[-1] == 30
    assert panel._title_bar.maximum_height_calls[-1] == 16777215
    assert panel._title_bar.fixed_height_calls[-1] == 30
    assert panel._title_bar_shell.update_geometry_count == 1
    assert panel._title_bar.update_geometry_count == 1
    assert calls == [
        "sync_title",
        "activate_layouts",
        "top_seam",
        "interaction_seams",
        "floating_buttons",
    ]


def test_restore_title_bar_after_window_restore_keeps_collapsed_capsule():
    class DummyWidget:
        def __init__(self, *, visible=False):
            self.visible = bool(visible)
            self.show_count = 0
            self.hide_count = 0

        def show(self):
            self.visible = True
            self.show_count += 1

        def hide(self):
            self.visible = False
            self.hide_count += 1

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._panel_collapsed = True
    panel._view_stack = DummyWidget(visible=True)
    panel._collapsed_widget = DummyWidget(visible=False)
    panel._title_bar_shell = DummyWidget(visible=False)
    panel._title_bar = DummyWidget(visible=False)
    panel._should_show_title_bar = lambda: (_ for _ in ()).throw(AssertionError("collapsed state should not ask for title visibility"))
    panel._sync_title_bar_visibility = lambda: calls.append("sync_title")
    panel._activate_panel_layouts = lambda: calls.append("activate_layouts")

    OcrTextPanel._restore_title_bar_after_window_restore(panel)

    assert panel._view_stack.visible is False
    assert panel._collapsed_widget.visible is True
    assert panel._title_bar_shell.show_count == 0
    assert panel._title_bar.show_count == 0
    assert calls == ["sync_title", "activate_layouts"]


def test_ai_panel_uncollapse_defers_heavy_reposition(monkeypatch):
    scheduled = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay, callback):
            scheduled.append((int(delay), callback))

    class DummyWidget:
        def __init__(self):
            self.visible = False

        def hide(self):
            self.visible = False

        def show(self):
            self.visible = True

        def setMinimumSize(self, *args):
            calls.append(("widget_min", args))

        def setMaximumSize(self, *args):
            calls.append(("widget_max", args))

        def setMinimumHeight(self, value):
            calls.append(("widget_min_h", int(value)))

        def setMaximumHeight(self, value):
            calls.append(("widget_max_h", int(value)))

    class DummyLayout:
        def setContentsMargins(self, *args):
            calls.append(("margins", args))

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._advance_window_mode_generation = (
        lambda: OcrTextPanel._advance_window_mode_generation(panel)
    )
    panel._force_normal_window_state = (
        lambda: OcrTextPanel._force_normal_window_state(panel)
    )
    panel._panel_collapsed = True
    panel._panel_collapse_restore_size = QSize(640, 520)
    panel._collapsed_widget = DummyWidget()
    panel._view_stack = DummyWidget()
    panel._root_layout = DummyLayout()
    panel._editor_container = DummyWidget()
    panel._bubble_view = DummyWidget()
    panel._bubble_view.hide_transient_overlays = lambda: calls.append("hide_overlays")
    panel._hide_chat_transient_overlays = lambda: OcrTextPanel._hide_chat_transient_overlays(panel)
    panel._set_panel_collapsed_property = lambda collapsed: calls.append(("collapsed_property", bool(collapsed)))
    panel._set_maximized_property = lambda maximized: calls.append(("maximized_property", bool(maximized)))
    panel._release_panel_size_constraints = lambda **kwargs: calls.append(("release_constraints", kwargs))
    panel.setUpdatesEnabled = lambda value: calls.append(("updates", bool(value)))
    panel.setMinimumSize = lambda *args: calls.append(("panel_min", args))
    panel.setMaximumSize = lambda *args: calls.append(("panel_max", args))
    panel.pos = lambda: QPoint(30, 40)
    panel.resize = lambda size: calls.append(("resize", int(size.width()), int(size.height())))
    panel.move = lambda point: calls.append(("move", int(point.x()), int(point.y())))
    panel._clamp_window_pos = lambda point, width, height: QPoint(int(point.x()) + 1, int(point.y()) + 2)
    panel.show = lambda: calls.append("show")
    panel.raise_ = lambda: calls.append("raise")
    panel.update = lambda: calls.append("update")
    panel._finish_panel_uncollapse_after_show = lambda: calls.append("finish")
    panel._reposition = lambda: calls.append("reposition")

    monkeypatch.setattr(post_capture_actions.text_panel, "QTimer", FakeTimer)

    OcrTextPanel._toggle_panel_collapse(panel, False)

    assert panel._panel_collapsed is False
    assert "hide_overlays" in calls
    assert ("resize", 640, 520) in calls
    assert "show" in calls
    assert "raise" in calls
    assert "reposition" not in calls
    assert len(scheduled) == 1

    scheduled[0][1]()
    assert "finish" in calls


def test_ai_panel_maximized_capsule_restore_returns_to_native_maximized(monkeypatch):
    scheduled = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay, callback):
            scheduled.append((int(delay), callback))

    class DummyWidget:
        def __init__(self):
            self.visible = False

        def hide(self):
            self.visible = False

        def show(self):
            self.visible = True

        def setFixedSize(self, size):
            calls.append(("widget_fixed_size", size.width(), size.height()))

        def setMinimumSize(self, *args):
            calls.append(("widget_min_size", args))

        def setMaximumSize(self, *args):
            calls.append(("widget_max_size", args))

    class DummyButton:
        def setIcon(self, _icon):
            calls.append("restore_icon")

        def setToolTip(self, text):
            calls.append(("maximize_tooltip", text))

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._advance_window_mode_generation = (
        lambda: OcrTextPanel._advance_window_mode_generation(panel)
    )
    native_state = {"maximized": True}
    normal_geometry = QRect(100, 120, 640, 520)
    maximized_geometry = QRect(0, 0, 1600, 900)
    panel._panel_collapsed = False
    panel._is_maximized = True
    panel._pre_max_geometry = QRect(normal_geometry)
    panel._watermark = DummyWidget()
    panel._translation_info = DummyWidget()
    panel._view_stack = DummyWidget()
    panel._collapsed_widget = DummyWidget()
    panel._editor_container = DummyWidget()
    panel._bubble_view = DummyWidget()
    panel._title_bar = type(
        "DummyTitleBar",
        (),
        {"_icon_size": 16, "btn_maximize": DummyButton()},
    )()
    panel.geometry = lambda: QRect(maximized_geometry if native_state["maximized"] else normal_geometry)
    panel.frameGeometry = panel.geometry
    panel.width = lambda: panel.geometry().width()
    panel.height = lambda: panel.geometry().height()
    panel.pos = lambda: QPoint(0, 0)
    panel.isMaximized = lambda: native_state["maximized"]
    panel._hide_chat_transient_overlays = lambda: calls.append("hide_overlays")
    panel._set_panel_collapsed_property = lambda collapsed: calls.append(("collapsed_property", bool(collapsed)))
    panel._set_maximized_property = lambda maximized: calls.append(("maximized_property", bool(maximized)))
    panel.setMinimumSize = lambda *args: calls.append(("panel_min_size", args))
    panel.setMaximumSize = lambda *args: calls.append(("panel_max_size", args))

    def show_normal():
        native_state["maximized"] = False
        calls.append("show_normal")

    def show_maximized():
        native_state["maximized"] = True
        calls.append("show_maximized")

    panel.showNormal = show_normal
    panel.showMaximized = show_maximized
    panel._force_normal_window_state = (
        lambda: OcrTextPanel._force_normal_window_state(panel)
    )
    panel._apply_panel_collapse_geometry = (
        lambda geometry: OcrTextPanel._apply_panel_collapse_geometry(panel, geometry)
    )
    panel._ensure_panel_collapse_geometry = (
        lambda generation: OcrTextPanel._ensure_panel_collapse_geometry(panel, generation)
    )
    panel._schedule_panel_collapse_geometry_confirmation = (
        lambda generation: OcrTextPanel._schedule_panel_collapse_geometry_confirmation(panel, generation)
    )
    panel._refresh_panel_collapse_visual = lambda: calls.append("refresh_capsule")
    panel._collapsed_panel_size = lambda: QSize(220, 46)
    panel._collapsed_window_size = lambda: QSize(236, 62)
    panel.setFixedSize = lambda size: calls.append(("panel_fixed_size", size.width(), size.height()))
    panel._clamp_window_pos = lambda point, width, height: QPoint(point)
    panel.move = lambda point: calls.append(("move", point.x(), point.y()))
    panel.show = lambda: calls.append("show")
    panel.raise_ = lambda: calls.append("raise")
    panel._set_native_topmost = lambda value: calls.append(("topmost", bool(value)))
    panel.setUpdatesEnabled = lambda value: calls.append(("updates", bool(value)))
    panel._release_panel_size_constraints = lambda **kwargs: calls.append(("release_constraints", kwargs))
    panel._maximized_work_area_geometry = QRect(maximized_geometry)
    panel._screen_available_geometry_for_window = lambda: QRect(maximized_geometry)
    panel._capture_maximized_work_area = (
        lambda: OcrTextPanel._capture_maximized_work_area(panel)
    )
    panel._target_maximized_work_area = (
        lambda: OcrTextPanel._target_maximized_work_area(panel)
    )
    panel._has_effective_maximized_geometry = (
        lambda tolerance=4: OcrTextPanel._has_effective_maximized_geometry(panel, tolerance)
    )
    panel._force_native_maximized_window_state = show_maximized
    panel._apply_maximized_work_area_fallback = lambda: calls.append("maximized_fallback")
    panel._ensure_native_maximized_state = (
        lambda **kwargs: OcrTextPanel._ensure_native_maximized_state(panel, **kwargs)
    )
    panel._schedule_native_maximized_state_confirmation = (
        lambda: OcrTextPanel._schedule_native_maximized_state_confirmation(panel)
    )
    panel.setGeometry = lambda rect: calls.append(("geometry", QRect(rect)))
    panel.update = lambda: calls.append("update")
    panel._finish_panel_uncollapse_after_show = lambda: calls.append("finish_uncollapse")

    monkeypatch.setattr(post_capture_actions.text_panel, "QTimer", FakeTimer)
    original_make_restore_icon = post_capture_actions.OcrTitleBar._make_restore_icon
    post_capture_actions.OcrTitleBar._make_restore_icon = staticmethod(lambda _size=16: object())
    try:
        OcrTextPanel._toggle_panel_collapse(panel, True)
        assert panel._panel_collapse_restore_maximized is True
        assert panel._panel_collapse_restore_geometry == normal_geometry
        assert "show_normal" in calls
        collapse_geometry_calls = [
            call for call in calls
            if isinstance(call, tuple) and call[0] == "geometry"
        ]
        assert collapse_geometry_calls[-1][1] == QRect(100, 120, 236, 62)

        OcrTextPanel._toggle_panel_collapse(panel, False)
    finally:
        post_capture_actions.OcrTitleBar._make_restore_icon = staticmethod(original_make_restore_icon)

    assert panel._panel_collapsed is False
    assert panel._is_maximized is True
    assert native_state["maximized"] is True
    assert "show_maximized" in calls
    assert ("maximized_property", True) in calls
    assert [
        call for call in calls
        if isinstance(call, tuple) and call[0] == "geometry"
    ] == collapse_geometry_calls
    assert [delay for delay, _callback in scheduled] == [0, 80, 250, 0, 80, 750, 1450, 0]

    native_state["maximized"] = False
    scheduled[5][1]()
    assert native_state["maximized"] is True
    assert calls.count("show_maximized") == 2
    assert panel._pending_native_maximize_restore is False


def test_ai_panel_uncollapse_finish_skips_heavy_layout_once(monkeypatch):
    scheduled = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay, callback):
            scheduled.append((int(delay), callback))

    class DummyBubbleView:
        def is_empty(self):
            return False

    class DummyEditor:
        def setFixedHeight(self, value):
            calls.append(("editor_fixed_h", int(value)))

    class DummyInfo:
        def show(self):
            calls.append("info_show")

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._panel_collapsed = False
    panel._skip_uncollapse_reposition_once = True
    panel._bubble_view = DummyBubbleView()
    panel._editor_container = DummyEditor()
    panel._history_showing = False
    panel._translation_info = DummyInfo()
    panel._panel_layout_state = lambda: "conversation"
    panel._restore_uncollapsed_editor_height = lambda: OcrTextPanel._restore_uncollapsed_editor_height(panel)
    panel._reposition = lambda: calls.append("reposition")
    panel._ensure_latest_chat_visible_after_layout = lambda defer=True: calls.append(("ensure_latest", bool(defer)))
    panel._update_translation_info_pos = lambda: calls.append("info_pos")
    panel._update_watermark_pos = lambda: calls.append("watermark_pos")
    panel._restore_title_bar_after_window_restore = lambda: calls.append("restore_title")
    panel._schedule_title_bar_restore_check = lambda: calls.append("schedule_title_check")
    panel._activate_for_text_input = lambda: calls.append("activate_input")

    monkeypatch.setattr(post_capture_actions.text_panel, "QTimer", FakeTimer)

    OcrTextPanel._finish_panel_uncollapse_after_show(panel)

    assert panel._skip_uncollapse_reposition_once is False
    assert "reposition" not in calls
    assert not any(call[0] == "ensure_latest" for call in calls if isinstance(call, tuple))
    assert ("editor_fixed_h", 120) in calls
    assert "info_show" in calls
    assert "restore_title" in calls
    assert scheduled == [(0, panel._activate_for_text_input)]


def test_ai_panel_uncollapse_finish_restores_empty_input_layout(monkeypatch):
    scheduled = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay, callback):
            scheduled.append((int(delay), callback))

    class DummyBubbleView:
        def is_empty(self):
            return True

        def hide(self):
            calls.append("bubble_hide")

        def setMinimumHeight(self, value):
            calls.append(("bubble_min_h", int(value)))

        def setMaximumHeight(self, value):
            calls.append(("bubble_max_h", int(value)))

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._panel_collapsed = False
    panel._skip_uncollapse_reposition_once = True
    panel._bubble_view = DummyBubbleView()
    panel._history_showing = False
    panel._translation_info = type("DummyInfo", (), {"show": lambda self: calls.append("info_show")})()
    panel._panel_layout_state = lambda: "empty"
    panel.height = lambda: 520
    panel._restore_uncollapsed_editor_height = lambda: OcrTextPanel._restore_uncollapsed_editor_height(panel)
    panel._sync_compact_editor_height = lambda height=None: calls.append(("sync_compact", int(height)))
    panel._reposition = lambda: calls.append("reposition")
    panel._ensure_latest_chat_visible_after_layout = lambda defer=True: calls.append(("ensure_latest", bool(defer)))
    panel._update_translation_info_pos = lambda: calls.append("info_pos")
    panel._update_watermark_pos = lambda: calls.append("watermark_pos")
    panel._restore_title_bar_after_window_restore = lambda: calls.append("restore_title")
    panel._schedule_title_bar_restore_check = lambda: calls.append("schedule_title_check")
    panel._activate_for_text_input = lambda: calls.append("activate_input")

    monkeypatch.setattr(post_capture_actions.text_panel, "QTimer", FakeTimer)

    OcrTextPanel._finish_panel_uncollapse_after_show(panel)

    assert "reposition" not in calls
    assert ("ensure_latest", True) not in calls
    assert "bubble_hide" in calls
    assert ("bubble_min_h", 0) in calls
    assert ("bubble_max_h", 16777215) in calls
    assert ("sync_compact", 520) in calls


def test_ai_panel_uncollapse_restores_saved_geometry(monkeypatch):
    scheduled = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay, callback):
            scheduled.append((int(delay), callback))

    class DummyWidget:
        def hide(self):
            pass

        def show(self):
            pass

        def setMinimumSize(self, *args):
            pass

        def setMaximumSize(self, *args):
            pass

        def setMinimumHeight(self, value):
            pass

        def setMaximumHeight(self, value):
            pass

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._advance_window_mode_generation = (
        lambda: OcrTextPanel._advance_window_mode_generation(panel)
    )
    panel._force_normal_window_state = (
        lambda: OcrTextPanel._force_normal_window_state(panel)
    )
    panel._panel_collapsed = True
    panel._panel_collapse_restore_size = QSize(640, 520)
    panel._panel_collapse_restore_geometry = QRect(100, 120, 640, 520)
    panel._collapsed_widget = DummyWidget()
    panel._view_stack = DummyWidget()
    panel._root_layout = type("DummyLayout", (), {"setContentsMargins": lambda self, *args: None})()
    panel._editor_container = DummyWidget()
    panel._bubble_view = DummyWidget()
    panel._hide_chat_transient_overlays = lambda: calls.append("hide_overlays")
    panel._set_panel_collapsed_property = lambda collapsed: calls.append(("collapsed_property", bool(collapsed)))
    panel._set_maximized_property = lambda maximized: calls.append(("maximized_property", bool(maximized)))
    panel._release_panel_size_constraints = lambda **kwargs: calls.append(("release_constraints", kwargs))
    panel.setUpdatesEnabled = lambda value: calls.append(("updates", bool(value)))
    panel.setMinimumSize = lambda *args: None
    panel.setMaximumSize = lambda *args: None
    panel.pos = lambda: QPoint(30, 40)
    panel.resize = lambda size: calls.append(("resize", int(size.width()), int(size.height())))
    panel.move = lambda point: calls.append(("move", int(point.x()), int(point.y())))
    panel.setGeometry = lambda rect: calls.append(("geometry", rect.x(), rect.y(), rect.width(), rect.height()))
    panel._clamp_window_pos = lambda point, width, height: QPoint(int(point.x()) + 3, int(point.y()) + 4)
    panel.show = lambda: calls.append("show")
    panel.raise_ = lambda: calls.append("raise")
    panel.update = lambda: calls.append("update")
    panel._finish_panel_uncollapse_after_show = lambda: calls.append("finish")

    monkeypatch.setattr(post_capture_actions.text_panel, "QTimer", FakeTimer)

    OcrTextPanel._toggle_panel_collapse(panel, False)

    assert ("geometry", 103, 124, 640, 520) in calls
    assert not any(call[0] in {"resize", "move"} for call in calls if isinstance(call, tuple))
    assert panel._skip_uncollapse_reposition_once is True
    assert len(scheduled) == 1


def test_bubble_list_hides_transient_overlays_before_clear():
    calls = []

    class DummyBubble:
        def hide_transient_overlays(self):
            calls.append("bubble_hide")

    class DummyNav:
        def set_items(self, items):
            calls.append(("nav_items", list(items)))

        def _show_card(self, idx):
            calls.append(("nav_card", int(idx)))

    class DummyTimer:
        def stop(self):
            calls.append("timer_stop")

    class DummyLayout:
        def count(self):
            return 1

    view = chat_bubbles.BubbleListView.__new__(chat_bubbles.BubbleListView)
    view._layout_flush_timer = DummyTimer()
    view._stream_flush_timer = DummyTimer()
    view._pending_stream_updates = {}
    view._pending_keep_bottom = True
    view._scroll_to_bottom_pending = True
    view._scroll_generation = 0
    view._progressive_generation = 0
    view._vbox = DummyLayout()
    view._bubbles = [DummyBubble()]
    view._last_ai = object()
    view._conversation_nav = DummyNav()
    view._update_placeholder_visibility = lambda: None

    chat_bubbles.BubbleListView.clear(view)

    assert calls[:3] == ["timer_stop", "timer_stop", "bubble_hide"]
    assert ("nav_card", -1) in calls
    assert ("nav_items", []) in calls
    assert view._bubbles == []


def test_interaction_seam_covers_track_thinking_and_editor_edges():
    class DummyCover:
        def __init__(self):
            self._geometry = QRect()
            self.shown = False
            self.hidden = False
            self.raised = False
            self.updated = False

        def geometry(self):
            return QRect(self._geometry)

        def setGeometry(self, geometry):
            self._geometry = QRect(geometry)

        def show(self):
            self.shown = True
            self.hidden = False

        def hide(self):
            self.hidden = True
            self.shown = False

        def raise_(self):
            self.raised = True

        def update(self):
            self.updated = True

    class DummyParent:
        def __init__(self):
            self.updates = []

        def isVisible(self):
            return True

        def width(self):
            return 640

        def update(self, rect):
            self.updates.append(QRect(rect))

    class DummyTarget:
        def __init__(self, y, visible=True):
            self.y = y
            self.visible = visible

        def isVisible(self):
            return self.visible

        def mapTo(self, _parent, _point):
            return QPoint(0, self.y)

    parent = DummyParent()
    top_cover = DummyCover()
    editor_cover = DummyCover()
    thinking = DummyTarget(120, visible=True)
    editor = DummyTarget(188, visible=True)
    bubble = DummyTarget(0, visible=True)
    panel = type("DummyPanel", (), {})()
    panel._panel_collapsed = False
    panel._history_showing = False
    panel._interaction_container = parent
    panel._interaction_top_seam_cover = top_cover
    panel._editor_top_seam_cover = editor_cover
    panel._bubble_view = bubble
    panel._thinking_card = thinking
    panel._editor_container = editor
    panel.isVisible = lambda: True

    OcrTextPanel._update_interaction_seam_covers(panel)

    assert top_cover.geometry() == QRect(1, 118, 638, 4)
    assert editor_cover.geometry() == QRect(1, 186, 638, 4)
    assert top_cover.shown is True
    assert editor_cover.shown is True
    assert top_cover.raised is True
    assert editor_cover.raised is True
    assert parent.updates

    thinking.visible = False
    OcrTextPanel._update_interaction_seam_covers(panel)

    assert top_cover.geometry() == QRect(1, 186, 638, 4)
    assert editor_cover.hidden is True


def test_refresh_interaction_surface_updates_container_viewport_and_thinking_card():
    calls = []

    class DummyWidget:
        def __init__(self, name, *, visible=True):
            self.name = name
            self.visible = visible

        def isVisible(self):
            return self.visible

        def update(self):
            calls.append(("update", self.name))

    class DummyBubbleView(DummyWidget):
        def __init__(self):
            super().__init__("bubble")
            self._viewport = DummyWidget("viewport")

        def viewport(self):
            return self._viewport

    panel = type("DummyPanel", (), {})()
    panel._interaction_container = DummyWidget("container")
    panel._bubble_view = DummyBubbleView()
    panel._thinking_card = DummyWidget("thinking")
    panel._update_interaction_seam_covers = lambda **kwargs: calls.append(("seams", kwargs))

    OcrTextPanel._refresh_interaction_surface(panel)

    assert ("update", "container") in calls
    assert ("update", "bubble") in calls
    assert ("update", "viewport") in calls
    assert ("update", "thinking") in calls
    assert ("seams", {"defer": False}) in calls


def test_thinking_card_geometry_change_prefers_full_surface_refresh():
    calls = []
    panel = type("DummyPanel", (), {})()
    panel._refresh_interaction_surface = lambda **kwargs: calls.append(("surface", kwargs))
    panel._update_interaction_seam_covers = lambda **kwargs: calls.append(("seams", kwargs))
    card = type("DummyThinkingCard", (), {"window": lambda self: panel})()

    post_capture_actions._ThinkingCard._notify_panel_seam_refresh(card)

    assert calls == [("surface", {"defer": True})]

    def failed_surface_refresh(**_kwargs):
        raise RuntimeError("surface unavailable")

    panel._refresh_interaction_surface = failed_surface_refresh
    post_capture_actions._ThinkingCard._notify_panel_seam_refresh(card)

    assert calls[-1] == ("seams", {"defer": True})


def test_typing_after_clear_keeps_output_area_layout():
    class DummyBubbleView:
        def __init__(self):
            self.visible = True
            self.clear_count = 0
            self.hide_count = 0
            self.show_count = 0
            self.minimum_height_calls = []
            self.maximum_height_calls = []

        def isVisible(self):
            return self.visible

        def clear(self):
            self.clear_count += 1

        def show(self):
            self.visible = True
            self.show_count += 1

        def hide(self):
            self.visible = False
            self.hide_count += 1

        def setMinimumHeight(self, value):
            self.minimum_height_calls.append(value)

        def setMaximumHeight(self, value):
            self.maximum_height_calls.append(value)

    class DummyEditorContainer:
        def __init__(self):
            self.minimum_height_calls = []
            self.maximum_height_calls = []

        def setMinimumHeight(self, value):
            self.minimum_height_calls.append(value)

        def setMaximumHeight(self, value):
            self.maximum_height_calls.append(value)

    class DummyInfo:
        def __init__(self):
            self.cleared = False
            self.hidden = False

        def clear(self):
            self.cleared = True

        def hide(self):
            self.hidden = True

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._loading_text = False
    panel._is_chatting = False
    panel._history_showing = False
    panel._preserve_cleared_output_area = True
    panel._cleared_output_restore_geometry = QRect(30, 40, 640, 520)
    panel._cleared_output_restore_bubble_height = 300
    panel._bubble_view = DummyBubbleView()
    panel._editor_container = DummyEditorContainer()
    panel._translation_info = DummyInfo()
    panel._sync_pending_attachments_from_editor_text = lambda: calls.append("sync_attachments")
    panel._activate_panel_layouts = lambda: calls.append("activate_layouts")
    panel._lock_editor_container_for_output_area = (
        lambda: OcrTextPanel._lock_editor_container_for_output_area(panel)
    )

    OcrTextPanel._on_source_text_changed(panel)

    assert calls == ["sync_attachments", "activate_layouts"]
    assert panel._bubble_view.clear_count == 1
    assert panel._bubble_view.show_count == 1
    assert panel._bubble_view.hide_count == 0
    assert panel._bubble_view.minimum_height_calls == [300]
    assert panel._bubble_view.maximum_height_calls == [16777215]
    assert panel._editor_container.minimum_height_calls[-1] == 120
    assert panel._editor_container.maximum_height_calls[-1] == 120
    assert panel._translation_info.cleared is True
    assert panel._translation_info.hidden is True
    assert panel._preserve_cleared_output_area is True


def test_restore_after_maximize_preserves_cleared_output_geometry():
    class DummyButton:
        def __init__(self):
            self.tooltip = None

        def setIcon(self, _icon):
            pass

        def setToolTip(self, text):
            self.tooltip = text

    class DummyTitleBar:
        def __init__(self):
            self._icon_size = 16
            self.btn_maximize = DummyButton()

    class DummyBubbleView:
        def __init__(self):
            self._block_refresh = False
            self.visible = True
            self.clear_count = 0
            self.show_count = 0
            self.minimum_height_calls = []
            self.maximum_height_calls = []

        def isVisible(self):
            return self.visible

        def clear(self):
            self.clear_count += 1

        def show(self):
            self.visible = True
            self.show_count += 1

        def setMinimumHeight(self, value):
            self.minimum_height_calls.append(value)

        def setMaximumHeight(self, value):
            self.maximum_height_calls.append(value)

    class DummyEditorContainer:
        def __init__(self):
            self.minimum_height_calls = []
            self.maximum_height_calls = []

        def setMinimumHeight(self, value):
            self.minimum_height_calls.append(value)

        def setMaximumHeight(self, value):
            self.maximum_height_calls.append(value)

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._advance_window_mode_generation = (
        lambda: OcrTextPanel._advance_window_mode_generation(panel)
    )
    panel._force_normal_window_state = (
        lambda: OcrTextPanel._force_normal_window_state(panel)
    )
    target_geo = QRect(10, 20, 640, 520)
    polluted_pre_max_geo = QRect(10, 20, 640, 900)
    panel._is_maximized = True
    panel._preserve_cleared_output_area = True
    panel._cleared_output_restore_geometry = target_geo
    panel._cleared_output_restore_bubble_height = 300
    panel._pre_max_geometry = polluted_pre_max_geo
    panel._title_bar = DummyTitleBar()
    panel._bubble_view = DummyBubbleView()
    panel._editor_container = DummyEditorContainer()
    panel._set_maximized_property = lambda state: calls.append(("max_prop", state))
    panel.setMinimumSize = lambda *args: calls.append(("min_size", args))
    panel.setMaximumSize = lambda *args: calls.append(("max_size", args))
    panel.setMinimumHeight = lambda value: calls.append(("min_h", value))
    panel.setMaximumHeight = lambda value: calls.append(("max_h", value))
    panel.setGeometry = lambda geometry: calls.append(("geometry", geometry))
    panel.showNormal = lambda: calls.append("show_normal")
    panel._release_panel_size_constraints = (
        lambda **kwargs: OcrTextPanel._release_panel_size_constraints(panel, **kwargs)
    )
    panel._lock_editor_container_for_output_area = (
        lambda: OcrTextPanel._lock_editor_container_for_output_area(panel)
    )
    panel._apply_restored_panel_geometry = (
        lambda geometry: OcrTextPanel._apply_restored_panel_geometry(panel, geometry)
    )
    panel._activate_panel_layouts = lambda: calls.append("activate_layouts")
    panel._update_top_seam_cover = lambda: calls.append("top_seam")
    panel._update_interaction_seam_covers = lambda **kwargs: calls.append(("interaction_seams", kwargs))
    panel._sync_bubble_view_after_geometry_change = lambda **kwargs: calls.append(("sync_bubble", kwargs))
    panel._reposition = lambda keep_bottom_y=None, **kwargs: calls.append(("reposition", keep_bottom_y, kwargs))

    original_make_maximize_icon = post_capture_actions.OcrTitleBar._make_maximize_icon
    post_capture_actions.OcrTitleBar._make_maximize_icon = staticmethod(lambda _size=16: object())
    try:
        OcrTextPanel.toggle_maximize(panel)
    finally:
        post_capture_actions.OcrTitleBar._make_maximize_icon = staticmethod(original_make_maximize_icon)

    assert panel._is_maximized is False
    assert panel._title_bar.btn_maximize.tooltip == "最大化"
    assert "show_normal" in calls
    assert ("geometry", target_geo) in calls
    assert ("geometry", polluted_pre_max_geo) not in calls
    assert panel._bubble_view.clear_count == 1
    assert panel._bubble_view.show_count == 1
    assert panel._bubble_view.minimum_height_calls[-1] == 300
    assert panel._bubble_view.maximum_height_calls[-1] == 16777215
    assert panel._editor_container.minimum_height_calls[-1] == 120
    assert panel._editor_container.maximum_height_calls[-1] == 120
    assert panel._bubble_view._block_refresh is False
    assert ("reposition", target_geo.y() + target_geo.height(), {}) not in calls
    assert calls.count(("geometry", target_geo)) == 1
    assert (
        "sync_bubble",
        {
            "keep_bottom": True,
            "scroll_anchor": {"at_bottom": True, "distance_from_bottom": 0},
        },
    ) in calls
    assert "activate_layouts" in calls


def test_restore_after_maximize_uses_original_geometry_for_content_chat():
    class DummyButton:
        def __init__(self):
            self.tooltip = None

        def setIcon(self, _icon):
            pass

        def setToolTip(self, text):
            self.tooltip = text

        def sizeHint(self):
            return type("Size", (), {"height": lambda self: 40})()

    class DummyTitleBar:
        def __init__(self):
            self._icon_size = 16
            self.btn_maximize = DummyButton()

    class DummyBubbleView:
        def __init__(self):
            self._block_refresh = False
            self.minimum_height_calls = []
            self.maximum_height_calls = []

        def isVisible(self):
            return True

        def setMinimumHeight(self, value):
            self.minimum_height_calls.append(value)

        def setMaximumHeight(self, value):
            self.maximum_height_calls.append(value)

        def content_height(self):
            return 80

        def capture_viewport_scroll_anchor(self):
            return {"at_bottom": False, "distance_from_bottom": 75}

    class DummyEditorContainer:
        def __init__(self):
            self.minimum_height_calls = []
            self.maximum_height_calls = []
            self.fixed_height_calls = []

        def setMinimumHeight(self, value):
            self.minimum_height_calls.append(value)

        def setMaximumHeight(self, value):
            self.maximum_height_calls.append(value)

        def setFixedHeight(self, value):
            self.fixed_height_calls.append(value)

    class DummyThinking:
        def isVisible(self):
            return False

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._advance_window_mode_generation = (
        lambda: OcrTextPanel._advance_window_mode_generation(panel)
    )
    panel._force_normal_window_state = (
        lambda: OcrTextPanel._force_normal_window_state(panel)
    )
    target_geo = QRect(12, 24, 640, 400)
    panel._is_maximized = True
    panel._is_chatting = True
    panel._preserve_cleared_output_area = False
    panel._pre_max_geometry = target_geo
    panel._title_bar = DummyTitleBar()
    panel._btn_translate = DummyButton()
    panel._bubble_view = DummyBubbleView()
    panel._editor_container = DummyEditorContainer()
    panel._thinking_card = DummyThinking()
    panel._set_maximized_property = lambda state: calls.append(("max_prop", state))
    panel.setMinimumSize = lambda *args: calls.append(("min_size", args))
    panel.setMaximumSize = lambda *args: calls.append(("max_size", args))
    panel.setMaximumHeight = lambda value: calls.append(("max_h", value))
    panel.setGeometry = lambda geometry: calls.append(("geometry", geometry))
    panel.showNormal = lambda: calls.append("show_normal")
    panel.updatesEnabled = lambda: True
    panel.setUpdatesEnabled = lambda enabled: calls.append(("panel_updates", bool(enabled)))
    panel.update = lambda: calls.append("panel_update")
    panel._release_panel_size_constraints = (
        lambda **kwargs: OcrTextPanel._release_panel_size_constraints(panel, **kwargs)
    )
    panel.frameGeometry = lambda: target_geo
    panel._pre_max_editor_height = 120
    panel._pre_max_bubble_height = 260
    panel._apply_restored_panel_geometry = (
        lambda geometry: OcrTextPanel._apply_restored_panel_geometry(panel, geometry)
    )
    panel._activate_panel_layouts = lambda: calls.append("activate_layouts")
    panel._update_top_seam_cover = lambda: calls.append("top_seam")
    panel._update_interaction_seam_covers = lambda **kwargs: calls.append(("interaction_seams", kwargs))
    panel._sync_bubble_view_after_geometry_change = lambda **kwargs: calls.append(("sync_bubble", kwargs))
    panel._reposition = lambda keep_bottom_y=None, **kwargs: calls.append(("reposition", keep_bottom_y, kwargs))

    original_make_maximize_icon = post_capture_actions.OcrTitleBar._make_maximize_icon
    post_capture_actions.OcrTitleBar._make_maximize_icon = staticmethod(lambda _size=16: object())
    try:
        OcrTextPanel.toggle_maximize(panel)
    finally:
        post_capture_actions.OcrTitleBar._make_maximize_icon = staticmethod(original_make_maximize_icon)

    assert panel._is_maximized is False
    assert panel._title_bar.btn_maximize.tooltip == "最大化"
    assert "show_normal" in calls
    assert ("geometry", target_geo) in calls
    assert not any(call[0] == "reposition" for call in calls if isinstance(call, tuple))
    assert panel._editor_container.fixed_height_calls[-1] == 120
    assert panel._bubble_view.minimum_height_calls[-1] == 0
    assert panel._bubble_view.maximum_height_calls[-1] == 16777215
    assert (
        "sync_bubble",
        {
            "keep_bottom": False,
            "scroll_anchor": {"at_bottom": False, "distance_from_bottom": 75},
        },
    ) in calls
    assert calls.index(("panel_updates", False)) < calls.index("show_normal")
    assert calls.index(("panel_updates", True)) > calls.index(
        (
            "sync_bubble",
            {
                "keep_bottom": False,
                "scroll_anchor": {"at_bottom": False, "distance_from_bottom": 75},
            },
        )
    )


def test_maximize_snapshots_geometry_before_property_refresh(monkeypatch):
    class DummyButton:
        def __init__(self):
            self.tooltip = None

        def setIcon(self, _icon):
            pass

        def setToolTip(self, text):
            self.tooltip = text

        def sizeHint(self):
            return type("Size", (), {"height": lambda self: 40})()

    class DummyTitleBar:
        def __init__(self):
            self._icon_size = 16
            self.btn_maximize = DummyButton()

    class DummyBubbleView:
        def __init__(self):
            self._block_refresh = False
            self.minimum_height_calls = []
            self.maximum_height_calls = []

        def isVisible(self):
            return True

        def height(self):
            return 260

        def setMinimumHeight(self, value):
            self.minimum_height_calls.append(value)

        def setMaximumHeight(self, value):
            self.maximum_height_calls.append(value)

    class DummyEditorContainer:
        def __init__(self):
            self.minimum_height_calls = []
            self.maximum_height_calls = []

        def height(self):
            return 120

        def setMinimumHeight(self, value):
            self.minimum_height_calls.append(value)

        def setMaximumHeight(self, value):
            self.maximum_height_calls.append(value)

    class DummyThinking:
        def isVisible(self):
            return False

    panel = type("DummyPanel", (), {})()
    calls = []
    panel._advance_window_mode_generation = (
        lambda: OcrTextPanel._advance_window_mode_generation(panel)
    )
    original_geo = QRect(100, 120, 640, 420)
    polluted_geo = QRect(200, 4, 1200, 892)
    geometry_state = {"value": QRect(original_geo)}

    def set_geometry(*args):
        if len(args) == 1:
            geometry_state["value"] = QRect(args[0])
        else:
            geometry_state["value"] = QRect(*args)
        calls.append(("geometry", QRect(geometry_state["value"])))

    def set_maximized_property(state):
        calls.append(("max_prop", state))
        if state:
            geometry_state["value"] = QRect(polluted_geo)

    panel._is_maximized = False
    panel._history_showing = False
    panel._title_bar = DummyTitleBar()
    panel._btn_translate = DummyButton()
    panel._bubble_view = DummyBubbleView()
    panel._editor_container = DummyEditorContainer()
    panel._thinking_card = DummyThinking()
    panel.geometry = lambda: QRect(geometry_state["value"])
    panel.setGeometry = set_geometry
    panel.setMinimumSize = lambda *args: calls.append(("min_size", args))
    panel.setMaximumSize = lambda *args: calls.append(("max_size", args))
    panel._set_maximized_property = set_maximized_property
    panel._release_panel_size_constraints = (
        lambda **kwargs: OcrTextPanel._release_panel_size_constraints(panel, **kwargs)
    )
    panel._capture_maximized_work_area = lambda: calls.append("capture_maximized_work_area")
    panel._force_native_maximized_window_state = lambda: calls.append("show_maximized")
    panel._schedule_native_maximized_state_confirmation = lambda: calls.append("schedule_maximized_confirmation")
    panel._finish_native_maximize_layout = lambda **kwargs: calls.append(("finish_maximized_layout", kwargs))
    panel._compact_editor_fill_height = lambda _height: 120
    panel._activate_panel_layouts = lambda: calls.append("activate_layouts")
    panel._update_top_seam_cover = lambda: calls.append("top_seam")
    panel._sync_bubble_view_after_geometry_change = lambda **kwargs: calls.append(("sync_bubble", kwargs))

    original_make_restore_icon = post_capture_actions.OcrTitleBar._make_restore_icon
    post_capture_actions.OcrTitleBar._make_restore_icon = staticmethod(lambda _size=16: object())
    try:
        OcrTextPanel.toggle_maximize(panel)
    finally:
        post_capture_actions.OcrTitleBar._make_restore_icon = staticmethod(original_make_restore_icon)

    assert panel._is_maximized is True
    assert panel._pre_max_geometry == original_geo
    assert panel._pre_max_geometry != polluted_geo
    assert panel._pre_max_editor_height == 120
    assert panel._pre_max_bubble_height == 260
    assert panel._title_bar.btn_maximize.tooltip == "还原窗口"
    assert panel._bubble_view._block_refresh is False
    assert "show_maximized" in calls
    assert "schedule_maximized_confirmation" in calls
    assert ("finish_maximized_layout", {"sync_bubbles": False}) in calls
    assert not any(call[0] == "geometry" for call in calls if isinstance(call, tuple))
    assert (
        "sync_bubble",
        {
            "keep_bottom": True,
            "scroll_anchor": {"at_bottom": True, "distance_from_bottom": 0},
        },
    ) in calls


def test_geometry_change_sync_uses_single_atomic_bubble_refresh():
    calls = []

    class DummyBubbleView:
        def isVisible(self):
            return True

        def refresh_for_viewport_resize(self, anchor):
            calls.append(("atomic_refresh", dict(anchor)))

        def refresh_layout(self, **_kwargs):
            calls.append(("legacy_layout",))

        def scroll_to_bottom(self):
            calls.append(("delayed_scroll",))

    panel = type("DummyPanel", (), {"_bubble_view": DummyBubbleView()})()
    anchor = {"at_bottom": False, "distance_from_bottom": 96}

    OcrTextPanel._sync_bubble_view_after_geometry_change(
        panel,
        keep_bottom=False,
        scroll_anchor=anchor,
    )

    assert calls == [("atomic_refresh", anchor)]


def test_finished_signal_from_cleared_worker_is_ignored():
    worker = object()
    panel = type("DummyPanel", (), {})()
    panel._translation_worker = worker
    panel._aborted_worker_ids = {id(worker)}
    panel.sender = lambda: (_ for _ in ()).throw(AssertionError("生命周期判断不应再调用 QObject.sender()"))
    panel._chat_history = [{"role": "user", "content": "old"}, {"role": "assistant", "content": ""}]
    panel._is_chatting = False
    panel._render_chat_history = lambda *args, **kwargs: setattr(panel, "rendered", True)
    panel._set_translation_message = lambda *args, **kwargs: setattr(panel, "message", args[0])

    OcrTextPanel._on_translation_finished(panel, "late result", True, "", 1, id(worker))

    assert panel._translation_worker is None
    assert id(worker) not in panel._aborted_worker_ids
    assert panel._chat_history[-1]["content"] == ""
    assert not hasattr(panel, "rendered")
    assert not hasattr(panel, "message")


def test_ocr_text_panel_auto_expand_when_collapsed():
    class DummyPanel:
        def __init__(self):
            self._editor = type("DummyEditor", (), {"toPlainText": lambda self: "Hello"})()
            self._btn_collapse = type("DummyCollapse", (), {"_collapsed": True, "set_collapsed": lambda self, state: None})()
            self._collapsed_called = False
            self._source_lang = type("Combo", (), {"currentText": lambda self: "自动检测"})()
            self._target_lang = type("Combo", (), {"currentText": lambda self: "双向"})()

        def _toggle_bottom_collapsed(self):
            self._collapsed_called = True
            self._btn_collapse._collapsed = False

        def _run_quick_command(self, *args, **kwargs):
            pass

    panel = DummyPanel()
    OcrTextPanel._translate_text(panel)
    assert panel._collapsed_called is True


def test_ocr_text_panel_no_expand_when_already_expanded():
    class DummyPanel:
        def __init__(self):
            self._editor = type("DummyEditor", (), {"toPlainText": lambda self: "Hello"})()
            self._btn_collapse = type("DummyCollapse", (), {"_collapsed": False, "set_collapsed": lambda self, state: None})()
            self._collapsed_called = False
            self._ocr_input_auto_height_cap = 800
            self._source_lang = type("Combo", (), {"currentText": lambda self: "自动检测"})()
            self._target_lang = type("Combo", (), {"currentText": lambda self: "双向"})()

        def _toggle_bottom_collapsed(self):
            self._collapsed_called = True

        def _run_quick_command(self, *args, **kwargs):
            pass

    panel = DummyPanel()
    OcrTextPanel._translate_text(panel)
    assert panel._collapsed_called is False
    assert panel._ocr_input_auto_height_cap == 0


def test_ocr_text_panel_sync_pending_attachments_after_placeholder_deleted():
    class DummyEditor:
        def __init__(self, text):
            self._text = text

        def toPlainText(self):
            return self._text

    panel = type("DummyPanel", (), {})()
    panel._editor = DummyEditor("正文 📎 [已添加附件: b.png] ")
    panel._pending_attachments = [{"name": "a.png"}, {"name": "b.png"}]
    panel._pending_attachment_previews = [None, {"id": "preview-b"}]
    panel._pending_attachment_labels = ["a.png", "b.png"]

    OcrTextPanel._sync_pending_attachments_from_editor_text(panel)

    assert panel._pending_attachments == [{"name": "b.png"}]
    assert panel._pending_attachment_previews == [{"id": "preview-b"}]
    assert panel._pending_attachment_labels == ["b.png"]


def test_ocr_text_panel_sync_pending_attachments_clears_when_all_placeholders_deleted():
    class DummyEditor:
        def toPlainText(self):
            return "正文"

    panel = type("DummyPanel", (), {})()
    panel._editor = DummyEditor()
    panel._pending_attachments = [{"name": "a.png"}]
    panel._pending_attachment_previews = [{"id": "preview-a"}]
    panel._pending_attachment_labels = ["a.png"]

    OcrTextPanel._sync_pending_attachments_from_editor_text(panel)

    assert panel._pending_attachments == []
    assert panel._pending_attachment_previews == []
    assert panel._pending_attachment_labels == []


def test_attachment_preview_chip_detects_text_file_kind():
    attachment = {
        "type": "file_url",
        "file_url": {
            "url": "data:text/plain;base64,SGVsbG8=",
            "name": "notes.txt",
        },
    }

    assert post_capture_actions._AttachmentPreviewChip.attachment_file_kind("notes.txt", attachment) == "text"


def test_attachment_preview_chip_detects_unknown_file_kind():
    attachment = {
        "type": "file_url",
        "file_url": {
            "url": "data:application/octet-stream;base64,AA==",
            "name": "payload",
        },
    }

    assert post_capture_actions._AttachmentPreviewChip.attachment_file_kind("payload", attachment) == "unknown"


def test_post_capture_ai_recognition_action_uses_ai_callback():
    image = object()
    calls = []

    class DummyActions:
        _image_pending = False
        _on_toast = lambda self, title, message, duration: calls.append(("toast", title, message, duration))

        def export_image_bgr(self):
            return image

        def _on_ai_recognize(self, incoming):
            calls.append(("ai", incoming))
            return True

        def _close_all(self):
            calls.append("close")

    post_capture_actions.PostCaptureActions._ai_recognize(DummyActions())

    assert calls == [("ai", image), "close"]


def test_post_capture_ocr_success_opens_in_adaptive_ai_input_view():
    calls = []

    class FakePanel:
        def set_text_and_reposition(self, *args, **kwargs):
            calls.append(("set_text", args, kwargs))

    dummy = SimpleNamespace(
        _region=QRect(10, 20, 300, 120),
        _on_ocr_panel_visibility_changed=lambda visible: calls.append(("visible", visible)),
    )
    dummy._ensure_ocr_panel = lambda: FakePanel()

    post_capture_actions.PostCaptureActions._show_ocr_panel(dummy, "识别结果", elapsed=1.25)

    assert calls[0][0] == "set_text"
    assert calls[0][1][0] == "识别结果"
    assert calls[0][2]["auto_ocr_translate"] is False
    assert calls[0][2]["force_initial_view"] is True
    assert calls[0][2]["ocr_input_auto_height"] is True
    assert calls[0][2]["input_origin"] == "ocr"
    assert calls[1] == ("visible", True)


def test_post_capture_ocr_failure_stays_in_initial_input_view():
    calls = []

    class FakePanel:
        def set_text_and_reposition(self, *args, **kwargs):
            calls.append((args, kwargs))

    dummy = SimpleNamespace(
        _region=QRect(10, 20, 300, 120),
        _on_ocr_text_result=lambda *_args: (_ for _ in ()).throw(AssertionError("失败结果不应发送到AI")),
        _on_ocr_panel_visibility_changed=lambda _visible: None,
    )
    dummy._ensure_ocr_panel = lambda: FakePanel()

    post_capture_actions.PostCaptureActions._show_ocr_panel(
        dummy,
        "【未识别到文字】\n（无识别结果）",
        elapsed=1.25,
        submit_to_ai=False,
    )

    assert calls[0][1]["ocr_input_auto_height"] is False
    assert calls[0][1]["auto_ocr_translate"] is False
    assert calls[0][1]["input_origin"] == "ocr"


def test_ocr_success_closes_capture_controls_but_failure_keeps_them(monkeypatch):
    class FakeButton:
        def setEnabled(self, _enabled):
            pass

    class FakeTooltip:
        def hide(self):
            pass

    def build_dummy(text: str):
        worker = object()
        calls = []
        dummy = SimpleNamespace(
            _ocr_worker=worker,
            _ocr_cancel_token=object(),
            _ocr_started_ts=time.time(),
            _image_bgr=np.zeros((10, 10, 3), dtype=np.uint8),
            _fix_ocr_backslash=lambda value: value,
            _show_ocr_panel=lambda value, **kwargs: calls.append(("panel", value, kwargs)),
            _show_action_toast=lambda *args: calls.append(("toast", args)),
            _set_action_button_text=lambda *_args: None,
            _btn_ocr=FakeButton(),
            _refresh_ocr_availability=lambda: None,
            _ocr_busy_tooltip_custom=False,
            _icon_tooltip=FakeTooltip(),
            _close_all=lambda: calls.append(("close", None)),
            sender=lambda: worker,
        )
        post_capture_actions.PostCaptureActions._on_ocr_extract_finished(dummy, text, "提示", "")
        return calls

    monkeypatch.setattr(post_capture_actions.QTimer, "singleShot", lambda _delay, callback: callback())

    success_calls = build_dummy("识别成功")
    failure_calls = build_dummy("")

    assert ("close", None) in success_calls
    assert success_calls[0][2]["submit_to_ai"] is True
    assert ("close", None) not in failure_calls
    assert failure_calls[0][2]["submit_to_ai"] is False


def test_post_capture_close_all_retains_ocr_panel_until_user_closes_it():
    import gc
    import weakref

    from deepcat.ui.post_capture_actions import actions as post_capture_actions_module

    calls = []

    class FakeDestroyedSignal:
        def __init__(self):
            self.disconnected = []
            self.connected = []

        def connect(self, callback):
            self.connected.append(callback)

        def disconnect(self, callback):
            self.disconnected.append(callback)

        def emit(self):
            for callback in list(self.connected):
                callback()

    class FakePanel:
        def __init__(self):
            self.destroyed = FakeDestroyedSignal()
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

        def isVisible(self):
            return True

    panel = FakePanel()
    panel_ref = weakref.ref(panel)
    callback = object()
    dummy = SimpleNamespace(
        _closing_all=False,
        _recording=False,
        _stop_record=lambda wait_join=False: calls.append(("stop_record", wait_join)),
        _close_ocr_action_menu=lambda: calls.append("close_menu"),
        _icon_tooltip=SimpleNamespace(close=lambda: calls.append("close_tooltip")),
        _annotation_overlay=None,
        _ocr_panel=panel,
        _ocr_panel_destroyed_callback=callback,
        _release_large_image_refs=lambda: calls.append("release_refs"),
        close=lambda: calls.append("close_toolbar"),
        _notify_close_once=lambda: calls.append("notify_close"),
    )
    dummy._live_ocr_panel = lambda: dummy._ocr_panel
    dummy._detach_ocr_panel = lambda: post_capture_actions.PostCaptureActions._detach_ocr_panel(dummy)

    post_capture_actions.PostCaptureActions._close_all(dummy)

    assert panel.close_calls == 0
    assert dummy._ocr_panel is None
    assert dummy._ocr_panel_destroyed_callback is None
    assert panel.destroyed.disconnected == [callback]
    assert panel in post_capture_actions_module._ACTIVE_OCR_RESULT_PANELS
    assert "close_toolbar" in calls
    assert "notify_close" in calls

    del panel
    gc.collect()
    retained_panel = panel_ref()
    assert retained_panel is not None

    retained_panel.destroyed.emit()
    assert retained_panel not in post_capture_actions_module._ACTIVE_OCR_RESULT_PANELS


def test_post_capture_ocr_action_menu_contains_ocr_and_ai_entries():
    class DummyActions:
        _image_pending = False
        _ocr_worker = None

        def _ocr_disabled_reason(self):
            return ""

        def _ocr_extract_from_menu(self):
            return None

        def _ppocrv6_extract_from_menu(self):
            return None

        def _ai_recognize_from_menu(self):
            return None

        def _on_ai_recognize(self, _image):
            return True

    items = post_capture_actions.PostCaptureActions._ocr_action_menu_items(DummyActions())

    assert [(item[0], item[2]) for item in items] == [
        ("OCR识别", True),
        ("PP-OCRv6识别", True),
        ("AI识别", True),
    ]


def test_post_capture_ocr_button_stays_available_for_ai_when_ocr_disabled():
    class DummyButton:
        def __init__(self):
            self.enabled = None
            self.tooltip = ""
            self.cursor = None

        def setEnabled(self, enabled):
            self.enabled = bool(enabled)

        def setToolTip(self, tooltip):
            self.tooltip = str(tooltip)

        def setCursor(self, cursor):
            self.cursor = cursor

    class DummyActions:
        def __init__(self):
            self._image_pending = False
            self._ocr_worker = None
            self._on_ai_recognize = lambda _image: True
            self._btn_ocr = DummyButton()
            self._button_labels = {self._btn_ocr: "识别"}
            self._static_button_labels = {self._btn_ocr: "识别"}

        def _ocr_disabled_reason(self):
            return "当前图片高度过大"

    actions = DummyActions()

    post_capture_actions.PostCaptureActions._refresh_ocr_availability(actions)

    assert actions._btn_ocr.enabled is True
    assert actions._btn_ocr.tooltip == "识别"


def test_post_capture_set_image_reuses_single_large_array_until_edit():
    import numpy as np

    image = np.zeros((4, 5, 3), dtype=np.uint8)
    calls = {"invalidate": 0, "refresh": 0}
    actions = SimpleNamespace(
        _on_annotation_clear=None,
        _annotation_overlay=None,
        _region=QRect(0, 0, 5, 4),
        _invalidate_pin_cache=lambda *, schedule=False: calls.__setitem__("invalidate", calls["invalidate"] + 1),
        _refresh_ocr_availability=lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
    )

    post_capture_actions.PostCaptureActions.set_image(actions, image)

    assert actions._base_image_bgr is image
    assert actions._image_bgr is image
    assert calls == {"invalidate": 1, "refresh": 1}


def test_post_capture_apply_annotations_without_overlay_does_not_copy_base_image():
    import numpy as np

    image = np.zeros((3, 4, 3), dtype=np.uint8)
    calls = {"invalidate": 0}
    actions = SimpleNamespace(
        _on_render_annotations=None,
        _annotation_overlay=None,
        _base_image_bgr=image,
        _image_bgr=None,
        _on_image_annotated=None,
        _invalidate_pin_cache=lambda *, schedule=False: calls.__setitem__("invalidate", calls["invalidate"] + 1),
    )

    post_capture_actions.PostCaptureActions._apply_annotations_to_image(actions)

    assert actions._image_bgr is image
    assert calls["invalidate"] == 1


def test_post_capture_release_large_image_refs_clears_regenerable_cache():
    class DummyTimer:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    timer = DummyTimer()
    actions = SimpleNamespace(
        _pin_cache_seq=7,
        _pin_cache_dirty=False,
        _pin_cache_bgr=object(),
        _pin_cache_pixmap=object(),
        _pin_cache_timer=timer,
        _image_bgr=object(),
        _base_image_bgr=object(),
    )
    actions._invalidate_pin_cache = lambda *, schedule=False: post_capture_actions.PostCaptureActions._invalidate_pin_cache(actions, schedule=schedule)
    actions._release_pin_cache = lambda: post_capture_actions.PostCaptureActions._release_pin_cache(actions)

    post_capture_actions.PostCaptureActions._release_large_image_refs(actions)

    assert actions._pin_cache_seq == 8
    assert actions._pin_cache_dirty is True
    assert actions._pin_cache_bgr is None
    assert actions._pin_cache_pixmap is None
    assert actions._image_bgr is None
    assert actions._base_image_bgr is None
    assert timer.stopped is True


def test_ocr_model_menu_popup_test_queue_serialized(monkeypatch):
    from deepcat.ui.post_capture_actions import _OcrModelMenuPopup
    monkeypatch.setattr(post_capture_actions.model_menus, "_local_gemini_test_next_at", 0.0)

    class DummyRuntime:
        def __init__(self, model_name):
            self.model_name = model_name
            self.api_key = "123"
            self.model_type = "glm"
            if model_name.startswith("gemini-"):
                self.base_url = "http://127.0.0.1:8081"
                self.use_proxy = False
            else:
                self.base_url = "https://api.openai.com/v1"
                self.use_proxy = True
            self.proxy_url = ""

    import deepcat.translator_engine
    monkeypatch.setattr(
        deepcat.translator_engine,
        "load_translator_runtime",
        lambda name, *, purpose="translate": DummyRuntime(name)
    )

    class DummyWorker:
        def __init__(self, cfg, **kwargs):
            self._cfg = cfg
            self._running = False

        class TestedSignal:
            def connect(self, callback):
                pass
        class FinishedSignal:
            def connect(self, callback):
                pass

        tested = TestedSignal()
        finished = FinishedSignal()

        def isRunning(self):
            return self._running
        def start(self):
            self._running = True
        def deleteLater(self):
            pass

    import deepcat.ui.settings_dialog
    monkeypatch.setattr(
        deepcat.ui.settings_dialog,
        "TranslatorConnectionTestWorker",
        DummyWorker
    )

    class FakePopup(_OcrModelMenuPopup):
        def __init__(self):
            self._model_test_buttons = {}
            self._test_queue = []
            self._max_active_workers = 4
            self._purpose = "translate"
            self._gemini_next_test_at = 0.0
            self._test_queue_retry_scheduled = False
            self._group_batch_tests = {}

        def _build(self, grouped_models, current_model):
            pass

    popup = FakePopup()

    models = ["gemini-3.5", "gemini-flash", "openai-gpt4", "glm-4"]
    for m in models:
        btn = type("DummyBtn", (), {})()
        btn._state = "testing"
        btn._worker = None
        btn.state = lambda b=btn: b._state
        btn.set_state = lambda s, b=btn: setattr(b, "_state", s)
        btn.set_worker = lambda w, b=btn: setattr(b, "_worker", w)
        btn.worker = lambda b=btn: b._worker
        btn.has_active_worker = lambda b=btn: b._worker is not None and b._worker.isRunning()
        btn.show = lambda: None
        popup._model_test_buttons[m] = btn

    for m in models:
        popup._test_queue.append((m, "group1"))

    popup._process_test_queue()

    gemini_running = sum(1 for m in ["gemini-3.5", "gemini-flash"] if popup._model_test_buttons[m].has_active_worker())
    other_running = sum(1 for m in ["openai-gpt4", "glm-4"] if popup._model_test_buttons[m].has_active_worker())

    assert gemini_running == 1
    assert other_running == 2
    assert len(popup._test_queue) == 1
    assert popup._test_queue[0][0] in {"gemini-3.5", "gemini-flash"}


def test_ocr_model_menu_popup_gemini_waits_after_finished(monkeypatch):
    from deepcat.ui.post_capture_actions import _OcrModelMenuPopup
    monkeypatch.setattr(post_capture_actions.model_menus, "_local_gemini_test_next_at", 0.0)

    class DummyRuntime:
        def __init__(self, model_name):
            self.model_name = model_name
            self.api_key = "123"
            self.model_type = "glm"
            self.base_url = "http://127.0.0.1:8081"
            self.use_proxy = False
            self.proxy_url = ""

    import deepcat.translator_engine
    monkeypatch.setattr(
        deepcat.translator_engine,
        "load_translator_runtime",
        lambda name, *, purpose="translate": DummyRuntime(name)
    )

    class DummySignal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

    class DummyWorker:
        def __init__(self, cfg, **kwargs):
            self._cfg = cfg
            self._running = False
            self.tested = DummySignal()
            self.finished = DummySignal()

        def isRunning(self):
            return self._running

        def start(self):
            self._running = True

        def deleteLater(self):
            pass

    import deepcat.ui.settings_dialog
    monkeypatch.setattr(
        deepcat.ui.settings_dialog,
        "TranslatorConnectionTestWorker",
        DummyWorker
    )

    scheduled = []
    monkeypatch.setattr(post_capture_actions.QTimer, "singleShot", lambda delay, callback: scheduled.append(delay))

    class FakePopup(_OcrModelMenuPopup):
        def __init__(self):
            self._model_test_buttons = {}
            self._test_queue = []
            self._max_active_workers = 4
            self._purpose = "translate"
            self._gemini_next_test_at = 0.0
            self._test_queue_retry_scheduled = False

        def _build(self, grouped_models, current_model):
            pass

    popup = FakePopup()

    for model_name in ["gemini-a", "gemini-b"]:
        btn = type("DummyBtn", (), {})()
        btn._state = "testing"
        btn._worker = None
        btn.state = lambda b=btn: b._state
        btn.set_state = lambda state, b=btn: setattr(b, "_state", state)
        btn.set_worker = lambda worker, b=btn: setattr(b, "_worker", worker)
        btn.worker = lambda b=btn: b._worker
        btn.has_active_worker = lambda b=btn: b._worker is not None and b._worker.isRunning()
        btn.show = lambda: None
        popup._model_test_buttons[model_name] = btn

    popup._test_queue.extend([("gemini-a", None), ("gemini-b", None)])
    popup._process_test_queue()
    assert popup._model_test_buttons["gemini-a"].has_active_worker()
    assert not popup._model_test_buttons["gemini-b"].has_active_worker()

    popup._model_test_buttons["gemini-a"].worker()._running = False
    popup._on_model_test_finished("gemini-a", True, "模型连接成功")

    assert popup._model_test_buttons["gemini-a"].state() == "success"
    assert not popup._model_test_buttons["gemini-b"].has_active_worker()
    assert scheduled


def test_ocr_model_menu_popup_uses_qa_runtime(monkeypatch):
    from deepcat.ui.post_capture_actions import _OcrModelMenuPopup
    monkeypatch.setattr(post_capture_actions.model_menus, "_local_gemini_test_next_at", 0.0)

    captured = {}

    class DummyRuntime:
        model_name = "qa-model"
        api_key = "123"
        model_type = "glm"
        base_url = "https://api.example.com/v1"
        use_proxy = False
        proxy_url = ""

    import deepcat.translator_engine
    def load_runtime(name, *, purpose="translate"):
        captured["purpose"] = purpose
        return DummyRuntime()

    monkeypatch.setattr(
        deepcat.translator_engine,
        "load_translator_runtime",
        load_runtime
    )

    class DummySignal:
        def connect(self, callback):
            pass

    class DummyWorker:
        def __init__(self, cfg, **kwargs):
            self._cfg = cfg
            self._running = False
            self.tested = DummySignal()
            self.finished = DummySignal()

        def isRunning(self):
            return self._running

        def start(self):
            self._running = True

        def deleteLater(self):
            pass

    import deepcat.ui.settings_dialog
    monkeypatch.setattr(
        deepcat.ui.settings_dialog,
        "TranslatorConnectionTestWorker",
        DummyWorker
    )

    class FakePopup(_OcrModelMenuPopup):
        def __init__(self):
            self._model_test_buttons = {}
            self._test_queue = []
            self._max_active_workers = 4
            self._purpose = "qa"
            self._gemini_next_test_at = 0.0
            self._test_queue_retry_scheduled = False

        def _build(self, grouped_models, current_model):
            pass

    popup = FakePopup()
    btn = type("DummyBtn", (), {})()
    btn._state = "testing"
    btn._worker = None
    btn.state = lambda b=btn: b._state
    btn.set_state = lambda state, b=btn: setattr(b, "_state", state)
    btn.set_worker = lambda worker, b=btn: setattr(b, "_worker", worker)
    btn.worker = lambda b=btn: b._worker
    btn.has_active_worker = lambda b=btn: b._worker is not None and b._worker.isRunning()
    btn.show = lambda: None
    popup._model_test_buttons["qa-model"] = btn
    popup._test_queue.append(("qa-model", None))

    popup._process_test_queue()

    assert captured["purpose"] == "qa"


def test_ocr_model_menu_popup_adjust_position_keeps_height_and_allows_parent_overflow(monkeypatch):
    from deepcat.ui.post_capture_actions import _OcrModelMenuPopup

    parent_frame = QRect(0, 300, 320, 80)

    class DummyScreen:
        def availableGeometry(self):
            return QRect(0, 0, 320, 500)

    class DummyGuiApplication:
        @staticmethod
        def screenAt(_point):
            return DummyScreen()

        @staticmethod
        def primaryScreen():
            return DummyScreen()

    class DummyAnchor:
        def mapToGlobal(self, point):
            return QPoint(40 + point.x(), 360 + point.y())

    class DummyTopLevel:
        def frameGeometry(self):
            return QRect(parent_frame)

    class DummyParent:
        def window(self):
            return DummyTopLevel()

    class DummyPopup:
        def __init__(self):
            self._anchor = DummyAnchor()
            self._global_pos = QPoint(260, 452)
            self._match_parent_width = False
            self._width = 220
            self._height = 260
            self.moved_to = None
            self.fixed_height_calls = []

        def parentWidget(self):
            return DummyParent()

        def width(self):
            return self._width

        def height(self):
            return self._height

        def setFixedHeight(self, height):
            self.fixed_height_calls.append(int(height))
            self._height = int(height)

        def move(self, point):
            self.moved_to = QPoint(point)

    monkeypatch.setattr(post_capture_actions.model_menus, "QGuiApplication", DummyGuiApplication)
    popup = DummyPopup()

    _OcrModelMenuPopup._adjust_position(popup)

    assert popup.height() == 260
    assert popup.fixed_height_calls == []
    assert popup.moved_to == QPoint(94, 100)
    assert popup.moved_to.x() >= 6
    assert popup.moved_to.y() >= 6
    assert popup.moved_to.x() + popup.width() <= 314
    assert popup.moved_to.y() + popup.height() <= 494
    assert popup.moved_to.y() < parent_frame.top()


def test_ocr_model_menu_popup_delete_selection_mode_picks_arbitrary_models():
    from deepcat.ui.post_capture_actions import _OcrModelMenuPopup

    class DummyCheckbox:
        def __init__(self):
            self.checked = False
            self.visible = False

        def blockSignals(self, blocked):
            pass

        def setChecked(self, checked):
            self.checked = bool(checked)

        def isChecked(self):
            return self.checked

        def setVisible(self, visible):
            self.visible = bool(visible)

        def hide(self):
            self.visible = False

    class FakePopup(_OcrModelMenuPopup):
        def __init__(self):
            self._on_batch_delete = lambda names: deleted.append(names)
            self._delete_selection_mode = False
            self._selected_delete_models = set()
            self._delete_selection_bar = None
            self._delete_selection_count_label = None
            self._delete_selection_confirm_btn = None
            self._normal_popup_height = 1
            self._list_items = [
                {"kind": "model", "text": "模型A", "checkbox": DummyCheckbox(), "deletable": True},
                {"kind": "model", "text": "模型B", "checkbox": DummyCheckbox(), "deletable": True},
                {"kind": "model", "text": "DeepLX", "checkbox": DummyCheckbox(), "deletable": False},
            ]

        def _hide_other_model_buttons(self, *args, **kwargs):
            pass

        def _hide_other_group_buttons(self, *args, **kwargs):
            pass

        def _set_popup_content_height(self, base_height):
            pass

        def close(self):
            pass

    deleted = []
    popup = FakePopup()

    popup._enter_delete_selection_mode()

    assert popup._delete_selection_mode
    assert popup._selected_delete_models == set()

    popup._activate_model_row("模型A")
    popup._activate_model_row("DeepLX")
    popup._activate_model_row("模型B")
    popup._activate_model_row("模型A")

    assert popup._selected_delete_models == {"模型B"}

    popup._confirm_selected_delete_models()

    assert deleted == [["模型B"]]


def test_ocr_extract_worker_emits_text_and_stderr():
    from deepcat.ui.post_capture_actions import OcrExtractWorker

    results = []
    worker = OcrExtractWorker(object(), lambda img: ("识别结果", "warn"))
    worker.ocr_finished.connect(lambda t, e, x: results.append((t, e, x)))
    worker.run()
    assert results == [("识别结果", "warn", "")]


def test_ocr_extract_worker_emits_exception_text():
    from deepcat.ui.post_capture_actions import OcrExtractWorker

    def boom(img):
        raise RuntimeError("ocr blew up")

    results = []
    worker = OcrExtractWorker(object(), boom)
    worker.ocr_finished.connect(lambda t, e, x: results.append((t, e, x)))
    worker.run()
    assert results == [("", "", "ocr blew up")]


def test_classify_ocr_error_message_splits_common_failures():
    from deepcat.ui.post_capture_actions import classify_ocr_error_message

    cases = [
        (
            "ModuleNotFoundError: No module named 'rapidocr_onnxruntime'",
            "OCR组件缺失：未安装 rapidocr-onnxruntime",
        ),
        (
            "ImportError: DLL load failed while importing onnxruntime_pybind11_state: 动态链接库(DLL)初始化例程失败。",
            "OCR运行库加载失败：ONNX Runtime DLL 初始化失败，请检查 VC++ 运行库版本或 DLL 搜索路径",
        ),
        (
            "subprocess.TimeoutExpired: Command timed out after 80 seconds",
            "OCR识别超时，请缩小截图区域或稍后重试",
        ),
        (
            "write_tmp_failed",
            "OCR临时图片写入失败，请检查临时目录权限或磁盘空间",
        ),
        (
            "FileNotFoundError: ch_ppocr_v3_det_infer.onnx not found",
            "OCR模型文件缺失或损坏，请重新安装 rapidocr-onnxruntime",
        ),
    ]

    for raw, expected in cases:
        assert classify_ocr_error_message(raw) == expected


def test_wrap_error_message_behavior():
    from deepcat.ui.post_capture_actions import wrap_error_message

    assert wrap_error_message("") == ""
    assert wrap_error_message(None) == ""

    # 限制宽度为 10，期待强制折行
    msg = "1234567890abcdefg"
    wrapped = wrap_error_message(msg, max_width=10)
    lines = wrapped.split("\n")
    assert lines[0] == "1234567890"
    assert lines[1] == "abcdefg"

    # 测试标点符号/空格自然断行
    msg2 = "1234, 6789"
    wrapped2 = wrap_error_message(msg2, max_width=6)
    lines2 = wrapped2.split("\n")
    assert lines2[0] == "1234,"
    assert lines2[1] == "6789"

    long_msg = "1\n2\n3\n4\n5\n6\n7\n8\n9"
    wrapped_long = wrap_error_message(long_msg, max_lines=5)
    assert len(wrapped_long.split("\n")) == 6
    assert wrapped_long.endswith("...")


def test_notebook_attachment_plain_text_flow():
    from deepcat.ui.post_capture_actions.text_panel import OcrTextPanel
    import copy

    # 1. 模拟文本格式化显示 _format_display_attachment
    panel = type("DummyPanel", (), {})()

    # 模拟 text + is_notebook 格式的笔记本附件对象
    notebook_attachment = {
        "type": "text",
        "text": "\n\n[关联笔记本附件: MyNote]\n--- MyNote 内容开始 ---\nHello Note\n--- MyNote 内容结束 ---\n",
        "is_notebook": True,
        "name": "MyNote.txt"
    }

    label = OcrTextPanel._format_display_attachment(panel, notebook_attachment, None)
    assert label == "📎 [附件: MyNote.txt]"

    # 2. 模拟从消息中提取待重新发送的附件 _message_attachments_for_resend
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "分析下我的笔记"},
            notebook_attachment,
            {"type": "file_url", "file_url": {"url": "data:text/plain;base64,YWJj", "name": "other.txt"}}
        ]
    }

    resend_attachments = OcrTextPanel._message_attachments_for_resend(msg)
    assert len(resend_attachments) == 2
    assert resend_attachments[0]["is_notebook"] is True
    assert resend_attachments[0]["name"] == "MyNote.txt"
    assert resend_attachments[1]["type"] == "file_url"

    # 3. 模拟获取附件标签名称 _attachment_label_from_payload
    lbl1 = OcrTextPanel._attachment_label_from_payload(notebook_attachment)
    assert lbl1 == "MyNote.txt"


def test_post_capture_actions_button_order_and_close_tooltip():
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication([])
    assert app is not None

    dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
    toolbar = post_capture_actions.PostCaptureActions(
        region_rect=QRect(0, 0, 100, 100),
        image_bgr=dummy_img,
        default_dir="",
        default_format="png",
        jpg_quality=95,
        capture_frames=1,
        region_px=(0, 0, 100, 100),
        on_close=lambda: None,
        auto_show=False,
    )
    try:
        # 1. 验证关闭按钮提示文本为“退出截图”
        assert toolbar._button_tooltips[toolbar._btn_close] == "退出截图"
        assert toolbar._btn_close.toolTip() == "退出截图"

        # 2. 验证第四组中复制按钮移动到关闭按钮左边 (save -> pin -> copy -> close)
        row_widgets = []
        for i in range(toolbar._row1.count()):
            item = toolbar._row1.itemAt(i)
            if item and item.widget():
                row_widgets.append(item.widget())

        copy_idx = row_widgets.index(toolbar._btn_copy)
        close_idx = row_widgets.index(toolbar._btn_close)
        save_idx = row_widgets.index(toolbar._btn_save)
        pin_idx = row_widgets.index(toolbar._btn_pin)

        assert save_idx < pin_idx < copy_idx < close_idx
        assert copy_idx == close_idx - 1
    finally:
        toolbar.close()
        toolbar.deleteLater()
