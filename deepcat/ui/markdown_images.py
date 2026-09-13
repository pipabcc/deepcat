"""聊天与富文本共用的图片标记解析，保留 URL 和本地路径中的括号。"""

from __future__ import annotations

from dataclasses import dataclass
import html
import re
from typing import Callable, Iterator
from urllib.parse import urlsplit


_PREFIX = re.compile(r"(!?)\[((?:\\.|[^\]\\])*)\]\(")
_NEXT_MARKER = re.compile(r"[!`\[~]")
_IMAGE_EXTENSION = re.compile(r"\.(?:png|jpe?g|webp|gif|bmp|avif)(?:[?#].*)?$", re.IGNORECASE)
_LOCAL_SOURCE = re.compile(r"^(?:file://|[A-Za-z]:[\\/])", re.IGNORECASE)
_DATA_SOURCE = re.compile(r"^data:image/(?:png|jpe?g|webp|gif|bmp|avif);base64,[A-Za-z0-9+/=]+$", re.IGNORECASE)


@dataclass(frozen=True)
class MarkdownImage:
    start: int
    end: int
    alt: str
    source: str


def _destination_end(text: str, start: int) -> int | None:
    while start < len(text) and text[start] in " \t":
        start += 1
    first_close = text.find(")", start)
    if first_close < 0:
        return None
    # 常见 URL 和大段 base64 直接交给字符串查找，避免逐字扫描或复制图片载荷。
    if all(text.find(char, start, first_close) < 0 for char in ("(", "\\", "<", '"', "'", "\n", "\r")):
        return first_close
    depth = 0
    in_angle = text.startswith("<", start)
    quote = ""
    index = start
    while index < len(text):
        char = text[index]
        if char in "\r\n":
            return None
        if char == "\\" and index + 1 < len(text) and text[index + 1] in "()\\\"'":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ""
        elif in_angle:
            if char == ">":
                in_angle = False
        elif char in "\"'" and depth == 0 and index > start and text[index - 1] in " \t":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return index
            depth -= 1
        index += 1
    return None


def _supported_source(source: str, *, explicit_image: bool, alt: str) -> bool:
    if source.lower().startswith("data:image/"):
        return bool(_DATA_SOURCE.fullmatch(source))
    if _LOCAL_SOURCE.match(source):
        return bool(_IMAGE_EXTENSION.search(source))
    try:
        parsed = urlsplit(source)
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.netloc)
        and (explicit_image or alt.strip().lower() == "generated image")
    )


def _raw_images(text: str) -> Iterator[MarkdownImage]:
    position = 0
    while position < len(text):
        marker = _NEXT_MARKER.search(text, position)
        if marker is None:
            break
        position = marker.start()
        escape_start = position
        while escape_start > 0 and text[escape_start - 1] == "\\":
            escape_start -= 1
        if (position - escape_start) % 2:
            position += 1
            continue
        if text[position] in "`~":
            end = position
            while end < len(text) and text[end] == text[position]:
                end += 1
            delimiter = text[position:end]
            line_start = text.rfind("\n", 0, position) + 1
            if len(delimiter) >= 3 and position - line_start <= 3 and not text[line_start:position].strip():
                closing_fence = re.compile(
                    rf"(?m)^ {{0,3}}{re.escape(text[position])}{{{len(delimiter)},}}[ \t]*(?:\r?\n|$)"
                )
                closing = closing_fence.search(text, end)
                position = closing.end() if closing else len(text)
                continue
            if text[position] == "~":
                position = end
                continue
            closing = text.find(delimiter, end)
            position = closing + len(delimiter) if closing >= 0 else end
            continue
        if text[position] not in "![" or (position and text[position - 1] == "!"):
            position += 1
            continue
        match = _PREFIX.match(text, position)
        if match is None:
            position += 1
            continue
        end = _destination_end(text, match.end())
        if end is None:
            next_line = text.find("\n", match.end())
            position = next_line + 1 if next_line >= 0 else len(text)
            continue
        destination = text[match.end() : end].strip()
        if destination.startswith("<") and ">" in destination:
            destination = destination[1 : destination.index(">")]
        else:
            destination = re.sub(r"""\s+["'][^\r\n]*["']$""", "", destination)
        source = html.unescape(re.sub(r"\\([()])", r"\1", destination))
        alt = html.unescape(match.group(2).replace(r"\]", "]").replace(r"\[", "["))
        if _supported_source(source, explicit_image=bool(match.group(1)), alt=alt):
            yield MarkdownImage(position, end + 1, alt, source)
        position = end + 1


def _legacy_source_pair(first: MarkdownImage, second: MarkdownImage, text: str) -> bool:
    if first.alt.lower() != "generated image" or second.alt.lower() != "generated image":
        return False
    if text[first.end : second.start].strip() or _IMAGE_EXTENSION.search(first.source):
        return False
    try:
        source = urlsplit(first.source)
        thumbnail = urlsplit(second.source)
    except ValueError:
        return False
    return (
        source.scheme in {"http", "https"}
        and source.hostname != "images.openai.com"
        and thumbnail.hostname == "images.openai.com"
    )


def iter_markdown_images(text: str) -> Iterator[MarkdownImage]:
    pending: MarkdownImage | None = None
    for current in _raw_images(text):
        if pending is not None:
            if _legacy_source_pair(pending, current, text):
                # 兼容旧历史中“来源网页 + OpenAI 缩略图”被误写成两张图片的记录。
                yield MarkdownImage(pending.start, current.end, current.alt, current.source)
                pending = None
                continue
            yield pending
        try:
            source = urlsplit(current.source)
            can_be_legacy_source = (
                current.alt.lower() == "generated image"
                and source.scheme in {"http", "https"}
                and source.hostname != "images.openai.com"
                and not _IMAGE_EXTENSION.search(current.source)
            )
        except ValueError:
            can_be_legacy_source = False
        if can_be_legacy_source:
            pending = current
        else:
            pending = None
            yield current
    if pending is not None:
        yield pending


def has_markdown_image(text: str) -> bool:
    return next(iter_markdown_images(text), None) is not None


def replace_markdown_images(text: str, render: Callable[[MarkdownImage], str]) -> str:
    result = []
    position = 0
    for image in iter_markdown_images(text):
        result.extend((text[position : image.start], render(image)))
        position = image.end
    result.append(text[position:])
    return "".join(result)
