from __future__ import annotations

import ctypes
import hashlib
import os
import re
import weakref
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtCore import QAbstractNativeEventFilter
from PyQt6.QtWidgets import QApplication, QWidget

from deepcat.utils.logger import get_logger

logger = get_logger("clipboard_history")

WM_CLIPBOARDUPDATE = 0x031D

# 显式声明 Windows API 原型，避免 64 位句柄被截断
_AddClipboardFormatListener = ctypes.windll.user32.AddClipboardFormatListener
_AddClipboardFormatListener.argtypes = [ctypes.c_void_p]
_AddClipboardFormatListener.restype = ctypes.c_bool

_RemoveClipboardFormatListener = ctypes.windll.user32.RemoveClipboardFormatListener
_RemoveClipboardFormatListener.argtypes = [ctypes.c_void_p]
_RemoveClipboardFormatListener.restype = ctypes.c_bool


def _get_foreground_app_name() -> str:
    try:
        import win32gui
        import win32process
    except Exception as e:
        # 打包后 pywin32 顶层模块缺失会导致此处失败，记录一次 warning 便于诊断
        logger.warning("win32gui/win32process not available: %s", e)
        return "Unknown"
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "Unknown"
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid <= 0:
            return "Unknown"
        try:
            import psutil

            p = psutil.Process(pid)
            name = p.name()
            if name:
                return os.path.splitext(name)[0]
        except Exception as e:
            # psutil 缺失或进程已退出，仅 debug 级别
            logger.debug("psutil.Process failed: %s", e)
        return "Unknown"
    except Exception as e:
        logger.debug("GetForegroundWindow info failed: %s", e)
        return "Unknown"


class ContentTypeDetector:
    """剪贴板内容类型识别器"""

    URL_RE = re.compile(r"^(https?|ftp)://|^www\.", re.IGNORECASE)
    WIN_PATH_RE = re.compile(r"^[a-zA-Z]:[/\\].*")
    @staticmethod
    def detect(content: str) -> dict[str, Any]:
        text = str(content or "").strip()
        if not text:
            return {
                "content": "",
                "content_type": "text",
                "file_type": "",
                "file_path": "",
                "file_size": 0,
                "source_app": "Unknown",
                "metadata": {},
            }

        result: dict[str, Any] = {
            "content": text,
            "content_type": "text",
            "file_type": "",
            "file_path": "",
            "file_size": 0,
            "source_app": _get_foreground_app_name(),
            "metadata": {},
        }

        lines = [line.strip().strip('"').strip("'") for line in text.splitlines() if line.strip()]
        if len(lines) > 1 and all(
            ContentTypeDetector.is_file_path(line) or ContentTypeDetector.is_folder_path(line)
            for line in lines
        ):
            result["content_type"] = "multi_file"
            file_size = 0
            for line in lines:
                try:
                    if os.path.isfile(line):
                        file_size += int(os.path.getsize(line))
                except Exception:
                    pass
            result["file_size"] = file_size
            result["metadata"] = {"files": lines}
            return result

        if ContentTypeDetector.is_file_path(text):
            result["content_type"] = "file_path"
            result["file_path"] = text
            result["file_type"] = os.path.splitext(text)[1].lower()
            try:
                result["file_size"] = os.path.getsize(text)
            except Exception:
                pass
            return result

        if ContentTypeDetector.is_folder_path(text):
            result["content_type"] = "folder_path"
            result["file_path"] = text
            return result

        if ContentTypeDetector.is_url(text):
            result["content_type"] = "url"
            return result

        return result

    @staticmethod
    def is_url(text: str) -> bool:
        return bool(ContentTypeDetector.URL_RE.search(text.strip()))

    @staticmethod
    def is_file_path(text: str) -> bool:
        t = text.strip().strip('"').strip("'")
        if "\n" in t or "\r" in t:
            return False
        if not ContentTypeDetector.WIN_PATH_RE.match(t):
            return False
        if os.path.isfile(t):
            return True
        # 即使文件暂时不可访问，也按格式识别为文件路径
        ext = os.path.splitext(t)[1]
        if ext and "." in ext and not ext.endswith(("\\", "/")):
            return True
        return False

    @staticmethod
    def is_folder_path(text: str) -> bool:
        t = text.strip().strip('"').strip("'")
        if "\n" in t or "\r" in t:
            return False
        if not ContentTypeDetector.WIN_PATH_RE.match(t):
            return False
        if os.path.isdir(t):
            return True
        return False

class _MSG(ctypes.Structure):
    """Windows MSG 结构体"""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_ulong),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


