"""异步读取远程聊天图片，缓存原始字节并解码尺寸受限的缩略图。"""

from __future__ import annotations

from collections import OrderedDict

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QImage, QImageReader
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


MAX_IMAGE_BYTES = 64 * 1024 * 1024
_cache: OrderedDict[str, tuple[bytes, str, QImage]] = OrderedDict()
_cache_bytes = 0


def decode_thumbnail(data: bytes) -> tuple[str, QImage]:
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    image_format = bytes(reader.format()).decode("ascii", errors="ignore").lower()
    mime = "image/jpeg" if image_format in {"jpg", "jpeg"} else "image/" + (image_format or "png")
    if image_format not in {"png", "jpeg", "jpg", "webp", "gif", "bmp", "avif"}:
        return mime, QImage()
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 80_000_000:
        return mime, QImage()
    reader.setAutoTransform(True)
    if size.width() > 680 or size.height() > 760:
        reader.setScaledSize(size.scaled(QSize(680, 760), Qt.AspectRatioMode.KeepAspectRatio))
    return mime, reader.read()


class ChatImageLoader(QObject):
    loaded = pyqtSignal(object, str, object)
    failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._data = bytearray()
        self._source = ""
        self._failure = ""
        self._cancelled = False
        self._done = False

    def load(self, source: str) -> None:
        self.cancel()
        self._data.clear()
        self._failure = ""
        self._cancelled = False
        self._done = False
        self._source = source
        cached = _cache.get(source)
        if cached is not None:
            _cache.move_to_end(source)
            self.loaded.emit(*cached)
            return
        url = QUrl(source)
        if url.scheme().lower() not in {"http", "https"} or not url.host():
            self.failed.emit("图片地址无效")
            return
        request = QNetworkRequest(url)
        request.setTransferTimeout(15000)
        request.setMaximumRedirectsAllowed(5)
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy
        )
        request.setAttribute(QNetworkRequest.Attribute.CookieLoadControlAttribute, QNetworkRequest.LoadControl.Manual)
        request.setAttribute(QNetworkRequest.Attribute.CookieSaveControlAttribute, QNetworkRequest.LoadControl.Manual)
        request.setRawHeader(b"Accept", b"image/webp,image/png,image/jpeg,image/gif;q=0.9,*/*;q=0.1")
        self._reply = self._manager.get(request)
        self._reply.readyRead.connect(self._read_available)
        self._reply.finished.connect(self._finished)

    def cancel(self) -> None:
        self._cancelled = True
        if self._reply is not None:
            reply = self._reply
            self._reply = None
            reply.readyRead.disconnect(self._read_available)
            reply.finished.disconnect(self._finished)
            reply.abort()
            reply.deleteLater()
        self._data.clear()

    def _read_available(self) -> None:
        if self._reply is None or self._failure or self._cancelled:
            return
        chunk = bytes(self._reply.readAll())
        if len(self._data) + len(chunk) > MAX_IMAGE_BYTES:
            self._failure = "图片超过预览大小限制"
            self._reply.abort()
            return
        self._data.extend(chunk)

    def _finished(self) -> None:
        global _cache_bytes
        if self._done or self._reply is None:
            return
        self._done = True
        self._read_available()
        reply = self._reply
        try:
            if self._cancelled:
                return
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if self._failure:
                self.failed.emit(self._failure)
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.failed.emit(f"图片请求失败（HTTP {status}）" if status else "图片连接失败")
                return
            data = bytes(self._data)
            self._data.clear()
            mime, thumbnail = decode_thumbnail(data)
            if thumbnail.isNull():
                self.failed.emit("返回内容不是可预览的图片")
                return
            cost = len(data) + thumbnail.sizeInBytes()
            if cost <= MAX_IMAGE_BYTES:
                previous = _cache.pop(self._source, None)
                if previous is not None:
                    _cache_bytes -= len(previous[0]) + previous[2].sizeInBytes()
                while _cache and _cache_bytes + cost > MAX_IMAGE_BYTES:
                    old_data, _mime, old_image = _cache.popitem(last=False)[1]
                    _cache_bytes -= len(old_data) + old_image.sizeInBytes()
                _cache[self._source] = (data, mime, thumbnail)
                _cache_bytes += cost
            self.loaded.emit(data, mime, thumbnail)
        finally:
            self._data.clear()
            reply.deleteLater()
            self._reply = None
