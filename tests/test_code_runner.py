from __future__ import annotations

import os
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from deepcat.clipboard_history.code_runner import (
    is_runnable_web_code,
    preview_code_in_browser,
    wrap_html_content,
)
from deepcat.clipboard_history.clipboard_history_page import _RecordItemWidget, _asset_icon


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_is_runnable_web_code_full_html():
    # 用户截图中的完整 HTML 声明
    html_doc = '<!DOCTYPE html> <html lang="zh-CN"> <head> <meta name="viewport" content="width=device-width"> <title>Test</title> </head> <body> <h1>Hello</h1> </body> </html>'
    assert is_runnable_web_code(html_doc) is True

    # 简单小写 html
    assert is_runnable_web_code("<html><body><p>Hello world</p></body></html>") is True


def test_is_runnable_web_code_svg():
    svg_code = '<svg width="100" height="100" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><circle cx="50" cy="50" r="40" fill="red" /></svg>'
    assert is_runnable_web_code(svg_code) is True

    xml_svg = '<?xml version="1.0" encoding="utf-8"?><svg><rect width="10" height="10"/></svg>'
    assert is_runnable_web_code(xml_svg) is True


def test_is_runnable_web_code_snippets():
    snippet = "<div>\n  <h3>Vue/React Demo</h3>\n  <button onclick=\"alert(1)\">Click</button>\n</div>"
    assert is_runnable_web_code(snippet) is True

    table_snippet = "<table><tr><td>Cell 1</td><td>Cell 2</td></tr></table>"
    assert is_runnable_web_code(table_snippet) is True

    script_snippet = "<script>console.log('hi')</script><div>Test</div>"
    assert is_runnable_web_code(script_snippet) is True


def test_is_runnable_web_code_markdown_wrapped():
    md_html = "```html\n<!DOCTYPE html>\n<html>\n<body>\n<h1>Title</h1>\n</body>\n</html>\n```"
    assert is_runnable_web_code(md_html) is True


def test_is_runnable_web_code_local_file(tmp_path: Path):
    html_file = tmp_path / "page.html"
    html_file.write_text("<h1>File content</h1>", encoding="utf-8")
    assert is_runnable_web_code("", content_type="file_path", file_path=str(html_file)) is True

    txt_file = tmp_path / "note.txt"
    txt_file.write_text("Hello note", encoding="utf-8")
    assert is_runnable_web_code("", content_type="file_path", file_path=str(txt_file)) is False


def test_is_runnable_web_code_negative_cases():
    # 普通命令与配置（如用户截图中的普通记录）
    assert is_runnable_web_code('model_provider = "codex_local_access" model = "gpt-5..."') is False
    assert is_runnable_web_code("python -m deepcat.main --gui") is False
    assert is_runnable_web_code("./.venv/Scripts/python.exe main.py --gui") is False
    assert is_runnable_web_code("她没有答应 初二那年，她没有答应我。后来，我们谈了八年...") is False
    assert is_runnable_web_code("a < b and c > d") is False
    assert is_runnable_web_code("<user@domain.com>") is False
    assert is_runnable_web_code("") is False
    assert is_runnable_web_code("   ") is False


def test_wrap_html_content_preserves_and_injects_utf8():
    full_html_no_charset = "<html><head><title>Test</title></head><body><h1>测试中文</h1></body></html>"
    wrapped = wrap_html_content(full_html_no_charset)
    assert 'charset="UTF-8"' in wrapped
    assert "测试中文" in wrapped

    snippet = "<div><button>点击按钮</button></div>"
    wrapped_snippet = wrap_html_content(snippet, title="自定义标题")
    assert "<!DOCTYPE html>" in wrapped_snippet
    assert '<meta charset="UTF-8">' in wrapped_snippet
    assert "自定义标题" in wrapped_snippet
    assert snippet in wrapped_snippet


def test_preview_code_in_browser(tmp_path: Path, monkeypatch):
    # Mock webbrowser.open 和 QDesktopServices.openUrl
    monkeypatch.setattr("deepcat.clipboard_history.code_runner.QDesktopServices.openUrl", lambda url: True)
    html_content = "<!DOCTYPE html><html><body><h1>Browser Preview Test</h1></body></html>"
    ok = preview_code_in_browser(html_content, title="测试预览")
    assert ok is True

    # 测试本地 html 文件路径直接打开
    local_file = tmp_path / "direct.html"
    local_file.write_text("<h1>Direct file</h1>", encoding="utf-8")
    ok_file = preview_code_in_browser("", content_type="file_path", file_path=str(local_file))
    assert ok_file is True


def test_record_item_widget_run_button(qapp):
    copy_icon = _asset_icon("icon_action_copy.svg")
    del_icon = _asset_icon("icon_todo_delete.svg")
    pin_icon = _asset_icon("icon_action_pin.svg")
    edit_icon = _asset_icon("icon_todo_edit.svg")
    run_icon = _asset_icon("icon_code_preview.svg")

    # 1. HTML 记录项：应包含 _run_btn
    html_record = (
        1,
        "<!DOCTYPE html><html><body><h1>Page</h1></body></html>",
        "text",
        None,
        "",
        0,
        0,
        "2026-08-29 12:00:00",
        "",
        "",
        "",
        0,
        0,
        "",
    )
    w_html = _RecordItemWidget(
        html_record,
        time_text="08/29 12:00",
        copy_icon=copy_icon,
        delete_icon=del_icon,
        pin_icon=pin_icon,
        edit_icon=edit_icon,
        run_icon=run_icon,
    )
    assert w_html._is_runnable is True
    assert w_html._run_btn is not None

    # 模拟悬停并检查按钮是否显示
    w_html._set_actions_visible(True)
    assert not w_html._run_btn.isHidden()
    assert w_html._run_btn.isEnabled() is True

    # 模拟点击运行按钮
    emitted_id = []
    w_html.run_requested.connect(lambda rid: emitted_id.append(rid))
    w_html._run_btn.click()
    assert emitted_id == [1]

    # 2. 普通文本记录项：_run_btn 应为 None
    text_record = (
        2,
        "python -m deepcat.main --gui",
        "text",
        None,
        "",
        0,
        0,
        "2026-08-29 12:00:00",
        "",
        "",
        "",
        0,
        0,
        "",
    )
    w_text = _RecordItemWidget(
        text_record,
        time_text="08/29 12:00",
        copy_icon=copy_icon,
        delete_icon=del_icon,
        pin_icon=pin_icon,
        edit_icon=edit_icon,
        run_icon=run_icon,
    )
    assert w_text._is_runnable is False
    assert w_text._run_btn is None
