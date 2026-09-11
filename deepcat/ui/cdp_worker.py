from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from deepcat.cdp.chrome_cdp import capture_full_page_screenshot_sync
from deepcat.input.hotkey_listener import HotkeyListener
from deepcat.utils.logger import get_logger
from deepcat.settings_store import get_image_output_dir

logger = get_logger()


@dataclass(frozen=True)
class CDPSettings:
    port: int = 9888
    url_contains: Optional[str] = None
    content_only: bool = False


class CDPWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str, str)

    def __init__(self, settings: CDPSettings) -> None:
        super().__init__()
        self._settings = settings
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        pause_hotkey = "<ctrl>+<space>"
        listener = HotkeyListener(pause_hotkey=pause_hotkey)
        try:
            try:
                listener.start()
            except Exception:
                listener = None  # type: ignore[assignment]

            def should_stop() -> bool:
                if self._stop_event.is_set():
                    return True
                if listener is not None and listener.should_stop():
                    return True
                return False

            res = capture_full_page_screenshot_sync(
                port=int(self._settings.port),
                output_dir=str(get_image_output_dir(validate_writable=True)),
                url_contains=self._settings.url_contains,
                content_only=bool(self._settings.content_only),
                should_stop=should_stop,
            )
            if len(res.paths) == 1:
                self.finished.emit(res.paths[0])
            else:
                self.finished.emit(res.paths)
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("CDP 截图失败: %s", repr(e))
            logger.error(tb)
            self.failed.emit(str(e) or repr(e), tb)
        finally:
            try:
                if listener is not None:
                    listener.cleanup()
            except Exception:
                pass
