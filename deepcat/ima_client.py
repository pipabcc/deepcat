from __future__ import annotations

"""IMA OpenAPI client used by notebook tabs.

The client intentionally keeps credentials in memory only. Callers are
responsible for loading/saving encrypted values through the app store layer.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


IMA_BASE_URL = "https://ima.qq.com"


class ImaApiError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(str(message or "IMA 请求失败"))
        self.code = code


@dataclass(frozen=True)
class ImaCredentials:
    client_id: str
    api_key: str

    def validate(self) -> None:
        if not str(self.client_id or "").strip():
            raise ImaApiError("缺少 Client ID")
        if not str(self.api_key or "").strip():
            raise ImaApiError("缺少 API Key")


class ImaClient:
    def __init__(self, credentials: ImaCredentials, *, base_url: str = IMA_BASE_URL, timeout: float = 20.0) -> None:
        credentials.validate()
        self._credentials = credentials
        self._base_url = str(base_url or IMA_BASE_URL).rstrip("/")
        self._timeout = float(timeout or 20.0)

    def post(self, api_path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        path = str(api_path or "").strip().lstrip("/")
        if not path:
            raise ImaApiError("缺少 IMA API 路径")

        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/{path}",
            data=body,
            method="POST",
            headers={
                "ima-openapi-clientid": self._credentials.client_id,
                "ima-openapi-apikey": self._credentials.api_key,
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp is not None else ""
            raise ImaApiError(_response_error_message(raw) or f"IMA HTTP 错误：{exc.code}", code=exc.code) from exc
        except urllib.error.URLError as exc:
            raise ImaApiError(f"无法连接 IMA：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ImaApiError("连接 IMA 超时") from exc

        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ImaApiError("IMA 返回了无法解析的数据") from exc

        code = data.get("code")
        if code not in (0, "0", None):
            try:
                normalized_code = int(code)
            except Exception:
                normalized_code = None
            raise ImaApiError(str(data.get("msg") or "IMA 业务请求失败"), code=normalized_code)
        return data

    # Notes
    def list_notebooks(self, *, cursor: str = "0", limit: int = 20) -> dict[str, Any]:
        return self.post(
            "openapi/note/v1/list_notebook",
            {"cursor": str(cursor), "limit": max(1, min(int(limit), 20))},
        )

    def list_notes(self, *, folder_id: str = "", cursor: str = "", limit: int = 20) -> dict[str, Any]:
        payload: dict[str, Any] = {"folder_id": str(folder_id or ""), "cursor": str(cursor), "limit": max(1, min(int(limit), 20))}
        return self.post("openapi/note/v1/list_note", payload)

    def search_notes(self, query: str, *, search_type: int = 1, start: int = 0, end: int = 20) -> dict[str, Any]:
        clean_query = str(query or "").strip()
        if not clean_query:
            raise ImaApiError("请输入笔记搜索内容")
        normalized_type = 1 if int(search_type or 0) == 1 else 0
        start_value = max(0, int(start or 0))
        end_value = max(start_value + 1, min(start_value + 20, int(end or 20)))
        query_key = "content" if normalized_type == 1 else "title"
        return self.post(
            "openapi/note/v1/search_note",
            {
                "search_type": normalized_type,
                "query_info": {query_key: clean_query},
                "start": start_value,
                "end": end_value,
            },
        )

    def import_note(self, content: str, *, folder_id: str = "", folder_name: str = "") -> str:
        clean_content = _clean_note_markdown(content)
        if not clean_content.strip():
            raise ImaApiError("笔记内容为空，无法追加到 IMA")
        payload: dict[str, Any] = {"content_format": 1, "content": clean_content}
        if str(folder_id or "").strip():
            payload["folder_id"] = str(folder_id).strip()
        if str(folder_name or "").strip():
            payload["folder_name"] = str(folder_name).strip()
        data = self.post("openapi/note/v1/import_doc", payload).get("data") or {}
        note_id = str(data.get("note_id") or "").strip()
        if not note_id:
            raise ImaApiError("IMA 未返回新笔记 ID")
        return note_id

    def append_note(self, note_id: str, content: str) -> str:
        target = str(note_id or "").strip()
        if not target:
            raise ImaApiError("缺少 IMA 笔记 ID")
        clean_content = _clean_note_markdown(content)
        if not clean_content.strip():
            raise ImaApiError("追加内容为空")
        data = self.post(
            "openapi/note/v1/append_doc",
            {"note_id": target, "content_format": 1, "content": clean_content},
        ).get("data") or {}
        return str(data.get("note_id") or target)

    def get_note_content(self, note_id: str, *, target_content_format: int = 0) -> str:
        target = str(note_id or "").strip()
        if not target:
            raise ImaApiError("缺少 IMA 笔记 ID")
        data = self.post(
            "openapi/note/v1/get_doc_content",
            {"note_id": target, "target_content_format": int(target_content_format)},
        ).get("data") or {}
        return str(data.get("content") or "")

    def build_note_search_prompt(self, query: str, *, limit: int = 5) -> str:
        clean_query = str(query or "").strip()
        if not clean_query:
            raise ImaApiError("请输入笔记搜索内容")

        selected: list[dict[str, Any]] = []
        seen_note_ids: set[str] = set()
        max_items = max(1, min(int(limit or 5), 10))
        for search_type in (1, 0):
            data = self.search_notes(clean_query, search_type=search_type, start=0, end=20).get("data") or {}
            items = data.get("search_note_infos") or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                note_info = _search_note_info(item)
                note_id = str(note_info.get("note_id") or "").strip()
                dedupe_key = note_id or f"{note_info.get('title', '')}:{len(selected)}"
                if dedupe_key in seen_note_ids:
                    continue
                seen_note_ids.add(dedupe_key)
                selected.append(item)
                if len(selected) >= max_items:
                    break
            if len(selected) >= max_items:
                break

        if not selected:
            return (
                f"用户想搜索 IMA 笔记并总结，搜索词：{clean_query}\n\n"
                "IMA 笔记没有返回匹配结果。请直接告诉用户未找到相关笔记，并建议换一个关键词。"
            )

        blocks: list[str] = []
        for idx, item in enumerate(selected, 1):
            note_info = _search_note_info(item)
            note_id = str(note_info.get("note_id") or "").strip()
            title = str(note_info.get("title") or "未命名笔记").strip()
            summary = str(note_info.get("summary") or "").strip()
            ext_info = note_info.get("note_ext_info") or {}
            folder_name = str(ext_info.get("folder_name") or "").strip() if isinstance(ext_info, dict) else ""
            highlight = _search_note_highlight(item)
            content = ""
            if note_id:
                try:
                    content = self.get_note_content(note_id)
                except Exception as exc:
                    content = f"读取笔记正文失败：{exc}"

            parts = [f"### 笔记 {idx}: {title}"]
            if note_id:
                parts.append(f"- note_id: {note_id}")
            if folder_name:
                parts.append(f"- 笔记本: {folder_name}")
            if summary:
                parts.append(f"- 摘要: {summary}")
            if highlight:
                parts.append(f"- 命中片段: {highlight}")
            if content:
                parts.append(f"- 笔记正文:\n{_clip_text(content, 4000)}")
            blocks.append("\n".join(parts))

        return (
            "请基于下面从 IMA 笔记检索到的内容进行总结。\n"
            "要求：先给出 3-5 条要点，再补充必要细节；如果检索结果不足，请明确说明不足之处。\n\n"
            f"搜索词：{clean_query}\n\n"
            + "\n\n".join(blocks)
        )

    # Knowledge base
    def search_knowledge_bases(self, query: str = "", *, cursor: str = "", limit: int = 20) -> dict[str, Any]:
        return self.post(
            "openapi/wiki/v1/search_knowledge_base",
            {"query": str(query or ""), "cursor": str(cursor), "limit": max(1, min(int(limit), 20))},
        )

    def search_knowledge(self, knowledge_base_id: str, query: str, *, cursor: str = "") -> dict[str, Any]:
        kb_id = str(knowledge_base_id or "").strip()
        clean_query = str(query or "").strip()
        if not kb_id:
            raise ImaApiError("缺少知识库")
        if not clean_query:
            raise ImaApiError("请输入搜索关键词")
        return self.post(
            "openapi/wiki/v1/search_knowledge",
            {"knowledge_base_id": kb_id, "query": clean_query, "cursor": str(cursor or "")},
        )

    def get_media_info(self, media_id: str) -> dict[str, Any]:
        target = str(media_id or "").strip()
        if not target:
            raise ImaApiError("缺少知识库条目 ID")
        return self.post("openapi/wiki/v1/get_media_info", {"media_id": target}).get("data") or {}

    def build_knowledge_search_prompt(self, knowledge_base_id: str, query: str, *, limit: int = 5) -> str:
        clean_query = str(query or "").strip()
        if not clean_query:
            raise ImaApiError("请输入知识库搜索内容")
        data = self.search_knowledge(knowledge_base_id, clean_query).get("data") or {}
        items = data.get("info_list") or []
        if not isinstance(items, list):
            items = []
        selected = [item for item in items if isinstance(item, dict)][: max(1, min(int(limit or 5), 10))]
        if not selected:
            return (
                f"用户想搜索 IMA 知识库并总结，搜索词：{clean_query}\n\n"
                "IMA 知识库没有返回匹配结果。请直接告诉用户未找到相关内容，并建议换一个关键词。"
            )

        blocks: list[str] = []
        for idx, item in enumerate(selected, 1):
            title = str(item.get("title") or item.get("name") or "未命名条目").strip()
            media_id = str(item.get("media_id") or "").strip()
            highlight = _strip_html_tags(str(item.get("highlight_content") or "").strip())
            content = ""
            source_note = ""
            if media_id:
                try:
                    media = self.get_media_info(media_id)
                    if int(media.get("media_type") or 0) == 11:
                        note_info = media.get("notebook_ext_info") or {}
                        note_id = str(note_info.get("notebook_id") or "").strip()
                        if note_id:
                            source_note = note_id
                            content = self.get_note_content(note_id)
                    elif isinstance(media.get("url_info"), dict):
                        content = _read_url_info_text(
                            media.get("url_info") or {},
                            timeout=self._timeout,
                        )
                except Exception as exc:
                    content = f"读取原文失败：{exc}"
            parts = [f"### 结果 {idx}: {title}"]
            if media_id:
                parts.append(f"- media_id: {media_id}")
            if source_note:
                parts.append(f"- note_id: {source_note}")
            if highlight:
                parts.append(f"- 高亮片段: {highlight}")
            if content:
                parts.append(f"- 可读取内容:\n{_clip_text(content, 4000)}")
            blocks.append("\n".join(parts))

        return (
            "请基于下面从 IMA 知识库检索到的内容进行总结。\n"
            "要求：先给出 3-5 条要点，再补充必要细节；如果检索结果不足，请明确说明不足之处。\n\n"
            f"搜索词：{clean_query}\n"
            f"知识库 ID：{knowledge_base_id}\n\n"
            + "\n\n".join(blocks)
        )

    def get_addable_knowledge_bases(self, *, cursor: str = "", limit: int = 20) -> dict[str, Any]:
        return self.post(
            "openapi/wiki/v1/get_addable_knowledge_base_list",
            {"cursor": str(cursor), "limit": max(1, min(int(limit), 50))},
        )

    def import_urls(self, knowledge_base_id: str, urls: list[str], *, folder_id: str = "") -> dict[str, Any]:
        kb_id = str(knowledge_base_id or "").strip()
        clean_urls = [str(url or "").strip() for url in urls if str(url or "").strip()]
        if not kb_id:
            raise ImaApiError("缺少知识库")
        if not clean_urls:
            raise ImaApiError("缺少要添加的网址")
        payload: dict[str, Any] = {"knowledge_base_id": kb_id, "urls": clean_urls[:10]}
        if str(folder_id or "").strip():
            payload["folder_id"] = str(folder_id).strip()
        return self.post("openapi/wiki/v1/import_urls", payload)

    def add_note_to_knowledge_base(
        self,
        *,
        knowledge_base_id: str,
        note_id: str,
        title: str,
        folder_id: str = "",
    ) -> str:
        kb_id = str(knowledge_base_id or "").strip()
        target_note_id = str(note_id or "").strip()
        clean_title = str(title or "").strip() or "未命名笔记"
        if not kb_id:
            raise ImaApiError("缺少知识库")
        if not target_note_id:
            raise ImaApiError("缺少 IMA 笔记 ID")
        payload: dict[str, Any] = {
            "media_type": 11,
            "note_info": {"content_id": target_note_id},
            "title": clean_title,
            "knowledge_base_id": kb_id,
        }
        if str(folder_id or "").strip():
            payload["folder_id"] = str(folder_id).strip()
        data = self.post("openapi/wiki/v1/add_knowledge", payload).get("data") or {}
        media_id = str(data.get("media_id") or "").strip()
        if not media_id:
            raise ImaApiError("IMA 未返回知识库条目 ID")
        return media_id


def note_html_to_markdown(title: str, html_text: str) -> str:
    """Convert a local QTextEdit HTML note into conservative Markdown text."""
    plain = _html_to_plain_text(html_text)
    clean_title = str(title or "").strip() or "未命名笔记"
    if plain.strip():
        body = plain.strip()
        # 如果正文第一行与标题相同，去掉正文里重复的第一行，避免标题和内容首行重复
        lines = body.split("\n", 1)
        if lines and lines[0].strip() == clean_title:
            body = lines[1].lstrip("\n").strip() if len(lines) > 1 else ""
        if body:
            return f"# {clean_title}\n\n{body}\n"
        return f"# {clean_title}\n"
    return f"# {clean_title}\n"


def _html_to_plain_text(html_text: str) -> str:
    try:
        from PyQt6.QtGui import QTextDocument

        doc = QTextDocument()
        doc.setHtml(str(html_text or ""))
        return doc.toPlainText()
    except Exception:
        import re
        import html as html_lib

        text = re.sub(r"<br\s*/?>", "\n", str(html_text or ""), flags=re.IGNORECASE)
        text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return html_lib.unescape(text)


def _clean_note_markdown(content: str) -> str:
    text = str(content or "")
    text = _LOCAL_IMAGE_RE.sub("", text)
    return text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")


def _strip_html_tags(text: str) -> str:
    import html as html_lib
    import re

    clean = re.sub(r"</?em[^>]*>", "", str(text or ""), flags=re.IGNORECASE)
    clean = re.sub(r"<[^>]+>", "", clean)
    return html_lib.unescape(clean).strip()


def _search_note_info(item: dict[str, Any]) -> dict[str, Any]:
    note_info = item.get("note_book_info") or item.get("noteBookInfo") or item.get("note_info") or {}
    return note_info if isinstance(note_info, dict) else {}


def _search_note_highlight(item: dict[str, Any]) -> str:
    highlight_info = item.get("highlightInfo") or item.get("highlight_info") or {}
    if isinstance(highlight_info, dict):
        values = [_strip_html_tags(str(value or "")) for value in highlight_info.values()]
        return "\n".join(value for value in values if value)
    return _strip_html_tags(str(highlight_info or ""))


def _clip_text(text: str, limit: int) -> str:
    value = str(text or "").strip()
    max_len = max(200, int(limit or 200))
    if len(value) <= max_len:
        return value
    return value[:max_len].rstrip() + "\n...[内容过长，已截断]"


def _read_url_info_text(url_info: dict[str, Any], *, timeout: float = 20.0, max_bytes: int = 1_000_000) -> str:
    url = str((url_info or {}).get("url") or "").strip()
    if not url:
        return ""

    header_values = (url_info or {}).get("headers") or {}
    headers = {str(k): str(v) for k, v in header_values.items()} if isinstance(header_values, dict) else {}
    headers.setdefault("User-Agent", "DeepCat/IMA-Knowledge-Reader")
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = str(response.headers.get("Content-Type") or "")
            body = response.read(max(1, int(max_bytes or 1_000_000)) + 1)
    except Exception as exc:
        return f"原文链接：{url}\n原文读取失败：{exc}"

    if len(body) > max_bytes:
        body = body[:max_bytes]
        truncated = "\n...[原文过长，已截断]"
    else:
        truncated = ""

    if not _is_probably_text_content(content_type, body):
        clean_type = content_type.split(";", 1)[0].strip() or "未知类型"
        return f"原文链接：{url}\n原文类型：{clean_type}，当前仅支持直接抽取网页/文本内容。"

    text = _decode_response_text(body, content_type)
    if "html" in content_type.lower() or _looks_like_html(text):
        text = _html_to_plain_text(text)
    text = _normalize_text(text)
    if not text:
        return f"原文链接：{url}\n原文内容为空或无法解析。"
    return f"原文链接：{url}\n原文文本：\n{text}{truncated}"


def _is_probably_text_content(content_type: str, body: bytes) -> bool:
    clean_type = str(content_type or "").lower().split(";", 1)[0].strip()
    if (
        clean_type.startswith("text/")
        or clean_type in {"application/json", "application/xml", "application/xhtml+xml"}
        or clean_type.endswith("+json")
        or clean_type.endswith("+xml")
        or clean_type in {"application/markdown", "application/md"}
    ):
        return True
    sample = body[:512].lstrip()
    if sample.startswith((b"<!doctype", b"<html", b"{", b"[")):
        return True
    return b"\x00" not in sample


def _decode_response_text(body: bytes, content_type: str) -> str:
    import re

    match = re.search(r"charset=([\w.-]+)", str(content_type or ""), flags=re.IGNORECASE)
    encodings = [match.group(1)] if match else []
    encodings.extend(["utf-8", "gb18030", "latin-1"])
    for encoding in encodings:
        try:
            return body.decode(encoding)
        except Exception:
            continue
    return body.decode("utf-8", errors="replace")


def _looks_like_html(text: str) -> bool:
    import re

    return bool(re.search(r"<(?:!doctype\s+html|html|head|body|article|main|p|div|br)\b", str(text or ""), flags=re.IGNORECASE))


def _normalize_text(text: str) -> str:
    import re

    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _response_error_message(raw: str) -> str:
    try:
        data = json.loads(raw or "{}")
    except Exception:
        return ""
    return str(data.get("msg") or data.get("message") or "")


_LOCAL_IMAGE_RE = __import__("re").compile(
    r"!\[[^\]]*\]\((?:file://|[A-Za-z]:\\|/)[^)]+\)",
    __import__("re").IGNORECASE,
)
