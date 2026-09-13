"""网络缩略图异步加载、错误恢复、内部预览和原图数据保留。"""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QColor, QDesktopServices, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from deepcat.ui import chat_image_loader as image_loader
from deepcat.ui.chat_bubbles import ChatBubble, ChatImageWidget


def _wait_until(predicate, timeout=4.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), "等待图片回调超时"


@pytest.fixture
def original_png():
    image = QImage(1440, 960, QImage.Format.Format_RGB32)
    image.fill(QColor("#4f78a8"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(data)


@pytest.fixture(autouse=True)
def isolated_image_cache():
    image_loader._cache.clear()
    image_loader._cache_bytes = 0
    yield
    image_loader._cache.clear()
    image_loader._cache_bytes = 0


@pytest.fixture
def image_server(original_png):
    state = SimpleNamespace(requests=[], release=threading.Event(), retries=0)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state.requests.append((self.path, dict(self.headers)))
            if self.path.startswith("/slow"):
                state.release.wait(3)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/image")
                self.send_header("Set-Cookie", "unwanted_cookie=1")
                self.end_headers()
                return
            if self.path == "/retry":
                state.retries += 1
            failed = self.path == "/retry" and state.retries == 1
            payload = b"<html>not an image</html>" if self.path == "/html" else original_png
            self.send_response(503 if failed else 200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    yield state
    state.release.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_http_image_is_a_widget_and_does_not_block_text(image_server, original_png):
    bubble = ChatBubble("assistant")
    bubble.set_content(f"前文\n![图片]({image_server.url}/slow)\n后文", is_markdown=True, streaming=True)
    widget = bubble.findChild(ChatImageWidget)
    assert widget is not None
    assert not widget._image_bytes
    _wait_until(lambda: bool(image_server.requests))
    assert widget._loading_image
    assert "图片加载中" in widget._label.text()
    image_server.release.set()
    _wait_until(lambda: bool(widget._image_bytes))
    assert widget._image_bytes == original_png
    assert not widget._pixmap.isNull()
    assert widget.width() <= ChatImageWidget.THUMBNAIL_MAX_WIDTH
    assert widget._label.height() <= ChatImageWidget.THUMBNAIL_MAX_HEIGHT
    assert image_server.url not in bubble._label.text()
    sip.delete(bubble)


def test_html_response_is_rejected(image_server):
    loader = image_loader.ChatImageLoader()
    results, errors = [], []
    loader.loaded.connect(lambda *args: results.append(args))
    loader.failed.connect(errors.append)
    loader.load(image_server.url + "/html")
    _wait_until(lambda: bool(errors))
    assert results == []
    assert "不是可预览的图片" in errors[0]
    sip.delete(loader)


def test_redirect_does_not_forward_cookies_or_authorization(image_server):
    loader = image_loader.ChatImageLoader()
    results = []
    loader.loaded.connect(lambda *args: results.append(args))
    loader.load(image_server.url + "/redirect")
    _wait_until(lambda: bool(results))
    assert [path for path, _headers in image_server.requests] == ["/redirect", "/image"]
    assert all("Cookie" not in headers and "Authorization" not in headers for _, headers in image_server.requests)
    sip.delete(loader)


def test_clicking_failed_remote_image_retries(image_server, original_png):
    widget = ChatImageWidget(image_server.url + "/retry")
    widget.show()
    _wait_until(lambda: bool(widget._image_error))
    QTest.mouseClick(widget._label, Qt.MouseButton.LeftButton)
    _wait_until(lambda: widget._image_bytes == original_png)
    assert image_server.retries == 2
    assert widget._image_error == ""
    assert widget._label.toolTip() == "点击查看原图"
    sip.delete(widget)


def test_destroying_widget_during_download_does_not_call_deleted_objects(image_server):
    widget = ChatImageWidget(image_server.url + "/slow")
    _wait_until(lambda: bool(image_server.requests))
    sip.delete(widget)
    image_server.release.set()
    QTest.qWait(80)
    assert sip.isdeleted(widget)


def test_cancelled_loader_does_not_emit_completion(image_server):
    loader = image_loader.ChatImageLoader()
    events = []
    loader.loaded.connect(lambda *_args: events.append("loaded"))
    loader.failed.connect(lambda *_args: events.append("failed"))
    loader.load(image_server.url + "/slow")
    _wait_until(lambda: bool(image_server.requests))
    loader.cancel()
    image_server.release.set()
    QTest.qWait(60)
    assert events == []
    sip.delete(loader)


def test_loader_can_replace_an_unfinished_request(image_server, original_png):
    loader = image_loader.ChatImageLoader()
    results = []
    loader.loaded.connect(lambda *args: results.append(args))
    loader.load(image_server.url + "/slow")
    _wait_until(lambda: bool(image_server.requests))
    loader.load(image_server.url + "/image")
    _wait_until(lambda: bool(results))
    image_server.release.set()
    QTest.qWait(40)
    assert len(results) == 1
    assert results[0][0] == original_png
    assert list(image_loader._cache) == [image_server.url + "/image"]
    sip.delete(loader)


def test_image_cache_evicts_old_entries_within_memory_budget(image_server, original_png, monkeypatch):
    _mime, thumbnail = image_loader.decode_thumbnail(original_png)
    budget = len(original_png) + thumbnail.sizeInBytes() + 100
    monkeypatch.setattr(image_loader, "MAX_IMAGE_BYTES", budget)
    loader = image_loader.ChatImageLoader()
    results = []
    loader.loaded.connect(lambda *args: results.append(args))
    loader.load(image_server.url + "/one")
    _wait_until(lambda: len(results) == 1)
    loader.load(image_server.url + "/two")
    _wait_until(lambda: len(results) == 2)
    assert list(image_loader._cache) == [image_server.url + "/two"]
    assert image_loader._cache_bytes <= budget
    sip.delete(loader)


def test_concurrent_same_image_cache_is_counted_once(image_server, original_png):
    loaders = [image_loader.ChatImageLoader(), image_loader.ChatImageLoader()]
    results = []
    for loader in loaders:
        loader.loaded.connect(lambda *args: results.append(args))
        loader.load(image_server.url + "/slow")
    _wait_until(lambda: len(image_server.requests) == 2)
    image_server.release.set()
    _wait_until(lambda: len(results) == 2)
    assert len(image_loader._cache) == 1
    assert image_loader._cache_bytes == len(original_png) + results[0][2].sizeInBytes()
    cached_loader = image_loader.ChatImageLoader()
    cached_loader.loaded.connect(lambda *args: results.append(args))
    cached_loader.load(image_server.url + "/slow")
    assert len(results) == 3
    assert len(image_server.requests) == 2
    for loader in [*loaders, cached_loader]:
        sip.delete(loader)


def test_oversized_response_is_rejected(image_server, monkeypatch):
    monkeypatch.setattr(image_loader, "MAX_IMAGE_BYTES", 100)
    loader = image_loader.ChatImageLoader()
    errors = []
    loader.failed.connect(errors.append)
    loader.load(image_server.url + "/image")
    _wait_until(lambda: bool(errors))
    assert "大小限制" in errors[0]
    assert not image_loader._cache
    sip.delete(loader)


def test_thumbnail_click_opens_internal_dialog_with_original_file(original_png, tmp_path, monkeypatch):
    import deepcat.ui.main_window as main_window

    image_path = tmp_path / "original.png"
    image_path.write_bytes(original_png)
    widget = ChatImageWidget(image_path.as_uri())
    calls = []

    class Preview:
        def __init__(self, path, parent=None):
            calls.append((path, parent))

        def setAttribute(self, attribute, enabled):
            assert attribute == Qt.WidgetAttribute.WA_DeleteOnClose
            assert enabled

        def exec(self):
            calls.append("opened")

    monkeypatch.setattr(main_window, "ImagePreviewDialog", Preview)
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda *_args: pytest.fail("不应打开外部程序"))
    widget.show()
    QApplication.processEvents()
    QTest.mouseClick(widget._label, Qt.MouseButton.LeftButton)
    assert calls == [(str(image_path), widget), "opened"]
    sip.delete(widget)


def test_copy_and_download_keep_original_resolution(original_png, tmp_path, monkeypatch):
    from deepcat.ui import chat_bubbles

    image_path = tmp_path / "original.png"
    image_path.write_bytes(original_png)
    widget = ChatImageWidget(image_path.as_uri())
    assert widget._pixmap.width() < 1440
    copied = []
    clipboard = SimpleNamespace(setImage=lambda image: copied.append(QImage(image)))
    monkeypatch.setattr(chat_bubbles.QGuiApplication, "clipboard", lambda: clipboard)
    target = tmp_path / "saved.png"
    monkeypatch.setattr(chat_bubbles.QFileDialog, "getSaveFileName", lambda *_args: (str(target), "PNG"))
    widget._copy_image()
    widget._download_image()
    assert (copied[0].width(), copied[0].height()) == (1440, 960)
    assert target.read_bytes() == original_png
    sip.delete(widget)
