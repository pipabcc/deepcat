from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from deepcat.ocr_ipc import PROTOCOL_VERSION, receive_message, send_message
from deepcat.ocr_worker import run_ocr_worker, serve_ocr_worker
from deepcat.ocr_worker_client import (
    OcrCancellationToken,
    OcrRequestCancelled,
    OcrWorkerClient,
    OcrWorkerStartupError,
    build_ocr_worker_command,
)
from deepcat.ui.main_window.translator import TranslatorMixin
from deepcat.ui.post_capture_actions.actions import PostCaptureActions


def _dummy_actions() -> SimpleNamespace:
    return SimpleNamespace(
        _preprocess_ocr_pad_height=lambda image: image,
        _preprocess_ocr_resize_large=lambda image: image,
    )


def test_main_ocr_path_always_uses_subprocess(monkeypatch) -> None:
    dummy = SimpleNamespace(
        _ocr_budget_seconds=lambda _image: 12.0,
        _ocr_extract_rapidocr_subprocess=lambda _image, timeout_seconds: ("ok", str(timeout_seconds)),
        _ocr_extract_rapidocr_inprocess=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("主进程不应加载 OCR")
        ),
    )

    result = PostCaptureActions._ocr_extract_rapidocr(dummy, np.zeros((2, 2, 3), dtype=np.uint8))

    assert result == ("ok", "12.0")


def test_ocr_uses_persistent_client_with_raw_image(monkeypatch) -> None:
    captured_images: list[np.ndarray] = []

    class FakeClient:
        def recognize(self, image, budget_seconds: float, **_kwargs):
            captured_images.append(image)
            assert budget_seconds == 10.0
            return {
                "text": "识别结果",
                "stderr": "",
                "error": "",
                "timings": {"worker_reused": True},
            }

    monkeypatch.setattr("deepcat.ocr_worker_client.get_ocr_worker_client", lambda: FakeClient())
    result = PostCaptureActions._ocr_extract_rapidocr_subprocess(
        _dummy_actions(),
        np.zeros((4, 4, 3), dtype=np.uint8),
        timeout_seconds=10.0,
    )

    assert result == ("识别结果", "")
    assert len(captured_images) == 1
    assert captured_images[0].shape == (4, 4, 3)
    assert captured_images[0].dtype == np.uint8


