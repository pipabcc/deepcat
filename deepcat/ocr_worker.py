from __future__ import annotations

import argparse
import json
import os
import socket
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np

from deepcat.ocr_engines import OCR_ENGINE_PPOCRV6, OCR_ENGINE_RAPIDOCR, normalize_ocr_engine
from deepcat.ocr_ipc import (
    OcrProtocolError,
    PROTOCOL_VERSION,
    receive_message,
    receive_raw_bytes,
    send_message,
)


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _create_runner(engine: str = OCR_ENGINE_RAPIDOCR) -> tuple[object, Any]:
    engine = normalize_ocr_engine(engine)
    from deepcat.utils.ocr_runtime import configure_ocr_dll_search_paths

    configure_ocr_dll_search_paths()
    # ONNX Runtime 必须先于 PyQt 加载，避免 Windows 原生 DLL 初始化冲突。
    from deepcat.utils.ort_patch import patch_onnxruntime

    patch_onnxruntime()
    import onnxruntime  # noqa: F401

    import cv2

    if engine == OCR_ENGINE_PPOCRV6:
        from deepcat.ppocrv6_runtime import PpOcrV6Runner

        return PpOcrV6Runner(), cv2

    from rapidocr_onnxruntime import RapidOCR  # noqa: F401

    from deepcat.ui.post_capture_actions.actions import PostCaptureActions

    method_names = (
        "_ocr_extract_rapidocr_inprocess",
        "_ocr_size_bucket",
        "_get_ocr_engines",
        "_preprocess_ocr_pad_height",
        "_preprocess_ocr_resize_large",
        "_set_ocr_limit_side_len",
        "_preprocess_ocr_unsharp",
        "_preprocess_ocr_clahe",
        "_extract_ocr_entries_from_result",
        "_norm_bbox",
        "_subtract_padding",
        "_format_ocr_entries",
    )
    runner_type = type(
        "_OcrProcessRunner",
        (),
        {
            **{name: PostCaptureActions.__dict__[name] for name in method_names},
            "_ocr_engines_by_bucket": {},
            "_ocr_engine": None,
            "_ocr_engine_rec_only": None,
            "_ocr_engine_name": OCR_ENGINE_RAPIDOCR,
        },
    )
    return runner_type(), cv2


def _load_request_image(cv2: Any, request: dict[str, Any], connection: socket.socket) -> tuple[Any, float]:
    """从请求中取得 BGR ndarray：优先原始字节帧，兼容旧版 image_path 路径。

    返回 (图像, 读取耗时毫秒)；旧路径读取耗时同样记录。
    """
    shape = request.get("image_shape")
    read_started_at = time.perf_counter()
    if shape is not None:
        if (
            not isinstance(shape, (list, tuple))
            or len(shape) != 3
            or not all(isinstance(v, int) and v > 0 for v in shape)
        ):
            raise OcrProtocolError(f"OCR请求图像尺寸无效：{shape!r}")
        raw = receive_raw_bytes(connection)
        expected = int(shape[0]) * int(shape[1]) * int(shape[2])
        if len(raw) != expected:
            raise OcrProtocolError(f"OCR图像字节数不匹配：期望 {expected}，实际 {len(raw)}")
        # raw 为可写 bytearray，frombuffer 直接得到可写数组，无需再整图复制一份
        image = np.frombuffer(raw, dtype=np.uint8).reshape(int(shape[0]), int(shape[1]), int(shape[2]))
        return image, (time.perf_counter() - read_started_at) * 1000.0

    image_path = str(request.get("image_path", "") or "")
    if image_path:
        return cv2.imread(image_path), (time.perf_counter() - read_started_at) * 1000.0
    raise OcrProtocolError("OCR请求缺少图像数据")


def _run_request(runner: object, cv2: Any, image_bgr: Any, budget_seconds: float) -> dict[str, Any]:
    request_started_at = time.perf_counter()
    timings: dict[str, Any] = {}
    try:
        if image_bgr is None:
            raise RuntimeError("OCR图像数据无效")

        if getattr(runner, "_ocr_engine_name", OCR_ENGINE_RAPIDOCR) == OCR_ENGINE_PPOCRV6:
            text, stderr = runner.recognize(
                image_bgr,
                budget_seconds=float(budget_seconds),
                metrics=timings,
            )
        else:
            text, stderr = runner._ocr_extract_rapidocr_inprocess(
                image_bgr,
                budget_seconds=float(budget_seconds),
                metrics=timings,
            )
        return {
            "text": str(text or ""),
            "stderr": str(stderr or ""),
            "error": "",
            "timings": timings,
        }
    except BaseException as exc:
        return {
            "text": "",
            "stderr": "",
            "error": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            "timings": timings,
        }
    finally:
        timings["worker_request_ms"] = (time.perf_counter() - request_started_at) * 1000.0


