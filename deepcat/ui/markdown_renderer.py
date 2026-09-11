"""健壮的 Markdown -> Qt 富文本子集 HTML 转换器。

设计目标（替代脆弱的 QTextDocument.setMarkdown）：
- 纯 Python、零依赖，输出可直接喂给 QLabel.setText() / QTextEdit.setHtml() 的富文本片段；
- **先转义后解析**，任何输入的 ``<>&`` 都不会泄漏成标签（防注入）；
- **流式容错**：未闭合的 ``**`` / ``*`` / ``` `` ``` 按字面字符显示，绝不吐半个标签；
  未闭合的围栏代码块按已收集内容正常渲染；
- **绝不全局崩坏**：单行解析异常降级为普通段落，整体兜底为纯文本 + <br>。

QLabel 的富文本基于 QTextDocument，CSS 支持有限且**不吃外部样式表**，所以所有样式
一律内联书写；气泡外框的圆角由承载它的 QFrame 的 QSS 提供，不依赖此处。
"""

from __future__ import annotations

import html
import re
from typing import Optional

from deepcat.ui.latex_renderer import LatexRenderer

# ---- 内联样式常量（沿用 post_capture_actions 既有视觉风格）----
_P_STYLE = (
    "margin:4px 0 8px 0; line-height:165%;"
    " font-family:'Microsoft YaHei','Segoe UI',system-ui; font-size:14px; color:#1e293b;"
)
_LI_STYLE = (
    "margin:3px 0; line-height:160%;"
    " font-family:'Microsoft YaHei','Segoe UI',system-ui; font-size:14px; color:#1e293b;"
)
_UL_STYLE = "margin:6px 0 8px 0;"
_CODE_STYLE = (
    "font-family:'Consolas','Courier New',monospace; background-color:#f1f5f9;"
    " color:#0f172a; padding:2px 6px; font-size:13px;"
)
_PRE_STYLE = (
    "font-family:'Consolas','Courier New',monospace; background-color:#f8fafc;"
    " color:#0f172a; border:1px solid #e2e8f0; padding:8px 12px; margin:6px 0;"
    " font-size:13px; line-height:150%;"
)
_BQ_STYLE = (
    "border-left:3px solid #cbd5e1; padding:2px 0 2px 12px; margin:6px 0; color:#64748b;"
    " font-family:'Microsoft YaHei','Segoe UI',system-ui; font-size:14px; line-height:160%;"
)
_TABLE_STYLE = (
    "border-collapse:collapse; margin:6px 0 10px 0;"
    " font-family:'Microsoft YaHei','Segoe UI',system-ui; font-size:13px; color:#1e293b;"
)
_TH_STYLE = (
    "background-color:#f8fafc; font-weight:bold; color:#0f172a;"
    " padding:6px 8px; line-height:150%;"
)
_TD_STYLE = "padding:6px 8px; line-height:150%; vertical-align:top;"
_HR_STYLE = "border:none; border-top:1px solid #e2e8f0; margin:10px 0;"
_LINK_STYLE = "color:#2563eb; text-decoration:none;"
_H_STYLE = {
    1: "font-size:17px; font-weight:bold; color:#0f172a; margin:14px 0 8px 0;",
    2: "font-size:15px; font-weight:bold; color:#0f172a; margin:12px 0 6px 0;",
    3: "font-size:14px; font-weight:bold; color:#1e293b; margin:10px 0 6px 0;",
    4: "font-size:14px; font-weight:bold; color:#1e293b; margin:8px 0 4px 0;",
    5: "font-size:13px; font-weight:bold; color:#334155; margin:8px 0 4px 0;",
    6: "font-size:13px; font-weight:bold; color:#475569; margin:8px 0 4px 0;",
}

# ---- 块级识别 ----
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_BQ_RE = re.compile(r"^\s*>\s?(.*)$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^:?-{3,}:?$")
_LOOSE_HEADING_RE = re.compile(r"^(\s*)(#{1,6})([^#\s].*)$")
_ATTACHED_HR_RE = re.compile(r"^\s*([-*_])\1{5,}\s+(.+\S)\s*$")

# ---- 行内识别（在已 HTML 转义的文本上运行）----
_IMAGE_RE = re.compile(
    r"!\[([^\]]*)\]\("
    r"((?:data:image/(?:png|jpe?g|webp|gif|bmp|avif);base64,[A-Za-z0-9+/=]+|file://[^)]+?\.(?:png|jpe?g|webp|gif|bmp|avif)(?:[?#][^)]*)?|[A-Za-z]:[\\/][^)]+?\.(?:png|jpe?g|webp|gif|bmp|avif)))"
    r"\)",
    re.IGNORECASE,
)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_CODE_INLINE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_DEL_RE = re.compile(r"~~(.+?)~~")
# 单星斜体：两侧不能再是星号，避免吃掉粗体残留；故意不支持 ``_`` 斜体，避免 snake_case 误伤
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+?)\*(?!\*)")
_URL_OK_RE = re.compile(r"^(?:https?://|mailto:|deepcat-image-preview:)", re.IGNORECASE)
_DATA_IMAGE_OK_RE = re.compile(r"^data:image/(?:png|jpe?g|webp|gif|bmp|avif);base64,[A-Za-z0-9+/=]+$", re.IGNORECASE)
_FILE_IMAGE_OK_RE = re.compile(r"^(?:file://[^)]+?\.(?:png|jpe?g|webp|gif|bmp|avif)(?:[?#][^)]*)?|[A-Za-z]:[\\/][^)]+?\.(?:png|jpe?g|webp|gif|bmp|avif))$", re.IGNORECASE)