def test_frozen_worker_command_uses_early_dispatch(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    command = build_ocr_worker_command(12345, "token")

    assert command[:3] == [sys.executable, "--deepcat-ocr-worker", "serve"]
    assert command[command.index("--engine") + 1] == "rapidocr"
    assert command[-2:] == ["--token", "token"]


def test_worker_command_pins_requested_engine(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)

    command = build_ocr_worker_command(12345, "token", "ppocrv6")

    assert command[:3] == [sys.executable, "-m", "deepcat.ocr_worker"]
    assert command[command.index("--engine") + 1] == "ppocrv6"


def test_main_dispatches_ocr_worker_before_application_parser(monkeypatch) -> None:
    from deepcat import main as main_module

    captured: list[list[str]] = []
    monkeypatch.setattr("deepcat.ocr_worker.main", lambda args: captured.append(list(args)) or 17)

    exit_code = main_module.main(["--deepcat-ocr-worker", "serve", "--port", "1", "--token", "x"])

    assert exit_code == 17
    assert captured == [["serve", "--port", "1", "--token", "x"]]


def test_ocr_worker_writes_error_and_returns_nonzero(monkeypatch, tmp_path: Path) -> None:
    image_path = tmp_path / "input.png"
    result_path = tmp_path / "result.json"
    image_path.write_bytes(b"image")
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=object))
    monkeypatch.setattr("deepcat.utils.ocr_runtime.configure_ocr_dll_search_paths", lambda: ())
    monkeypatch.setattr("deepcat.utils.ort_patch.patch_onnxruntime", lambda: None)
    monkeypatch.setattr("cv2.imread", lambda _path: np.zeros((2, 2, 3), dtype=np.uint8))
    monkeypatch.setattr(
        PostCaptureActions,
        "_ocr_extract_rapidocr_inprocess",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("OCR加载失败")),
    )

    exit_code = run_ocr_worker(image_path, result_path, 8.0)
    payload = json.loads(result_path.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert "OCR加载失败" in payload["error"]


def test_ocr_timeout_propagates_without_temporary_files(monkeypatch) -> None:
    captured_images: list[np.ndarray] = []

    class TimeoutClient:
        def recognize(self, image, _budget_seconds: float, **_kwargs):
            captured_images.append(image)
            raise subprocess.TimeoutExpired("persistent OCR worker", 5.0)

    monkeypatch.setattr("deepcat.ocr_worker_client.get_ocr_worker_client", lambda: TimeoutClient())

    try:
        PostCaptureActions._ocr_extract_rapidocr_subprocess(
            _dummy_actions(),
            np.zeros((4, 4, 3), dtype=np.uint8),
            timeout_seconds=4.0,
        )
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("OCR 超时应继续向上报告")

    # 图像经原始字节直传，不再产生临时文件
    assert len(captured_images) == 1
    assert captured_images[0].shape == (4, 4, 3)


def test_normal_image_runs_at_most_two_detection_passes() -> None:
    class EmptyEngine:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def __call__(self, _image, **kwargs):
            self.calls.append(dict(kwargs))
            return [], None

    detector = EmptyEngine()
    recognizer = EmptyEngine()
    dummy = SimpleNamespace(
        _ocr_size_bucket=lambda _image: "normal",
        _get_ocr_engines=lambda _bucket: (detector, recognizer),
        _preprocess_ocr_pad_height=lambda image: image,
        _preprocess_ocr_resize_large=lambda image: image,
        _set_ocr_limit_side_len=lambda *_args: None,
        _preprocess_ocr_unsharp=lambda image: image,
        _extract_ocr_entries_from_result=lambda _result: [],
        _subtract_padding=lambda *_args: None,
        _preprocess_ocr_clahe=lambda image: image,
        _format_ocr_entries=lambda _entries: "",
    )
    image = np.zeros((300, 300, 3), dtype=np.uint8)
    image[:, 150:] = 255
    metrics: dict = {}

    result = PostCaptureActions._ocr_extract_rapidocr_inprocess(
        dummy,
        image,
        budget_seconds=8.0,
        metrics=metrics,
    )

    assert result == ("", "")
    assert len(detector.calls) == 2
    assert recognizer.calls == []
    assert metrics["pass_count"] == 2
    assert metrics["passes"] == ["standard", "normal_relaxed"]


def test_ocr_engines_are_shared_across_size_buckets(monkeypatch) -> None:
    created: list[dict] = []

    class FakeRapidOcr:
        def __init__(self, **kwargs) -> None:
            created.append(dict(kwargs))

    class Runner:
        _ocr_engines_by_bucket: dict = {}
        _ocr_engine = None
        _ocr_engine_rec_only = None

    monkeypatch.setattr(
        "deepcat.ui.post_capture_actions.actions.configure_ocr_dll_search_paths",
        lambda: (),
    )
    monkeypatch.setattr("deepcat.utils.ort_patch.patch_onnxruntime", lambda: None)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOcr))
    get_engines = PostCaptureActions.__dict__["_get_ocr_engines"].__get__(None, Runner)

    normal = get_engines("normal")
    wide = get_engines("wide")

    assert normal[0] is wide[0]
    assert normal[1] is None
    assert wide[1] is not None
    assert len(created) == 2
    assert list(Runner._ocr_engines_by_bucket) == ["shared"]


