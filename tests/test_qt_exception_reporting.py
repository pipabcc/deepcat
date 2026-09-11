"""即使用例断言通过，Qt 定时器异常也必须让测试失败。"""

import os
from pathlib import Path
import subprocess
import sys


def test_unhandled_qt_callback_fails_pytest_without_crashing_process(tmp_path):
    conftest = Path(__file__).with_name("conftest.py")
    (tmp_path / "conftest.py").write_text(conftest.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_callback.py").write_text(
        "from PyQt6.QtCore import QTimer\n"
        "from PyQt6.QtWidgets import QApplication\n"
        "def test_callback():\n"
        "    app = QApplication.instance() or QApplication([])\n"
        "    def fail():\n"
        "        raise RuntimeError('intentional_callback_error')\n"
        "    QTimer.singleShot(0, fail)\n"
        "    app.processEvents()\n"
        "    assert True\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "test_callback.py"],
        cwd=tmp_path,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "intentional_callback_error" in result.stdout
    assert "1 failed" in result.stdout
