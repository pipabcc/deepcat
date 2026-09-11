"""主窗口全局搜索的纯文本处理逻辑。"""

from __future__ import annotations

import html


def search_highlight_terms(keyword: str) -> list[str]:
    compact = " ".join(str(keyword or "").split())
    if not compact:
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for term in [compact, *compact.split()]:
        key = term.casefold()
        if key and key not in seen:
            seen.add(key)
            terms.append(term)
    return terms


def first_search_match(text: str, terms: list[str]) -> tuple[int, int]:
    haystack = str(text or "")
    lowered = haystack.casefold()
    best_start = -1
    best_len = 0
    for term in terms:
        needle = str(term or "").casefold()
        if not needle:
            continue
        start = lowered.find(needle)
        if start < 0:
            continue
        if best_start < 0 or start < best_start or (start == best_start and len(needle) > best_len):
            best_start = start
            best_len = len(str(term or ""))
    return best_start, best_len


def search_match_snippet(record: dict, keyword: str, limit: int = 64) -> str:
    limit = max(12, int(limit))
    terms = search_highlight_terms(keyword)
    candidates = (
        str(record.get("result_text") or ""),
        str(record.get("source_text") or ""),
        str(record.get("prompt_text") or ""),
    )
    if terms:
        for raw_text in candidates:
            text = " ".join(str(raw_text or "").split())
            if not text:
                continue
            start, length = first_search_match(text, terms)
            if start < 0:
                continue
            half = max(4, int((limit - max(1, length)) / 2))
            snippet_start = max(0, start - half)
            snippet_end = min(len(text), snippet_start + limit)
            snippet_start = max(0, min(snippet_start, snippet_end - limit))
            prefix = "..." if snippet_start > 0 else ""
            suffix = "..." if snippet_end < len(text) else ""
            return f"{prefix}{text[snippet_start:snippet_end]}{suffix}"

    for raw_text in candidates:
        text = " ".join(str(raw_text or "").split())
        if text:
            return text[:limit] + ("..." if len(text) > limit else "")
    return "命中完整对话内容"


def highlight_search_html(text: str, keyword: str) -> str:
    value = str(text or "")
    terms = search_highlight_terms(keyword)
    if not value or not terms:
        return html.escape(value)
    ranges: list[tuple[int, int]] = []
    lowered = value.casefold()
    for term in terms:
        needle = str(term or "").casefold()
        if not needle:
            continue
        start = 0
        while True:
            pos = lowered.find(needle, start)
            if pos < 0:
                break
            end = pos + len(str(term or ""))
            if end <= len(value):
                ranges.append((pos, end))
            start = max(pos + 1, end)
    if not ranges:
        return html.escape(value)

    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))

    parts: list[str] = []
    cursor = 0
    for start, end in merged:
        parts.append(html.escape(value[cursor:start]))
        parts.append(
            "<span style='background-color:#fff2a8;color:#0f172a;border-radius:3px;'>"
            f"{html.escape(value[start:end])}</span>"
        )
        cursor = end
    parts.append(html.escape(value[cursor:]))
    return "".join(parts)


def search_display_model_name(record: dict) -> str:
    model_name = " ".join(str(record.get("model_name") or "").split())
    if " | " in model_name:
        model_name = model_name.split(" | ", 1)[0].strip()
    return model_name
