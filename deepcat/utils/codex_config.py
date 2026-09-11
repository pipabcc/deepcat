import csv
import io
import json
import os
import shutil
import subprocess
import time
import tomllib
import uuid
from pathlib import Path

from deepcat.utils.secret_store import is_protected, protect_text, unprotect_text

CODEX_DIR = Path.home() / ".codex"
CONFIG_PATH = CODEX_DIR / "config.toml"
AUTH_PATH = CODEX_DIR / "auth.json"

BAK_CONFIG_PATH = CODEX_DIR / "deepcat_config.toml.bak"
BAK_AUTH_PATH = CODEX_DIR / "deepcat_auth.json.bak"
NONE_CONFIG_MARKER = CODEX_DIR / "deepcat_config.toml.none"
NONE_AUTH_MARKER = CODEX_DIR / "deepcat_auth.json.none"

DEFAULT_CODEX_API_KEY = "sk-deepcat-local"
DEFAULT_CODEX_MODEL = "deepcat-translate"
DEFAULT_CODEX_BASE_URL = "http://127.0.0.1:11888/v1"

PROVIDER_SECTION_HEADER = "[model_providers.codex_local_access]"
CHATGPT_PROCESS_NAMES = {"chatgpt", "chatgpt.exe"}
LEGACY_CODEX_PROCESS_NAMES = {"codex", "codex.exe"}

def _toml_string(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _target_section(base_url: str) -> str:
    return "\n".join(
        [
            "[model_providers.codex_local_access]",
            'name = "codex_local_access"',
            f"base_url = {_toml_string(base_url)}",
            'wire_api = "responses"',
            "requires_openai_auth = true",
            "supports_websockets = false",
        ]
    )


def _is_section_header(line: str) -> bool:
    trimmed = line.strip()
    return trimmed.startswith("[") and trimmed.endswith("]")


def _is_codex_client_process_name(name: str) -> bool:
    normalized = Path(str(name or "").strip()).name.casefold()
    return normalized in CHATGPT_PROCESS_NAMES | LEGACY_CODEX_PROCESS_NAMES


def _subprocess_no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _version_tuple(path: Path) -> tuple[int, ...] | None:
    name = path.name
    package_prefix = next(
        (prefix for prefix in ("OpenAI.Codex_", "OpenAI.ChatGPT_") if name.startswith(prefix)),
        None,
    )
    if package_prefix is None:
        return None
    version = name.removeprefix(package_prefix).split("_", 1)[0]
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def _windows_app_package_roots() -> list[Path]:
    roots: list[Path] = []
    for key in ("ProgramFiles", "ProgramW6432"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value) / "WindowsApps")
    roots.append(Path(r"C:\Program Files\WindowsApps"))
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _find_latest_codex_app_dir_from_roots(roots: list[Path]) -> Path | None:
    chatgpt_matches: list[tuple[tuple[int, ...], Path]] = []
    codex_matches: list[tuple[tuple[int, ...], Path]] = []
    for root in roots:
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            name = child.name
            if name.startswith("OpenAI.ChatGPT_"):
                version = _version_tuple(child)
                if version is not None:
                    app_dir = child / "app"
                    chatgpt_matches.append((version, app_dir if app_dir.is_dir() else child))
            elif name.startswith("OpenAI.Codex_"):
                version = _version_tuple(child)
                if version is not None:
                    app_dir = child / "app"
                    codex_matches.append((version, app_dir if app_dir.is_dir() else child))
    if chatgpt_matches:
        chatgpt_matches.sort(key=lambda item: item[0])
        return chatgpt_matches[-1][1]
    if codex_matches:
        codex_matches.sort(key=lambda item: item[0])
        return codex_matches[-1][1]
    return None


def _codex_app_dir_from_executable(executable: str | None) -> Path | None:
    if not executable:
        return None
    path = Path(executable)
    for parent in path.parents:
        if parent.name.casefold() == "app":
            return parent
        if parent.name.startswith(("OpenAI.Codex_", "OpenAI.ChatGPT_")):
            app_dir = parent / "app"
            return app_dir if app_dir.is_dir() else parent
    return None


def _resolve_codex_app_dir() -> Path | None:
    executable = shutil.which("codex.exe") or shutil.which("codex")
    return _codex_app_dir_from_executable(executable) or _find_latest_codex_app_dir_from_roots(
        _windows_app_package_roots()
    )


def _codex_app_user_model_id_from_app_dir(app_dir: Path) -> str | None:
    for part in reversed(app_dir.parts):
        if part.startswith(("OpenAI.Codex_", "OpenAI.ChatGPT_")) and "__" in part:
            identity_name = part.split("_", 1)[0]
            publisher_id = part.rsplit("__", 1)[1]
            if identity_name and publisher_id:
                return f"{identity_name}_{publisher_id}!App"
    return None


