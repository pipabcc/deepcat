"""翻译模型目录与配置身份的纯逻辑。"""

from __future__ import annotations

from typing import Any

from deepcat.settings_store import (
    infer_translator_model_provider,
    normalize_translator_provider,
    normalize_translator_settings,
)
from deepcat.ui.settings_dialog._shared import QA_EXCLUDED_TRANSLATOR_MODEL_NAMES


def models_by_provider(translator_obj: object, role: str = "") -> dict[str, list[str]]:
    translator = translator_obj if isinstance(translator_obj, dict) else normalize_translator_settings(translator_obj)
    normalized_role = "qa" if str(role or "").strip().lower() == "qa" else "translate"
    configs = dict(translator.get("model_configs") or {})
    grouped: dict[str, list[str]] = {}
    for raw_name, raw_cfg in configs.items():
        model_name = str(raw_name or "").strip()
        if not model_name:
            continue
        if normalized_role == "qa" and model_name in QA_EXCLUDED_TRANSLATOR_MODEL_NAMES:
            continue
        cfg = dict(raw_cfg or {}) if isinstance(raw_cfg, dict) else {}
        provider = normalize_translator_provider(
            cfg.get("provider"), infer_translator_model_provider(model_name, cfg)
        )
        grouped.setdefault(provider, []).append(model_name)
    return {provider: sorted(names) for provider, names in grouped.items() if names}


def normalize_api_url(value: object) -> str:
    return str(value or "").strip().rstrip("/")


def model_config_identity(
    note_name: str = "",
    cfg: dict[str, Any] | None = None,
    *,
    base_url: object | None = None,
    model_id: object | None = None,
    api_key: object | None = None,
    provider: object | None = None,
) -> dict[str, str]:
    cfg_dict = dict(cfg or {}) if isinstance(cfg, dict) else {}
    note_text = str(note_name or "").strip()
    provider_value = cfg_dict.get("provider") if provider is None else provider
    base_url_value = cfg_dict.get("base_url") if base_url is None else base_url
    model_id_value = cfg_dict.get("model_name") if model_id is None else model_id
    api_key_value = cfg_dict.get("api_key") if api_key is None else api_key
    api_key_text = str(api_key_value or "").strip()
    return {
        "provider": str(provider_value or "").strip(),
        "base_url": normalize_api_url(base_url_value),
        "model_id": str(model_id_value or note_text).strip(),
        "api_key": api_key_text,
        "api_key_state": "set" if api_key_text else "empty",
    }


def same_model_config_scope(left: dict[str, str], right: dict[str, str]) -> bool:
    if not left.get("model_id") or left.get("model_id") != right.get("model_id"):
        return False
    if not left.get("base_url") or left.get("base_url") != right.get("base_url"):
        return False
    left_provider = str(left.get("provider") or "").strip()
    right_provider = str(right.get("provider") or "").strip()
    return not (left_provider and right_provider and left_provider != right_provider)


def unique_model_note_name(base_name: str, existing_names, *, current_name: str = "") -> str:
    normalized_base = str(base_name or "").strip()
    if not normalized_base:
        return ""
    normalized_current = str(current_name or "").strip()
    existing = {str(name).strip() for name in (existing_names or []) if str(name).strip()}
    if normalized_current:
        existing.discard(normalized_current)
    if normalized_base not in existing:
        return normalized_base

    def suffix_from_index(index: int) -> str:
        chars: list[str] = []
        while index >= 0:
            index, remainder = divmod(index, 26)
            chars.append(chr(ord("A") + remainder))
            index -= 1
        return "".join(reversed(chars))

    index = 0
    while True:
        candidate = f"{normalized_base}-{suffix_from_index(index)}"
        if candidate not in existing:
            return candidate
        index += 1
