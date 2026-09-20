from __future__ import annotations

from harness.memory.manager.contract import MemoryManager
from harness.memory.manager.factory import get_memory_manager, reset_memory_manager
from harness.memory.manager.prism import PrismMemoryManager
from harness.memory.paths import DEFAULT_AGENT_BUCKET, resolve_memory_paths

"""编排包（manager）

    职责：把契约、Prism 实现、单例工厂聚成一个稳定的对外入口。
    内容：contract（抽象契约）、prism（PrismMem 实现）、factory（单例工厂）。

    对外暴露：
        - MemoryManager / PrismMemoryManager
        - get_memory_manager / reset_memory_manager
        - resolve_memory_paths / DEFAULT_AGENT_BUCKET（向后兼容转导出）
"""

__all__ = [
    "MemoryManager",
    "PrismMemoryManager",
    "get_memory_manager",
    "reset_memory_manager",
    "resolve_memory_paths",
    "DEFAULT_AGENT_BUCKET",
]