def run_ocr_worker(image_path: Path, result_path: Path, budget_seconds: float) -> int:
    """执行一次 OCR，保留为诊断和兼容入口。"""

    try:
        runner, cv2 = _create_runner(OCR_ENGINE_RAPIDOCR)
        image_bgr = cv2.imread(str(image_path))
        payload = _run_request(runner, cv2, image_bgr, budget_seconds)
    except BaseException as exc:
        payload = {
            "text": "",
            "stderr": "",
            "error": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            "timings": {},
        }
    _write_result(result_path, payload)
    return 1 if str(payload.get("error", "") or "").strip() else 0


def serve_ocr_worker(host: str, port: int, token: str, engine: str = OCR_ENGINE_RAPIDOCR) -> int:
    """在隔离子进程中常驻模型，串行处理来自父进程的 OCR 请求。"""

    engine = normalize_ocr_engine(engine)
    worker_started_at = time.perf_counter()
    connection = socket.create_connection((str(host), int(port)), timeout=10.0)
    connection.settimeout(None)
    try:
        send_message(
            connection,
            {
                "version": PROTOCOL_VERSION,
                "type": "hello",
                "token": str(token),
                "pid": os.getpid(),
                "engine": engine,
            },
        )
        try:
            runner, cv2 = _create_runner(engine)
        except BaseException as exc:
            send_message(
                connection,
                {
                    "version": PROTOCOL_VERSION,
                    "type": "ready",
                    "error": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                    "engine": engine,
                },
            )
            return 1

        startup_ms = (time.perf_counter() - worker_started_at) * 1000.0
        send_message(
            connection,
            {
                "version": PROTOCOL_VERSION,
                "type": "ready",
                "error": "",
                "startup_ms": startup_ms,
                "engine": engine,
            },
        )

        while True:
            try:
                request = receive_message(connection)
            except EOFError:
                return 0
            if request.get("version") != PROTOCOL_VERSION:
                raise OcrProtocolError("OCR请求协议版本不兼容")

            request_type = str(request.get("type", ""))
            if request_type == "shutdown":
                return 0
            request_id = str(request.get("id", ""))
            if request_type != "ocr" or not request_id:
                raise OcrProtocolError("OCR请求类型或ID无效")
            try:
                request_engine = normalize_ocr_engine(request.get("engine"))
            except ValueError as exc:
                raise OcrProtocolError("OCR请求引擎无效") from exc
            if request_engine != engine:
                raise OcrProtocolError("OCR请求引擎与工作进程不匹配")

            try:
                budget = float(max(4.0, min(130.0, float(request.get("budget_seconds", 8.0)))))
                image_bgr, image_read_ms = _load_request_image(cv2, request, connection)
            except BaseException as exc:
                payload = {
                    "text": "",
                    "stderr": "",
                    "error": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                    "timings": {},
                }
            else:
                payload = _run_request(runner, cv2, image_bgr, budget)
                payload_timings = payload.get("timings")
                if isinstance(payload_timings, dict):
                    payload_timings["image_read_ms"] = image_read_ms

            timings = payload.get("timings")
            if not isinstance(timings, dict):
                timings = {}
                payload["timings"] = timings
            timings["worker_startup_ms"] = startup_ms
            payload.update(
                {
                    "version": PROTOCOL_VERSION,
                    "type": "result",
                    "id": request_id,
                    "engine": engine,
                }
            )
            send_message(connection, payload)
    except (EOFError, OSError, OcrProtocolError) as exc:
        # 连接断开或协议错误退出；协议错误记录详情，避免客户端只能看到神秘失败
        from deepcat.utils.logger import get_logger

        get_logger("ocr_worker").warning("OCR Worker 退出：%s: %s", type(exc).__name__, exc)
        return 1
    finally:
        try:
            connection.close()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    raw_args = list(argv or [])
    if raw_args and raw_args[0] == "serve":
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", required=True, type=int)
        parser.add_argument("--token", required=True)
        parser.add_argument("--engine", default=OCR_ENGINE_RAPIDOCR)
        args = parser.parse_args(raw_args[1:])
        return serve_ocr_worker(args.host, args.port, args.token, args.engine)

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("image_path")
    parser.add_argument("result_path")
    parser.add_argument("budget_seconds", type=float)
    args = parser.parse_args(raw_args)
    return run_ocr_worker(Path(args.image_path), Path(args.result_path), args.budget_seconds)


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
