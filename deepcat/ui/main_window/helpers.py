from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from PyQt6.QtCore import Qt, QDate

from deepcat.ui.main_window._shared import _NOTE_AUTO_LINK_PATTERN, _NOTE_AUTO_LINK_TRAILING_CHARS, _RESOURCE_SHORTCUT_MAX_ITEMS


def _normalize_resource_shortcut_kind(value: Any) -> str:
    kind = str(value or "").strip().lower()
    if kind in {"app", "software", "program", "shortcut", "file", "软件", "快捷方式"}:
        return "app"
    return "url"


def _normalize_resource_shortcut_target(kind: str, target: Any) -> str:
    raw = str(target or "").strip().strip('"').strip()
    if not raw:
        return ""
    if kind == "app":
        return os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))
    url = raw
    if url.lower().startswith("www."):
        url = f"https://{url}"
    elif "://" not in url:
        url = f"https://{url}"
    return url


def _resource_shortcut_default_title(kind: str, target: str) -> str:
    if kind == "app":
        stem = Path(str(target or "")).stem.strip()
        return stem or "软件快捷方式"
    parsed = urlparse(str(target or ""))
    host = (parsed.hostname or parsed.netloc or str(target or "")).strip()
    if host.lower().startswith("www."):
        host = host[4:]
    return host or "网址URL"


def _normalize_resource_shortcuts(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    shortcuts: list[dict[str, str]] = []
    used_ids: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        kind = _normalize_resource_shortcut_kind(raw.get("kind"))
        target = _normalize_resource_shortcut_target(kind, raw.get("target"))
        if not target:
            continue
        if kind == "url":
            parsed = urlparse(target)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
        title = str(raw.get("title") or "").strip() or _resource_shortcut_default_title(kind, target)
        icon_path = str(raw.get("icon_path") or "").strip()
        raw_id = str(raw.get("id") or "").strip()
        shortcut_id = raw_id or hashlib.sha1(f"{kind}\0{target}".encode("utf-8", "ignore")).hexdigest()[:16]
        if shortcut_id in used_ids:
            suffix = 2
            base_id = shortcut_id
            while f"{base_id}-{suffix}" in used_ids:
                suffix += 1
            shortcut_id = f"{base_id}-{suffix}"
        used_ids.add(shortcut_id)
        item = {
            "id": shortcut_id,
            "kind": kind,
            "title": title[:40],
            "target": target,
        }
        if icon_path:
            item["icon_path"] = icon_path
        shortcuts.append(item)
        if len(shortcuts) >= _RESOURCE_SHORTCUT_MAX_ITEMS:
            break
    return shortcuts


def _note_auto_link_parts(text: str) -> list[tuple[str, str, str]]:
    """拆分笔记粘贴文本中的链接，返回 (kind, text, href)。"""
    raw_text = str(text or "")
    if not raw_text:
        return []

    parts: list[tuple[str, str, str]] = []
    link_found = False
    last_end = 0
    for match in _NOTE_AUTO_LINK_PATTERN.finditer(raw_text):
        link_text = match.group(1)
        trimmed_link = link_text.rstrip(_NOTE_AUTO_LINK_TRAILING_CHARS)
        trailing = link_text[len(trimmed_link):]
        if not trimmed_link:
            continue

        if match.start() > last_end:
            parts.append(("text", raw_text[last_end:match.start()], ""))

        href = trimmed_link
        if href.lower().startswith("www."):
            href = f"https://{href}"
        parts.append(("link", trimmed_link, href))
        link_found = True

        if trailing:
            parts.append(("text", trailing, ""))
        last_end = match.end()

    if not link_found:
        return []
    if last_end < len(raw_text):
        parts.append(("text", raw_text[last_end:], ""))
    return parts


def _date_key(date: QDate) -> str:
    return date.toString(Qt.DateFormat.ISODate)


def _safe_copy(obj: object) -> object:
    """对 dataclass 对象做浅拷贝，防止后续修改影响已 emit 的信号数据。"""
    try:
        from dataclasses import fields, is_dataclass
        if is_dataclass(obj) and not isinstance(obj, type):
            return type(obj)(**{f.name: getattr(obj, f.name) for f in fields(obj)})
    except Exception:
        pass
    return obj