_CODE_TOKEN_RE = re.compile(r"\x00C(\d+)\x00")
_MATH_TOKEN_RE = re.compile(r"\x00M(\d+)\x00")
_CITATION_MARKER_RE = re.compile(r"\ue200cite\ue202([^\ue201]+)\ue201")
_MANGLED_CITATION_MARKER_RE = re.compile(r"(?:■|\u25a0)?cite(?:☆|\u2606)([a-zA-Z0-9☆\u2606]+)(?:↩|\u21a9)?")
_ENTITY_RE = re.compile(r"(?:■|\u25a0|\ue200)entity(?:☆|\u2606|\ue202)(.*?)(?:↩|\u21a9|\ue201)")
_URL_RE = re.compile(r"(?:■|\u25a0|\ue200)url(?:☆|\u2606|\ue202)([^\ue202\u2606\n]*?)(?:☆|\u2606|\ue202)([^\ue201\u21a9\n]*?)(?:↩|\u21a9|\ue201)")
_ANY_MANGLED_MARKER_RE = re.compile(r"(?:■|\u25a0|\ue200)[a-zA-Z_]+(?:☆|\u2606|\ue202)(.*?)(?:↩|\u21a9|\ue201)")
_AI_COMPONENT_HINT_RE = re.compile(
    r"</?\s*Timeline(?:Event)?\b|"
    r"</?\s*(?:Sequence|Step)\b|"
    r"</?\s*ElicitationsGroup\b|<\s*(?:Elicitation|FollowUp)\b|"
    r"\{\s*/\s*Reason\s*:",
    re.IGNORECASE,
)
_AI_REASON_RE = re.compile(r"\{\s*/\s*Reason\s*:.*?/\s*\}", re.IGNORECASE | re.DOTALL)
_TIMELINE_CONTAINER_RE = re.compile(r"</?\s*Timeline\s*>", re.IGNORECASE)
_TIMELINE_EVENT_START_RE = re.compile(r"<\s*TimelineEvent\b([^>]*)>", re.IGNORECASE | re.DOTALL)
_TIMELINE_EVENT_END_RE = re.compile(r"</\s*TimelineEvent\s*>", re.IGNORECASE)
_SEQUENCE_CONTAINER_RE = re.compile(r"<\s*/?\s*Sequence\b[^>]*>", re.IGNORECASE)
_STEP_START_RE = re.compile(r"<\s*Step\b([^>]*)>", re.IGNORECASE | re.DOTALL)
_STEP_END_RE = re.compile(r"</\s*Step\s*>", re.IGNORECASE)
_ELICITATIONS_GROUP_OPEN_RE = re.compile(r"<\s*ElicitationsGroup\b([^>]*)>", re.IGNORECASE | re.DOTALL)
_ELICITATIONS_GROUP_CLOSE_RE = re.compile(r"</\s*ElicitationsGroup\s*>", re.IGNORECASE)
_ELICITATION_TAG_RE = re.compile(r"<\s*Elicitation\b([^>]*)/?>", re.IGNORECASE | re.DOTALL)
_FOLLOW_UP_TAG_RE = re.compile(r"<\s*FollowUp\b([^>]*)/?>", re.IGNORECASE | re.DOTALL)
_AI_COMPONENT_ATTR_RE = re.compile(r"([A-Za-z_][\w:-]*)\s*=\s*([\"'])(.*?)\2", re.DOTALL)
_FOLLOW_UP_COMPONENT_LINE_RE = re.compile(
    r"^\s*(?:[-*+]\s+|\d{1,3}[.)、]\s+)?(?P<label>[^：:\n]{2,180}?)[：:](?P<query>\s*\S.*)$"
)
_FOLLOW_UP_COMPONENT_LABEL_PREFIXES = (
    "想深入了解",
    "深入了解",
    "详细了解",
    "进一步了解",
    "继续了解",
    "想了解",
    "了解",
    "看一看",
    "看看",
    "查看",
    "详细分析",
)
_FOLLOW_UP_COMPONENT_MESSAGE_PREFIXES = ("想要深入了解", "如果您想继续", "可继续了解", "可以继续了解")


