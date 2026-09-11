from __future__ import annotations

import atexit
import hmac
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

from deepcat.ocr_engines import OCR_ENGINE_RAPIDOCR, normalize_ocr_engine
from deepcat.ocr_ipc import (
    OcrProtocolError,
    PROTOCOL_VERSION,
    receive_message,
    receive_raw_bytes,
    send_message,
    send_raw_bytes,
)
from deepcat.utils.logger import get_logger


logger = get_logger("ocr_worker_client")


class OcrWorkerStartupError(RuntimeError):
    """OCR Worker 已启动，但运行库或模型初始化失败。"""


class OcrRequestCancelled(RuntimeError):
    """OCR 请求已由所属窗口取消。"""


class OcrCancellationToken:
    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()


@dataclass(frozen=True)
class _ConnectionState:
    connection: socket.socket
    process: subprocess.Popen
    generation: int
    reused: bool
    startup_ms: float
    engine: str


def is_bundled_runtime() -> bool:
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def build_ocr_worker_command(port: int, token: str, engine: str = OCR_ENGINE_RAPIDOCR) -> list[str]:
    engine = normalize_ocr_engine(engine)
    worker_args = [
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(int(port)),
        "--engine",
        engine,
        "--token",
        str(token),
    ]
    if is_bundled_runtime():
        return [sys.executable, "--deepcat-ocr-worker", *worker_args]
    return [sys.executable, "-m", "deepcat.ocr_worker", *worker_args]