def test_persistent_worker_reuses_one_runner_for_multiple_requests(monkeypatch, tmp_path: Path) -> None:
    parent_socket, worker_socket = socket.socketpair()
    runner = object()
    create_calls: list[object] = []
    request_runners: list[object] = []

    def fake_create_runner(engine="rapidocr"):
        assert engine == "rapidocr"
        create_calls.append(runner)
        return runner, object()

    def fake_load_request_image(_cv2, request, _connection):
        image = SimpleNamespace(name=Path(str(request.get("image_path", ""))).name)
        return image, 0.0

    def fake_run_request(received_runner, _cv2, image_bgr, budget_seconds):
        request_runners.append(received_runner)
        return {
            "text": image_bgr.name,
            "stderr": "",
            "error": "",
            "timings": {"budget": budget_seconds},
        }

    monkeypatch.setattr("deepcat.ocr_worker.socket.create_connection", lambda *_args, **_kwargs: worker_socket)
    monkeypatch.setattr("deepcat.ocr_worker._create_runner", fake_create_runner)
    monkeypatch.setattr("deepcat.ocr_worker._load_request_image", fake_load_request_image)
    monkeypatch.setattr("deepcat.ocr_worker._run_request", fake_run_request)

    thread = threading.Thread(target=serve_ocr_worker, args=("127.0.0.1", 1, "secret"), daemon=True)
    thread.start()
    hello = receive_message(parent_socket)
    ready = receive_message(parent_socket)

    assert hello["token"] == "secret"
    assert ready["type"] == "ready"
    for index in range(2):
        request_id = f"request-{index}"
        send_message(
            parent_socket,
            {
                "version": PROTOCOL_VERSION,
                "type": "ocr",
                "id": request_id,
                "image_path": str(tmp_path / f"image-{index}.png"),
                "budget_seconds": 12.0,
            },
        )
        response = receive_message(parent_socket)
        assert response["id"] == request_id
        assert response["text"] == f"image-{index}.png"

    send_message(parent_socket, {"version": PROTOCOL_VERSION, "type": "shutdown", "id": "done"})
    thread.join(timeout=2.0)
    parent_socket.close()

    assert not thread.is_alive()
    assert create_calls == [runner]
    assert request_runners == [runner, runner]


def test_persistent_worker_rejects_request_for_different_engine(monkeypatch) -> None:
    parent_socket, worker_socket = socket.socketpair()

    monkeypatch.setattr("deepcat.ocr_worker.socket.create_connection", lambda *_args, **_kwargs: worker_socket)
    monkeypatch.setattr("deepcat.ocr_worker._create_runner", lambda engine="rapidocr": (object(), object()))

    thread = threading.Thread(
        target=serve_ocr_worker,
        args=("127.0.0.1", 1, "secret", "rapidocr"),
        daemon=True,
    )
    thread.start()
    assert receive_message(parent_socket)["engine"] == "rapidocr"
    assert receive_message(parent_socket)["engine"] == "rapidocr"

    send_message(
        parent_socket,
        {
            "version": PROTOCOL_VERSION,
            "type": "ocr",
            "id": "wrong-engine",
            "engine": "ppocrv6",
            "image_path": "unused.png",
            "budget_seconds": 8.0,
        },
    )
    thread.join(timeout=2.0)
    parent_socket.close()

    assert not thread.is_alive()


def test_client_serializes_requests_on_reused_connection(tmp_path: Path) -> None:
    client_socket, worker_socket = socket.socketpair()

    class FakeProcess:
        def __init__(self) -> None:
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def kill(self):
            self.running = False

        def wait(self, timeout=None):
            self.running = False
            return 0

    client = OcrWorkerClient()
    client._connection = client_socket
    client._process = FakeProcess()
    client._generation = 1
    observed_ids: list[str] = []

    def fake_worker() -> None:
        for index in range(2):
            request = receive_message(worker_socket)
            observed_ids.append(str(request["id"]))
            send_message(
                worker_socket,
                {
                    "version": PROTOCOL_VERSION,
                    "type": "result",
                    "id": request["id"],
                    "text": f"result-{index}",
                    "stderr": "",
                    "error": "",
                    "timings": {},
                },
            )
        receive_message(worker_socket)
        worker_socket.close()

    thread = threading.Thread(target=fake_worker, daemon=True)
    thread.start()
    first = client.recognize(tmp_path / "first.png", 8.0)
    second = client.recognize(tmp_path / "second.png", 8.0)
    client.shutdown()
    client.shutdown()
    thread.join(timeout=2.0)

    assert first["text"] == "result-0"
    assert second["text"] == "result-1"
    assert first["timings"]["worker_reused"] is True
    assert second["timings"]["worker_reused"] is True
    assert len(observed_ids) == 2
    assert observed_ids[0] != observed_ids[1]


