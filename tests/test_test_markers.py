from __future__ import annotations

from pathlib import Path

import conftest


TESTS_DIR = Path(__file__).resolve().parent


def test_direct_pyqt_tests_are_classified_as_gui() -> None:
    expected_gui_files = {
        "test_ai_input_expand_button.py",
        "test_clipboard_monitor_recovery.py",
        "test_clipboard_pinned_fold.py",
        "test_resource_shortcut_dialog.py",
        "test_stable_icons.py",
        "test_tray_menu_style.py",
    }

    for filename in expected_gui_files:
        assert conftest._primary_marker_for_test_file(TESTS_DIR / filename) == "gui"


def test_non_qt_logic_test_remains_unit() -> None:
    assert conftest._primary_marker_for_test_file(TESTS_DIR / "test_dependency_declarations.py") == "unit"


def test_integration_classification_takes_precedence() -> None:
    assert conftest._primary_marker_for_test_file(TESTS_DIR / "test_updater.py") == "integration"
