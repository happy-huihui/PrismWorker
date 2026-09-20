from __future__ import annotations

from harness.runtime.history.reader import count_messages, get_message_history

"""历史包（history）

    职责：把 checkpoint 里的对话原文读成前端可渲染的精简结构。
"""

__all__ = [
    "get_message_history",
    "count_messages",
]