class OcrWorkerClient:
    """串行管理常驻 OCR 子进程。

    - 成功请求后默认保留 worker 以复用已加载的模型，空闲一段时间后自动回收；
      显式传 release_on_success=True 时立即释放（保留给测试与特殊调用方）。
    - worker 在推理中途崩溃时，同一请求会用新进程自动重试一次。
    """

    IDLE_RELEASE_SECONDS = 120.0

    def __init__(self) -> None:
        self._request_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._teardown_lock = threading.Lock()
        self._idle_lock = threading.Lock()
        self._idle_timer: Optional[threading.Timer] = None
        self._process: Optional[subprocess.Popen] = None
        self._connection: Optional[socket.socket] = None
        self._listener: Optional[socket.socket] = None
        self._cleanup_processes: list[subprocess.Popen] = []
        self._active_cancel_token: Optional[OcrCancellationToken] = None
        self._worker_engine: Optional[str] = None
        self._generation = 0
        self._closed = False
        self._last_activity_monotonic = time.monotonic()

    def recognize(
        self,
        image: Any,
        budget_seconds: float,
        cancel_token: Optional[OcrCancellationToken] = None,
        release_on_success: bool = False,
        engine: str = OCR_ENGINE_RAPIDOCR,
    ) -> dict[str, Any]:
        engine = normalize_ocr_engine(engine)
        budget = float(max(4.0, min(130.0, budget_seconds)))
        with self._request_lock:
            self._last_activity_monotonic = time.monotonic()
            self._raise_if_cancelled(cancel_token)
            with self._state_lock:
                self._active_cancel_token = cancel_token
            released = False
            try:
                self._raise_if_cancelled(cancel_token)
                payload = self._recognize_locked(image, budget, cancel_token, release_on_success, engine)
                released = bool(payload.get("timings", {}).get("worker_released", False))
                return payload
            finally:
                self._last_activity_monotonic = time.monotonic()
                with self._state_lock:
                    if self._active_cancel_token is cancel_token:
                        self._active_cancel_token = None
                if not released and not self._closed:
                    # 常驻模式：成功请求后保留 worker，空闲一段时间后由计时器回收
                    self._arm_idle_release()

    def _arm_idle_release(self) -> None:
        with self._idle_lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
            timer = threading.Timer(self.IDLE_RELEASE_SECONDS, self._release_if_idle)
            timer.daemon = True
            self._idle_timer = timer
            timer.start()

    def _release_if_idle(self) -> None:
        if not self._request_lock.acquire(blocking=False):
            # 有请求进行中（或正在排队）：推迟到下一个空闲周期再检查
            if not self._closed:
                self._arm_idle_release()
            return
        try:
            with self._state_lock:
                if self._closed or self._process is None:
                    return
                generation = self._generation
            idle_seconds = time.monotonic() - self._last_activity_monotonic
            if idle_seconds < self.IDLE_RELEASE_SECONDS:
                self._arm_idle_release()
                return
            logger.info("OCR worker 空闲 %.0f 秒，回收进程", idle_seconds)
            self._discard_generation(generation)
        finally:
            self._request_lock.release()

    def _recognize_locked(
        self,
        image: Any,
        budget: float,
        cancel_token: Optional[OcrCancellationToken],
        release_on_success: bool,
        engine: str,
    ) -> dict[str, Any]:
        raw_image = self._prepare_raw_image(image)
        legacy_image_path: Optional[str] = None
        if raw_image is None and image is not None:
            legacy_image_path = str(Path(image).resolve())
        state: Optional[_ConnectionState] = None
        payload: Optional[dict[str, Any]] = None
        # 推理中途 worker 崩溃（EOF/OSError）时用新进程重试一次；
        # 每次尝试独立计算 deadline，避免启动慢耗尽重试预算。
        for attempt in range(2):
            self._raise_if_cancelled(cancel_token)
            deadline = time.perf_counter() + budget + 8.0
            try:
                state = self._ensure_worker(deadline, engine)
            except OcrWorkerStartupError:
                raise
            except socket.timeout as exc:
                raise subprocess.TimeoutExpired("persistent OCR worker startup", budget + 8.0) from exc
            except (EOFError, OSError, OcrProtocolError) as exc:
                if cancel_token is not None and cancel_token.is_cancelled():
                    raise OcrRequestCancelled("OCR请求已取消") from exc
                if attempt >= 1:
                    raise RuntimeError("OCR常驻工作进程启动失败") from exc
                continue

            try:
                payload = self._exchange_request(state, raw_image, legacy_image_path, budget, engine, deadline)
                break
            except OcrRequestCancelled:
                self._discard_generation(state.generation)
                raise
            except socket.timeout as exc:
                # 超时无法判断 worker 是否仍在推理，不安全复用，直接终止
                self._discard_generation(state.generation)
                raise subprocess.TimeoutExpired("persistent OCR worker", budget + 8.0) from exc
            except (EOFError, OSError, OcrProtocolError) as exc:
                self._discard_generation(state.generation)
                if cancel_token is not None and cancel_token.is_cancelled():
                    raise OcrRequestCancelled("OCR请求已取消") from exc
                if attempt >= 1:
                    raise RuntimeError("OCR常驻工作进程未返回有效结果") from exc
                logger.warning("OCR worker 崩溃（%s: %s），重启后重试", type(exc).__name__, exc)

        if payload is None or state is None:
            raise RuntimeError("OCR常驻工作进程未返回有效结果")

        timings = payload.get("timings")
        if not isinstance(timings, dict):
            timings = {}
        timings["worker_reused"] = bool(state.reused)
        timings["client_startup_ms"] = float(state.startup_ms)
        timings["response_wait_ms"] = float(payload.pop("response_wait_ms", 0.0) or 0.0)
        timings["image_send_ms"] = float(payload.pop("image_send_ms", 0.0) or 0.0)
        should_release = bool(
            release_on_success
            and str(payload.get("text", "") or "").strip()
            and not str(payload.get("error", "") or "").strip()
        )
        if should_release:
            self._discard_generation(state.generation)
        timings["worker_released"] = should_release
        payload["timings"] = timings
        return payload

    @staticmethod
    def _prepare_raw_image(image: Any) -> Optional[tuple[list[int], memoryview]]:
        """ndarray 输入转原始字节帧描述；Path/str 输入走旧版 image_path 协议。"""
        if image is None or isinstance(image, (str, Path)):
            return None
        array = np.asarray(image)
        if array.ndim not in (2, 3) or array.dtype != np.uint8:
            raise ValueError("OCR图像必须为 uint8 的 2D/3D ndarray")
        if array.ndim == 2:
            array = array[:, :, np.newaxis]
        if array.shape[2] not in (1, 3, 4):
            raise ValueError(f"OCR图像通道数无效：{array.shape[2]}")
        contiguous = np.ascontiguousarray(array)
        return [int(v) for v in contiguous.shape], memoryview(contiguous)

    def _exchange_request(
        self,
        state: _ConnectionState,
        raw_image: Optional[tuple[list[int], memoryview]],
        legacy_image_path: Optional[str],
        budget: float,
        engine: str,
        deadline: float,
    ) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        # 客户端总 deadline 包含了进程启动/回连耗时（冷启动可达 10-20s）。
        # worker 只应拿到"从现在起到客户端 deadline"的剩余推理预算，
        # 否则冷启动首请求会在 worker 自身预算尚未耗尽时被客户端提前误杀。
        remaining = float(deadline) - time.perf_counter()
        effective_budget = max(1.0, min(float(budget), remaining - 1.5))
        request: dict[str, Any] = {
            "version": PROTOCOL_VERSION,
            "type": "ocr",
            "id": request_id,
            "budget_seconds": effective_budget,
            "engine": engine,
        }
        self._set_deadline_timeout(state.connection, deadline)
        send_started_at = time.perf_counter()
        if raw_image is None:
            request["image_path"] = str(legacy_image_path or "")
            send_message(state.connection, request)
        else:
            shape, view = raw_image
            request["image_shape"] = shape
            send_message(state.connection, request)
            send_raw_bytes(state.connection, view)
        image_send_ms = (time.perf_counter() - send_started_at) * 1000.0

        response_started_at = time.perf_counter()
        self._set_deadline_timeout(state.connection, deadline)
        payload = receive_message(state.connection, deadline=deadline)
        payload["response_wait_ms"] = (time.perf_counter() - response_started_at) * 1000.0
        payload["image_send_ms"] = image_send_ms

        if str(payload.get("type", "")) == "error":
            raise RuntimeError(str(payload.get("error", "") or "OCR Worker返回错误"))
        if payload.get("version") != PROTOCOL_VERSION or payload.get("type") != "result":
            raise OcrProtocolError("OCR常驻工作进程返回了不兼容的协议消息")
        if str(payload.get("id", "")) != request_id:
            raise OcrProtocolError("OCR常驻工作进程返回了错配的请求结果")
        try:
            response_engine = normalize_ocr_engine(payload.get("engine"))
        except ValueError as exc:
            raise OcrProtocolError("OCR常驻工作进程返回了无效的引擎结果") from exc
        if response_engine != engine:
            raise OcrProtocolError("OCR常驻工作进程返回了错配的引擎结果")
        return payload

    @staticmethod
    def _raise_if_cancelled(cancel_token: Optional[OcrCancellationToken]) -> None:
        if cancel_token is not None and cancel_token.is_cancelled():
            raise OcrRequestCancelled("OCR请求已取消")

    def cancel_request(self, cancel_token: OcrCancellationToken) -> bool:
        cancel_token.cancel()
        with self._state_lock:
            if self._active_cancel_token is not cancel_token:
                return False
            connection = self._connection
            listener = self._listener
        # 只 shutdown 不 close：close 留给持有该 socket 的请求线程，
        # 避免跨线程 close 正被阻塞 recv 的 socket（WinSock 未定义行为）。
        for sock in (connection, listener):
            if sock is None:
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return True

    def shutdown(self) -> None:
        with self._idle_lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
        with self._state_lock:
            if (
                self._closed
                and self._process is None
                and self._connection is None
                and self._listener is None
                and not self._cleanup_processes
            ):
                return
            self._closed = True
            active_cancel_token = self._active_cancel_token
            connection = self._connection
            process = self._process
            listener = self._listener
            cleanup_processes = self._cleanup_processes
            self._connection = None
            self._process = None
            self._listener = None
            self._worker_engine = None
            self._cleanup_processes = []
            self._generation += 1

        if active_cancel_token is not None:
            active_cancel_token.cancel()

        request_lock_acquired = self._request_lock.acquire(blocking=False)
        if request_lock_acquired and connection is not None:
            try:
                connection.settimeout(0.2)
                send_message(
                    connection,
                    {"version": PROTOCOL_VERSION, "type": "shutdown", "id": uuid.uuid4().hex},
                )
            except Exception:
                pass
        if request_lock_acquired:
            self._request_lock.release()
        elif connection is not None:
            # 请求在途：与 cancel_request 一致，只 shutdown 唤醒阻塞中的 recv，
            # 不跨线程 close 正被 recv 的 socket（WinSock 未定义行为）；
            # fd 的最终关闭交给持有它的请求线程在清理路径中完成。
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        leftovers: list[subprocess.Popen] = []
        if request_lock_acquired:
            leftover = self._stop_resources(connection, process, listener)
        else:
            # 进程照常回收；被请求线程持有的 socket 不在此处 close
            leftover = self._stop_resources(None, process, None)
        if leftover is not None:
            leftovers.append(leftover)
        for stale_process in cleanup_processes:
            leftover = self._stop_resources(None, stale_process, None)
            if leftover is not None and all(leftover is not item for item in leftovers):
                leftovers.append(leftover)
        if leftovers:
            with self._state_lock:
                self._cleanup_processes.extend(leftovers)

    def _ensure_worker(self, deadline: float, engine: str) -> _ConnectionState:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("OCR常驻工作进程客户端已关闭")
            process = self._process
            connection = self._connection
            # 默认值仅用于兼容旧连接状态和测试注入；真实 Worker 在 ready 后会显式记录引擎。
            worker_engine = self._worker_engine or OCR_ENGINE_RAPIDOCR
            generation = self._generation
            if (
                process is not None
                and connection is not None
                and process.poll() is None
                and worker_engine == engine
            ):
                return _ConnectionState(connection, process, generation, True, 0.0, engine)

        self._discard_generation(generation)
        return self._start_worker(deadline, engine)

    def _start_worker(self, deadline: float, engine: str) -> _ConnectionState:
        self._retry_stale_cleanup()
        startup_started_at = time.perf_counter()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        port = int(listener.getsockname()[1])
        token = secrets.token_hex(32)

        with self._state_lock:
            if self._closed:
                listener.close()
                raise RuntimeError("OCR常驻工作进程客户端已关闭")
            self._generation += 1
            generation = self._generation
            self._listener = listener

        process: Optional[subprocess.Popen] = None
        connection: Optional[socket.socket] = None
        try:
            # 源码运行时确保 worker 能以 -m 方式导入 deepcat 包（不依赖启动时的 cwd/PYTHONPATH）
            popen_env: Optional[dict[str, str]] = None
            if not is_bundled_runtime():
                package_root = str(Path(__file__).resolve().parents[1])
                existing = os.environ.get("PYTHONPATH", "")
                parts = [part for part in existing.split(os.pathsep) if part]
                if package_root not in parts:
                    parts.insert(0, package_root)
                popen_env = dict(os.environ, PYTHONPATH=os.pathsep.join(parts))
            process = subprocess.Popen(
                build_ocr_worker_command(port, token, engine),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
                close_fds=True,
                env=popen_env,
            )
            with self._state_lock:
                if self._closed or self._generation != generation:
                    raise RuntimeError("OCR常驻工作进程启动已取消")
                self._process = process

            connection = self._accept_authenticated(listener, token, deadline, process, engine)
            self._set_deadline_timeout(connection, deadline)
            ready = receive_message(connection, deadline=deadline)
            if ready.get("version") != PROTOCOL_VERSION or ready.get("type") != "ready":
                raise OcrProtocolError("OCR Worker未返回就绪消息")
            if normalize_ocr_engine(ready.get("engine")) != engine:
                raise OcrProtocolError("OCR Worker返回了错配的引擎")
            startup_error = str(ready.get("error", "") or "").strip()
            if startup_error:
                raise OcrWorkerStartupError(startup_error)

            with self._state_lock:
                if self._closed or self._generation != generation:
                    raise RuntimeError("OCR常驻工作进程启动已取消")
                self._listener = None
                self._connection = connection
                self._worker_engine = engine
            listener.close()
            return _ConnectionState(
                connection,
                process,
                generation,
                False,
                (time.perf_counter() - startup_started_at) * 1000.0,
                engine,
            )
        except Exception:
            self._discard_generation(generation)
            leftover = self._stop_resources(connection, process, listener)
            if leftover is not None:
                self._remember_unreaped(leftover)
            raise

    def _accept_authenticated(
        self,
        listener: socket.socket,
        token: str,
        deadline: float,
        process: subprocess.Popen,
        expected_engine: str = OCR_ENGINE_RAPIDOCR,
    ) -> socket.socket:
        while True:
            remaining = float(deadline) - time.perf_counter()
            if remaining <= 0:
                raise socket.timeout("OCR Worker回连超时")
            listener.settimeout(min(0.1, remaining))
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                return_code = process.poll()
                if return_code is not None:
                    raise OcrWorkerStartupError(f"OCR Worker在回连前退出（退出码 {return_code}）")
                continue
            try:
                self._set_deadline_timeout(connection, deadline)
                hello = receive_message(connection, deadline=deadline)
                token_matches = hmac.compare_digest(str(hello.get("token", "")), token)
                if (
                    hello.get("version") == PROTOCOL_VERSION
                    and hello.get("type") == "hello"
                    and token_matches
                    and normalize_ocr_engine(hello.get("engine")) == expected_engine
                ):
                    return connection
            except (EOFError, OSError, OcrProtocolError, ValueError):
                pass
            connection.close()

    @staticmethod
    def _set_deadline_timeout(connection: socket.socket, deadline: float) -> None:
        remaining = float(deadline) - time.perf_counter()
        if remaining <= 0:
            raise socket.timeout("OCR Worker操作超时")
        connection.settimeout(remaining)

    def _discard_generation(self, generation: int) -> None:
        with self._state_lock:
            if generation != self._generation:
                return
            connection = self._connection
            process = self._process
            listener = self._listener
            self._connection = None
            self._process = None
            self._listener = None
            self._worker_engine = None
            self._generation += 1
        leftover = self._stop_resources(connection, process, listener)
        if leftover is not None:
            self._remember_unreaped(leftover)

    def _retry_stale_cleanup(self) -> None:
        with self._state_lock:
            stale_processes = self._cleanup_processes
            self._cleanup_processes = []
        leftovers: list[subprocess.Popen] = []
        for process in stale_processes:
            leftover = self._stop_resources(None, process, None)
            if leftover is not None:
                leftovers.append(leftover)
        if leftovers:
            with self._state_lock:
                self._cleanup_processes.extend(leftovers)

    def _remember_unreaped(self, process: subprocess.Popen) -> None:
        with self._state_lock:
            if all(process is not item for item in self._cleanup_processes):
                self._cleanup_processes.append(process)

    def _stop_resources(
        self,
        connection: Optional[socket.socket],
        process: Optional[subprocess.Popen],
        listener: Optional[socket.socket],
    ) -> Optional[subprocess.Popen]:
        # startup、timeout 和 shutdown 可能同时发现同一代资源失效，
        # 串行回收可避免对同一个 Popen 并发 terminate/kill/wait。
        with self._teardown_lock:
            for sock in (connection, listener):
                if sock is not None:
                    try:
                        # 先 shutdown 唤醒可能仍阻塞在对端 recv 的线程，再 close
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    try:
                        sock.close()
                    except OSError:
                        pass
            if process is None or process.poll() is not None:
                return None
            try:
                process.terminate()
                process.wait(timeout=0.75)
            except Exception:
                try:
                    process.kill()
                    process.wait(timeout=0.5)
                except Exception:
                    pass
            try:
                return process if process.poll() is None else None
            except Exception:
                return process


_default_client_lock = threading.Lock()
_default_client: Optional[OcrWorkerClient] = None


def get_ocr_worker_client() -> OcrWorkerClient:
    global _default_client
    with _default_client_lock:
        if _default_client is None:
            _default_client = OcrWorkerClient()
        return _default_client


def shutdown_ocr_worker() -> None:
    with _default_client_lock:
        client = _default_client
    if client is not None:
        client.shutdown()


atexit.register(shutdown_ocr_worker)
