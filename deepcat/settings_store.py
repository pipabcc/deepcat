from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, replace as dataclass_replace
from pathlib import Path
from typing import Any, Callable, Optional
import re
from urllib.parse import urlparse

from deepcat.utils.log_settings import DEFAULT_LOG_SETTINGS, normalize_log_settings
from deepcat.utils.logger import get_logger
from deepcat.utils.paths import get_app_dir, get_files_dir
from deepcat.utils.secret_store import protect_text, unprotect_text


logger = get_logger("deepcat.settings_store")


SETTINGS_VERSION = 6
DEFAULT_HOTKEY = "<f1>"
DEFAULT_SCROLL_HOTKEY = "<f2>"
DEFAULT_LATER_READ_HOTKEY = "<f3>"
DEFAULT_AI_QA_HOTKEY = "<alt>+<space>"
DEFAULT_SELECTION_TRANSLATE_HOTKEY = "<ctrl>+<space>"
DEFAULT_SELECTION_POPUP_HOTKEY = "<ctrl>+b"
LEGACY_DEFAULT_HOTKEY = "<ctrl>+<shift>+s"
DEEPLX_INFO_ADDRESS = "L站用户专属访问https://linux.do/t/topic/111737查看说明"
DEEPLX_OLD_DEFAULT_BASE_URL = "http://127.0.0.1:1188"
DEFAULT_AUTO_BACKUP_INTERVAL_MINUTES = 24 * 60
COPY_ON_CAPTURE_MODE_OFF = "off"
COPY_ON_CAPTURE_MODE_COPY_CLOSE = "copy_close"
COPY_ON_CAPTURE_MODE_COPY_KEEP = "copy_keep"
DEFAULT_COPY_ON_CAPTURE_MODE = COPY_ON_CAPTURE_MODE_OFF
NETWORK_PROBE_MODE_HTTP_204 = "http_204"
NETWORK_PROBE_MODE_TCP_CONNECT = "tcp_connect"
TRANSLATOR_MODEL_ALIASES = {
    "免费微软翻译": "微软翻译",
    "gemini 3.6 flash": "gemini-3.6-flash",
    "gemini 3.7 flash": "gemini-3.7-flash",
    "gemini 3.8 flash": "gemini-3.8-flash",
}

DEFAULT_ANNOTATION_STYLE = {
    "selection_border_color": "#2196F3",
    "arrow_color": "#FF0000",
    "pen_color": "#FF6600",
    "marker_color": "#FFF9C4",
    "number_color": "#FF0000",
    "text_color": "#FF0000",
    "rect_color": "#00BFA5",
    "line_style": "solid",
}

DEFAULT_CAT_REMINDER = {
    "enabled": False,
    "interval_minutes": 45,
    "duration_seconds": 20,
    "voice_enabled": False,
    "exit_enabled": True,
    "pre_notify_enabled": False,
    "pre_notify_seconds": 5,
    "message": "凡是过往，皆为序章；凡是未来，皆有可期。",
}

DEFAULT_TODO_ITEMS: list[dict[str, Any]] = []
DEFAULT_LATER_READ = {
    "enabled": True,
    "filter_keywords": ["纯水", "人工智能", "tag", "快问快答", "分钟", "个人资料"],
    "items": [],
}

DEFAULT_CLIPBOARD_HISTORY = {
    "monitor_enabled": True,
    "last_content_hash": "",
    "filter_keywords": [],
    "auto_cleanup_enabled": True,
    "retention_days": 90,
    "max_records": 10000,
    "max_capacity_mb": 1024,
}

DEFAULT_EXPLAIN_PROMPT = """请对下面划词内容进行系统性解释。

## 解释目标
- 用一句话先给出核心定义，避免绕圈。
- 拆解它的底层逻辑、关键要素和成立条件。
- 用一个生活化类比帮助建立直觉。
- 给出 2 到 3 个典型场景示例。
- 列出常见误区，并给出纠正。
- 补充相关概念、上位概念、下位概念和容易混淆的概念。

## 输出风格
- 用中文回答，语言简洁准确，讲人话。
- 层次清晰，重点词加粗。
- 不要空泛套话，不要自称 AI。

划词内容：
[划词内容]"""

DEFAULT_SUMMARY_PROMPT = """请对下面划词内容做高质量总结。

## 总结目标
- 先用 1 句话概括核心观点。
- 提炼 3 到 6 条关键要点，按重要性排序。
- 保留必要的事实、数字、条件和结论。
- 如果内容包含步骤、因果或对比，请整理成清晰结构。
- 最后给出一句“可直接记住的结论”。

## 输出风格
- 用中文回答，简洁、准确、信息密度高。
- 不添加原文没有的事实。
- 不要空泛套话，不要自称 AI。

划词内容：
[划词内容]"""

DEFAULT_REPLY_PROMPT = """你是一个真实的人，正在用手机快速回复消息。

根据下面的内容，写一条自然的回复：

【内容】
[划词内容]

回复规则：

像真人发消息，不是机器人客服
口语化，可以用"哈""呀""嗯""好的""没问题"这类日常用词
简短有温度，20～50字，不废话
直接输出回复，不加引号，不做任何解释
几个反面示例（不要这样写）：

❌ "您好，感谢您的反馈，我们会认真对待……"
❌ "非常感谢您的分享，这对我很有帮助……"
❌ 任何听起来像模板、公告、客服话术的句子
正面风格参考：

✅ 好嘞，我待会儿看看，有问题再找你
✅ 哈哈这个我也不确定，你问问别人？
✅ 行，明白了，我这边处理一下
现在，根据上面【内容】写回复：。"""

DEFAULT_FEATURE_VISIBILITY = {
    "todo": True,
    "later_read": True,
    "clipboard_history": True,
    "table_notes": True,
}

DEFAULT_UPDATER = {
    "auto_update_enabled": False,
    "last_check_at": "",
    "last_downloaded_version": "",
}

DEFAULT_OPTIMIZE_PROMPT = """请把下面的划词内容优化成更清晰、可执行、适合直接交给 AI 使用的提示词。

## 优化目标
- 保留原意，不编造额外背景。
- 明确任务目标、输入信息、输出格式、约束条件和评价标准。
- 如果原文过于简短，补全必要的角色、步骤和格式要求。
- 输出一段可直接复制使用的完整提示词。

## 输出要求
- 只输出优化后的提示词，不要解释优化过程。
- 使用中文，结构清晰。

划词内容：
[划词内容]"""

DEFAULT_AI_SEARCH_PROMPT = """请对下面的划词内容进行深度AI搜索和网络分析，搜集并整理相关的核心定义、背景知识、最新动态及详细解释，并以结构清晰的格式给出解答：

划词内容：
[划词内容]"""


DEFAULT_REPLY_PROMPT_BUTTON_NAME = "回复"
DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME = "搜索"
DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME = "解释"
DEFAULT_SUMMARY_PROMPT_BUTTON_NAME = "总结"
PROMPT_BUTTON_NAME_MAX_LENGTH = 12


def normalize_translator_provider(provider: Any, default: str = "其它") -> str:
    name = str(provider or "").strip()
    if not name:
        return "其它" if default is None else str(default)
    compact = re.sub(r"\s+", "", name).lower()
    legacy_groups = {
        "古法翻译": "免费翻译",
        "古法": "免费翻译",
        "免费翻译": "免费翻译",
        "微软": "免费翻译",
        "microsoft": "免费翻译",
        "microsofttranslator": "免费翻译",
        "微软翻译": "免费翻译",
        "google": "免费翻译",
        "google翻译": "免费翻译",
        "谷歌翻译": "免费翻译",
        "deeplx": "免费翻译",
        "智谱ai": "智谱AI",
        "智谱": "智谱AI",
        "zhipuai": "智谱AI",
        "zhipu": "智谱AI",
        "agnesai": "Agnes AI",
        "agenesai": "Agnes AI",
        "agnes": "Agnes AI",
        "agenes": "Agnes AI",
        "腾讯混元": "本地部署",
    }
    return legacy_groups.get(compact, name)


def infer_translator_model_type(model_name: str, cfg: dict[str, Any]) -> str:
    explicit_type = str(cfg.get("model_type", "") or "").strip().lower()
    if explicit_type in {"chatgpt_web", "chatgpt-web", "chatgpt_web2api", "chatgpt"}:
        return "chatgpt_web"
    if explicit_type in {"anthropic", "claude"}:
        return "anthropic"
    if explicit_type in {"openai_responses", "openai-responses", "responses", "responses_api"}:
        return "openai_responses"
    if explicit_type in {"openai_images", "openai-images", "images", "image_generation", "images_generations"}:
        return "openai_images"

    model_name_lower = str(model_name or "").lower()
    base_url_lower = str(cfg.get("base_url", "") or "").lower()
    if "127.0.0.1:8082" in base_url_lower or "localhost:8082" in base_url_lower:
        return "chatgpt_web"
    if "127.0.0.1:8081" in base_url_lower or "localhost:8081" in base_url_lower:
        return "glm"

    hint = " ".join(
        [
            str(model_name or "").lower(),
            str(cfg.get("model_name", "") or "").lower(),
            str(cfg.get("base_url", "") or "").lower(),
        ]
    )
    if "microsoft" in hint or "微软" in hint or "api-edge.cognitive.microsofttranslator" in hint:
        return "microsoft_free"
    if "google翻译" in hint or "google-free" in hint or "translate.googleapis" in hint:
        return "google_free"
    if "deeplx" in hint:
        return "deeplx"
    if is_anthropic_messages_url(str(cfg.get("base_url", "") or "")) or "api.anthropic.com" in base_url_lower:
        return "anthropic"
    if is_openai_images_url(str(cfg.get("base_url", "") or "")):
        return "openai_images"
    if is_openai_responses_url(str(cfg.get("base_url", "") or "")):
        return "openai_responses"
    if "generativelanguage.googleapis.com" in base_url_lower:
        return "gemini"
    return "glm"


