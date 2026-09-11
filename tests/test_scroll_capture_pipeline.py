import ast
from datetime import datetime
from pathlib import Path

import numpy as np

from deepcat.core.scroll_capture_status import (
    ScrollCaptureStatus,
    resolve_scroll_capture_status,
)
from deepcat.core.stitcher import PiecewiseStitcher
from deepcat.output.naming import make_capture_token, unique_output_path


def test_stitcher_has_single_public_stitch_frames_entry() -> None:
    source_path = Path(__file__).parents[1] / "deepcat" / "core" / "stitcher.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    definitions = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "stitch_frames"
    ]
    assert len(definitions) == 1


def test_piecewise_chunks_equal_full_result_and_report_peak() -> None:
    stitcher = PiecewiseStitcher()
    first = np.full((3, 4, 3), 11, dtype=np.uint8)
    second = np.full((2, 4, 3), 22, dtype=np.uint8)
    stitcher._append_piece(first)
    stitcher._append_piece(second)
    stitcher._rebuild_tail(2)

    full = stitcher.result_image()
    chunked = np.concatenate(list(stitcher.iter_result_chunks(max_rows=2)), axis=0)
    np.testing.assert_array_equal(chunked, full)

    profile = stitcher.memory_profile(chunk_rows=2)
    assert profile.result_bytes == full.nbytes
    assert profile.chunk_buffer_bytes == 2 * 4 * 3
    assert profile.estimated_peak_bytes == profile.piece_bytes + profile.tail_bytes + full.nbytes
    assert profile.estimated_chunked_peak_bytes < profile.estimated_peak_bytes


def test_scroll_capture_status_model_covers_all_outcomes() -> None:
    assert resolve_scroll_capture_status(
        stop_reason=None, stitch_degraded=False, skipped_frames=0
    ) == ScrollCaptureStatus.COMPLETE_SUCCESS
    assert resolve_scroll_capture_status(
        stop_reason=None, stitch_degraded=False, skipped_frames=1
    ) == ScrollCaptureStatus.PARTIAL_SUCCESS
    assert resolve_scroll_capture_status(
        stop_reason="已到达底部，自动停止", stitch_degraded=False, skipped_frames=0
    ) == ScrollCaptureStatus.BOTTOM_REACHED
    assert resolve_scroll_capture_status(
        stop_reason="用户手动停止", stitch_degraded=False, skipped_frames=0
    ) == ScrollCaptureStatus.USER_STOPPED
    assert resolve_scroll_capture_status(
        stop_reason=None, stitch_degraded=True, skipped_frames=0
    ) == ScrollCaptureStatus.STITCH_DEGRADED


def test_capture_naming_avoids_same_millisecond_collision(tmp_path: Path) -> None:
    now = datetime(2026, 7, 11, 12, 0, 0, 123000)
    first_token = make_capture_token(now)
    second_token = make_capture_token(now)
    assert first_token != second_token

    first_path = unique_output_path(tmp_path, "png", token="fixed")
    first_path.touch()
    second_path = unique_output_path(tmp_path, "png", token="fixed")
    assert first_path != second_path
