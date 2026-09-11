from unittest.mock import patch

import sys

from PyQt6.QtWidgets import QApplication

from deepcat.clipboard_history.clipboard_monitor import ClipboardMonitor


def _app() -> QApplication:
    return QApplication.instance() or QApplication(sys.argv)


def test_clipboard_monitor_retries_transient_read_failure():
    app = _app()
    monitor = ClipboardMonitor()
    detected = []
    scheduled = []
    monitor.running = True
    monitor.path_detected.connect(detected.append)

    with (
        patch.object(monitor, "detect_clipboard_content", side_effect=[None, {"content": "ok", "content_type": "text"}]),
        patch.object(monitor, "_detect_image", return_value=None),
        patch("deepcat.clipboard_history.clipboard_monitor.QTimer.singleShot", side_effect=lambda _ms, cb: scheduled.append(cb)),
    ):
        monitor._on_clipboard_changed()
        assert len(scheduled) == 1
        scheduled.pop()()

    assert detected and detected[0]["content"] == "ok"


def test_clipboard_monitor_stop_disables_reactivation():
    app = _app()
    monitor = ClipboardMonitor()
    monitor.running = True
    monitor.stop()

    assert monitor.running is False