def is_anthropic_messages_url(base_url: str) -> bool:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return False
    parsed = urlparse(base)
    path = str(parsed.path or base).rstrip("/").lower()
    return path.endswith("/v1/messages") or path.endswith("/messages")


def normalize_anthropic_messages_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if is_anthropic_messages_url(base):
        return base
    lower = base.lower()
    if lower.endswith("/v1"):
        return f"{base}/messages"
    parsed = urlparse(base)
    if parsed.scheme in {"http", "https"} and parsed.netloc and str(parsed.path or "").rstrip("/") == "":
        return f"{base}/v1/messages"
    return base


def is_openai_responses_url(base_url: str) -> bool:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return False
    parsed = urlparse(base)
    path = str(parsed.path or base).rstrip("/").lower()
    return path.endswith("/v1/responses") or path.endswith("/responses")


def is_openai_images_url(base_url: str) -> bool:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return False
    parsed = urlparse(base)
    path = str(parsed.path or base).rstrip("/").lower()
    return path.endswith("/images/generations") or path.endswith("/images")


def normalize_openai_images_url(base_url: str) -> str:
    """归一化到 OpenAI Images API 端点 …/v1/images/generations。"""
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    lower = base.lower()
    if lower.endswith("/images/generations"):
        return base
    if lower.endswith("/images"):
        return f"{base}/generations"
    for endpoint in ("/chat/completions", "/completions", "/responses", "/models"):
        if lower.endswith(endpoint):
            base = base[: -len(endpoint)].rstrip("/")
            lower = base.lower()
            break
    if lower.endswith(("/v1", "/v1beta", "/v2", "/v3", "/v4")):
        return f"{base}/images/generations"
    parsed = urlparse(base)
    if parsed.scheme in {"http", "https"} and parsed.netloc and str(parsed.path or "").rstrip("/") == "":
        return f"{base}/v1/images/generations"
    return base


def normalize_openai_responses_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if is_openai_responses_url(base):
        return base
    lower = base.lower()
    for endpoint in ("/chat/completions", "/completions", "/models"):
        if lower.endswith(endpoint):
            base = base[: -len(endpoint)].rstrip("/")
            lower = base.lower()
            break
    if lower.endswith(("/v1", "/v1beta", "/v2", "/v3", "/v4")):
        return f"{base}/responses"
    parsed = urlparse(base)
    if parsed.scheme in {"http", "https"} and parsed.netloc and str(parsed.path or "").rstrip("/") == "":
        return f"{base}/v1/responses"
    return base


def normalize_openai_chat_base_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""

    lower = base.lower()
    for endpoint in ("/images/generations", "/chat/completions", "/completions", "/responses", "/images", "/models"):
        if lower.endswith(endpoint):
            base = base[: -len(endpoint)].rstrip("/")
            lower = base.lower()
            break

    if lower.endswith(("/v1", "/v1beta", "/v2", "/v3", "/v4")):
        return base

    parsed = urlparse(base)
    if parsed.scheme in {"http", "https"} and parsed.netloc and str(parsed.path or "").rstrip("/") == "":
        return f"{base}/v1"
    return base


def infer_translator_model_provider(model_name: str, cfg: dict[str, Any]) -> str:
    prov = str(cfg.get("provider") or "").strip()
    if prov:
        return normalize_translator_provider(prov)

    name_lower = str(model_name or "").lower()
    hint = " ".join([
        str(model_name or "").lower(),
        str(cfg.get("model_name", "") or "").lower(),
        str(cfg.get("base_url", "") or "").lower(),
    ])

    if "microsoft" in hint or "微软" in hint or "api-edge.cognitive.microsofttranslator" in hint:
        return "免费翻译"
    if "gemini" in hint or "generativelanguage" in hint:
        return "Google Gemini"
    if "google翻译" in hint or "google-free" in hint or "translate.googleapis" in hint:
        return "免费翻译"
    if "deeplx" in hint:
        return "免费翻译"
    if is_anthropic_messages_url(str(cfg.get("base_url", "") or "")) or "api.anthropic.com" in hint:
        return "Anthropic"
    if is_openai_responses_url(str(cfg.get("base_url", "") or "")) or "api.openai.com" in hint:
        return "OpenAI"
    if "chatgpt" in hint or "127.0.0.1:8082" in hint or "localhost:8082" in hint:
        return "ChatGPT Web"
    if "mt1.5" in hint or "mt15" in hint or "mt2" in hint or "hy-mt" in hint or "腾讯" in hint:
        return "本地部署"
    if "glm" in hint or "bigmodel" in hint or "智谱" in hint:
        return "智谱AI"
    if "agnes" in hint:
        return "Agnes AI"

    return "其它"


BUILTIN_MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "Google翻译": {
        "base_url": "https://translate.googleapis.com",
        "model_name": "google-free",
        "api_key": "",
        "use_proxy": False,
        "model_type": "google_free",
        "provider": "免费翻译",
    },
    "DeepLX": {
        "base_url": DEEPLX_INFO_ADDRESS,
        "model_name": "deepLX-free",
        "api_key": "",
        "use_proxy": False,
        "model_type": "deeplx",
        "provider": "免费翻译",
    },
    "gemini-3.5-flash-thinking": {
        "base_url": "http://127.0.0.1:8081",
        "model_name": "gemini-3.5-flash-thinking",
        "api_key": "123456",
        "use_proxy": False,
        "model_type": "glm",
        "provider": "Google Gemini",
    },
    "gemini-3.6-flash": {
        "base_url": "http://127.0.0.1:8081",
        "model_name": "gemini-3.6-flash",
        "api_key": "123456",
        "use_proxy": False,
        "model_type": "glm",
        "provider": "Google Gemini",
    },
    "gemini-3.7-flash": {
        "base_url": "http://127.0.0.1:8081",
        "model_name": "gemini-3.7-flash",
        "api_key": "123456",
        "use_proxy": False,
        "model_type": "glm",
        "provider": "Google Gemini",
    },
    "gemini-3.8-flash": {
        "base_url": "http://127.0.0.1:8081",
        "model_name": "gemini-3.8-flash",
        "api_key": "123456",
        "use_proxy": False,
        "model_type": "glm",
        "provider": "Google Gemini",
    },
    "ChatGPT Web": {
        "base_url": "http://127.0.0.1:8082",
        "model_name": "gpt-5-3",
        "api_key": "",
        "use_proxy": False,
        "model_type": "chatgpt_web",
        "provider": "ChatGPT Web",
    },
    "ChatGPT Web 生图": {
        "base_url": "http://127.0.0.1:8082/v1/images/generations",
        "model_name": "gpt-image-2",
        "api_key": "",
        "use_proxy": False,
        "model_type": "openai_images",
        "provider": "ChatGPT Web",
    },
    "腾讯模型MT1.5": {
        "base_url": "http://127.0.0.1:8080",
        "model_name": "hy-mt15-1.8b-q4_k_m",
        "api_key": "sk-1234",
        "use_proxy": False,
        "model_type": "glm",
        "provider": "本地部署",
    },
    "腾讯模型MT2": {
        "base_url": "http://127.0.0.1:8080",
        "model_name": "hy-mt2-1.8b-q4_k_m",
        "api_key": "sk-1234",
        "use_proxy": False,
        "model_type": "glm",
        "provider": "本地部署",
    },
}


def get_model_catalog_path() -> Path:
    return get_app_dir() / "model_catalog.json"


_catalog_cache_lock = threading.Lock()
_catalog_cache_sig: Optional[tuple[str, int, int]] = None
_catalog_cache_value: Optional[dict[str, dict[str, Any]]] = None


def default_model_catalog() -> dict[str, dict[str, Any]]:
    merged = {str(name): dict(cfg) for name, cfg in BUILTIN_MODEL_CATALOG.items()}
    catalog = _read_model_catalog(get_model_catalog_path())
    if catalog:
        for name, cfg in catalog.items():
            merged[str(name)] = dict(cfg)
    for name, cfg in merged.items():
        if isinstance(cfg, dict):
            cfg["provider"] = normalize_translator_provider(
                cfg.get("provider"),
                infer_translator_model_provider(str(name), cfg),
            )
    return merged


def _read_model_catalog(path: Path) -> dict[str, dict[str, Any]]:
    global _catalog_cache_sig, _catalog_cache_value
    try:
        st = path.stat()
        sig: Optional[tuple[str, int, int]] = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    if sig is not None:
        with _catalog_cache_lock:
            if _catalog_cache_value is not None and _catalog_cache_sig == sig:
                return {name: dict(cfg) for name, cfg in _catalog_cache_value.items()}
    catalog = _parse_model_catalog(path)
    if sig is not None:
        with _catalog_cache_lock:
            _catalog_cache_sig = sig
            _catalog_cache_value = {name: dict(cfg) for name, cfg in catalog.items()}
    return catalog


