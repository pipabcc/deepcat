"""日志中的 URL 仅保留定位请求所需的信息。"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SECRET_QUERY_NAMES = {
    "apikey",
    "key",
    "token",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "password",
    "passwd",
    "secret",
    "clientsecret",
    "signature",
    "sig",
}


def redact_query(query: str) -> str:
    pairs = parse_qsl(str(query or ""), keep_blank_values=True)
    return urlencode(
        [
            (key, "[REDACTED]" if re.sub(r"[-_.]", "", key).casefold() in _SECRET_QUERY_NAMES else value)
            for key, value in pairs
        ]
    )


def redact_url(value: object) -> str:
    """覆盖查询密钥和 URL 用户信息，解析失败时不回显原文。"""
    try:
        parts = urlsplit(str(value or ""))
        host = parts.netloc.rsplit("@", 1)[-1]
        return urlunsplit((parts.scheme, host, parts.path, redact_query(parts.query), ""))
    except ValueError:
        return "[invalid URL]"
