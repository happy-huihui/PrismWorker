from __future__ import annotations

from harness.memory.integration.middleware import MemoryMiddleware, memory_flush_hook
from harness.memory.integration.tools import (
    delete_memory_tool,
    save_memory_tool,
    search_memory_tool,
)

"""上层适配包（integration）

    职责：把记忆能力接入 Agent 运行链的两个入口。
    内容：middleware（注入 + 自动提取 + 摘要冲刷钩子）、tools（模型自主存取工具）。
"""

__all__ = [
    "MemoryMiddleware",
    "memory_flush_hook",
    "save_memory_tool",
    "search_memory_tool",
    "delete_memory_tool",
]
