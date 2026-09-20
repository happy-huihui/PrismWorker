from __future__ import annotations

from harness.memory.extraction.queue import (
    ConversationContext,
    MemoryUpdateQueue,
    QueueFull,
    queue_key,
)
from harness.memory.extraction.updater import MemoryUpdater

"""提取引擎包（extraction）

    职责：把清洗后的对话增量提取为长期记忆，并做异步防抖排队。
    内容：updater（水位线 + LLM 提取 + 门控 + fact CRUD）、queue（防抖合并 + 背压）。
"""

__all__ = [
    "MemoryUpdater",
    "MemoryUpdateQueue",
    "QueueFull",
    "queue_key",
    "ConversationContext",
]
