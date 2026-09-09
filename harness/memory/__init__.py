"""记忆子系统统一出口。

对外暴露：
    - MemoryStore / get_memory_store  长期记忆存取（SQLite）
    - MemoryEntry                     结构化记忆行
    - resolve_memory_paths            两个库文件的路径解析
    - create_checkpointer             短期记忆 checkpoint 工厂
    - save_memory_tool / search_memory_tool / delete_memory_tool  LLM 工具
"""

from harness.memory.manager import (
    MemoryEntry,
    MemoryStore,
    get_memory_store,
    resolve_memory_paths,
)

__all__ = [
    "MemoryEntry",
    "MemoryStore",
    "get_memory_store",
    "resolve_memory_paths",
    "create_checkpointer",
    "save_memory_tool",
    "search_memory_tool",
    "delete_memory_tool",
]


def __getattr__(name: str):
    """延迟导出需要联网/重依赖的对象（checkpointer 与工具不做热路径）。"""
    if name == "create_checkpointer":
        from harness.memory.checkpointer import create_checkpointer

        globals()["create_checkpointer"] = create_checkpointer
        return create_checkpointer
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