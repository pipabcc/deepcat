from __future__ import annotations

import hashlib
import html
import logging
import os
import re
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices

logger = logging.getLogger(__name__)

# 常见知名 HTML 标签集合
_KNOWN_HTML_TAGS = {
    "html", "head", "body", "title", "meta", "link", "style", "script",
    "div", "span", "p", "a", "img", "button", "input", "textarea", "select", "option", "form",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "ul", "ol", "li", "dl", "dt", "dd",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "footer", "nav", "section", "article", "aside", "main",
    "canvas", "svg", "path", "circle", "rect", "iframe", "video", "audio",
    "pre", "code", "blockquote", "hr", "br",
}

# 匹配 HTML 闭合标签对 <tag ...>...</tag>
_HTML_PAIR_RE = re.compile(
    r"<\s*([a-zA-Z][a-zA-Z0-9-]*)(?:\s+[^>]*)?>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_markdown_codeblock(text: str) -> str:
    """提取 Markdown 代码块内部的代码文本。"""
    s = text.strip()
    if s.startswith("```") and s.endswith("```"):
        lines = s.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return s


def is_runnable_web_code(
    content: str,
    content_type: str = "text",
    file_path: str = "",
) -> bool:
    """
    判断当前记录是否为可在浏览器中直接运行预览的 HTML/SVG/前端网页代码。

    :param content: 记录的原始文本内容
    :param content_type: 记录内容类型（如 "text", "file_path", "image" 等）
    :param file_path: 记录关联的文件路径（若有）
    :return: True 如果可在网页中运行预览，否则 False
    """
    # 1. 检查文件路径
    fp = str(file_path or "").strip()
    if fp and (content_type in ("file_path", "text") or not content):
        suffix = Path(fp).suffix.lower()
        if suffix in (".html", ".htm", ".xhtml", ".svg"):
            try:
                if os.path.isfile(fp):
                    return True
            except Exception:
                pass

    raw = str(content or "").strip()
    if not raw or len(raw) < 5:
        return False

    code = _strip_markdown_codeblock(raw)
    code_lower = code.lower().lstrip()

    # 2. 完整 HTML 文档开头声明
    if code_lower.startswith("<!doctype html") or code_lower.startswith("<html"):
        return True

    # 3. SVG 矢量图代码
    if code_lower.startswith("<svg") and ("</svg>" in code_lower or "xmlns" in code_lower):
        return True
    if code_lower.startswith("<?xml") and "<svg" in code_lower and "</svg>" in code_lower:
        return True

    # 4. 包含明确的 HTML 骨架标签组合
    if "<html" in code_lower and "</html>" in code_lower:
        return True
    if "<head" in code_lower and "<body" in code_lower:
        return True

    # 5. 包含闭合的 script 或 style 标签且伴有其他标签结构
    if ("<script" in code_lower and "</script>" in code_lower) or (
        "<style" in code_lower and "</style>" in code_lower
    ):
        return True

    # 6. 检测知名 HTML 标签的闭合结构
    matches = _HTML_PAIR_RE.findall(code)
    known_matched = [m.lower() for m in matches if m.lower() in _KNOWN_HTML_TAGS]
    if len(known_matched) >= 1:
        # 如果包含如 div, table, p, form, canvas, button, ul, ol 等结构性标签
        structural_tags = {
            "html", "body", "div", "table", "form", "canvas", "svg", "section", "article",
            "header", "footer", "nav", "ul", "ol", "button", "iframe", "p", "tr", "td", "h1", "h2", "h3"
        }
        if any(t in structural_tags for t in known_matched):
            return True
        if len(known_matched) >= 2:
            return True

    return False


def wrap_html_content(raw_code: str, title: str = "DeepCat 代码预览") -> str:
    """
    将用户代码片段包装为规范的 HTML5 预览页面，并确保 UTF-8 编码声明以防乱码。
    """
    code = _strip_markdown_codeblock(raw_code.strip())
    code_lower = code.lower()

    # 如果已经是完整的 HTML 文档
    if "<!doctype html" in code_lower or "<html" in code_lower:
        # 确保包含 UTF-8 编码声明
        if "<meta" not in code_lower or "charset" not in code_lower:
            head_match = re.search(r"<\s*head[^>]*>", code, re.IGNORECASE)
            if head_match:
                insert_pos = head_match.end()
                return code[:insert_pos] + '\n    <meta charset="UTF-8">\n' + code[insert_pos:]
        return code

    # 如果是独立 SVG 矢量图代码
    if code_lower.startswith("<svg") or (code_lower.startswith("<?xml") and "<svg" in code_lower):
        escaped_title = html.escape(title)
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{escaped_title}</title>
    <style>
        body {{
            margin: 0;
            padding: 24px;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 90vh;
            background-color: #f8fafc;
            box-sizing: border-box;
        }}
        .svg-container {{
            background: #ffffff;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
            display: inline-block;
        }}
    </style>
</head>
<body>
    <div class="svg-container">
        {code}
    </div>
</body>
</html>"""

    # 普通 HTML 结构代码片段包装
    escaped_title = html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{escaped_title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #ffffff;
            color: #111827;
        }}
    </style>
</head>
<body>
{code}
</body>
</html>"""


def cleanup_stale_previews(max_age_days: int = 7) -> None:
    """清理超过保留期的历史预览临时 HTML，防止 %TEMP%/deepcat_code_preview 无限累积。"""
    try:
        preview_dir = Path(tempfile.gettempdir()) / "deepcat_code_preview"
        if not preview_dir.is_dir():
            return
        cutoff = time.time() - max(1, int(max_age_days)) * 86400.0
        for item in preview_dir.glob("preview_*.html"):
            try:
                if item.stat().st_mtime < cutoff:
                    item.unlink(missing_ok=True)
            except Exception:
                continue
    except Exception as exc:
        logger.debug("清理历史代码预览临时文件失败: %s", exc)


def preview_code_in_browser(
    content: str,
    content_type: str = "text",
    file_path: str = "",
    title: str = "DeepCat 代码预览",
) -> bool:
    """
    在系统默认浏览器中打开代码进行渲染预览。

    :param content: 记录的原始代码内容
    :param content_type: 记录内容类型
    :param file_path: 文件路径（若为本地 HTML/SVG 则直接打开）
    :param title: 预览页面标题
    :return: 是否成功调起浏览器
    """
    try:
        # 如果是本地 html/svg 文件，直接打开
        fp = str(file_path or "").strip()
        if fp and os.path.isfile(fp) and Path(fp).suffix.lower() in (".html", ".htm", ".xhtml", ".svg"):
            abs_fp = os.path.abspath(fp)
            url = QUrl.fromLocalFile(abs_fp)
            if QDesktopServices.openUrl(url):
                return True
            if os.name == "nt":
                try:
                    os.startfile(abs_fp)
                    return True
                except Exception:
                    pass
            return bool(webbrowser.open(url.toString()))

        # 否则保存到临时 HTML 文件并在浏览器中打开
        final_html = wrap_html_content(content, title=title)
        preview_dir = Path(tempfile.gettempdir()) / "deepcat_code_preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        cleanup_stale_previews()

        # 生成稳定的哈希文件名以避免创建无限个临时文件
        content_hash = hashlib.md5(final_html.encode("utf-8", errors="ignore")).hexdigest()[:12]
        temp_file = preview_dir / f"preview_{content_hash}.html"

        temp_file.write_text(final_html, encoding="utf-8")
        abs_temp = str(temp_file.resolve())
        url = QUrl.fromLocalFile(abs_temp)

        ok = False
        try:
            ok = bool(QDesktopServices.openUrl(url))
        except Exception:
            pass

        if not ok:
            try:
                ok = bool(webbrowser.open(url.toString()))
            except Exception:
                pass

        if not ok and os.name == "nt":
            try:
                os.startfile(abs_temp)
                ok = True
            except Exception:
                pass

        if ok:
            logger.info("成功调起默认浏览器预览代码: %s", temp_file)
        else:
            logger.warning("调起默认浏览器预览代码失败: %s", temp_file)
        return ok
    except Exception as exc:
        logger.exception("preview_code_in_browser 异常: %s", exc)
        return False