def _parse_model_catalog(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    raw = data.get("model_configs") if isinstance(data, dict) and isinstance(data.get("model_configs"), dict) else data
    if not isinstance(raw, dict):
        return {}
    catalog: dict[str, dict[str, Any]] = {}
    for name, cfg in raw.items():
        model_name = str(name or "").strip()
        if model_name and isinstance(cfg, dict):
            catalog[model_name] = dict(cfg)
    return catalog


def default_translator_settings() -> dict[str, Any]:
    return {
        "source_lang": "自动检测",
        "target_lang": "中英互译",
        "selection_translate_enabled": True,
        "selection_popup_enabled": False,
        "ocr_translate_enabled": False,
        "auto_copy_answers": False,
        "local_translation_service_enabled": False,
        "codex_config_enabled": False,
        "codex_current_model_config_enabled": False,
        "codex_current_model_config_model": "",
        "claude_code_current_model_config_model": "",
        "local_translation_service_host": "127.0.0.1",
        "local_translation_service_port": 11888,
        "local_translation_service_api_key": "sk-deepcat-local",
        "reply_prompt": DEFAULT_REPLY_PROMPT,
        "explain_prompt": DEFAULT_EXPLAIN_PROMPT,
        "summary_prompt": DEFAULT_SUMMARY_PROMPT,
        "ai_search_prompt": DEFAULT_AI_SEARCH_PROMPT,
        "optimize_prompt": DEFAULT_AI_SEARCH_PROMPT,
        "reply_prompt_button_name": DEFAULT_REPLY_PROMPT_BUTTON_NAME,
        "ai_search_prompt_button_name": DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
        "explain_prompt_button_name": DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
        "summary_prompt_button_name": DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
        "proxy_url": "socks5://127.0.0.1:1080",
        "current_model": "gemini-3.5-flash-thinking",
        "translate_model": "gemini-3.5-flash-thinking",
        "qa_model": "gemini-3.5-flash-thinking",
        "removed_models": [],
        "model_configs": default_model_catalog(),
        "provider_remarks": {"默认分组": "系统默认的托管分组"},
    }


def normalize_network_probe_settings(v: Any) -> dict[str, Any]:
    incoming = dict(v) if isinstance(v, dict) else {}
    raw_mode = str(incoming.get("mode", NETWORK_PROBE_MODE_HTTP_204) or "").strip().lower()
    mode = (
        NETWORK_PROBE_MODE_TCP_CONNECT
        if raw_mode in {"ping", "tcp", NETWORK_PROBE_MODE_TCP_CONNECT}
        else NETWORK_PROBE_MODE_HTTP_204
    )
    return {
        "enabled": _coerce_bool(incoming.get("enabled"), False),
        "mode": mode,
    }


def _normalize_hex_color(v: Any, default: str) -> str:
    s = str(v or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return s.upper()
    return str(default)


def normalize_annotation_style(v: Any) -> dict[str, Any]:
    incoming = dict(v) if isinstance(v, dict) else {}
    style = dict(DEFAULT_ANNOTATION_STYLE)
    for key, default in DEFAULT_ANNOTATION_STYLE.items():
        if key == "line_style":
            line_style = str(incoming.get(key, default) or default).strip().lower()
            style[key] = "solid" if line_style in {"solid", "实线"} else "dash"
        else:
            style[key] = _normalize_hex_color(incoming.get(key), str(default))
    return style


@dataclass(frozen=True)
class AppSettings:
    version: int
    autostart: bool
    auto_save: bool
    image_output_dir: str
    pdf_output_dir: str
    hotkey: str
    ui: dict[str, Any]
    notifications_enabled: bool = False


def get_settings_path() -> Path:
    return get_app_dir() / "settings.json"


def _defaults() -> AppSettings:
    d = str(get_files_dir())
    return AppSettings(
        version=int(SETTINGS_VERSION),
        autostart=False,
        auto_save=False,
        image_output_dir=d,
        pdf_output_dir=d,
        hotkey=DEFAULT_HOTKEY,
        ui={
            "mode": "框选截图",
            "cdp_enabled": False,
            "cdp_port": 9888,
            "adaptive_wait": False,
            "boost_scroll": True,
            "copy_on_capture": False,
            "copy_on_capture_mode": DEFAULT_COPY_ON_CAPTURE_MODE,
            "speed": 95,
            "output_format": "png",
            "merge_pdf": True,
            "merge_image": True,
            "dual_output": False,
            "window_geometry_b64": "",
            "resizable_page_sizes": {},
            "settings_dialog_geometry_b64": "",
            "qa_window_pos": None,
            "qa_window_pos_user_moved": False,
            "qa_history_window_geometry": None,
            "window_state": {"maximized": False},
            "theme": "",
            "language": "",
            "save_mode": "手动保存",
            "save_button_mode": "auto",
            "previous_capture_action": "pin",
            "post_capture_button_style": "icon",
            "auto_snap_enabled": True,
            "scroll_hotkey": DEFAULT_SCROLL_HOTKEY,
            "later_read_hotkey": DEFAULT_LATER_READ_HOTKEY,
            "ai_qa_hotkey": DEFAULT_AI_QA_HOTKEY,
            "selection_translate_hotkey": DEFAULT_SELECTION_TRANSLATE_HOTKEY,
            "selection_popup_hotkey": DEFAULT_SELECTION_POPUP_HOTKEY,
            "annotation_style": normalize_annotation_style(None),
            "translator": default_translator_settings(),
            "network_probe": normalize_network_probe_settings(None),
            "cat_reminder": dict(DEFAULT_CAT_REMINDER),
            "todo_items": list(DEFAULT_TODO_ITEMS),
            "later_read": {
                "enabled": bool(DEFAULT_LATER_READ["enabled"]),
                "filter_keywords": list(DEFAULT_LATER_READ["filter_keywords"]),
                "items": [],
            },
            "clipboard_history": dict(DEFAULT_CLIPBOARD_HISTORY),
            "feature_visibility": dict(DEFAULT_FEATURE_VISIBILITY),
            "logging": dict(DEFAULT_LOG_SETTINGS),
            "updater": dict(DEFAULT_UPDATER),
            "table_notes": {"table": [], "note_html": ""},
            "data_management": {
                "auto_backup_enabled": False,
                "auto_backup_dir": "",
                "auto_backup_interval_minutes": DEFAULT_AUTO_BACKUP_INTERVAL_MINUTES,
                "auto_backup_interval_hours": 24,
                "auto_backup_keep_count": 5,
                "webdav_enabled": False,
                "webdav_server": "https://dav.jianguoyun.com/dav/",
                "webdav_username": "",
                "webdav_password": "",
                "webdav_backup_dir": "DeepCatBackup",
                "webdav_keep_count": 5,
                "backup_options": {
                    "clipboard": True,
                    "table_notes": True,
                    "later_read": True,
                    "ai_chat_history": True,
                    "todo": True,
                    "settings": True,
                    "model_catalog": True
                },
                "cleanup_records": [],
                "last_backup_time": "",
            },
        },
        notifications_enabled=True,
    )


def _coerce_bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    return default


def _coerce_str(v: Any, default: str) -> str:
    if isinstance(v, str) and v.strip():
        return v.strip()
    return default


def _normalize_prompt_button_name(v: Any, default: str) -> str:
    name = _coerce_str(v, default)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return str(default)
    return name[:PROMPT_BUTTON_NAME_MAX_LENGTH]


def _coerce_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def coerce_auto_backup_interval_minutes(
    value: Any,
    legacy_hours: Any = None,
    default: int = DEFAULT_AUTO_BACKUP_INTERVAL_MINUTES,
) -> int:
    minutes = _coerce_int(value, 0)
    if minutes >= 1:
        return minutes

    hours = _coerce_int(legacy_hours, 0)
    if hours >= 1:
        return hours * 60

    return max(1, int(default))


def _coerce_dict(v: Any, default: dict[str, Any]) -> dict[str, Any]:
    if isinstance(v, dict):
        return dict(v)
    return dict(default)


def _normalize_capture_mode(v: Any, default: str) -> str:
    mode = _coerce_str(v, default)
    if mode in {"自动", "自动滚动"}:
        return "滚动截图"
    if mode in {"手动", "手动滚动", "区域", "区域选取", "框选截屏"}:
        return "框选截图"
    if mode in {"滚动截屏"}:
        return "滚动截图"
    if mode in {"全屏", "全屏截图", "滚动截图", "框选截图"}:
        if mode == "全屏":
            return "全屏截图"
        return mode
    return default


def _normalize_previous_capture_action(v: Any, default: str = "pin") -> str:
    action = _coerce_str(v, default).strip().lower()
    if action in {"pin", "pinned", "top", "topmost", "置顶", "自动置顶"}:
        return "pin"
    if action in {"save", "保存", "保存前图", "auto_save_previous", "autosaveprevious"}:
        return "save"
    if action in {"stitch", "拼接", "拼接前图"}:
        return "stitch"
    if action in {"stash", "暂存", "暂存前图"}:
        return "stash"
    return default


def _normalize_save_mode(v: Any, default: str = "自动保存") -> str:
    mode = _coerce_str(v, default).strip()
    if mode in {"手动", "手动保存", "手动保存（自己选路径）", "manual", "manual_save"}:
        return "手动保存"
    return "自动保存"


def _normalize_save_button_mode(v: Any, default: str = "auto") -> str:
    mode = _coerce_str(v, default).strip().lower()
    if mode in {"manual", "manual_save", "手动", "手动保存", "手动保存（自己选路径）"}:
        return "manual"
    return "auto"


def normalize_copy_on_capture_mode(
    v: Any,
    legacy_copy_on_capture: Any = None,
    default: str = DEFAULT_COPY_ON_CAPTURE_MODE,
) -> str:
    if isinstance(v, bool):
        return COPY_ON_CAPTURE_MODE_COPY_KEEP if bool(v) else COPY_ON_CAPTURE_MODE_OFF

    raw = _coerce_str(v, "").strip().lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    if raw in {"off", "none", "false", "no", "0", "disabled", "关闭复制", "不复制", "不自动复制"}:
        return COPY_ON_CAPTURE_MODE_OFF
    if raw in {
        "copy_close",
        "copyclose",
        "close",
        "复制关闭",
        "复制后关闭",
        "自动复制关闭",
    }:
        return COPY_ON_CAPTURE_MODE_COPY_CLOSE
    if raw in {
        "copy_keep",
        "copykeep",
        "keep",
        "true",
        "yes",
        "1",
        "复制保留",
        "复制后保留",
        "自动复制保留",
        "截图复制",
    }:
        return COPY_ON_CAPTURE_MODE_COPY_KEEP

    if legacy_copy_on_capture is not None:
        return COPY_ON_CAPTURE_MODE_COPY_KEEP if _coerce_bool(legacy_copy_on_capture, False) else COPY_ON_CAPTURE_MODE_OFF
    return default if default in {COPY_ON_CAPTURE_MODE_OFF, COPY_ON_CAPTURE_MODE_COPY_CLOSE, COPY_ON_CAPTURE_MODE_COPY_KEEP} else DEFAULT_COPY_ON_CAPTURE_MODE


def _normalize_post_capture_button_style(v: Any, default: str = "icon") -> str:
    style = _coerce_str(v, default).strip().lower()
    if style in {"text", "文字", "文字按钮"}:
        return "text"
    return "icon"


def normalize_feature_visibility(v: Any) -> dict[str, bool]:
    incoming = dict(v) if isinstance(v, dict) else {}
    return {
        key: _coerce_bool(incoming.get(key), bool(default))
        for key, default in DEFAULT_FEATURE_VISIBILITY.items()
    }


def normalize_updater_settings(v: Any) -> dict[str, Any]:
    incoming = dict(v) if isinstance(v, dict) else {}
    return {
        "auto_update_enabled": _coerce_bool(incoming.get("auto_update_enabled"), bool(DEFAULT_UPDATER["auto_update_enabled"])),
        "last_check_at": _coerce_str(incoming.get("last_check_at"), str(DEFAULT_UPDATER["last_check_at"])),
        "last_downloaded_version": _coerce_str(incoming.get("last_downloaded_version"), str(DEFAULT_UPDATER["last_downloaded_version"])),
    }


def normalize_cat_reminder_settings(v: Any) -> dict[str, Any]:
    incoming = dict(v) if isinstance(v, dict) else {}
    interval = int(max(1, min(24 * 60, _coerce_int(incoming.get("interval_minutes"), int(DEFAULT_CAT_REMINDER["interval_minutes"])))))
    duration = int(max(5, min(10 * 60, _coerce_int(incoming.get("duration_seconds"), int(DEFAULT_CAT_REMINDER["duration_seconds"])))))
    pre_notify_seconds = int(max(5, min(5 * 60, _coerce_int(incoming.get("pre_notify_seconds"), int(DEFAULT_CAT_REMINDER["pre_notify_seconds"])))))
    message = _coerce_str(incoming.get("message"), str(DEFAULT_CAT_REMINDER["message"]))
    if message in ("休息一下吧，猫咪已经接管屏幕。倒计时结束后再继续。", "让眼睛歇一歇，给疲惫的身心充个电吧。短暂的停歇，是为了接下来更精彩的出发。", "让眼睛歇一歇，给身心充个电，稍后见！", "让眼睛歇一歇，给身心充个电，然后向下一个山峰进发！"):
        message = str(DEFAULT_CAT_REMINDER["message"])
    return {
        "enabled": _coerce_bool(incoming.get("enabled"), bool(DEFAULT_CAT_REMINDER["enabled"])),
        "interval_minutes": interval,
        "duration_seconds": duration,
        "voice_enabled": _coerce_bool(incoming.get("voice_enabled"), bool(DEFAULT_CAT_REMINDER["voice_enabled"])),
        "exit_enabled": _coerce_bool(incoming.get("exit_enabled"), bool(DEFAULT_CAT_REMINDER["exit_enabled"])),
        "pre_notify_enabled": _coerce_bool(incoming.get("pre_notify_enabled"), bool(DEFAULT_CAT_REMINDER["pre_notify_enabled"])),
        "pre_notify_seconds": pre_notify_seconds,
        "message": message,
    }


def normalize_todo_items(v: Any) -> list[dict[str, Any]]:
    if not isinstance(v, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(v):
        if not isinstance(raw, dict):
            continue
        item_id = _coerce_str(raw.get("id"), "").strip() or f"todo-{index + 1}"
        title = _coerce_str(raw.get("title"), "").strip() or "未命名待办"
        content = _coerce_str(raw.get("content", raw.get("location", "")), "").strip()
        start = _coerce_str(raw.get("start"), "").strip()
        end = _coerce_str(raw.get("end"), "").strip()
        reminder_minutes = int(max(0, min(7 * 24 * 60, _coerce_int(raw.get("reminder_minutes"), 10))))
        snooze_minutes = int(max(1, min(24 * 60, _coerce_int(raw.get("snooze_minutes"), 10))))
        repeat = _coerce_str(raw.get("repeat"), "none").strip().lower()
        if repeat not in {"none", "daily", "weekly", "monthly"}:
            repeat = "none"
        repeat_weekday_raw = raw.get("repeat_weekday", 1)
        if isinstance(repeat_weekday_raw, list):
            repeat_weekday = [int(max(1, min(7, _coerce_int(x, 1)))) for x in repeat_weekday_raw]
        else:
            repeat_weekday = [int(max(1, min(7, _coerce_int(repeat_weekday_raw, 1))))]
        repeat_month_day_raw = raw.get("repeat_month_day", 1)
        if isinstance(repeat_month_day_raw, list):
            repeat_month_day = [int(max(1, min(31, _coerce_int(x, 1)))) for x in repeat_month_day_raw]
        else:
            repeat_month_day = [int(max(1, min(31, _coerce_int(repeat_month_day_raw, 1))))]
        reminded_occurrences = raw.get("reminded_occurrences")
        if isinstance(reminded_occurrences, list):
            occurrences = [_coerce_str(x, "").strip() for x in reminded_occurrences if _coerce_str(x, "").strip()]
        else:
            occurrences = []
        normalized.append(
            {
                "id": item_id,
                "title": title,
                "content": content,
                "start": start,
                "end": end,
                "all_day": _coerce_bool(raw.get("all_day"), False),
                "important": _coerce_bool(raw.get("important"), False),
                "reminder_minutes": reminder_minutes,
                "snooze_minutes": snooze_minutes,
                "repeat": repeat,
                "repeat_weekday": repeat_weekday,
                "repeat_month_day": repeat_month_day,
                "completed": _coerce_bool(raw.get("completed"), False),
                "struck_off": _coerce_bool(raw.get("struck_off"), False),
                "reminded_at": _coerce_str(raw.get("reminded_at"), "").strip(),
                "reminded_occurrences": occurrences,
                "next_remind_at": _coerce_str(raw.get("next_remind_at"), "").strip(),
                "next_remind_occurrence": _coerce_str(raw.get("next_remind_occurrence"), "").strip(),
            }
        )
    return normalized


def _site_from_url(url: str) -> str:
    try:
        parsed = urlparse(str(url).strip())
        host = str(parsed.netloc or "").strip().lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def normalize_clipboard_history_settings(v: Any) -> dict[str, Any]:
    incoming = dict(v) if isinstance(v, dict) else {}
    raw_keywords = incoming.get("filter_keywords", incoming.get("keywords"))
    if isinstance(raw_keywords, str):
        keyword_parts = re.split(r"[,，\r\n]+", raw_keywords)
    elif isinstance(raw_keywords, list):
        keyword_parts = [str(x) for x in raw_keywords]
    else:
        keyword_parts = []
    keywords: list[str] = []
    seen_keywords: set[str] = set()
    for part in keyword_parts:
        keyword = str(part or "").strip()
        key = keyword.lower()
        if not keyword or key in seen_keywords:
            continue
        seen_keywords.add(key)
        keywords.append(keyword[:80])
    return {
        "monitor_enabled": _coerce_bool(incoming.get("monitor_enabled"), bool(DEFAULT_CLIPBOARD_HISTORY["monitor_enabled"])),
        "last_content_hash": _coerce_str(incoming.get("last_content_hash"), str(DEFAULT_CLIPBOARD_HISTORY["last_content_hash"])),
        "filter_keywords": keywords,
        "auto_cleanup_enabled": _coerce_bool(
            incoming.get("auto_cleanup_enabled"), bool(DEFAULT_CLIPBOARD_HISTORY["auto_cleanup_enabled"])
        ),
        "retention_days": max(
            0,
            _coerce_int(incoming.get("retention_days"), int(DEFAULT_CLIPBOARD_HISTORY["retention_days"])),
        ),
        "max_records": max(
            0,
            _coerce_int(incoming.get("max_records"), int(DEFAULT_CLIPBOARD_HISTORY["max_records"])),
        ),
        "max_capacity_mb": max(
            0,
            _coerce_int(incoming.get("max_capacity_mb"), int(DEFAULT_CLIPBOARD_HISTORY["max_capacity_mb"])),
        ),
    }


def normalize_later_read_settings(v: Any) -> dict[str, Any]:
    incoming = dict(v) if isinstance(v, dict) else {}
    raw_keywords = incoming.get("filter_keywords", incoming.get("keywords"))
    if isinstance(raw_keywords, str):
        keyword_parts = re.split(r"[,，\r\n]+", raw_keywords)
    elif isinstance(raw_keywords, list):
        keyword_parts = [str(x) for x in raw_keywords]
    else:
        keyword_parts = [str(x) for x in DEFAULT_LATER_READ["filter_keywords"]]
    keywords: list[str] = []
    seen_keywords: set[str] = set()
    for part in keyword_parts:
        keyword = str(part or "").strip()
        key = keyword.lower()
        if not keyword or key in seen_keywords:
            continue
        seen_keywords.add(key)
        keywords.append(keyword[:80])
    if keywords == ["纯水", "人工智能", "tag", "快问快答"]:
        keywords = [str(x) for x in DEFAULT_LATER_READ["filter_keywords"]]
    raw_items = incoming.get("items")
    normalized_items: list[dict[str, Any]] = []
    if isinstance(raw_items, list):
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            url = _coerce_str(raw.get("url"), "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            title = _coerce_str(raw.get("title"), "").strip() or url
            item_id = _coerce_str(raw.get("id"), "").strip() or f"read-{index + 1}"
            site = _coerce_str(raw.get("site"), "").strip() or _site_from_url(url)
            created_at = _coerce_str(raw.get("created_at"), "").strip()
            normalized_items.append(
                {
                    "id": item_id,
                    "title": title[:240],
                    "url": url[:2000],
                    "site": site[:160],
                    "created_at": created_at,
                    "read": _coerce_bool(raw.get("read"), False),
                    "is_pinned": _coerce_bool(raw.get("is_pinned", raw.get("pinned")), False),
                }
            )
    return {
        "enabled": _coerce_bool(incoming.get("enabled"), bool(DEFAULT_LATER_READ["enabled"])),
        "filter_keywords": keywords,
        "items": normalized_items,
    }


def normalize_translator_settings(v: Any) -> dict[str, Any]:
    default = default_translator_settings()
    incoming = dict(v) if isinstance(v, dict) else {}
    removed_raw = incoming.get("removed_models")
    if isinstance(removed_raw, list):
        removed_models = {TRANSLATOR_MODEL_ALIASES.get(str(x).strip(), str(x).strip()) for x in removed_raw if str(x).strip()}
    else:
        removed_models = set()
    raw_model_configs = incoming.get("model_configs")
    if not isinstance(raw_model_configs, dict):
        raw_model_configs = {}
    model_configs: dict[str, Any] = {}
    for name, cfg in raw_model_configs.items():
        aliased_name = TRANSLATOR_MODEL_ALIASES.get(str(name).strip(), str(name).strip())
        if not aliased_name:
            continue
        current_cfg = dict(model_configs.get(aliased_name, {}) or {})
        if isinstance(cfg, dict):
            current_cfg.update(cfg)
        model_configs[aliased_name] = current_cfg
    merged_configs: dict[str, dict[str, Any]] = {}
    for name, cfg in dict(default["model_configs"]).items():
        if str(name) in removed_models:
            continue
        current = dict(cfg)
        if isinstance(model_configs.get(name), dict):
            current.update(model_configs.get(name) or {})
        if str(name) == "DeepLX":
            if str(current.get("base_url", "") or "").strip() == DEEPLX_OLD_DEFAULT_BASE_URL:
                current["base_url"] = DEEPLX_INFO_ADDRESS
            if str(current.get("model_name", "") or "").strip().lower() == "deeplx":
                current["model_name"] = "deepLX-free"
        merged_configs[name] = {
            "base_url": _coerce_str(current.get("base_url"), str(cfg.get("base_url", ""))),
            "model_name": _coerce_str(current.get("model_name"), str(cfg.get("model_name", ""))),
            "api_key": unprotect_text(str(current.get("api_key", "") or "")),
            "use_proxy": _coerce_bool(current.get("use_proxy"), bool(cfg.get("use_proxy", False))),
            "model_type": infer_translator_model_type(str(name), current),
            "provider": normalize_translator_provider(
                current.get("provider"),
                infer_translator_model_provider(str(name), current),
            ),
        }
    deprecated_builtin_models = {
        "glm-4-flash",
        "agenes-ai",
        "gemini-2.5-flash",
        "微软翻译",
        "免费微软翻译",
    }
    for name, cfg in model_configs.items():
        if str(name) in merged_configs or not isinstance(cfg, dict):
            continue
        if str(name) in removed_models or str(name) in deprecated_builtin_models:
            continue
        merged = {
            "base_url": _coerce_str(cfg.get("base_url"), ""),
            "model_name": _coerce_str(cfg.get("model_name"), str(name)),
            "api_key": unprotect_text(str(cfg.get("api_key", "") or "")),
            "use_proxy": _coerce_bool(cfg.get("use_proxy"), False),
        }
        merged["model_type"] = infer_translator_model_type(str(name), {**cfg, **merged})
        merged["provider"] = normalize_translator_provider(
            cfg.get("provider"),
            infer_translator_model_provider(str(name), {**cfg, **merged}),
        )
        merged_configs[str(name)] = merged
    legacy_current_model = TRANSLATOR_MODEL_ALIASES.get(
        _coerce_str(incoming.get("current_model"), str(default["current_model"])),
        _coerce_str(incoming.get("current_model"), str(default["current_model"])),
    )
    translate_model = TRANSLATOR_MODEL_ALIASES.get(
        _coerce_str(incoming.get("translate_model"), legacy_current_model),
        _coerce_str(incoming.get("translate_model"), legacy_current_model),
    )
    qa_model = TRANSLATOR_MODEL_ALIASES.get(
        _coerce_str(incoming.get("qa_model"), str(default.get("qa_model", "gemini-3.5-flash-thinking"))),
        _coerce_str(incoming.get("qa_model"), str(default.get("qa_model", "gemini-3.5-flash-thinking"))),
    )
    current_model = translate_model
    if current_model not in merged_configs:
        if "Google翻译" in merged_configs:
            current_model = "Google翻译"
        elif merged_configs:
            current_model = next(iter(merged_configs.keys()))
        else:
            current_model = "gemini-3.5-flash-thinking"
            merged_configs[current_model] = {
                "base_url": "http://127.0.0.1:8081",
                "model_name": "gemini-3.5-flash-thinking",
                "api_key": "123456",
                "use_proxy": False,
                "model_type": "glm",
            }
    translate_model = current_model
    if qa_model not in merged_configs:
        qa_model = ""
    if not qa_model:
        for name, cfg in merged_configs.items():
            # 生图模型可手动选为 QA 模型，但不作为自动回退的默认对话模型
            if str(cfg.get("model_type", "")).lower() not in {"microsoft_free", "google_free", "deeplx", "openai_images"}:
                qa_model = str(name)
                break
    if not qa_model:
        qa_model = translate_model
    codex_config_enabled = _coerce_bool(incoming.get("codex_config_enabled"), False)
    codex_current_model_config_enabled = _coerce_bool(incoming.get("codex_current_model_config_enabled"), False)
    codex_current_model_config_model = TRANSLATOR_MODEL_ALIASES.get(
        _coerce_str(incoming.get("codex_current_model_config_model"), ""),
        _coerce_str(incoming.get("codex_current_model_config_model"), ""),
    )
    if codex_config_enabled:
        codex_current_model_config_enabled = False
        codex_current_model_config_model = ""
    elif codex_current_model_config_enabled:
        if not codex_current_model_config_model:
            codex_current_model_config_model = qa_model
        if codex_current_model_config_model not in merged_configs:
            codex_current_model_config_enabled = False
            codex_current_model_config_model = ""
    claude_code_current_model_config_model = TRANSLATOR_MODEL_ALIASES.get(
        _coerce_str(incoming.get("claude_code_current_model_config_model"), ""),
        _coerce_str(incoming.get("claude_code_current_model_config_model"), ""),
    )
    if claude_code_current_model_config_model not in merged_configs:
        claude_code_current_model_config_model = ""
    target_lang = _coerce_str(incoming.get("target_lang"), str(default["target_lang"]))
    if target_lang in {"双向", "雙向"}:
        target_lang = "中英互译"
    ai_search_val = incoming.get("ai_search_prompt")
    if ai_search_val and ("请把下面的划词内容优化成" in str(ai_search_val) or "优化提示词" in str(ai_search_val)):
        ai_search_val = None

    old_optimize_val = incoming.get("optimize_prompt")
    if old_optimize_val and ("请把下面的划词内容优化成" in str(old_optimize_val) or "优化提示词" in str(old_optimize_val)):
        old_optimize_val = None

    provider_remarks: dict[str, Any] = {}
    for raw_provider, raw_remark in _coerce_dict(incoming.get("provider_remarks"), {"默认分组": "系统默认的托管分组"}).items():
        if not str(raw_provider or "").strip():
            continue
        provider = normalize_translator_provider(raw_provider)
        if provider and provider not in provider_remarks:
            provider_remarks[provider] = raw_remark

    ai_search_prompt = _coerce_str(
        ai_search_val or old_optimize_val,
        str(default["ai_search_prompt"])
    )

    return {
        "source_lang": _coerce_str(incoming.get("source_lang"), str(default["source_lang"])),
        "target_lang": target_lang,
        "selection_translate_enabled": _coerce_bool(incoming.get("selection_translate_enabled"), True),
        "selection_popup_enabled": _coerce_bool(incoming.get("selection_popup_enabled"), False),
        "ocr_translate_enabled": _coerce_bool(incoming.get("ocr_translate_enabled"), False),
        "auto_copy_answers": _coerce_bool(incoming.get("auto_copy_answers"), False),
        "local_translation_service_enabled": _coerce_bool(incoming.get("local_translation_service_enabled"), False),
        "codex_config_enabled": codex_config_enabled,
        "codex_current_model_config_enabled": codex_current_model_config_enabled,
        "codex_current_model_config_model": codex_current_model_config_model,
        "claude_code_current_model_config_model": claude_code_current_model_config_model,
        "local_translation_service_host": _coerce_str(incoming.get("local_translation_service_host"), "127.0.0.1"),
        "local_translation_service_port": int(max(1, min(65535, _coerce_int(incoming.get("local_translation_service_port"), 11888)))),
        "local_translation_service_api_key": unprotect_text(str(incoming.get("local_translation_service_api_key", "sk-deepcat-local") or "")),
        "reply_prompt": _coerce_str(incoming.get("reply_prompt"), str(default["reply_prompt"])),
        "explain_prompt": _coerce_str(incoming.get("explain_prompt"), str(default["explain_prompt"])),
        "summary_prompt": _coerce_str(incoming.get("summary_prompt"), str(default["summary_prompt"])),
        "ai_search_prompt": ai_search_prompt,
        "optimize_prompt": ai_search_prompt,
        "reply_prompt_button_name": _normalize_prompt_button_name(
            incoming.get("reply_prompt_button_name"),
            str(default["reply_prompt_button_name"]),
        ),
        "ai_search_prompt_button_name": _normalize_prompt_button_name(
            incoming.get("ai_search_prompt_button_name"),
            str(default["ai_search_prompt_button_name"]),
        ),
        "explain_prompt_button_name": _normalize_prompt_button_name(
            incoming.get("explain_prompt_button_name"),
            str(default["explain_prompt_button_name"]),
        ),
        "summary_prompt_button_name": _normalize_prompt_button_name(
            incoming.get("summary_prompt_button_name"),
            str(default["summary_prompt_button_name"]),
        ),
        "proxy_url": _coerce_str(incoming.get("proxy_url"), str(default["proxy_url"])),
        "current_model": current_model,
        "translate_model": translate_model,
        "qa_model": qa_model,
        "removed_models": sorted(removed_models),
        "model_configs": merged_configs,
        "provider_remarks": provider_remarks,
    }


def normalize_data_management_settings(v: Any) -> dict[str, Any]:
    d = {
        "auto_backup_enabled": False,
        "auto_backup_dir": "",
        "auto_backup_interval_minutes": DEFAULT_AUTO_BACKUP_INTERVAL_MINUTES,
        "auto_backup_interval_hours": 24,
        "auto_backup_keep_count": 5,
        "webdav_enabled": False,
        "webdav_server": "https://dav.jianguoyun.com/dav/",
        "webdav_username": "",
        "webdav_password": "",
        "webdav_backup_dir": "DeepCatBackup",
        "webdav_keep_count": 5,
        "backup_options": {
            "clipboard": True,
            "table_notes": True,
            "later_read": True,
            "ai_chat_history": True,
            "todo": True,
            "settings": True,
            "model_catalog": True
        },
        "cleanup_records": [],
        "last_backup_time": "",
    }
    if not isinstance(v, dict):
        return d

    d["auto_backup_enabled"] = bool(v.get("auto_backup_enabled", False))
    d["auto_backup_dir"] = str(v.get("auto_backup_dir", "") or "").strip()
    interval_minutes = coerce_auto_backup_interval_minutes(
        v.get("auto_backup_interval_minutes"),
        v.get("auto_backup_interval_hours"),
    )
    d["auto_backup_interval_minutes"] = interval_minutes
    d["auto_backup_interval_hours"] = max(1, (interval_minutes + 59) // 60)
    d["auto_backup_keep_count"] = max(1, _coerce_int(v.get("auto_backup_keep_count"), 5))

    d["webdav_enabled"] = bool(v.get("webdav_enabled", False))
    d["webdav_server"] = str(v.get("webdav_server", "https://dav.jianguoyun.com/dav/") or "").strip()
    d["webdav_username"] = str(v.get("webdav_username", "") or "").strip()
    d["webdav_password"] = unprotect_text(str(v.get("webdav_password", "") or "").strip())
    d["webdav_backup_dir"] = str(v.get("webdav_backup_dir", "DeepCatBackup") or "").strip()
    d["webdav_keep_count"] = max(1, _coerce_int(v.get("webdav_keep_count"), 5))

    opts = _coerce_dict(v.get("backup_options"), {})
    for k in d["backup_options"].keys():
        d["backup_options"][k] = bool(opts.get(k, True))

    cleanup_records: list[dict[str, str]] = []
    raw_records = v.get("cleanup_records")
    if isinstance(raw_records, list):
        for raw in raw_records:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title", "") or "").strip()
            detail = str(raw.get("detail", "") or "").strip()
            created_at = str(raw.get("created_at", "") or "").strip()
            if not title and not detail:
                continue
            cleanup_records.append(
                {
                    "title": title[:40] or "清理记录",
                    "detail": detail[:80],
                    "created_at": created_at[:19],
                }
            )
            if len(cleanup_records) >= 3:
                break
    d["cleanup_records"] = cleanup_records
    d["last_backup_time"] = str(v.get("last_backup_time", "") or "").strip()
    return d


def _settings_bak_path() -> Path:
    return get_settings_path().with_suffix(".json.bak")


# load/save/update 共用一把可重入锁：避免 Windows 下 tmp.replace(p) 与并发
# load 的读句柄冲突（PermissionError），同时保证 read-modify-write 的原子性。
_settings_lock = threading.RLock()


def _read_json(p: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _replace_with_retry(src: Path, dst: Path, attempts: int = 6) -> None:
    # 进程外的瞬时读句柄（杀毒/备份/编辑器）仍可能让 replace 抛 PermissionError，小退避重试
    for i in range(attempts):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.02 * (i + 1))


def _atomic_write_text(p: Path, text: str) -> None:
    tmp = p.parent / (p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    _replace_with_retry(tmp, p)


def _atomic_write_json(p: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if p.exists():
        # 旧文件直接重命名为 .bak（零拷贝）；若随后的新写入失败，load 会回退到 .bak
        try:
            _replace_with_retry(p, _settings_bak_path())
        except Exception:
            pass
    _atomic_write_text(p, text)


def _merge_ui(default_ui: dict[str, Any], incoming: Any) -> dict[str, Any]:
    ui = dict(default_ui)
    incoming_has_copy_mode = isinstance(incoming, dict) and "copy_on_capture_mode" in incoming
    incoming_has_legacy_copy = isinstance(incoming, dict) and "copy_on_capture" in incoming
    if isinstance(incoming, dict):
        for k, v in incoming.items():
            ui[k] = v
    ui["cdp_port"] = _coerce_int(ui.get("cdp_port"), int(default_ui.get("cdp_port", 9888)))
    ui["speed"] = int(max(0, min(100, _coerce_int(ui.get("speed"), int(default_ui.get("speed", 95))))))
    ui["cdp_enabled"] = _coerce_bool(ui.get("cdp_enabled"), bool(default_ui.get("cdp_enabled", False)))
    ui["adaptive_wait"] = _coerce_bool(ui.get("adaptive_wait"), bool(default_ui.get("adaptive_wait", False)))
    ui["boost_scroll"] = _coerce_bool(ui.get("boost_scroll"), bool(default_ui.get("boost_scroll", True)))
    legacy_copy = ui.get("copy_on_capture") if incoming_has_legacy_copy and not incoming_has_copy_mode else None
    copy_mode_value = ui.get("copy_on_capture_mode") if incoming_has_copy_mode else None
    ui["copy_on_capture_mode"] = normalize_copy_on_capture_mode(
        copy_mode_value,
        legacy_copy,
        str(default_ui.get("copy_on_capture_mode", DEFAULT_COPY_ON_CAPTURE_MODE)),
    )
    ui["copy_on_capture"] = ui["copy_on_capture_mode"] != COPY_ON_CAPTURE_MODE_OFF
    ui["merge_pdf"] = _coerce_bool(ui.get("merge_pdf"), bool(default_ui.get("merge_pdf", True)))
    ui["merge_image"] = _coerce_bool(ui.get("merge_image"), bool(default_ui.get("merge_image", True)))
    ui["dual_output"] = _coerce_bool(ui.get("dual_output"), bool(default_ui.get("dual_output", False)))
    ui["mode"] = _normalize_capture_mode(ui.get("mode"), str(default_ui.get("mode", "框选截图")))
    ui["output_format"] = _coerce_str(ui.get("output_format"), str(default_ui.get("output_format", "png")))
    ui["window_geometry_b64"] = _coerce_str(ui.get("window_geometry_b64"), "")
    ui["resizable_page_sizes"] = _coerce_dict(ui.get("resizable_page_sizes"), {})
    ui["settings_dialog_geometry_b64"] = _coerce_str(ui.get("settings_dialog_geometry_b64"), "")
    ui["window_state"] = _coerce_dict(ui.get("window_state"), dict(default_ui.get("window_state", {"maximized": False})))
    ui["theme"] = _coerce_str(ui.get("theme"), "")
    ui["language"] = _coerce_str(ui.get("language"), "")
    ui["save_mode"] = _normalize_save_mode(ui.get("save_mode"), str(default_ui.get("save_mode", "手动保存")))
    ui["save_button_mode"] = _normalize_save_button_mode(ui.get("save_button_mode"), str(default_ui.get("save_button_mode", "auto")))
    ui["previous_capture_action"] = _normalize_previous_capture_action(ui.get("previous_capture_action"), "pin")
    ui["post_capture_button_style"] = _normalize_post_capture_button_style(ui.get("post_capture_button_style"), str(default_ui.get("post_capture_button_style", "icon")))
    ui["auto_snap_enabled"] = _coerce_bool(ui.get("auto_snap_enabled"), bool(default_ui.get("auto_snap_enabled", True)))
    ui["scroll_hotkey"] = _coerce_str(ui.get("scroll_hotkey"), str(default_ui.get("scroll_hotkey", DEFAULT_SCROLL_HOTKEY)))
    ui["later_read_hotkey"] = _coerce_str(ui.get("later_read_hotkey"), str(default_ui.get("later_read_hotkey", DEFAULT_LATER_READ_HOTKEY)))
    ui["ai_qa_hotkey"] = _coerce_str(ui.get("ai_qa_hotkey"), str(default_ui.get("ai_qa_hotkey", DEFAULT_AI_QA_HOTKEY)))
    ui["selection_translate_hotkey"] = _coerce_str(ui.get("selection_translate_hotkey"), str(default_ui.get("selection_translate_hotkey", DEFAULT_SELECTION_TRANSLATE_HOTKEY)))
    ui["selection_popup_hotkey"] = _coerce_str(ui.get("selection_popup_hotkey"), str(default_ui.get("selection_popup_hotkey", DEFAULT_SELECTION_POPUP_HOTKEY)))
    ui["annotation_style"] = normalize_annotation_style(ui.get("annotation_style"))
    ui["translator"] = normalize_translator_settings(ui.get("translator"))
    ui["network_probe"] = normalize_network_probe_settings(ui.get("network_probe"))
    ui["cat_reminder"] = normalize_cat_reminder_settings(ui.get("cat_reminder"))
    ui["todo_items"] = normalize_todo_items(ui.get("todo_items"))
    ui["later_read"] = normalize_later_read_settings(ui.get("later_read"))
    ui["clipboard_history"] = normalize_clipboard_history_settings(ui.get("clipboard_history"))
    ui["feature_visibility"] = normalize_feature_visibility(ui.get("feature_visibility"))
    ui["logging"] = normalize_log_settings(ui.get("logging"))
    ui["updater"] = normalize_updater_settings(ui.get("updater"))
    ui["data_management"] = normalize_data_management_settings(ui.get("data_management"))
    table_notes = dict(ui.get("table_notes")) if isinstance(ui.get("table_notes"), dict) else {}
    ui["table_notes"] = {
        "table": table_notes.get("table") if isinstance(table_notes.get("table"), list) else [],
        "note_html": str(table_notes.get("note_html", "") or ""),
    }
    qa_window_pos = ui.get("qa_window_pos")
    if isinstance(qa_window_pos, list) and len(qa_window_pos) == 2:
        ui["qa_window_pos"] = [int(qa_window_pos[0]), int(qa_window_pos[1])]
    else:
        ui["qa_window_pos"] = None
    ui["qa_window_pos_user_moved"] = _coerce_bool(ui.get("qa_window_pos_user_moved"), False)
    # 自动迁移历史上的老默认快捷键到新官方标准默认值，避免旧配置残留冲突
    if ui.get("selection_translate_hotkey") == "<alt>+<space>":
        ui["selection_translate_hotkey"] = str(DEFAULT_SELECTION_TRANSLATE_HOTKEY)
    if ui.get("selection_popup_hotkey") == "<alt>+b":
        ui["selection_popup_hotkey"] = str(DEFAULT_SELECTION_POPUP_HOTKEY)

    return ui


_shared_prompt_store: Optional[Any] = None
_shared_prompt_store_app_dir: Optional[str] = None
_shared_prompt_store_lock = threading.RLock()


def _get_shared_prompt_store() -> Any:
    """进程级复用一个 PromptStore，避免每次 load_settings 都重开 SQLite 连接并执行建表脚本。

    实例与当前应用目录绑定（测试会临时切换应用目录，路径变化时重建）；
    所有读取都经 _shared_prompt_store_lock 串行化；外部写入方使用各自独立连接，
    SQLite WAL 下每次 SELECT 都能看到已提交的最新数据。
    """
    global _shared_prompt_store, _shared_prompt_store_app_dir
    with _shared_prompt_store_lock:
        app_dir = str(get_app_dir())
        if _shared_prompt_store is None or _shared_prompt_store_app_dir != app_dir:
            if _shared_prompt_store is not None:
                try:
                    _shared_prompt_store.close()
                except Exception:
                    pass
            from deepcat.prompt_store import PromptStore

            _shared_prompt_store = PromptStore()
            _shared_prompt_store_app_dir = app_dir
        return _shared_prompt_store


def _reset_shared_prompt_store() -> None:
    global _shared_prompt_store, _shared_prompt_store_app_dir
    with _shared_prompt_store_lock:
        if _shared_prompt_store is not None:
            try:
                _shared_prompt_store.close()
            except Exception:
                pass
        _shared_prompt_store = None
        _shared_prompt_store_app_dir = None


def load_settings(*, validate_dirs: bool = False, persist_normalized: bool = False) -> AppSettings:
    """Load settings with a fast, read-only default path.

    Directory writability probes are opt-in because this function is used by UI
    startup, resize/move persistence and save destination lookup.  Pass
    validate_dirs=True only from explicit validation or immediately before a
    save operation.
    """
    default = _defaults()
    p = get_settings_path()
    with _settings_lock:
        data = _read_json(p) if p.exists() else None
        if data is None:
            bak = _settings_bak_path()
            data = _read_json(bak) if bak.exists() else None
            if data is None:
                if persist_normalized:
                    try:
                        _atomic_write_json(p, to_payload(default))
                    except Exception:
                        pass
                return default
            if persist_normalized:
                try:
                    _atomic_write_json(p, data)
                except Exception:
                    pass
    ver = _coerce_int(data.get("version", 1), 1)
    ui_in = data.get("ui", {}) if ver >= 2 else {}
    ui = _merge_ui(default.ui, ui_in)
    try:
        import json
        store = _get_shared_prompt_store()
        with _shared_prompt_store_lock:
            if store.is_empty():
                store.migrate_from_settings(ui)
            db_prompts = store.load_all_prompts()

        # 1. 合并翻译器自定义提示词及按钮名称
        if "translator" in ui and isinstance(ui["translator"], dict):
            translator_prompts = {k: v for k, v in db_prompts.items() if k != "chat_placeholder_cards"}
            ui["translator"].update(translator_prompts)

        # 2. 合并快捷聊天卡片自定义数据
        if "chat_placeholder_cards" in db_prompts:
            try:
                ui["chat_placeholder_cards"] = json.loads(db_prompts["chat_placeholder_cards"])
            except Exception:
                pass
    except Exception as e:
        logger.error("Failed to load or migrate prompts: %s", e)
        # 连接可能已损坏（如库文件被外部删除），重置以便下次调用重建
        _reset_shared_prompt_store()
    if int(ver) < int(SETTINGS_VERSION) and str(ui.get("mode", "")) in {"滚动截屏", "滚动截图"}:
        ui["mode"] = "框选截图"
    if int(ver) < int(SETTINGS_VERSION) and str(ui.get("previous_capture_action", "")).strip().lower() == "close":
        ui["previous_capture_action"] = "pin"
    hotkey = _coerce_str(data.get("hotkey"), default.hotkey)
    if int(ver) < int(SETTINGS_VERSION) and hotkey.strip().lower() == LEGACY_DEFAULT_HOTKEY:
        hotkey = default.hotkey
    s = AppSettings(
        version=int(SETTINGS_VERSION),
        autostart=_coerce_bool(data.get("autostart"), default.autostart),
        auto_save=_coerce_bool(data.get("auto_save"), default.auto_save),
        image_output_dir=_coerce_str(data.get("image_output_dir"), default.image_output_dir),
        pdf_output_dir=_coerce_str(data.get("pdf_output_dir"), default.pdf_output_dir),
        hotkey=hotkey,
        ui=ui,
        notifications_enabled=_coerce_bool(data.get("notifications_enabled"), default.notifications_enabled),
    )
    s = AppSettings(
        version=int(s.version),
        autostart=bool(s.autostart),
        auto_save=bool(s.auto_save),
        image_output_dir=ensure_output_dir(str(s.image_output_dir), default.image_output_dir, validate_writable=validate_dirs),
        pdf_output_dir=ensure_output_dir(str(s.pdf_output_dir), default.pdf_output_dir, validate_writable=validate_dirs),
        hotkey=str(s.hotkey),
        ui={**dict(s.ui), "save_mode": "自动保存" if bool(s.auto_save) else "手动保存"},
        notifications_enabled=bool(s.notifications_enabled),
    )
    if persist_normalized:
        try:
            with _settings_lock:
                _atomic_write_json(p, to_payload(s))
        except Exception:
            pass
    return s


def save_settings(s: AppSettings, *, validate_dirs: bool = False) -> None:
    if validate_dirs:
        default = _defaults()
        s = AppSettings(
            version=int(s.version),
            autostart=bool(s.autostart),
            auto_save=bool(s.auto_save),
            image_output_dir=ensure_output_dir(str(s.image_output_dir), default.image_output_dir, validate_writable=True),
            pdf_output_dir=ensure_output_dir(str(s.pdf_output_dir), default.pdf_output_dir, validate_writable=True),
            hotkey=str(s.hotkey),
            ui=dict(s.ui) if isinstance(s.ui, dict) else {},
            notifications_enabled=bool(getattr(s, "notifications_enabled", False)),
        )
    p = get_settings_path()
    with _settings_lock:
        _atomic_write_json(p, to_payload(s))


def update_settings(mutator: Callable[[AppSettings], Optional[AppSettings]], *, validate_dirs: bool = False) -> AppSettings:
    """统一的 read-modify-write 写入口。

    在进程内锁保护下：读取磁盘上最新的设置 → 应用 mutator → 写盘。
    组件不要长期持有 AppSettings 快照后整体 save_settings —— 那会把快照期间
    其他组件已写入的字段覆盖回旧值。需要写入时传入只修改自己字段的 mutator。
    mutator 返回 None（或原对象）表示放弃本次写入。返回写盘后的设置。
    """
    with _settings_lock:
        current = load_settings()
        updated = mutator(current)
        if updated is None or updated is current:
            return current
        save_settings(updated, validate_dirs=validate_dirs)
        return updated


def update_settings_fields(**fields: Any) -> AppSettings:
    """增量更新 AppSettings 顶层字段，如 update_settings_fields(autostart=True)。"""
    return update_settings(lambda s: dataclass_replace(s, **fields))


def update_ui_settings(**entries: Any) -> AppSettings:
    """增量更新 ui 字典中的键，如 update_ui_settings(theme="dark", translator=translator)。"""

    def _mut(s: AppSettings) -> AppSettings:
        ui = dict(s.ui)
        ui.update(entries)
        return dataclass_replace(s, ui=ui)

    return update_settings(_mut)


def _canonical_model_config(model_name: str, cfg: Any) -> dict[str, Any]:
    raw = dict(cfg) if isinstance(cfg, dict) else {}
    if str(model_name) == "DeepLX":
        if str(raw.get("base_url", "") or "").strip() == DEEPLX_OLD_DEFAULT_BASE_URL:
            raw["base_url"] = DEEPLX_INFO_ADDRESS
        if str(raw.get("model_name", "") or "").strip().lower() == "deeplx":
            raw["model_name"] = "deepLX-free"
    return {
        "base_url": _coerce_str(raw.get("base_url"), ""),
        "model_name": _coerce_str(raw.get("model_name"), str(model_name)),
        "api_key": str(raw.get("api_key", "") or ""),
        "use_proxy": _coerce_bool(raw.get("use_proxy"), False),
        "model_type": infer_translator_model_type(str(model_name), raw),
        "provider": normalize_translator_provider(
            raw.get("provider"),
            infer_translator_model_provider(str(model_name), raw),
        ),
    }


def _settings_ui_payload(ui: Any) -> dict[str, Any]:
    payload = dict(ui) if isinstance(ui, dict) else {}
    translator_raw = payload.get("translator")
    if isinstance(translator_raw, dict):
        translator = dict(translator_raw)
        configs = dict(translator.get("model_configs") or {})
        catalog = default_model_catalog()
        stored_configs: dict[str, Any] = {}
        for name, cfg in configs.items():
            model_name = str(name or "").strip()
            if not model_name:
                continue
            catalog_cfg = catalog.get(model_name)
            if catalog_cfg is not None and _canonical_model_config(model_name, cfg) == _canonical_model_config(model_name, catalog_cfg):
                continue
            stored_configs[model_name] = dict(cfg) if isinstance(cfg, dict) else cfg

        if "local_translation_service_api_key" in translator:
            translator["local_translation_service_api_key"] = str(
                translator.get("local_translation_service_api_key", "") or ""
            )

        if stored_configs:
            translator["model_configs"] = stored_configs
        else:
            translator.pop("model_configs", None)

        # 移除已独立保存在 prompt.db 中的自定义提示词
        from deepcat.prompt_store import PROMPT_DEFAULTS
        for key in PROMPT_DEFAULTS:
            translator.pop(key, None)

        payload["translator"] = translator

    data_management_raw = payload.get("data_management")
    if isinstance(data_management_raw, dict):
        data_management = dict(data_management_raw)
        if "webdav_password" in data_management:
            data_management["webdav_password"] = protect_text(str(data_management.get("webdav_password", "") or ""))
        payload["data_management"] = data_management

    # 移除已独立保存在 prompt.db 中的快捷卡片数据
    payload.pop("chat_placeholder_cards", None)

    return payload


def to_payload(s: AppSettings) -> dict[str, Any]:
    return {
        "version": int(SETTINGS_VERSION),
        "autostart": bool(s.autostart),
        "auto_save": bool(s.auto_save),
        "image_output_dir": str(s.image_output_dir),
        "pdf_output_dir": str(s.pdf_output_dir),
        "hotkey": str(s.hotkey),
        "ui": _settings_ui_payload(s.ui),
        "notifications_enabled": bool(getattr(s, "notifications_enabled", False)),
    }


def encode_qbytearray(b: bytes) -> str:
    if not b:
        return ""
    return base64.b64encode(b).decode("ascii")


def decode_qbytearray(s: str) -> bytes:
    t = str(s or "").strip()
    if not t:
        return b""
    try:
        return base64.b64decode(t.encode("ascii"), validate=True)
    except Exception:
        return b""


def validate_output_dir(path: str) -> tuple[bool, str]:
    p = Path(str(path or "")).expanduser()
    if not p.exists() or not p.is_dir():
        return False, "目录不存在"
    try:
        fd, tmp = tempfile.mkstemp(prefix="ls_write_", dir=str(p))
        os.close(fd)
        os.remove(tmp)
    except Exception as e:
        return False, f"目录不可写：{e}"
    return True, ""


def normalize_output_dir(path: str) -> Optional[str]:
    p = Path(str(path or "")).expanduser()
    try:
        return str(p.resolve())
    except Exception:
        return str(p)


def ensure_output_dir(path: str, fallback: str, *, validate_writable: bool = False) -> str:
    p = Path(str(path or "")).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        p = Path(str(fallback or "")).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            return str(fallback)
    if validate_writable:
        ok, _ = validate_output_dir(str(p))
        if not ok:
            return str(fallback)
    elif not p.exists() or not p.is_dir():
        return str(fallback)
    try:
        return str(p.resolve())
    except Exception:
        return str(p)


def get_image_output_dir(*, validate_writable: bool = False) -> Path:
    s = load_settings()
    return Path(
        ensure_output_dir(
            str(Path(str(s.image_output_dir)).expanduser()),
            _defaults().image_output_dir,
            validate_writable=validate_writable,
        )
    )


def get_pdf_output_dir(*, validate_writable: bool = False) -> Path:
    s = load_settings()
    return Path(
        ensure_output_dir(
            str(Path(str(s.pdf_output_dir)).expanduser()),
            _defaults().pdf_output_dir,
            validate_writable=validate_writable,
        )
    )


def validate_settings_output_dirs(s: Optional[AppSettings] = None) -> dict[str, tuple[bool, str]]:
    current = s or load_settings()
    return {
        "image_output_dir": validate_output_dir(str(current.image_output_dir)),
        "pdf_output_dir": validate_output_dir(str(current.pdf_output_dir)),
    }


def validate_settings_output_dirs_async(
    s: Optional[AppSettings] = None,
    callback: Optional[Callable[[dict[str, tuple[bool, str]]], None]] = None,
) -> threading.Thread:
    current = s or load_settings()

    def run() -> None:
        result = validate_settings_output_dirs(current)
        if callback is not None:
            try:
                callback(result)
            except Exception:
                pass

    thread = threading.Thread(target=run, name="deepcat-settings-dir-validator", daemon=True)
    thread.start()
    return thread