def test_successful_request_releases_worker_process(tmp_path: Path) -> None:
    client_socket, worker_socket = socket.socketpair()

    class FakeProcess:
        def __init__(self) -> None:
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def kill(self):
            self.running = False

        def wait(self, timeout=None):
            self.running = False
            return 0

    process = FakeProcess()
    client = OcrWorkerClient()
    client._connection = client_socket
    client._process = process
    client._generation = 1

    def fake_worker() -> None:
        request = receive_message(worker_socket)
        send_message(
            worker_socket,
            {
                "version": PROTOCOL_VERSION,
                "type": "result",
                "id": request["id"],
                "text": "识别成功",
                "stderr": "",
                "error": "",
                "timings": {},
            },
        )
        try:
            receive_message(worker_socket)
        except (EOFError, OSError):
            pass
        worker_socket.close()

    thread = threading.Thread(target=fake_worker, daemon=True)
    thread.start()
    payload = client.recognize(tmp_path / "success.png", 8.0, release_on_success=True)
    thread.join(timeout=2.0)

    assert payload["timings"]["worker_released"] is True
    assert process.running is False
    assert client._process is None
    assert client._connection is None


def test_empty_ocr_result_keeps_worker_for_retry(tmp_path: Path) -> None:
    client_socket, worker_socket = socket.socketpair()

    class FakeProcess:
        running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def kill(self):
            self.running = False

        def wait(self, timeout=None):
            self.running = False
            return 0

    process = FakeProcess()
    client = OcrWorkerClient()
    client._connection = client_socket
    client._process = process
    client._generation = 1

    def fake_worker() -> None:
        request = receive_message(worker_socket)
        send_message(
            worker_socket,
            {
                "version": PROTOCOL_VERSION,
                "type": "result",
                "id": request["id"],
                "text": "",
                "stderr": "",
                "error": "",
                "timings": {},
            },
        )
        receive_message(worker_socket)
        worker_socket.close()

    thread = threading.Thread(target=fake_worker, daemon=True)
    thread.start()
    payload = client.recognize(tmp_path / "empty.png", 8.0, release_on_success=True)

    assert payload["timings"]["worker_released"] is False
    assert process.running is True
    assert client._process is process
    client.shutdown()
    thread.join(timeout=2.0)


