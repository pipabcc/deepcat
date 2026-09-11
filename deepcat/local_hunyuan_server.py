from __future__ import annotations

import atexit
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request
from urllib.parse import urlparse


IDLE_SECONDS = 180
API_KEY = "sk-1234"
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 2


@dataclass(frozen=True)
class HunyuanModelSpec:
    alias: str
    model_path: str


MODEL_SPECS: dict[str, HunyuanModelSpec] = {
    "hy-mt15-1.8b-q4_k_m": HunyuanModelSpec(
        alias="hy-mt15-1.8b-q4_k_m",
        model_path=r"models\hy-mt-1.8b\HY-MT1.5-1.8B-Q4_K_M.gguf",
    ),
    "hy-mt2-1.8b-q4_k_m": HunyuanModelSpec(
        alias="hy-mt2-1.8b-q4_k_m",
        model_path=r"models\hy-mt2-1.8b\Hy-MT2-1.8B-Q4_K_M.gguf",
    ),
}


_lock = threading.RLock()
_process: subprocess.Popen[Any] | None = None
_log_handle = None
_current_alias = ""
_active_count = 0
_idle_timer: threading.Timer | None = None


def repo_root() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "tools").exists():
            return exe_dir
        if (exe_dir.parent / "tools").exists():
            return exe_dir.parent
        return exe_dir
    else:
        return Path(__file__).resolve().parent.parent


def is_local_hunyuan_config(cfg: dict[str, object]) -> bool:
    return identify_spec(cfg) is not None


def identify_spec(cfg: dict[str, object]) -> HunyuanModelSpec | None:
    hint = " ".join(
        str(cfg.get(key, "") or "").lower()
        for key in ("display_name", "model_name", "base_url")
    )
    model_name = str(cfg.get("model_name", "") or "").strip().lower()
    if model_name in MODEL_SPECS:
        return MODEL_SPECS[model_name]
    if "mt1.5" in hint or "mt15" in hint or "hy-mt1.5" in hint:
        return MODEL_SPECS["hy-mt15-1.8b-q4_k_m"]
    if "mt2" in hint or "hy-mt2" in hint:
        return MODEL_SPECS["hy-mt2-1.8b-q4_k_m"]
    return None


def ensure_server(cfg: dict[str, object]) -> None:
    global _active_count
    spec = identify_spec(cfg)
    if spec is None:
        return
    base_url = _base_url(cfg)
    api_key = str(cfg.get("api_key", "") or API_KEY)
    with _lock:
        _cancel_idle_timer_locked()
        if _current_alias and _current_alias != spec.alias:
            _active_count = 0
            _stop_server_locked(stop_external=True)
        if _server_has_model(base_url, api_key, spec.alias):
            _mark_active_locked(spec.alias)
            return
        _stop_server_locked(stop_external=True)
        _start_server_locked(spec, base_url, api_key)
        _wait_until_ready(spec, base_url, api_key)
        _mark_active_locked(spec.alias)


def release_server(cfg: dict[str, object]) -> None:
    if identify_spec(cfg) is None:
        return
    with _lock:
        global _active_count
        _active_count = max(0, _active_count - 1)
        if _active_count == 0:
            _schedule_idle_stop_locked()


def stop_server_now() -> None:
    with _lock:
        _cancel_idle_timer_locked()
        _stop_server_locked(stop_external=True)


def _mark_active_locked(alias: str) -> None:
    global _active_count, _current_alias
    _active_count += 1
    _current_alias = alias


def _base_url(cfg: dict[str, object]) -> str:
    base = str(cfg.get("base_url", "") or "http://127.0.0.1:8080").strip().rstrip("/")
    return base or "http://127.0.0.1:8080"


def _host_port(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8080)
    return host, port


