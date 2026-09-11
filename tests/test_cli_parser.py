from __future__ import annotations

import unittest
from unittest.mock import Mock, patch


class TestCliParser(unittest.TestCase):
    def test_gui_launch_does_not_force_output_format(self) -> None:
        from deepcat.main import build_parser

        args = build_parser().parse_args(["--gui"])
        self.assertTrue(args.gui)
        self.assertIsNone(args.format)

    def test_explicit_format_is_preserved(self) -> None:
        from deepcat.main import build_parser

        args = build_parser().parse_args(["--gui", "--format", "jpg"])
        self.assertEqual(args.format, "jpg")

    def test_translate_service_args(self) -> None:
        from deepcat.main import build_parser

        args = build_parser().parse_args(["--serve-translate", "--translate-port", "11999"])
        self.assertTrue(args.serve_translate)
        self.assertEqual(args.translate_host, "127.0.0.1")
        self.assertEqual(args.translate_port, 11999)
        self.assertEqual(args.translate_api_key, "sk-deepcat-local")

    def test_translate_service_empty_api_key_is_preserved(self) -> None:
        from deepcat.main import build_parser

        args = build_parser().parse_args(["--serve-translate", "--translate-api-key", ""])
        self.assertEqual(args.translate_api_key, "")

    def test_cli_scroll_wait_detects_frame_change_without_name_error(self) -> None:
        from deepcat.main import build_parser, run_cli

        args = build_parser().parse_args(["--start-delay", "0", "--max-frames", "2"])
        listener = Mock()
        listener.should_stop.return_value = False
        captured_frames = [object(), object(), object(), object()]
        stitched_image = object()

        with (
            patch("builtins.input", return_value=""),
            patch("deepcat.main.ensure_deps", return_value=True),
            patch("deepcat.main.countdown"),
            patch("deepcat.core.capturer.capture_screen", side_effect=captured_frames) as capture_screen,
            patch("deepcat.core.detector.detect_fixed_regions", return_value=object()),
            patch("deepcat.core.detector.is_at_bottom", return_value=False),
            patch("deepcat.core.detector.mean_abs_diff", return_value=10.0) as mean_abs_diff,
            patch("deepcat.core.scroller.scroll_down"),
            patch("deepcat.core.stitcher.stitch_images", return_value=stitched_image),
            patch("deepcat.input.hotkey_listener.HotkeyListener", return_value=listener),
            patch("deepcat.output.clipboard.copy_image_to_clipboard", return_value=True),
            patch("deepcat.output.saver.save_image", return_value="out.png"),
        ):
            exit_code = run_cli(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(capture_screen.call_count, 4)
        self.assertEqual(mean_abs_diff.call_count, 2)
        listener.cleanup.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