def test_empty_result_then_engine_switch_restarts_worker(monkeypatch, tmp_path: Path) -> None:
    rapid_client_socket, rapid_worker_socket = socket.socketpair()
    ppocr_client_socket, ppocr_worker_socket = socket.socketpair()

    class FakeProcess:
        def __init__(self) -> None:
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def kill(self):
            self.running = False

        def wait(self, timeout=None):
            self.running = False
            return 0

    rapid_process = FakeProcess()
    ppocr_process = FakeProcess()
    client = OcrWorkerClient()
    client._connection = rapid_client_socket
    client._process = rapid_process
    client._worker_engine = "rapidocr"
    client._generation = 1
    started_engines: list[str] = []

    def start_worker(_deadline: float, engine: str):
        assert rapid_process.running is False
        client._generation += 1
        client._connection = ppocr_client_socket
        client._process = ppocr_process
        client._worker_engine = engine
        started_engines.append(engine)
        return SimpleNamespace(
            connection=ppocr_client_socket,
            process=ppocr_process,
            generation=client._generation,
            reused=False,
            startup_ms=0.0,
            engine=engine,
        )

    monkeypatch.setattr(client, "_start_worker", start_worker)

    def rapid_worker() -> None:
        request = receive_message(rapid_worker_socket)
        send_message(
            rapid_worker_socket,
            {
                "version": PROTOCOL_VERSION,
                "type": "result",
                "id": request["id"],
                "engine": "rapidocr",
                "text": "",
                "stderr": "",
                "error": "",
                "timings": {},
            },
        )
        try:
            receive_message(rapid_worker_socket)
        except (EOFError, OSError):
            pass
        rapid_worker_socket.close()

    def ppocr_worker() -> None:
        request = receive_message(ppocr_worker_socket)
        assert request["engine"] == "ppocrv6"
        send_message(
            ppocr_worker_socket,
            {
                "version": PROTOCOL_VERSION,
                "type": "result",
                "id": request["id"],
                "engine": "ppocrv6",
                "text": "PP-OCRv6",
                "stderr": "",
                "error": "",
                "timings": {},
            },
        )
        receive_message(ppocr_worker_socket)
        ppocr_worker_socket.close()

    rapid_thread = threading.Thread(target=rapid_worker, daemon=True)
    ppocr_thread = threading.Thread(target=ppocr_worker, daemon=True)
    rapid_thread.start()
    ppocr_thread.start()

    empty_payload = client.recognize(tmp_path / "empty.png", 8.0, release_on_success=True)
    switched_payload = client.recognize(tmp_path / "ppocr.png", 8.0, engine="ppocrv6")
    client.shutdown()
    rapid_thread.join(timeout=2.0)
    ppocr_thread.join(timeout=2.0)

    assert empty_payload["timings"]["worker_released"] is False
    assert switched_payload["text"] == "PP-OCRv6"
    assert switched_payload["timings"]["worker_reused"] is False
    assert started_engines == ["ppocrv6"]
    assert not rapid_thread.is_alive()
    assert not ppocr_thread.is_alive()


def test_receive_message_enforces_absolute_deadline() -> None:
    sender, receiver = socket.socketpair()
    payload = json.dumps({"type": "result"}).encode("utf-8")
    frame = len(payload).to_bytes(4, "big") + payload

    def send_slowly() -> None:
        try:
            for value in frame:
                sender.send(bytes((value,)))
                time.sleep(0.02)
        except OSError:
            pass

    thread = threading.Thread(target=send_slowly, daemon=True)
    thread.start()
    with pytest.raises(socket.timeout):
        receive_message(receiver, deadline=time.perf_counter() + 0.05)
    receiver.close()
    sender.close()
    thread.join(timeout=1.0)


def test_shutdown_interrupts_active_request_without_sending_second_frame(tmp_path: Path) -> None:
    client_socket, worker_socket = socket.socketpair()

    class FakeProcess:
        def __init__(self) -> None:
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def kill(self):
            self.running = False

        def wait(self, timeout=None):
            self.running = False
            return 0

    client = OcrWorkerClient()
    client._connection = client_socket
    client._process = FakeProcess()
    client._generation = 1
    request_received = threading.Event()
    extra_messages: list[dict] = []
    errors: list[BaseException] = []

    def fake_worker() -> None:
        try:
            receive_message(worker_socket)
            request_received.set()
            extra_messages.append(receive_message(worker_socket))
        except (EOFError, OSError):
            pass
        finally:
            worker_socket.close()

    def recognize() -> None:
        try:
            client.recognize(tmp_path / "active.png", 8.0)
        except BaseException as exc:
            errors.append(exc)

    worker_thread = threading.Thread(target=fake_worker, daemon=True)
    request_thread = threading.Thread(target=recognize, daemon=True)
    worker_thread.start()
    request_thread.start()
    assert request_received.wait(timeout=1.0)

    client.shutdown()
    request_thread.join(timeout=2.0)
    worker_thread.join(timeout=2.0)

    assert not request_thread.is_alive()
    assert not worker_thread.is_alive()
    assert extra_messages == []
    assert errors


