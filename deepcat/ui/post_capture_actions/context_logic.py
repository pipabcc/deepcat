"""AI 对话上下文裁剪和消息身份计算的纯逻辑。"""

from __future__ import annotations

import json
from typing import Optional


def trim_messages_to_recent_rounds(messages: list[dict], round_limit: Optional[int]) -> list[dict]:
    if not round_limit or round_limit <= 0:
        return list(messages)
    user_count = 0
    start_index = 0
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, dict) and str(message.get("role", "") or "") == "user":
            user_count += 1
            if user_count >= int(round_limit):
                start_index = index
                break
    return list(messages[start_index:])


def context_message_identity(message: dict) -> tuple[str, str]:
    role = str((message or {}).get("role", "") or "")
    content = (message or {}).get("content", "")
    try:
        content_key = json.dumps(content, ensure_ascii=False, sort_keys=True)
    except Exception:
        content_key = str(content)
    return role, content_key