class _ClipboardListenerWidget(QWidget):
    """仅用于提供 HWND 以注册剪贴板监听器"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hwnd = 0
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        try:
            self._hwnd = int(self.winId())
            ok = _AddClipboardFormatListener(ctypes.c_void_p(self._hwnd))
            if not ok:
                logger.warning("AddClipboardFormatListener failed")
        except Exception as e:
            logger.error(f"Register clipboard listener error: {e}")
            self._hwnd = 0

    def cleanup(self) -> None:
        try:
            if self._hwnd:
                _RemoveClipboardFormatListener(ctypes.c_void_p(self._hwnd))
        except Exception as e:
            logger.error(f"Remove clipboard listener error: {e}")
        self.deleteLater()


class _ClipboardEventFilter(QAbstractNativeEventFilter):
    """全局 native 事件过滤器，捕获 WM_CLIPBOARDUPDATE"""

    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, eventType, message):
        if eventType != b"windows_generic_MSG":
            return False, 0
        try:
            msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
            if msg.message == WM_CLIPBOARDUPDATE:
                QTimer.singleShot(10, self._callback)
                return True, 0
        except Exception:
            pass
        return False, 0


class ClipboardMonitor(QObject):
    """剪贴板监控（事件驱动模式）"""

    path_detected = pyqtSignal(dict)
    listener_failed = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.running = False
        self.last_content = ""
        self._last_signature = ""
        self._ignored_contents: set[str] = set()
        self.editing_session_active = False
        self._listener_widget: Optional[_ClipboardListenerWidget] = None
        self._event_filter: Optional[_ClipboardEventFilter] = None
        self._main_window: Optional[weakref.ref] = None
        self._activation_connected = False
        self._restart_pending = False

    @staticmethod
    def _normalize_clipboard_text(content: str) -> str:
        lines = [line.strip() for line in str(content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        return "\n".join(line for line in lines if line).strip()

    def start(self) -> None:
        self.running = True
        if self._listener_widget is not None:
            return
        parent = self.parent()
        if isinstance(parent, QWidget):
            self._listener_widget = _ClipboardListenerWidget(parent)
        else:
            self._listener_widget = _ClipboardListenerWidget()
        self._event_filter = _ClipboardEventFilter(self._on_clipboard_changed)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._event_filter)
            if not self._activation_connected:
                app.applicationStateChanged.connect(self._on_application_state_changed)
                self._activation_connected = True

    def stop(self) -> None:
        self.running = False
        if self._event_filter is not None:
            try:
                app = QApplication.instance()
                if app is not None:
                    app.removeNativeEventFilter(self._event_filter)
            except Exception:
                pass
            self._event_filter = None
        if self._listener_widget is not None:
            try:
                self._listener_widget.cleanup()
            except Exception:
                pass
            self._listener_widget = None

    def cleanup(self) -> None:
        """永久释放监听器及应用级信号连接。"""
        self.stop()
        app = QApplication.instance()
        if app is not None and self._activation_connected:
            try:
                app.applicationStateChanged.disconnect(self._on_application_state_changed)
            except Exception:
                pass
        self._activation_connected = False

    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        if state != Qt.ApplicationState.ApplicationActive or not self.running:
            return
        if self._restart_pending:
            return
        self._restart_pending = True
        # 系统从休眠恢复时 Windows 的剪贴板监听注册可能已经失效。
        QTimer.singleShot(600, self._restart_after_activation)

    def _restart_after_activation(self) -> None:
        self._restart_pending = False
        if not self.running:
            return
        logger.info("Re-register clipboard listener after application activation")
        self.stop()
        self.start()
        # 主动同步一次，补偿恢复/解锁期间可能丢失的 WM_CLIPBOARDUPDATE。
        QTimer.singleShot(80, self._on_clipboard_changed)

    def pause_monitoring(self) -> None:
        self.running = False

    def resume_monitoring(self) -> None:
        self.running = True

    def ignore_next_content(self, content: str) -> None:
        text = self._normalize_clipboard_text(content)
        if not text:
            return
        self._ignored_contents.add(text)
        QTimer.singleShot(5000, lambda text=text: self._ignored_contents.discard(text))

    def _on_clipboard_changed(self, retry_count: int = 0) -> None:
        if not self.running:
            return
        if self.editing_session_active:
            return
        try:
            data = self.detect_clipboard_content()
            if data is None:
                data = self._detect_image()
            if data is None:
                # 其他进程可能短暂持有剪贴板锁；有限重试避免一次竞争导致永久漏记。
                if retry_count < 2:
                    QTimer.singleShot(
                        50 * (retry_count + 1),
                        lambda attempt=retry_count + 1: self._on_clipboard_changed(attempt),
                    )
                return
            content = data.get("content", "")
            signature = self._content_signature(data)
            ignored_content = self._normalize_clipboard_text(str(content or ""))
            if ignored_content and ignored_content in self._ignored_contents:
                self._last_signature = signature
                self.last_content = content
                return
            if signature and signature == self._last_signature:
                return
            if not signature and content == self.last_content:
                return
            self._last_signature = signature
            self.last_content = content
            self.path_detected.emit(data)
        except Exception as e:
            logger.error(f"Clipboard change handling error: {e}")

    def _content_signature(self, data: dict[str, Any]) -> str:
        try:
            metadata = data.get("metadata", {})
            if isinstance(metadata, dict):
                signature = str(metadata.get("signature", "") or "")
                if signature:
                    return signature
            content_type = str(data.get("content_type", "") or "")
            if content_type in {"file_path", "folder_path", "image"}:
                file_path = str(data.get("file_path", "") or "")
                if file_path:
                    return f"{content_type}:{file_path}"
            content = str(data.get("content", "") or "")
            if content:
                return f"{content_type}:{content}"
        except Exception:
            pass
        return ""

    def detect_clipboard_content(self) -> Optional[dict[str, Any]]:
        try:
            import win32clipboard
            import win32con
        except Exception as e:
            # 打包后若 pywin32 顶层模块未收集进 exe，这里会失败；
            # 必须记录详细错误，便于定位，否则剪贴板监听静默失效、用户无感知。
            logger.warning("win32clipboard not available: %s", e)
            return None

        try:
            win32clipboard.OpenClipboard()
        except Exception as e:
            # OpenClipboard 失败常见于其他进程正持有剪贴板锁，属正常竞争，仅 debug 级别
            logger.debug("OpenClipboard failed: %s", e)
            return None

        try:
            # 1. 尝试 CF_HDROP（文件拖放）
            try:
                files = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                if files:
                    if len(files) == 1:
                        path = files[0]
                        if os.path.isdir(path):
                            result = ContentTypeDetector.detect(path)
                            result["content_type"] = "folder_path"
                            result["file_path"] = path
                            return result
                        else:
                            result = ContentTypeDetector.detect(path)
                            result["content_type"] = "file_path"
                            result["file_path"] = path
                            return result
                    else:
                        content = "\n".join(files)
                        return {
                            "content": content,
                            "content_type": "multi_file",
                            "file_type": "",
                            "file_path": "",
                            "file_size": 0,
                            "source_app": _get_foreground_app_name(),
                            "metadata": {"files": files},
                        }
            except Exception:
                pass

            # 2. 尝试 CF_UNICODETEXT
            try:
                text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                if text is not None:
                    return ContentTypeDetector.detect(text)
            except Exception:
                pass

            return None
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _detect_image(self) -> Optional[dict[str, Any]]:
        """使用 QClipboard 检测并保存剪贴板图片"""
        try:
            from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
            from PyQt6.QtGui import QClipboard, QGuiApplication, QImage
            clipboard = QGuiApplication.clipboard()
            mime = clipboard.mimeData()
            if mime is None or not mime.hasImage():
                return None
            image = clipboard.image()
            if image.isNull():
                return None
            buffer_bytes = QByteArray()
            buffer = QBuffer(buffer_bytes)
            if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
                return None
            try:
                if not image.save(buffer, "PNG"):
                    return None
            finally:
                buffer.close()
            png_bytes = bytes(buffer_bytes)
            signature = f"image:{image.width()}x{image.height()}:{hashlib.sha256(png_bytes).hexdigest()}"
            if signature == self._last_signature:
                return None
            from datetime import datetime
            from pathlib import Path
            from deepcat.utils.paths import get_app_dir
            img_dir = Path(get_app_dir()) / "data" / "clipboard_images"
            img_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            img_path = img_dir / f"clipboard_{ts}.png"
            if img_path.write_bytes(png_bytes) > 0:
                return {
                    "content": f"[图片] {img_path.name}",
                    "content_type": "image",
                    "file_type": ".png",
                    "file_path": str(img_path),
                    "file_size": img_path.stat().st_size,
                    "source_app": _get_foreground_app_name(),
                    "metadata": {"signature": signature},
                }
        except Exception as e:
            logger.error(f"Detect image failed: {e}")
        return None