def _clean_entities_and_urls(text: str) -> str:
    if not text:
        return text

    def _replace_entity(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        import json
        try:
            data = json.loads(content)
            if isinstance(data, list):
                if len(data) >= 2:
                    return str(data[1])
                elif len(data) == 1:
                    return str(data[0])
        except Exception:
            pass
        names = re.findall(r'"([^"]+)"', content)
        if names:
            if len(names) >= 2:
                return names[1]
            return names[0]
        return content

    def _replace_url(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        url = match.group(2).strip()
        if label and url:
            return f"[{label} ↗]({url})"
        elif url:
            return f"[{url} ↗]({url})"
        return label or url

    result = _ENTITY_RE.sub(_replace_entity, text)
    result = _URL_RE.sub(_replace_url, result)
    result = _ANY_MANGLED_MARKER_RE.sub("", result)
    return result


def _is_escaped(text: str, idx: int) -> bool:
    slash_count = 0
    pos = int(idx) - 1
    while pos >= 0 and text[pos] == "\\":
        slash_count += 1
        pos -= 1
    return bool(slash_count % 2)


def _normalize_citation_markers(text: str) -> str:
    """把模型私有 citation 占位符转成可读文本，避免 Qt 字体显示成乱码符号。"""

    def _replace(match: "re.Match[str]") -> str:
        return ""

    normalized = _CITATION_MARKER_RE.sub(_replace, str(text or ""))
    normalized = _MANGLED_CITATION_MARKER_RE.sub(_replace, normalized)
    normalized = _clean_entities_and_urls(normalized)
    return normalized


def _parse_ai_component_attrs(raw_attrs: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _AI_COMPONENT_ATTR_RE.finditer(str(raw_attrs or "")):
        key = match.group(1).strip().lower()
        value = html.unescape(match.group(3)).strip()
        if key:
            attrs[key] = value
    return attrs


def _strip_timeline_control_markup(text: str) -> str:
    cleaned = _AI_REASON_RE.sub("", str(text or ""))
    cleaned = _TIMELINE_CONTAINER_RE.sub("", cleaned)
    cleaned = _TIMELINE_EVENT_END_RE.sub("", cleaned)
    return cleaned


def _timeline_event_to_markdown(raw_attrs: str, raw_body: str) -> str:
    attrs = _parse_ai_component_attrs(raw_attrs)
    time_text = attrs.get("time", "")
    title_text = attrs.get("title", "")
    body = _strip_timeline_control_markup(raw_body).strip()
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)

    if time_text and title_text:
        heading = f"{time_text}：{title_text}"
    else:
        heading = title_text or time_text or "时间线事件"

    parts = [f"### {heading}"]
    if body:
        parts.append(body)
    return "\n\n".join(parts)


def _normalize_timeline_chunk(text: str) -> str:
    chunk = str(text or "")
    event_starts = list(_TIMELINE_EVENT_START_RE.finditer(chunk))
    if not event_starts:
        return _strip_timeline_control_markup(chunk)

    out: list[str] = []
    pos = 0
    for idx, event_start in enumerate(event_starts):
        prefix = _strip_timeline_control_markup(chunk[pos : event_start.start()]).strip()
        if prefix:
            out.append(prefix)

        next_start = event_starts[idx + 1].start() if idx + 1 < len(event_starts) else len(chunk)
        event_end = _TIMELINE_EVENT_END_RE.search(chunk, event_start.end())
        if event_end is not None and event_end.start() < next_start:
            body_end = event_end.start()
            pos = event_end.end()
        else:
            body_end = next_start
            pos = body_end

        event_md = _timeline_event_to_markdown(event_start.group(1), chunk[event_start.end() : body_end])
        if event_md.strip():
            out.append(event_md.strip())

    suffix = _strip_timeline_control_markup(chunk[pos:]).strip()
    if suffix:
        out.append(suffix)

    return "\n\n".join(out)


def _strip_sequence_control_markup(text: str) -> str:
    cleaned = _AI_REASON_RE.sub("", str(text or ""))
    cleaned = _SEQUENCE_CONTAINER_RE.sub("", cleaned)
    cleaned = _STEP_END_RE.sub("", cleaned)
    return cleaned


def _sequence_step_to_markdown(raw_attrs: str, raw_body: str, step_number: int) -> str:
    attrs = _parse_ai_component_attrs(raw_attrs)
    title = re.sub(r"\s+", " ", str(attrs.get("title") or "")).strip()
    subtitle = re.sub(r"\s+", " ", str(attrs.get("subtitle") or "")).strip()
    body = _strip_sequence_control_markup(raw_body).strip()
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)

    heading = title or f"步骤 {step_number}"
    parts = [f"### {step_number}. {heading}"]
    if subtitle:
        parts.append(f"> {subtitle}")
    if body:
        parts.append(body)
    return "\n\n".join(parts)


def _normalize_sequence_chunk(text: str) -> str:
    chunk = str(text or "")
    step_starts = list(_STEP_START_RE.finditer(chunk))
    if not step_starts:
        return _strip_sequence_control_markup(chunk)

    out: list[str] = []
    pos = 0
    for idx, step_start in enumerate(step_starts):
        prefix = _strip_sequence_control_markup(chunk[pos : step_start.start()]).strip()
        if prefix:
            out.append(prefix)

        next_start = step_starts[idx + 1].start() if idx + 1 < len(step_starts) else len(chunk)
        step_end = _STEP_END_RE.search(chunk, step_start.end())
        if step_end is not None and step_end.start() < next_start:
            body_end = step_end.start()
            pos = step_end.end()
        else:
            body_end = next_start
            pos = body_end

        step_md = _sequence_step_to_markdown(
            step_start.group(1),
            chunk[step_start.end() : body_end],
            idx + 1,
        )
        if step_md.strip():
            out.append(step_md.strip())

    suffix = _strip_sequence_control_markup(chunk[pos:]).strip()
    if suffix:
        out.append(suffix)

    return "\n\n".join(out)


def _format_elicitation_markdown(raw_attrs: str) -> str:
    attrs = _parse_ai_component_attrs(raw_attrs)
    label = str(attrs.get("label") or "").strip()
    query = str(attrs.get("query") or "").strip()
    if label and query and label != query:
        return f"- {label}：{query}"
    if label or query:
        return f"- {label or query}"
    return ""


def _follow_up_component_dedupe_key(line: str) -> str:
    text = re.sub(r"\s+", " ", str(line or "").strip())
    if not text:
        return ""
    item_match = _FOLLOW_UP_COMPONENT_LINE_RE.match(text)
    if item_match:
        label = str(item_match.group("label") or "").strip()
        query = str(item_match.group("query") or "").strip()
        if any(label.startswith(prefix) for prefix in _FOLLOW_UP_COMPONENT_LABEL_PREFIXES):
            label_key = re.sub(r"\s+", "", label)
            query_key = re.sub(r"\s+", "", query)
            return f"item:{label_key}\0{query_key}"
    if any(text.startswith(prefix) for prefix in _FOLLOW_UP_COMPONENT_MESSAGE_PREFIXES):
        return "message:" + re.sub(r"\s+", "", text)
    return ""


def _dedupe_follow_up_component_lines(text: str) -> str:
    lines = str(text or "").splitlines()
    last_index_by_key: dict[str, int] = {}
    keys: list[str] = []
    for idx, line in enumerate(lines):
        key = _follow_up_component_dedupe_key(line)
        keys.append(key)
        if key:
            last_index_by_key[key] = idx

    out: list[str] = []
    for idx, line in enumerate(lines):
        key = keys[idx]
        if key and last_index_by_key.get(key) != idx:
            continue
        out.append(line)
    return "\n".join(out)


def _normalize_elicitations_chunk(text: str) -> str:
    chunk = _AI_REASON_RE.sub("", str(text or ""))
    chunk = _ELICITATIONS_GROUP_OPEN_RE.sub(
        lambda match: str(_parse_ai_component_attrs(match.group(1)).get("message") or "").strip(),
        chunk,
    )
    chunk = _ELICITATIONS_GROUP_CLOSE_RE.sub("", chunk)
    chunk = _ELICITATION_TAG_RE.sub(lambda match: _format_elicitation_markdown(match.group(1)), chunk)
    chunk = _FOLLOW_UP_TAG_RE.sub(lambda match: _format_elicitation_markdown(match.group(1)), chunk)
    return _dedupe_follow_up_component_lines(chunk)


def _normalize_ai_component_chunk(text: str) -> str:
    chunk = _normalize_timeline_chunk(str(text or ""))
    chunk = _normalize_sequence_chunk(chunk)
    return _normalize_elicitations_chunk(chunk)


def _normalize_ai_component_tags(text: str) -> str:
    """把模型输出的 UI 组件标签降级为普通 Markdown，且不触碰围栏代码块。"""
    source = str(text or "")
    if not _AI_COMPONENT_HINT_RE.search(source):
        return source

    out: list[str] = []
    text_buf: list[str] = []
    code_buf: list[str] = []
    in_fence = False

    def flush_text() -> None:
        if text_buf:
            out.append(_normalize_ai_component_chunk("".join(text_buf)))
            text_buf.clear()

    for line in source.splitlines(keepends=True):
        if _FENCE_RE.match(line):
            if in_fence:
                code_buf.append(line)
                out.append("".join(code_buf))
                code_buf.clear()
                in_fence = False
            else:
                flush_text()
                in_fence = True
                code_buf.append(line)
            continue

        if in_fence:
            code_buf.append(line)
        else:
            text_buf.append(line)

    if in_fence and code_buf:
        out.append("".join(code_buf))
    flush_text()
    return "".join(out)


def _normalize_loose_markdown(text: str) -> str:
    """保守修正常见的模型 Markdown 小偏差，不依赖提供商或模型名称。"""
    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    in_fence = False

    for raw_line in source.split("\n"):
        if _FENCE_RE.match(raw_line):
            in_fence = not in_fence
            out.append(raw_line)
            continue
        if in_fence:
            out.append(raw_line)
            continue

        line = raw_line
        heading = _LOOSE_HEADING_RE.match(line)
        if heading:
            line = f"{heading.group(1)}{heading.group(2)} {heading.group(3).lstrip()}"

        # 只有明显呈现多列表格结构时才把全角竖线转成 Markdown 分隔符。
        if line.count("｜") >= 2 or ("|" in line and "｜" in line):
            line = line.replace("｜", "|")

        # 某些模型会把分隔线和下一段粘在同一行；六个以上同类符号是足够强的证据。
        attached_hr = _ATTACHED_HR_RE.match(line)
        if attached_hr:
            out.append(attached_hr.group(1) * 3)
            out.append(attached_hr.group(2).lstrip())
            continue

        # 宽松表格分隔行：允许全角冒号、Unicode 横线以及多余空格。
        if "|" in line:
            cells = _split_table_row(line)
            normalized_cells: list[str] = []
            separator_like = len(cells) >= 2
            for cell in cells:
                token = cell.strip().replace("：", ":").replace("–", "-").replace("—", "-")
                compact = token.replace(" ", "")
                if not re.fullmatch(r":?-{3,}:?", compact):
                    separator_like = False
                    break
                normalized_cells.append(compact)
            if separator_like:
                line = "| " + " | ".join(normalized_cells) + " |"

        out.append(line)
    return "\n".join(out)


def _find_unescaped(text: str, token: str, start: int) -> int:
    pos = int(start)
    while pos < len(text):
        hit = text.find(token, pos)
        if hit < 0:
            return -1
        if not _is_escaped(text, hit):
            return hit
        pos = hit + len(token)
    return -1


def _looks_like_inline_math(content: str) -> bool:
    s = str(content or "")
    if not s or "\n" in s:
        return False
    if s[0].isspace() or s[-1].isspace():
        return False
    if re.fullmatch(r"\d+(?:[.,]\d+)?", s):
        return False
    return bool(re.search(r"\\[A-Za-z]+|[_^{}=+\-*/<>]|[A-Za-z]", s))


def _stash_inline_math(text: str, maths: list[str]) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("$$", i):
            end = _find_unescaped(text, "$$", i + 2)
            if end >= 0:
                content = text[i + 2 : end]
                if _looks_like_inline_math(content):
                    maths.append(content)
                    out.append(f"\x00M{len(maths) - 1}\x00")
                    i = end + 2
                    continue
        if text.startswith(r"\[", i):
            end = _find_unescaped(text, r"\]", i + 2)
            if end >= 0:
                content = text[i + 2 : end]
                if _looks_like_inline_math(content):
                    maths.append(content)
                    out.append(f"\x00M{len(maths) - 1}\x00")
                    i = end + 2
                    continue
        if text.startswith(r"\(", i):
            end = _find_unescaped(text, r"\)", i + 2)
            if end >= 0:
                content = text[i + 2 : end]
                if _looks_like_inline_math(content):
                    maths.append(content)
                    out.append(f"\x00M{len(maths) - 1}\x00")
                    i = end + 2
                    continue
        if text[i] == "$" and not _is_escaped(text, i) and not text.startswith("$$", i):
            end = i + 1
            while end < n:
                end = text.find("$", end)
                if end < 0:
                    break
                if not _is_escaped(text, end) and not text.startswith("$$", end):
                    break
                end += 1
            if end >= 0:
                content = text[i + 1 : end]
                if _looks_like_inline_math(content):
                    maths.append(content)
                    out.append(f"\x00M{len(maths) - 1}\x00")
                    i = end + 1
                    continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _render_inline(text: str) -> str:
    """把一行（或合并后的一段）markdown 行内语法转为富文本，先转义保证安全。"""
    # 1) 抽出行内代码占位，避免其内部被其它规则二次处理
    codes: list[str] = []
    maths: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        codes.append(m.group(1))
        return f"\x00C{len(codes) - 1}\x00"

    tmp = _CODE_INLINE_RE.sub(_stash, text)
    tmp = _stash_inline_math(tmp, maths)

    # 2) 整体 HTML 转义（控制符不会泄漏成标签）
    tmp = html.escape(tmp, quote=False)

    # 3) 图片与链接（白名单协议，否则按字面）
    def _image(m: "re.Match[str]") -> str:
        alt = m.group(1)
        url = m.group(2).replace("&amp;", "&")
        if not (_DATA_IMAGE_OK_RE.match(url) or _FILE_IMAGE_OK_RE.match(url)):
            return m.group(0)
        safe_alt = html.escape(alt or "generated image", quote=True)
        safe_url = html.escape(url, quote=True)
        return (
            f'<br><img src="{safe_url}" alt="{safe_alt}" '
            'width="640" style="max-width:640px; margin:6px 0; border-radius:8px;">'
        )

    tmp = _IMAGE_RE.sub(_image, tmp)

    def _link(m: "re.Match[str]") -> str:
        label = m.group(1)
        url = m.group(2)
        raw = url.replace("&amp;", "&")
        if not _URL_OK_RE.match(raw):
            return m.group(0)
        safe_url = html.escape(raw, quote=True)
        return f'<a href="{safe_url}" style="{_LINK_STYLE}">{label}</a>'

    tmp = _LINK_RE.sub(_link, tmp)

    # 4) 粗体 / 删除线 / 斜体（成对匹配；落单标记天然留作字面字符）
    tmp = _BOLD_RE.sub(r"<b>\1</b>", tmp)
    tmp = _DEL_RE.sub(r"<s>\1</s>", tmp)
    tmp = _ITALIC_RE.sub(r"<i>\1</i>", tmp)

    def _unstash_math(m: "re.Match[str]") -> str:
        idx = int(m.group(1))
        if 0 <= idx < len(maths):
            try:
                return LatexRenderer.to_html(maths[idx], display=False)
            except Exception:
                return html.escape(maths[idx], quote=False)
        return ""

    tmp = _MATH_TOKEN_RE.sub(_unstash_math, tmp)

    # 5) 还原行内代码（内容转义后包 <code>）
    def _unstash(m: "re.Match[str]") -> str:
        idx = int(m.group(1))
        if 0 <= idx < len(codes):
            return f'<code style="{_CODE_STYLE}">{html.escape(codes[idx], quote=False)}</code>'
        return ""

    return _CODE_TOKEN_RE.sub(_unstash, tmp)


def _is_block_start(line: str) -> bool:
    return bool(
        _FENCE_RE.match(line)
        or _HEADING_RE.match(line)
        or _HR_RE.match(line)
        or _BQ_RE.match(line)
        or _LIST_RE.match(line)
    )


def _math_block_bounds(lines: list[str], i: int, n: int) -> tuple[str, int] | None:
    if i >= n:
        return None
    raw = str(lines[i] or "")
    stripped = raw.strip()
    if stripped.startswith("$$"):
        opener = "$$"
        closer = "$$"
    elif stripped.startswith(r"\["):
        opener = r"\["
        closer = r"\]"
    else:
        return None

    first = stripped[len(opener) :]
    end = _find_unescaped(first, closer, 0)
    if end >= 0:
        trailing = first[end + len(closer) :].strip()
        if trailing:
            return None
        return first[:end], i + 1

    buf = [first]
    j = i + 1
    while j < n:
        line = str(lines[j] or "")
        end = _find_unescaped(line, closer, 0)
        if end >= 0:
            trailing = line[end + len(closer) :].strip()
            if trailing:
                return None
            buf.append(line[:end])
            return "\n".join(buf), j + 1
        buf.append(line)
        j += 1
    return None


def _split_table_row(line: str) -> list[str]:
    s = str(line or "").strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def _is_table_separator(cells: list[str]) -> bool:
    if len(cells) < 2:
        return False
    return all(bool(_TABLE_SEP_RE.match(cell.replace(" ", ""))) for cell in cells)


def _is_table_start_at(lines: list[str], i: int, n: int) -> bool:
    if i + 1 >= n:
        return False
    header = _split_table_row(lines[i])
    separator = _split_table_row(lines[i + 1])
    return len(header) >= 2 and _is_table_separator(separator)


def _is_block_start_at(lines: list[str], i: int, n: int) -> bool:
    return bool(_is_block_start(lines[i]) or _is_table_start_at(lines, i, n) or _math_block_bounds(lines, i, n))


def _table_align(cell: str) -> str:
    token = cell.replace(" ", "")
    if token.startswith(":") and token.endswith(":"):
        return "center"
    if token.endswith(":"):
        return "right"
    return "left"


def _normalize_table_cells(cells: list[str], count: int) -> list[str]:
    out = list(cells[:count])
    while len(out) < count:
        out.append("")
    return out


def _render_table_cell(text: str) -> str:
    rendered = _render_inline(str(text or ""))
    return re.sub(r"&lt;br\s*/?&gt;", "<br>", rendered, flags=re.IGNORECASE)


def _build_table_html(header: list[str], aligns: list[str], rows: list[list[str]]) -> str:
    count = max(1, len(header))
    aligns = _normalize_table_cells(aligns, count)
    out = [f'<table border="1" cellspacing="0" cellpadding="0" style="{_TABLE_STYLE}">']
    out.append("<tr>")
    for idx, cell in enumerate(_normalize_table_cells(header, count)):
        align = html.escape(_table_align(aligns[idx]), quote=True)
        out.append(
            f'<td bgcolor="#f8fafc" align="{align}" style="{_TH_STYLE}">'
            f"<b>{_render_table_cell(cell)}</b></td>"
        )
    out.append("</tr>")
    for row in rows:
        out.append("<tr>")
        for idx, cell in enumerate(_normalize_table_cells(row, count)):
            align = html.escape(_table_align(aligns[idx]), quote=True)
            out.append(
                f'<td align="{align}" style="{_TD_STYLE}">'
                f"{_render_table_cell(cell)}</td>"
            )
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _consume_table(lines: list[str], i: int, n: int) -> tuple[str, int]:
    header = _split_table_row(lines[i])
    aligns = _split_table_row(lines[i + 1])
    count = len(header)
    rows: list[list[str]] = []
    i += 2
    while i < n and lines[i].strip():
        cells = _split_table_row(lines[i])
        if len(cells) < 2:
            break
        if _is_table_separator(cells):
            i += 1
            continue
        rows.append(cells)
        i += 1
    return _build_table_html(header, aligns, rows), i


def _render_code_block(buf: list[str], lang: str = "", code_idx: int = 0, *, include_code_tools: bool = True) -> str:
    body = "<br>".join(
        html.escape(line, quote=False).replace("\t", "&nbsp;" * 4).replace(" ", "&nbsp;")
        for line in buf
    )

    lang_display = str(lang or "").strip()
    if lang_display:
        if lang_display.lower() in {"js", "ts", "css", "html", "sql", "xml", "json", "yaml", "yml"}:
            lang_display = lang_display.upper()
        else:
            lang_display = lang_display.capitalize()
    else:
        lang_display = "Code"

    # 获取 SVG 图标的本地 URI
    import os
    from pathlib import Path
    assets_dir = Path(__file__).resolve().parent / "assets"
    copy_uri = (assets_dir / "icon_action_copy.svg").as_uri()
    save_uri = (assets_dir / "icon_action_save.svg").as_uri()
    preview_uri = (assets_dir / "icon_action_play.svg").as_uri()

    # 拼装图 2 极简线条风格的 SVG 工具按钮（超链接中嵌入图片）
    copy_btn = f'<a href="code-copy:{code_idx}" title="复制代码" style="text-decoration: none; margin: 0 8px;"><img src="{copy_uri}" width="16" height="16" style="vertical-align: middle;"></a>'
    save_btn = f'<a href="code-download:{code_idx}-{lang_display}" title="下载文件" style="text-decoration: none; margin: 0 8px;"><img src="{save_uri}" width="16" height="16" style="vertical-align: middle;"></a>'

    preview_btn = ""
    if lang_display.upper() == "HTML":
        preview_btn = f'<a href="code-preview:{code_idx}" title="网页预览" style="text-decoration: none; margin: 0 8px;"><img src="{preview_uri}" width="16" height="16" style="vertical-align: middle;"></a>'

    # 完美的防御性悬浮设计：默认 0.20 高级淡隐，Hover 时亮起，不支持 Hover 的环境则优雅常驻
    style_html = (
        "<style>"
        " .code-card-container .code-toolbar { opacity: 0.20; background-color: transparent; border: none; outline: none; }"
        " .code-card-container:hover .code-toolbar { opacity: 1.0; }"
        "</style>"
    )

    card_style = (
        "border: 1px solid #e2e8f0; border-radius: 8px; margin: 12px 0;"
        " background-color: #f8fafc; font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;"
    )
    header_style = (
        "background-color: #f1f5f9; border-bottom: 1px solid #e2e8f0;"
        " padding: 8px 16px; font-size: 13px; font-weight: bold; color: #0f172a;"
    )
    code_content_style = (
        "padding: 12px 16px 12px 16px; font-family: 'Consolas', 'Courier New', monospace;"
        " font-size: 13px; line-height: 150%; color: #0f172a; background-color: #f8fafc;"
    )

    header_html = (
        f'<div style="{header_style}">'
        f'📄 {lang_display}'
        f'</div>'
    )

    if not bool(include_code_tools):
        return (
            f'<div class="code-card-container" style="{card_style}">'
            f'{header_html}'
            f'<div style="{code_content_style}">{body}</div>'
            f'</div>'
        )

    toolbar_inner = (
        f'<div class="code-toolbar" style="text-align: center; margin-top: 8px; background-color: transparent; border: none;">'
        f'{copy_btn}'
        f'{save_btn}'
        f'{preview_btn}'
        f'</div>'
    )

    return (
        f'{style_html}'
        f'<div class="code-card-container" style="{card_style}">'
        f'{header_html}'
        f'<div style="{code_content_style}">{body}{toolbar_inner}</div>'
        f'</div>'
    )


def _split_code_segments(md: str) -> list[dict[str, object]]:
    text = str(md or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    segments: list[dict[str, object]] = []
    text_buf: list[str] = []
    i, n = 0, len(lines)

    def flush_text() -> None:
        if text_buf:
            segments.append({"type": "markdown", "text": "\n".join(text_buf)})
            text_buf.clear()

    while i < n:
        line = lines[i]
        if _FENCE_RE.match(line):
            flush_text()
            lang = line.strip("`~ \t\r\n")
            i += 1
            code_buf: list[str] = []
            closed = False
            while i < n:
                if _FENCE_RE.match(lines[i]):
                    closed = True
                    i += 1
                    break
                code_buf.append(lines[i])
                i += 1
            segments.append(
                {
                    "type": "code",
                    "text": "\n".join(code_buf),
                    "lang": lang,
                    "closed": closed,
                }
            )
            continue

        text_buf.append(line)
        i += 1

    flush_text()
    return segments


def _build_list_html(items: list[tuple[int, bool, str]]) -> str:
    """按缩进用栈构建嵌套列表（两级以上自动降级到就近层）。"""
    out: list[str] = []
    stack: list[tuple[int, str]] = []  # (indent, tag)
    for indent, ordered, content in items:
        tag = "ol" if ordered else "ul"
        while stack and stack[-1][0] > indent:
            out.append(f"</{stack.pop()[1]}>")
        if not stack or stack[-1][0] < indent:
            out.append(f'<{tag} style="{_UL_STYLE}">')
            stack.append((indent, tag))
        out.append(f'<li style="{_LI_STYLE}">{_render_inline(content)}</li>')
    while stack:
        out.append(f"</{stack.pop()[1]}>")
    return "".join(out)


def _consume_list(lines: list[str], i: int, n: int) -> tuple[str, int]:
    items: list[tuple[int, bool, str]] = []
    while i < n:
        m = _LIST_RE.match(lines[i])
        if m:
            indent = len(m.group(1).expandtabs(4))
            marker = m.group(2)
            ordered = marker[0] not in "-*+"
            items.append((indent, ordered, m.group(3)))
            i += 1
        elif lines[i].strip() == "" or _is_block_start_at(lines, i, n):
            break
        elif items:
            # 列表项的续行：并入上一项
            prev = items[-1]
            items[-1] = (prev[0], prev[1], prev[2] + " " + lines[i].strip())
            i += 1
        else:
            break
    return _build_list_html(items), i


def _render_blocks(md: str, code_blocks_out: Optional[list[str]] = None, *, include_code_tools: bool = True) -> str:
    text = md.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]

        # 围栏代码块（未闭合也按已收集内容渲染 —— 流式容错）
        if _FENCE_RE.match(line):
            lang = line.strip("`~ \t\r\n")
            i += 1
            buf: list[str] = []
            while i < n and not _FENCE_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            if i < n:  # 跳过闭合 fence
                i += 1

            code_text = "\n".join(buf)
            code_idx = 0
            if code_blocks_out is not None:
                code_idx = len(code_blocks_out)
                code_blocks_out.append(code_text)

            out.append(_render_code_block(buf, lang, code_idx, include_code_tools=include_code_tools))
            continue

        if not line.strip():
            i += 1
            continue

        math_block = _math_block_bounds(lines, i, n)
        if math_block is not None:
            expr, i = math_block
            try:
                out.append(LatexRenderer.to_html(expr, display=True))
            except Exception:
                out.append(f'<pre style="white-space:pre-wrap">{html.escape(expr, quote=False)}</pre>')
            continue

        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            out.append(
                f'<h{level} style="{_H_STYLE[level]}">{_render_inline(m.group(2).strip())}</h{level}>'
            )
            i += 1
            continue

        if _HR_RE.match(line):
            out.append(f'<hr style="{_HR_STYLE}"/>')
            i += 1
            continue

        if _BQ_RE.match(line):
            bq_buf: list[str] = []
            while i < n:
                mm = _BQ_RE.match(lines[i])
                if mm is None:
                    break
                bq_buf.append(mm.group(1))
                i += 1
            inner = "<br>".join(_render_inline(x) for x in bq_buf)
            out.append(f'<div style="{_BQ_STYLE}">{inner}</div>')
            continue

        if _is_table_start_at(lines, i, n):
            block, i = _consume_table(lines, i, n)
            out.append(block)
            continue

        if _LIST_RE.match(line):
            block, i = _consume_list(lines, i, n)
            out.append(block)
            continue

        # 普通段落：聚合连续的非块级、非空行，行间以 <br> 保留视觉换行
        buf = []
        while i < n and lines[i].strip() and not _is_block_start_at(lines, i, n):
            buf.append(lines[i].strip())
            i += 1
        out.append(f'<p style="{_P_STYLE}">' + "<br>".join(_render_inline(x) for x in buf) + "</p>")

    return "".join(out)


class MarkdownRenderer:
    """无状态 Markdown 渲染器。"""

    @staticmethod
    def to_html(
        md: str,
        *,
        streaming: bool = False,
        code_blocks_out: Optional[list[str]] = None,
        include_code_tools: bool = True,
    ) -> str:
        """把 markdown 文本转为 Qt 富文本子集 HTML 片段。

        ``streaming`` 仅作语义标记：本实现对未闭合标记天然容错，流式与终态走同一路径。
        失败时逐行兜底，最终兜底为纯文本 + <br>，绝不抛出。
        """
        _ = streaming  # 预留参数：当前流式与终态共用同一容错路径
        if not md:
            return ""
        text = _normalize_loose_markdown(
            _normalize_ai_component_tags(_normalize_citation_markers(str(md)))
        )
        try:
            return _render_blocks(text, code_blocks_out, include_code_tools=include_code_tools)
        except Exception:
            try:
                return (
                    f'<p style="{_P_STYLE}">'
                    + html.escape(text, quote=False).replace("\n", "<br>")
                    + "</p>"
                )
            except Exception:
                return ""

    @staticmethod
    def split_code_segments(md: str) -> list[dict[str, object]]:
        """把 Markdown 按围栏代码块切分，供 Qt 组件渲染真实可滚动代码块。"""
        try:
            return _split_code_segments(str(md or ""))
        except Exception:
            return [{"type": "markdown", "text": str(md or "")}]