def _server_has_model(base_url: str, api_key: str, alias: str) -> bool:
    try:
        req = request.Request(
            f"{base_url}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with request.urlopen(req, timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return False
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return False
    return any(str(item.get("id", "") if isinstance(item, dict) else "") == alias for item in data)


def _rotate_log_if_needed(log_path: Path) -> None:
    # llama-server 持有日志句柄期间无法轮转，只能在每次启动前按大小滚动
    try:
        if not log_path.exists() or log_path.stat().st_size < LOG_MAX_BYTES:
            return
        for i in range(LOG_BACKUP_COUNT - 1, 0, -1):
            src = log_path.with_name(f"{log_path.name}.{i}")
            if src.exists():
                src.replace(log_path.with_name(f"{log_path.name}.{i + 1}"))
        log_path.replace(log_path.with_name(f"{log_path.name}.1"))
    except Exception:
        pass


def _start_server_locked(spec: HunyuanModelSpec, base_url: str, api_key: str) -> None:
    global _process, _log_handle, _current_alias

    # 智能寻找 llama-server.exe 物理路径
    server = None
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        # 1. 尝试从打包后的 _internal 内部查找 (PyInstaller 默认 datas 路径)
        p1 = Path(sys._MEIPASS) / "tools" / "llama.cpp-b9360" / "llama-server.exe"
        # 2. 尝试从 exe 同级目录物理查找
        p2 = exe_dir / "tools" / "llama.cpp-b9360" / "llama-server.exe"
        # 3. 开发编译测试结构：exe 在 dist/deepcat 内，tools 在上一级目录
        p3 = exe_dir.parent / "tools" / "llama.cpp-b9360" / "llama-server.exe"

        for p in (p1, p2, p3):
            if p.exists():
                server = p
                break
    else:
        root = repo_root()
        server = root / "tools" / "llama.cpp-b9360" / "llama-server.exe"

    if server is None or not server.exists():
        # Fallback to general lookup
        root = repo_root()
        server = root / "tools" / "llama.cpp-b9360" / "llama-server.exe"

    # 智能寻找模型物理路径
    model = None
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        p1 = exe_dir / spec.model_path
        p2 = exe_dir.parent / spec.model_path
        for p in (p1, p2):
            if p.exists():
                model = p
                break
    else:
        root = repo_root()
        model = root / spec.model_path

    if model is None or not model.exists():
        root = repo_root()
        model = root / spec.model_path

    if not server.exists():
        raise FileNotFoundError(f"找不到 llama-server.exe：{server}")
    if not model.exists():
        raise FileNotFoundError(f"找不到本地模型文件：{model}")

    # 获取日志和工作目录
    root = repo_root()
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"llama_cpp_server_{spec.alias}.log"
    if _log_handle is not None:
        try:
            _log_handle.close()
        except Exception:
            pass
    _rotate_log_if_needed(log_path)
    _log_handle = log_path.open("a", encoding="utf-8", buffering=1)
    host, port = _host_port(base_url)
    cmd = [
        str(server),
        "--model",
        str(model),
        "--alias",
        spec.alias,
        "--host",
        host,
        "--port",
        str(port),
        "--ctx-size",
        "4096",
        "--threads",
        "4",
        "--threads-batch",
        "4",
        "--parallel",
        "1",
        "--api-key",
        api_key,
        "--temp",
        "0.7",
        "--top-p",
        "0.6",
        "--top-k",
        "20",
        "--repeat-penalty",
        "1.05",
        "--predict",
        "4096",
        "--n-gpu-layers",
        "0",
        "--fit",
        "off",
        "--no-repack",
        "--no-ui",
        "--log-verbosity",
        "2",
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _process = subprocess.Popen(
        cmd,
        cwd=str(root),
        stdout=_log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    _current_alias = spec.alias
    try:
        # 记录 PID 供跨运行定向清理（应用崩溃遗留的 llama-server 也能被精确终止）
        _pid_record_path().write_text(str(int(_process.pid)), encoding="utf-8")
    except Exception:
        pass


def _wait_until_ready(spec: HunyuanModelSpec, base_url: str, api_key: str) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if _process is not None and _process.poll() is not None:
            raise RuntimeError(f"本地腾讯模型服务启动失败：{spec.alias}")
        if _server_has_model(base_url, api_key, spec.alias):
            return
        time.sleep(0.5)
    raise TimeoutError(f"本地腾讯模型服务启动超时：{spec.alias}")


def _schedule_idle_stop_locked() -> None:
    global _idle_timer
    _cancel_idle_timer_locked()
    _idle_timer = threading.Timer(IDLE_SECONDS, _idle_stop)
    _idle_timer.daemon = True
    _idle_timer.start()


def _cancel_idle_timer_locked() -> None:
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None


def _idle_stop() -> None:
    with _lock:
        if _active_count == 0:
            _stop_server_locked(stop_external=True)


_PID_RECORD_NAME = "llama_cpp_server.pid"
_LLAMA_SERVER_IMAGE = "llama-server.exe"


def _pid_record_path() -> Path:
    return repo_root() / "logs" / _PID_RECORD_NAME


def _windows_process_image(pid: int) -> str:
    """查询指定 PID 的进程映像名；查不到返回空串。"""
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=creationflags,
            check=False,
        )
        lines = (result.stdout or "").strip().splitlines()
        if not lines:
            return ""
        return lines[0].split(",")[0].strip().strip('"').lower()
    except Exception:
        return ""


def _kill_llama_server_pid(pid: int) -> None:
    """按 PID 定向终止 llama-server：先核对映像名，避免 PID 被无关进程复用后误杀。"""
    if int(pid) <= 0:
        return
    if _windows_process_image(int(pid)) != _LLAMA_SERVER_IMAGE:
        return
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(int(pid))],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            timeout=10,
            check=False,
        )
    except Exception:
        pass


def _stop_server_locked(*, stop_external: bool) -> None:
    global _process, _current_alias, _log_handle
    if _process is not None and _process.poll() is None:
        try:
            _process.terminate()
            _process.wait(timeout=5)
        except Exception:
            try:
                _process.kill()
            except Exception:
                pass
    _process = None
    if stop_external:
        # 只定向终止 DeepCat 记录过的 llama-server PID（含子进程树）。
        # 旧的 taskkill /IM llama-server.exe 会把用户自己运行的无关同名进程一并杀掉。
        candidates: list[int] = []
        try:
            record = _pid_record_path()
            if record.exists():
                recorded = int(record.read_text(encoding="utf-8").strip() or 0)
                if recorded > 0:
                    candidates.append(recorded)
        except Exception:
            pass
        for pid in dict.fromkeys(candidates):
            _kill_llama_server_pid(pid)
        try:
            _pid_record_path().unlink(missing_ok=True)
        except Exception:
            pass
    _current_alias = ""
    if _log_handle is not None:
        try:
            _log_handle.close()
        except Exception:
            pass
        _log_handle = None


atexit.register(stop_server_now)