def _codex_app_user_model_id() -> str | None:
    app_dir = _resolve_codex_app_dir()
    if app_dir is None:
        return None
    return _codex_app_user_model_id_from_app_dir(app_dir)


def _guid(value: str):
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def _activate_windows_packaged_app(app_user_model_id: str, arguments: str = "") -> int:
    import ctypes
    from ctypes import wintypes

    ole32 = ctypes.OleDLL("ole32")
    clsid_application_activation_manager = _guid("45BA127D-10A8-46EA-8AB7-56EA9078943C")
    iid_application_activation_manager = _guid("2e941141-7f97-4756-ba1d-9decde894a3d")
    clsctx_local_server = 0x4
    coinit_apartmentthreaded = 0x2
    rpc_e_changed_mode = -2147417850

    ole32.CoInitializeEx.argtypes = [wintypes.LPVOID, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    coinit_result = ole32.CoInitializeEx(None, coinit_apartmentthreaded)
    should_uninitialize = coinit_result >= 0
    if coinit_result < 0 and coinit_result != rpc_e_changed_mode:
        raise OSError(f"CoInitializeEx failed: 0x{coinit_result & 0xFFFFFFFF:08X}")

    manager = ctypes.c_void_p()
    try:
        ole32.CoCreateInstance.argtypes = [
            ctypes.c_void_p,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.c_long
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid_application_activation_manager),
            None,
            clsctx_local_server,
            ctypes.byref(iid_application_activation_manager),
            ctypes.byref(manager),
        )
        if hr < 0:
            raise OSError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08X}")

        vtable = ctypes.cast(manager, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        activate_application = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )(vtable[3])
        process_id = wintypes.DWORD(0)
        hr = activate_application(
            manager,
            str(app_user_model_id),
            str(arguments or ""),
            0,
            ctypes.byref(process_id),
        )
        if hr < 0:
            raise OSError(f"ActivateApplication failed: 0x{hr & 0xFFFFFFFF:08X}")
        return int(process_id.value)
    finally:
        if manager.value:
            release = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(
                ctypes.cast(manager, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents[2]
            )
            release(manager)
        if should_uninitialize:
            ole32.CoUninitialize()


def _activate_windows_packaged_app_via_shell(app_user_model_id: str) -> None:
    subprocess.Popen(
        ["explorer.exe", f"shell:AppsFolder\\{app_user_model_id}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=_subprocess_no_window_flags(),
    )


def _activate_codex_packaged_app() -> bool:
    if os.name != "nt":
        return False
    app_user_model_id = _codex_app_user_model_id()
    if not app_user_model_id:
        return False
    try:
        _activate_windows_packaged_app(app_user_model_id)
    except Exception:
        _activate_windows_packaged_app_via_shell(app_user_model_id)
    return True


def _windows_tasklist_process_names() -> list[str]:
    result = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=5,
        check=False,
        creationflags=_subprocess_no_window_flags(),
    )
    if result.returncode not in {0, 1}:
        return []
    names: list[str] = []
    for row in csv.reader(io.StringIO(result.stdout or "")):
        if row:
            names.append(str(row[0] or ""))
    return names


def _posix_process_names() -> list[str]:
    result = subprocess.run(
        ["ps", "-A", "-o", "comm="],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in str(result.stdout or "").splitlines()]


def list_codex_client_process_names() -> list[str]:
    """返回桌面客户端进程名，排除 codex-command-runner 等辅助进程。"""
    names = _windows_tasklist_process_names() if os.name == "nt" else _posix_process_names()
    matched: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not _is_codex_client_process_name(name):
            continue
        normalized = Path(str(name or "").strip()).name.casefold()
        if normalized not in seen:
            seen.add(normalized)
            matched.append(name)
    return matched


def has_codex_client_process() -> bool:
    return bool(list_codex_client_process_names())


def launch_codex_client() -> None:
    try:
        if _activate_codex_packaged_app():
            return
    except Exception:
        pass
    executable = (
        shutil.which("ChatGPT.exe")
        or shutil.which("chatgpt")
        or shutil.which("codex.exe")
        or shutil.which("codex")
        or "ChatGPT.exe"
    )
    subprocess.Popen(
        [executable],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=_subprocess_no_window_flags(),
    )


def restart_codex_client() -> None:
    stop_codex_client()
    time.sleep(0.5)
    launch_codex_client()


def stop_codex_client() -> None:
    if os.name == "nt":
        running_names = list_codex_client_process_names()
        normalized_names = {Path(name).name.casefold() for name in running_names}

        targets = []
        if normalized_names & CHATGPT_PROCESS_NAMES:
            targets = ["ChatGPT.exe"]
        elif normalized_names & LEGACY_CODEX_PROCESS_NAMES:
            targets = ["codex.exe"]

        if not targets:
            return

        for target in targets:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/IM", target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                timeout=8,
                check=False,
                creationflags=_subprocess_no_window_flags(),
            )
            if result.returncode not in {0, 128}:
                raise RuntimeError(f"关闭 {target} 失败，taskkill 返回代码 {result.returncode}")

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            remaining = {
                Path(name).name.casefold()
                for name in list_codex_client_process_names()
            }
            if not remaining.intersection(CHATGPT_PROCESS_NAMES | LEGACY_CODEX_PROCESS_NAMES):
                return
            time.sleep(0.1)
        raise TimeoutError("等待 ChatGPT/Codex 客户端退出超时")
    else:
        subprocess.run(
            ["pkill", "-f", "codex"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.deepcat-{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _file_snapshot(path: Path) -> tuple[bool, bytes]:
    return (True, path.read_bytes()) if path.exists() else (False, b"")


def _restore_file_snapshot(path: Path, snapshot: tuple[bool, bytes]) -> None:
    existed, content = snapshot
    if existed:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.deepcat-rollback-{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_bytes(content)
            os.replace(temp_path, path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    else:
        path.unlink(missing_ok=True)


def update_toml_content(
    content: str,
    *,
    base_url: str = DEFAULT_CODEX_BASE_URL,
    model: str = DEFAULT_CODEX_MODEL,
) -> str:
    """只增改属于 deepcat 的配置：顶层四个键 + [model_providers.codex_local_access] 节。

    用户已有的其他 provider、MCP server、profile 等节原样保留。
    结果经 tomllib 校验，避免写出非法 TOML。
    """
    if content.strip():
        try:
            tomllib.loads(content)
        except Exception as e:
            raise ValueError(f"~/.codex/config.toml 不是合法的 TOML，已取消修改：{e}") from e

    lines = content.splitlines()

    # 1. 区分顶层和各个 section
    first_section_idx = len(lines)
    for idx, line in enumerate(lines):
        if _is_section_header(line):
            first_section_idx = idx
            break

    top_lines = lines[:first_section_idx]
    rest_lines = lines[first_section_idx:]

    # 过滤顶层中将被覆盖的键
    keys_to_remove = {"model_provider", "model", "model_reasoning_effort", "disable_response_storage"}
    new_top_lines = []
    for line in top_lines:
        trimmed = line.strip()
        is_target_key = False
        if "=" in trimmed:
            key = trimmed.split("=", 1)[0].strip()
            is_target_key = key in keys_to_remove
        if not is_target_key:
            new_top_lines.append(line)

    inserted_top = [
        'model_provider = "codex_local_access"',
        f"model = {_toml_string(model)}",
        'model_reasoning_effort = "xhigh"',
        'disable_response_storage = true',
    ]
    new_top_lines = inserted_top + new_top_lines

    # 2. 仅移除已存在的 codex_local_access 节（节头到下一个节头之间），其余节不动
    cleaned_rest: list[str] = []
    skipping = False
    for line in rest_lines:
        if _is_section_header(line):
            skipping = line.strip() == PROVIDER_SECTION_HEADER
            if skipping:
                continue
        if not skipping:
            cleaned_rest.append(line)

    rest_content = "\n".join(cleaned_rest).strip("\n")
    if rest_content:
        rest_content = rest_content + "\n\n" + _target_section(base_url)
    else:
        rest_content = _target_section(base_url)

    top_content = "\n".join(new_top_lines).strip("\n")
    final_content = top_content + "\n\n" + rest_content + "\n"

    try:
        tomllib.loads(final_content)
    except Exception as e:
        raise ValueError(f"生成的 config.toml 校验失败，已取消修改：{e}") from e
    return final_content


def _backup_auth_file() -> None:
    # 备份内容含用户原 OPENAI_API_KEY，用 DPAPI 加密后落盘，不留明文副本
    if BAK_AUTH_PATH.exists() or NONE_AUTH_MARKER.exists():
        return
    original = AUTH_PATH.read_text(encoding="utf-8")
    BAK_AUTH_PATH.write_text(protect_text(original), encoding="utf-8")


def apply_codex_config(
    enabled: bool,
    *,
    base_url: str = DEFAULT_CODEX_BASE_URL,
    model: str = DEFAULT_CODEX_MODEL,
    api_key: str = DEFAULT_CODEX_API_KEY,
) -> None:
    tracked_paths = (
        CONFIG_PATH,
        AUTH_PATH,
        BAK_CONFIG_PATH,
        BAK_AUTH_PATH,
        NONE_CONFIG_MARKER,
        NONE_AUTH_MARKER,
    )
    snapshots = {path: _file_snapshot(path) for path in tracked_paths}

    try:
        if enabled:
            base_url = str(base_url or "").strip()
            model = str(model or "").strip()
            api_key = str(api_key or "").strip()
            if not base_url:
                raise ValueError("Codex API 地址不能为空")
            if not model:
                raise ValueError("Codex 模型 ID 不能为空")
            if not api_key:
                raise ValueError("Codex API 密钥不能为空")

            CODEX_DIR.mkdir(parents=True, exist_ok=True)
            if CONFIG_PATH.exists():
                old_content = CONFIG_PATH.read_text(encoding="utf-8")
                new_content = update_toml_content(old_content, base_url=base_url, model=model)
                if not BAK_CONFIG_PATH.exists() and not NONE_CONFIG_MARKER.exists():
                    shutil.copy2(CONFIG_PATH, BAK_CONFIG_PATH)
            else:
                new_content = update_toml_content("", base_url=base_url, model=model)
                if not BAK_CONFIG_PATH.exists():
                    NONE_CONFIG_MARKER.touch(exist_ok=True)

            auth_content = json.dumps(
                {"OPENAI_API_KEY": api_key},
                ensure_ascii=False,
                indent=2,
            )
            if AUTH_PATH.exists():
                _backup_auth_file()
            elif not BAK_AUTH_PATH.exists():
                NONE_AUTH_MARKER.touch(exist_ok=True)

            _atomic_write_text(CONFIG_PATH, new_content)
            _atomic_write_text(AUTH_PATH, auth_content)
            return

        if BAK_CONFIG_PATH.exists():
            _atomic_write_text(CONFIG_PATH, BAK_CONFIG_PATH.read_text(encoding="utf-8"))
            BAK_CONFIG_PATH.unlink()
        elif NONE_CONFIG_MARKER.exists():
            CONFIG_PATH.unlink(missing_ok=True)
            NONE_CONFIG_MARKER.unlink()

        if BAK_AUTH_PATH.exists():
            raw = BAK_AUTH_PATH.read_text(encoding="utf-8")
            restored = unprotect_text(raw) if is_protected(raw) else raw
            if not restored:
                raise ValueError("Codex 认证配置备份无法解密，已取消还原")
            _atomic_write_text(AUTH_PATH, restored)
            BAK_AUTH_PATH.unlink()
        elif NONE_AUTH_MARKER.exists():
            AUTH_PATH.unlink(missing_ok=True)
            NONE_AUTH_MARKER.unlink()
    except Exception:
        for path, snapshot in snapshots.items():
            try:
                _restore_file_snapshot(path, snapshot)
            except OSError:
                pass
        raise


def _get_target_process_pids(target_process_names: set[str]) -> set[int]:
    import csv
    import io
    from pathlib import Path

    result = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=5,
        check=False,
        creationflags=_subprocess_no_window_flags() if os.name == "nt" else 0,
    )
    if result.returncode not in {0, 1}:
        return set()
    pids = set()
    target_names_lower = {name.casefold() for name in target_process_names}
    for row in csv.reader(io.StringIO(result.stdout or "")):
        if len(row) >= 2:
            name = Path(str(row[0] or "").strip()).name.casefold()
            if name in target_names_lower:
                try:
                    pids.add(int(row[1]))
                except ValueError:
                    pass
    return pids


def _has_visible_window_for_pids(pids: set[int]) -> bool:
    if not pids:
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = False

    def enum_windows_callback(hwnd, lparam):
        nonlocal found
        if not user32.IsWindowVisible(hwnd):
            return True

        lpdw_process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(lpdw_process_id))
        pid = int(lpdw_process_id.value)

        if pid in pids:
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                rect = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    width = rect.right - rect.left
                    height = rect.bottom - rect.top
                    if width > 100 and height > 100:
                        found = True
                        return False
        return True

    user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)
    return found


def check_client_window_loaded(client_name: str) -> bool:
    """检查客户端界面是否已加载成功。"""
    if os.name != "nt":
        return has_codex_client_process()

    running_names = list_codex_client_process_names()
    normalized_names = {Path(name).name.casefold() for name in running_names}

    targets = set()
    if str(client_name or "").casefold() == "chatgpt":
        targets = CHATGPT_PROCESS_NAMES
    else:
        targets = LEGACY_CODEX_PROCESS_NAMES

    if not (normalized_names & targets):
        return False

    try:
        pids = _get_target_process_pids(targets)
        return _has_visible_window_for_pids(pids)
    except Exception:
        return True
