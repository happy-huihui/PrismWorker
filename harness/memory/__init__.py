from __future__ import annotations

from harness.memory.extraction import MemoryUpdater, MemoryUpdateQueue
from harness.memory.manager import (
    MemoryManager,
    get_memory_manager,
    reset_memory_manager,
)
from harness.memory.paths import resolve_memory_paths
from harness.memory.storage import MemoryStorage

"""记忆子系统出口

    职责：跨会话长期记忆的完整子系统，按分层聚包，对外只从这里取
        - config / paths：后端私有配置、路径与身份解析
        - processing：无状态纯变换（清洗 / 信号 / 渲染）
        - storage：memory.json 落盘（乐观锁）+ 旧库迁移
        - extraction：LLM 增量提取 + 防抖队列
        - manager：契约 + PrismMem 实现 + 单例工厂
        - short_term：会话 checkpoint 工厂
        - integration：中间件 + 模型自主存取工具

    对外暴露：
        - MemoryManager / get_memory_manager / reset_memory_manager
        - MemoryStorage / MemoryUpdateQueue / MemoryUpdater / resolve_memory_paths
        - create_checkpointer / MemoryMiddleware / memory_flush_hook
        - save_memory_tool / search_memory_tool / delete_memory_tool
          （后三组走 __getattr__ 延迟导出，避开热路径重依赖）
"""

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


def __getattr__(name: str):
    """延迟导出需要联网/重依赖的对象（checkpointer、中间件与工具不热加载）。"""
    # 短期记忆 checkpoint 工厂
    if name == "create_checkpointer":
        from harness.memory.short_term import create_checkpointer

        globals()["create_checkpointer"] = create_checkpointer
        return create_checkpointer
    # 记忆中间件与摘要冲刷钩子（避免与中间件链的循环依赖影响热路径）
    if name in {"MemoryMiddleware", "memory_flush_hook"}:
        from harness.memory.integration import MemoryMiddleware, memory_flush_hook

        globals()["MemoryMiddleware"] = MemoryMiddleware
        globals()["memory_flush_hook"] = memory_flush_hook
        return globals()[name]
    # 模型自主存取工具（tool 模式才注册）
    if name in {"save_memory_tool", "search_memory_tool", "delete_memory_tool"}:
        from harness.memory.integration import (
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
