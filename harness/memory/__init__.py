"""记忆子系统统一出口。

对外暴露：
    - MemoryManager / get_memory_manager / reset_memory_manager
      分层上下文体系的「跨会话记忆」管理器（memory.json 文档 + LLM 防抖提取）
    - MemoryMiddleware / memory_flush_hook  中间件与摘要冲刷联动
    - MemoryStorage / MemoryUpdateQueue / MemoryUpdater  底层模块（调试/扩展用）
    - resolve_memory_paths       记忆与 checkpoint 库路径（兼容旧接口）
    - create_checkpointer        短期记忆 checkpoint 工厂
    - save_memory_tool / search_memory_tool / delete_memory_tool  LLM 工具
"""

from harness.memory.manager import (
    MemoryManager,
    get_memory_manager,
    reset_memory_manager,
)
from harness.memory.storage import MemoryStorage
from harness.memory.queue import MemoryUpdateQueue
from harness.memory.updater import MemoryUpdater
from harness.memory.paths import resolve_memory_paths

__all__ = [
    "MemoryManager",
    "get_memory_manager",
    "reset_memory_manager",
    "MemoryStorage",
    "MemoryUpdateQueue",
    "MemoryUpdater",
    "resolve_memory_paths",
    "create_checkpointer",
    "MemoryMiddleware",
    "memory_flush_hook",
    "save_memory_tool",
    "search_memory_tool",
    "delete_memory_tool",
]
# MemoryMiddleware / memory_flush_hook 延迟导入（避免记忆包与中间件链的
# 循环依赖影响热路径）


def __getattr__(name: str):
    """延迟导出需要联网/重依赖的对象（checkpointer、中间件与工具不热加载）。"""
    if name == "create_checkpointer":
        from harness.memory.checkpointer import create_checkpointer

        globals()["create_checkpointer"] = create_checkpointer
        return create_checkpointer
    if name in {"MemoryMiddleware", "memory_flush_hook"}:
        from harness.memory.middleware import MemoryMiddleware, memory_flush_hook

        globals()["MemoryMiddleware"] = MemoryMiddleware
        globals()["memory_flush_hook"] = memory_flush_hook
        return globals()[name]
    if name in {"save_memory_tool", "search_memory_tool", "delete_memory_tool"}:
        from harness.memory.tools import (
            delete_memory_tool,
            save_memory_tool,
            search_memory_tool,
        )

        exports = {
            "save_memory_tool": save_memory_tool,
            "search_memory_tool": search_memory_tool,
            "delete_memory_tool": delete_memory_tool,
        }
        for export_name, value in exports.items():
            globals()[export_name] = value
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")