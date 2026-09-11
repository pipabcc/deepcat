from __future__ import annotations

import logging
from PyQt6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PyQt6.QtWidgets import QMenu
from deepcat.settings_store import infer_translator_model_provider, normalize_translator_provider
from deepcat.utils.logger import get_log_dir, get_logger
from PyQt6.QtCore import Qt
from PyQt6.QtCore import QPointF, QPoint, QRect, QSize, Qt

from deepcat.ui.post_capture_actions._shared import (
    _AI_GEOMETRY_TRACE_ENABLED,
    _MODEL_MENU_EXCLUDED_QA_MODELS,
    _ROUNDED_POPUP_MENU_MAX_HEIGHT,
    _ai_history_debug_logger,
)


_ai_geometry_logger = None


def _log_ai_history_debug(message: str, *args) -> None:
    try:
        _ai_history_debug_logger.info(message, *args)
    except Exception:
        pass


def _get_ai_geometry_logger():
    global _ai_geometry_logger
    if _ai_geometry_logger is None:
        _ai_geometry_logger = get_logger(
            "deepcat.ai_geometry",
            level=logging.INFO,
            enable_console=False,
            enable_file=True,
            file_path=get_log_dir() / "ai_window_geometry.log",
        )
        for handler in _ai_geometry_logger.handlers:
            handler.setFormatter(logging.Formatter("%(message)s"))
    return _ai_geometry_logger


def _rect_to_log_value(rect) -> dict[str, int] | str:
    try:
        return {
            "x": int(rect.x()),
            "y": int(rect.y()),
            "w": int(rect.width()),
            "h": int(rect.height()),
        }
    except Exception:
        return str(rect)


def _point_to_log_value(point) -> dict[str, int] | str:
    try:
        return {"x": int(point.x()), "y": int(point.y())}
    except Exception:
        return str(point)


def _size_to_log_value(size) -> dict[str, int] | str:
    try:
        return {"w": int(size.width()), "h": int(size.height())}
    except Exception:
        return str(size)


def _normalize_ai_log_value(value):
    try:
        if isinstance(value, QRect):
            return _rect_to_log_value(value)
        if isinstance(value, QPoint):
            return _point_to_log_value(value)
        if isinstance(value, QPointF):
            return {"x": round(float(value.x()), 2), "y": round(float(value.y()), 2)}
        if isinstance(value, QSize):
            return _size_to_log_value(value)
        if isinstance(value, dict):
            return {str(k): _normalize_ai_log_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_normalize_ai_log_value(v) for v in value]
    except Exception:
        return str(value)
    return value


def _trace_ai_panel(panel, event_name: str, **fields) -> None:
    if not _AI_GEOMETRY_TRACE_ENABLED:
        return
    tracer = getattr(panel, "_log_ai_geometry", None)
    if callable(tracer):
        tracer(event_name, **fields)


