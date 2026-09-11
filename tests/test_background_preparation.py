from __future__ import annotations

import base64
from pathlib import Path

import numpy as np


def test_file_attachment_prepare_worker_reads_and_encodes_off_ui(tmp_path: Path) -> None:
    from deepcat.ui.post_capture_actions.workers import FileAttachmentPrepareWorker

    source = tmp_path / "note.md"
    source.write_text("DeepCat", encoding="utf-8")
    results: list[dict] = []
    errors: list[str] = []
    worker = FileAttachmentPrepareWorker(str(source))
    worker.prepared.connect(results.append)
    worker.failed.connect(errors.append)

    worker.run()

    assert errors == []
    payload = results[0]
    assert payload["filename"] == "note.md"
    url = payload["attachment"]["file_url"]["url"]
    assert url.startswith("data:text/markdown;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"DeepCat"


def test_image_attachment_prepare_worker_encodes_png() -> None:
    from deepcat.ui.post_capture_actions.workers import ImageAttachmentPrepareWorker

    results: list[tuple[str, str]] = []
    errors: list[str] = []
    image = np.zeros((8, 12, 3), dtype=np.uint8)
    worker = ImageAttachmentPrepareWorker(image, "分析图片")
    worker.prepared.connect(lambda data, prompt: results.append((data, prompt)))
    worker.failed.connect(errors.append)

    worker.run()

    assert errors == []
    encoded, prompt = results[0]
    assert prompt == "分析图片"
    assert base64.b64decode(encoded).startswith(b"\x89PNG\r\n\x1a\n")


def test_scroll_result_prepare_worker_loads_and_stacks_saved_parts(tmp_path: Path) -> None:
    import cv2

    from deepcat.ui.main_window.notes import ScrollResultPrepareWorker

    paths = []
    for index, value in enumerate((30, 90)):
        image = np.full((4, 6, 3), value, dtype=np.uint8)
        ok, encoded = cv2.imencode(".png", image)
        assert ok
        path = tmp_path / f"part-{index}.png"
        path.write_bytes(encoded.tobytes())
        paths.append(str(path))

    results: list[dict] = []
    worker = ScrollResultPrepareWorker(paths, False, None)
    worker.prepared.connect(results.append)
    worker.run()

    assert results[0]["image_bgr"].shape == (8, 6, 3)
    assert results[0]["rgb"].shape == (8, 6, 3)


def test_translation_worker_cancel_closes_active_response() -> None:
    from deepcat.ui.post_capture_actions.workers import OcrTranslationWorker

    class FakeResponse:
        closed = False

        def close(self) -> None:
            self.closed = True

    worker = OcrTranslationWorker("text", "自动检测", "英语", {})
    response = FakeResponse()
    worker._remember_response(response)

    worker.request_cancel()

    assert worker._is_cancelled()
    assert response.closed is True


def test_ui_sources_do_not_force_terminate_qthreads() -> None:
    root = Path(__file__).resolve().parents[1] / "deepcat" / "ui"
    offenders = []
    for path in root.rglob("*.py"):
        if ".terminate(" in path.read_text(encoding="utf-8-sig"):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
