from __future__ import annotations

import html
import re

from PyQt6.QtCore import QMimeData
from PyQt6.QtGui import QGuiApplication, QTextDocument

from deepcat.ui.markdown_renderer import MarkdownRenderer


def clean_clipboard_text(text: str) -> str:
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    previous_blank = True
    for line in lines:
        item = line.replace("\u00a0", " ").rstrip()
        if not item.strip():
            if not previous_blank and out:
                out.append("")
            previous_blank = True
            continue
        out.append(item)
        previous_blank = False
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def markdown_to_clipboard_html(markdown: str) -> str:
    text = str(markdown or "")
    fragment = MarkdownRenderer.to_html(text, include_code_tools=False)
    if not fragment:
        fragment = html.escape(text, quote=False).replace("\n", "<br>")
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        '<body style="font-family:\'Microsoft YaHei\',\'Segoe UI\',Arial,sans-serif;'
        'font-size:14px;color:#1e293b;">'
        f"{fragment}"
        "</body></html>"
    )


def markdown_to_plain_text(markdown: str) -> str:
    text = str(markdown or "")
    if not text.strip():
        return ""
    try:
        doc = QTextDocument()
        doc.setHtml(markdown_to_clipboard_html(text))
        plain = doc.toPlainText()
        if plain.strip():
            return clean_clipboard_text(plain)
    except Exception:
        pass
    return clean_clipboard_text(_rough_markdown_to_text(text))


def copy_markdown_to_clipboard(markdown: str) -> bool:
    text = str(markdown or "")
    plain = markdown_to_plain_text(text)
    if not plain:
        return False
    clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        return False
    try:
        mime = QMimeData()
        mime.setHtml(markdown_to_clipboard_html(text))
        mime.setText(plain)
        clipboard.setMimeData(mime)
    except Exception:
        try:
            clipboard.setText(plain)
        except Exception:
            return False
    return True


def copy_plain_text_to_clipboard(text: str) -> bool:
    plain = clean_clipboard_text(str(text or ""))
    if not plain:
        return False
    clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        return False
    try:
        clipboard.setText(plain)
    except Exception:
        return False
    return True


def _rough_markdown_to_text(markdown: str) -> str:
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^\s*(```|~~~)\w*\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"~~([^~\n]+)~~", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    return text