def test_shutdown_retains_unreaped_process_for_retry() -> None:
    class UnkillableProcess:
        def __init__(self) -> None:
            self.terminate_calls = 0
            self.kill_calls = 0

        def poll(self):
            return None

        def terminate(self):
            self.terminate_calls += 1
            raise OSError("terminate failed")

        def kill(self):
            self.kill_calls += 1
            raise OSError("kill failed")

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("unkillable", timeout)

    process = UnkillableProcess()
    client = OcrWorkerClient()
    client._process = process

    client.shutdown()
    first_terminate_calls = process.terminate_calls
    assert process in client._cleanup_processes

    client.shutdown()
    assert process.terminate_calls > first_terminate_calls
    assert process in client._cleanup_processes


def test_cancelled_queued_request_never_reaches_worker(tmp_path: Path) -> None:
    client_socket, worker_socket = socket.socketpair()

    class FakeProcess:
        def __init__(self) -> None:
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def kill(self):
            self.running = False

        def wait(self, timeout=None):
            self.running = False
            return 0

    client = OcrWorkerClient()
    client._connection = client_socket
    client._process = FakeProcess()
    client._generation = 1
    first_token = OcrCancellationToken()
    second_token = OcrCancellationToken()
    first_request_received = threading.Event()
    release_first_response = threading.Event()
    received_types: list[str] = []
    results: list[str] = []
    errors: list[BaseException] = []

    def fake_worker() -> None:
        first = receive_message(worker_socket)
        received_types.append(str(first["type"]))
        first_request_received.set()
        assert release_first_response.wait(timeout=1.0)
        send_message(
            worker_socket,
            {
                "version": PROTOCOL_VERSION,
                "type": "result",
                "id": first["id"],
                "text": "first",
                "stderr": "",
                "error": "",
                "timings": {},
            },
        )
        received_types.append(str(receive_message(worker_socket)["type"]))
        worker_socket.close()

    def recognize(path: str, token: OcrCancellationToken) -> None:
        try:
            payload = client.recognize(tmp_path / path, 8.0, cancel_token=token)
            results.append(str(payload["text"]))
        except BaseException as exc:
            errors.append(exc)

    worker_thread = threading.Thread(target=fake_worker, daemon=True)
    first_thread = threading.Thread(target=recognize, args=("first.png", first_token), daemon=True)
    second_thread = threading.Thread(target=recognize, args=("second.png", second_token), daemon=True)
    worker_thread.start()
    first_thread.start()
    assert first_request_received.wait(timeout=1.0)
    second_thread.start()
    assert client.cancel_request(second_token) is False
    release_first_response.set()
    first_thread.join(timeout=2.0)
    second_thread.join(timeout=2.0)
    client.shutdown()
    worker_thread.join(timeout=2.0)

    assert results == ["first"]
    assert any(isinstance(exc, OcrRequestCancelled) for exc in errors)
    assert received_types == ["ocr", "shutdown"]


def test_worker_exit_before_connect_fails_fast() -> None:
    class ExitedProcess:
        @staticmethod
        def poll():
            return 23

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = OcrWorkerClient()
    started_at = time.perf_counter()
    try:
        with pytest.raises(OcrWorkerStartupError, match="退出码 23"):
            client._accept_authenticated(
                listener,
                "token",
                time.perf_counter() + 5.0,
                ExitedProcess(),
            )
    finally:
        listener.close()

    assert time.perf_counter() - started_at < 0.5


def test_startup_ocr_selfcheck_does_not_import_runtime() -> None:
    dummy = SimpleNamespace(_ocr_selfcheck_done=False)

    TranslatorMixin._startup_ocr_selfcheck(dummy)

    assert dummy._ocr_selfcheck_done is True
