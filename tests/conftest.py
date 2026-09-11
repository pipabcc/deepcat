"""测试全局兜底配置。

背景：PyQt6 在 Qt 回调（槽函数/定时器/事件处理）里出现未处理的 Python
异常时，若 sys.excepthook 仍是解释器默认值，会调用 qFatal() 以
0xC0000409 (fail-fast) 直接终止进程——绕过 faulthandler/WER，表现为
全量测试跑到任意位置无声硬崩溃、无回溯、无摘要。

典型雷源：某测试创建的控件调度了 QTimer.singleShot(延迟, lambda ...
self.widget ...)，测试结束控件销毁，之后任何测试泵事件时定时器触发，
lambda 访问已删除的 C++ 对象抛 RuntimeError → qFatal。

这里拦截异常以避免进程硬崩溃，同时把异常归入当前测试的失败报告。
"""
import os
import re
import sys
import traceback
from collections import deque
from pathlib import Path

import pytest


_QT_CALLBACK_ERRORS: deque[str] = deque()


def _non_fatal_excepthook(exc_type, exc, tb):
    _QT_CALLBACK_ERRORS.append("".join(traceback.format_exception(exc_type, exc, tb)))
    sys.stderr.write("\n[conftest] Qt 回调中的未处理异常（已拦截，不终止进程）:\n")
    traceback.print_exception(exc_type, exc, tb, file=sys.stderr)


sys.excepthook = _non_fatal_excepthook


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    """整个测试会话持有应用对象，避免临时引用释放后 Qt 控件失去运行环境。"""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    errors = []
    while _QT_CALLBACK_ERRORS:
        errors.append(_QT_CALLBACK_ERRORS.popleft())
    if errors:
        previous = str(report.longrepr) if report.failed else ""
        report.outcome = "failed"
        report.longrepr = previous + "\nQt 回调中出现未处理异常：\n" + "\n".join(errors)


def pytest_sessionfinish(session, exitstatus):
    if _QT_CALLBACK_ERRORS:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_sep("=", "测试之间出现未处理的 Qt 回调异常")
            for error in _QT_CALLBACK_ERRORS:
                reporter.write_line(error)


def pytest_configure(config) -> None:
    """为每个测试进程分配独立的工作区临时目录，规避 Windows 目录锁冲突。"""
    if not getattr(config.option, "basetemp", None):
        temp_root = Path.cwd() / ".pytest-runs"
        temp_root.mkdir(exist_ok=True)
        config.option.basetemp = str(temp_root / f"run-{os.getpid()}")


_INTEGRATION_TEST_FILES = {
    "test_chatgpt_web2api.py",
    "test_data_backup_exclude_images.py",
    "test_data_manager.py",
    "test_gemini_web2api_responses.py",
    "test_local_gemini_web2api_server.py",
    "test_security_hardening.py",
    "test_translation_service.py",
    "test_updater.py",
    "test_webdav.py",
}

_GUI_TEST_PREFIXES = (
    "test_ai_chat_",
    "test_chat_bubbles_",
    "test_cursor_",
    "test_later_read_",
    "test_main_window_",
    "test_markdown_",
    "test_notes_editor_",
    "test_notifications",
    "test_ocr_panel_",
    "test_output_quick_",
    "test_pinned_image_",
    "test_post_capture_",
    "test_region_",
    "test_selection_",
    "test_settings_dialog_",
    "test_ui_",
)

_SLOW_TEST_FILES = {
    "test_chatgpt_web2api.py",
    "test_settings_dialog_models.py",
    "test_translation_service.py",
}


_QT_IMPORT_RE = re.compile(r"(?m)^\s*(?:from|import)\s+PyQt6(?:\.|\s|$)")
_QT_TEST_FILE_CACHE: dict[Path, bool] = {}


def _test_file_imports_pyqt(path: Path) -> bool:
    resolved = Path(path).resolve()
    cached = _QT_TEST_FILE_CACHE.get(resolved)
    if cached is not None:
        return cached
    try:
        imports_pyqt = bool(_QT_IMPORT_RE.search(resolved.read_text(encoding="utf-8")))
    except OSError:
        imports_pyqt = False
    _QT_TEST_FILE_CACHE[resolved] = imports_pyqt
    return imports_pyqt


def _primary_marker_for_test_file(path: Path) -> str:
    filename = path.name
    if filename in _INTEGRATION_TEST_FILES:
        return "integration"
    if filename.startswith(_GUI_TEST_PREFIXES) or _test_file_imports_pyqt(path):
        return "gui"
    return "unit"


def pytest_collection_modifyitems(items) -> None:
    """按测试文件职责自动附加主分类，避免新增用例遗漏分组。"""
    for item in items:
        filename = item.path.name
        item.add_marker(getattr(pytest.mark, _primary_marker_for_test_file(item.path)))
        if filename in _SLOW_TEST_FILES:
            item.add_marker(pytest.mark.slow)
