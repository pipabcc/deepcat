import json
import shutil
from pathlib import Path
from typing import Any


CLAUDE_CODE_DIR = Path.home() / ".claude"
SETTINGS_FILE_NAME = "settings.json"
LEGACY_SETTINGS_FILE_NAME = "claude.json"


def claude_code_settings_path() -> Path:
    settings_path = CLAUDE_CODE_DIR / SETTINGS_FILE_NAME
    if settings_path.exists():
        return settings_path
    legacy_path = CLAUDE_CODE_DIR / LEGACY_SETTINGS_FILE_NAME
    if legacy_path.exists():
        return legacy_path
    return settings_path


def _backup_path(path: Path) -> Path:
    return path.with_name(f"deepcat_{path.name}.bak")


def _none_marker_path(path: Path) -> Path:
    return path.with_name(f"deepcat_{path.name}.none")


def _backup_settings_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = _backup_path(path)
    marker_path = _none_marker_path(path)
    if path.exists():
        if not backup_path.exists() and not marker_path.exists():
            shutil.copy2(path, backup_path)
        return
    if not backup_path.exists():
        marker_path.touch(exist_ok=True)


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude Code 配置文件不是合法 JSON，已取消修改：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Claude Code 配置文件根节点必须是 JSON 对象")
    return data


def _ensure_env(settings: dict[str, Any]) -> dict[str, Any]:
    env = settings.get("env")
    if env is None:
        env = {}
        settings["env"] = env
    if not isinstance(env, dict):
        raise ValueError("Claude Code 配置文件中的 env 必须是 JSON 对象")
    return env


def _api_key_field(env: dict[str, Any]) -> str:
    if "ANTHROPIC_AUTH_TOKEN" in env:
        return "ANTHROPIC_AUTH_TOKEN"
    if "ANTHROPIC_API_KEY" in env:
        return "ANTHROPIC_API_KEY"
    return "ANTHROPIC_AUTH_TOKEN"


def apply_claude_code_config(*, base_url: str, model: str, api_key: str) -> Path:
    base_url = str(base_url or "").strip()
    model = str(model or "").strip()
    api_key = str(api_key or "").strip()
    if not base_url:
        raise ValueError("Claude Code API 地址不能为空")
    if not model:
        raise ValueError("Claude Code 模型 ID 不能为空")
    if not api_key:
        raise ValueError("Claude Code API 密钥不能为空")

    path = claude_code_settings_path()
    settings = _load_settings(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup_settings_file(path)
    env = _ensure_env(settings)

    env[_api_key_field(env)] = api_key
    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_MODEL"] = model
    for role in ("HAIKU", "SONNET", "OPUS", "FABLE"):
        env[f"ANTHROPIC_DEFAULT_{role}_MODEL"] = model
        env[f"ANTHROPIC_DEFAULT_{role}_MODEL_NAME"] = model

    path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def restore_claude_code_config() -> Path:
    restored_path: Path | None = None
    for path in (
        CLAUDE_CODE_DIR / SETTINGS_FILE_NAME,
        CLAUDE_CODE_DIR / LEGACY_SETTINGS_FILE_NAME,
    ):
        backup_path = _backup_path(path)
        marker_path = _none_marker_path(path)
        if backup_path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, path)
            backup_path.unlink()
            restored_path = path
        elif marker_path.exists():
            if path.exists():
                path.unlink()
            marker_path.unlink()
            restored_path = path
    if restored_path is None:
        raise FileNotFoundError("未找到 DeepCat 填入前的 Claude Code 配置备份")
    return restored_path