def _preview_log_text(value: object, limit: int = 180) -> str:
    text = str(value or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _history_text_has_image(value: object) -> bool:
    text = str(value or "")
    return any(token in text for token in ("![generated image](", "[generated image](", "file://", "data:image/"))


def wrap_error_message(text: str, max_width: int = 50, max_lines: int = 8) -> str:
    if not text:
        return ""
    paragraphs = text.split("\n")
    final_lines = []
    for p in paragraphs:
        if not p.strip():
            final_lines.append("")
            continue
        current_line = []
        current_len = 0
        i = 0
        while i < len(p):
            char = p[i]
            char_w = 2 if ord(char) > 127 else 1
            if current_len + char_w > max_width:
                break_idx = -1
                for offset in range(1, min(10, len(current_line))):
                    c = current_line[-offset]
                    if c in (" ", ",", ";", ":", ")", "]", "}", "，", "；", "：", "）"):
                        break_idx = len(current_line) - offset
                        break
                if break_idx != -1:
                    left = current_line[:break_idx]
                    right = current_line[break_idx:]
                    left_str = "".join(left).strip()
                    if left_str:
                        final_lines.append(left_str)
                    current_line = right + [char]
                    while current_line and current_line[0] == " ":
                        current_line.pop(0)
                    current_len = sum(2 if ord(c) > 127 else 1 for c in current_line)
                else:
                    final_lines.append("".join(current_line).strip())
                    current_line = [char]
                    current_len = char_w
            else:
                current_line.append(char)
                current_len += char_w
            i += 1
        if current_line:
            final_lines.append("".join(current_line).strip())

    cleaned_lines = [line.strip() for line in final_lines]
    if len(cleaned_lines) > max_lines:
        return "\n".join(cleaned_lines[:max_lines]) + "\n..."
    return "\n".join(cleaned_lines)


def classify_ocr_error_message(message: str) -> str:
    msg = str(message or "").strip()
    if not msg:
        return ""

    lower = msg.lower()
    if "write_tmp_failed" in lower:
        return "OCR临时图片写入失败，请检查临时目录权限或磁盘空间"
    if "timed out" in lower or "timeoutexpired" in lower or "timeout" in lower:
        return "OCR识别超时，请缩小截图区域或稍后重试"
    if "rapidocr_onnxruntime" in lower and (
        "modulenotfounderror" in lower or "no module named" in lower
    ):
        return "OCR组件缺失：未安装 rapidocr-onnxruntime"
    if ("paddleocr" in lower or "paddlex" in lower) and (
        "modulenotfounderror" in lower or "no module named" in lower
    ):
        return "PP-OCRv6组件缺失：未安装 PaddleOCR 3.7 运行依赖"
    if "onnxruntime" in lower and (
        "modulenotfounderror" in lower or "no module named" in lower
    ):
        return "OCR推理运行时缺失：未安装 onnxruntime"
    if "onnxruntime_pybind11_state" in lower or "onnxruntime.dll" in lower:
        if "dll load failed" in lower or "动态链接库" in lower or "initialization routine" in lower:
            return "OCR运行库加载失败：ONNX Runtime DLL 初始化失败，请检查 VC++ 运行库版本或 DLL 搜索路径"
    if ("cv2" in lower or "opencv" in lower) and "dll load failed" in lower:
        return "OCR图像处理库加载失败：OpenCV DLL 加载失败"
    if "dll load failed" in lower or "动态链接库" in lower:
        return "OCR原生依赖加载失败：DLL 加载失败"
    if "pp-ocrv6" in lower and (
        "缺失" in msg or "不存在" in msg or "校验失败" in msg or "不匹配" in msg or "not found" in lower
    ):
        return "PP-OCRv6 Small 模型文件缺失或损坏，请重新安装 DeepCat"
    if (".onnx" in lower or "ch_ppocr" in lower or "rapidocr_onnxruntime" in lower) and (
        "no such file" in lower or "filenotfounderror" in lower or "not found" in lower
    ):
        return "OCR模型文件缺失或损坏，请重新安装 rapidocr-onnxruntime"
    if "permission denied" in lower or "access is denied" in lower or "拒绝访问" in lower:
        return "OCR文件访问失败，请检查临时目录权限"
    if "out of memory" in lower or "bad allocation" in lower or "memoryerror" in lower:
        return "OCR内存不足，请缩小截图区域后重试"
    if "importerror" in lower:
        return f"OCR组件导入失败：{msg[:160]}"
    return f"OCR引擎错误：{msg[:200]}"


def _group_translator_model_menu_items(translator: dict[str, object], purpose: str) -> list[tuple[str, list[str]]]:
    configs = dict(translator.get("model_configs") or {})
    grouped: dict[str, list[str]] = {}
    for raw_name, raw_cfg in configs.items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        if str(purpose or "") == "qa" and name in _MODEL_MENU_EXCLUDED_QA_MODELS:
            continue
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
        provider = normalize_translator_provider(cfg.get("provider"), infer_translator_model_provider(name, cfg))
        grouped.setdefault(provider, []).append(name)

    providers = sorted(grouped)
    if "其它" in providers:
        providers.remove("其它")
        providers.append("其它")
    return [(provider, sorted(grouped[provider])) for provider in providers if grouped.get(provider)]


def _translator_model_search_text(model_name: str, cfg: object, provider: str = "") -> str:
    cfg_dict = cfg if isinstance(cfg, dict) else {}
    parts = [
        str(model_name or ""),
        str(cfg_dict.get("display_name", "") or ""),
        str(cfg_dict.get("model_name", "") or ""),
        str(cfg_dict.get("base_url", "") or ""),
        str(cfg_dict.get("api_key", "") or ""),
        str(provider or cfg_dict.get("provider", "") or ""),
        str(cfg_dict.get("model_type", "") or ""),
        str(cfg_dict.get("proxy_url", "") or ""),
    ]
    return " ".join(part.strip() for part in parts if part and part.strip())


def _style_rounded_popup_menu(menu: QMenu, *, group_headers: bool = False) -> None:
    flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
    try:
        flags |= Qt.WindowType.NoDropShadowWindowHint
    except AttributeError:
        pass
    menu.setWindowFlags(flags)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    menu.setMaximumHeight(_ROUNDED_POPUP_MENU_MAX_HEIGHT)

    item_padding = "0 12px" if not group_headers else "0 12px 0 22px"
    disabled_style = (
        "color: #4b5563; font-weight: 700; padding-left: 6px;"
        if group_headers
        else "color: #94a3b8; font-weight: 500;"
    )
    menu.setStyleSheet(f"""
        QMenu {{
            background-color: #ffffff;
            border: 1px solid #dfe4ec;
            border-radius: 8px;
            padding: 6px;
            margin: 0px;
            max-height: {_ROUNDED_POPUP_MENU_MAX_HEIGHT}px;
        }}
        QMenu::item {{
            min-height: 32px;
            padding: {item_padding};
            border-radius: 6px;
            color: #374151;
            font-size: 13px;
            font-weight: 500;
        }}
        QMenu::item:selected {{
            background-color: #f3f4f6;
            color: #111827;
        }}
        QMenu::item:checked {{
            background-color: #e5e7eb;
            color: #111827;
            font-weight: 800;
        }}
        QMenu::item:disabled {{
            background: transparent;
            {disabled_style}
        }}
        QMenu::separator {{
            height: 2px;
            margin: 4px 6px;
            background: transparent;
        }}
    """)
