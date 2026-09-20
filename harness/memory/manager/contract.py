from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal

"""记忆管理器契约（manager.contract）

    职责：定义「跨会话长期记忆后端」的统一接口，主链路（中间件 / 工具 /
         注入 / 生命周期）只依赖本抽象，不绑定具体实现。
    分层：tier-1 必须实现（写 add / 读注入 get_context）；tier-2 检索与管理
         （search / get_memory / clear / fact CRUD / 生命周期）给默认实现，
         不强制每个后端都写。
"""


class MemoryManager(ABC):
    """记忆管理器契约（tier-1 必须实现，tier-2 带默认实现）。"""

    # 能力声明（接入检索后端时用于标注；本项目恒为可检索）
    supports_search: ClassVar[bool] = True
    # 运行模式：middleware（自动注入+自动提取）/ tool（只注入，写靠工具）
    mode: Literal["middleware", "tool"] = "middleware"

    # ── Tier 1：写 / 读注入 ────────────────────────────────────────────
    @abstractmethod
    def add(
        self,
        thread_id: str,
        messages: list[Any],
        *,
        agent_name: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """把一轮对话入队，防抖异步更新记忆（实现内部完成过滤与信号检测）。"""

    @abstractmethod
    def add_nowait(
        self,
        thread_id: str,
        messages: list[Any],
        *,
        agent_name: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """紧急入队立即处理（摘要压缩前调用，bypass 水位线）。"""

    @abstractmethod
    def get_context(
        self,
        user_id: str | None,
        *,
        agent_name: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """返回可直接注入系统提示的记忆文本（空串 = 无可注入内容）。"""

    # ── Tier 2：检索与管理 ─────────────────────────────────────────────
    @abstractmethod
    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """检索匹配事实（按置信度降序；空 query 返回空列表）。"""

    @abstractmethod
    def get_memory(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        """返回用户记忆完整文档。"""

    def import_memory(
        self,
        memory_data: dict[str, Any],
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        """导入记忆文档（默认不支持，PrismMem 覆盖）。"""
        raise NotImplementedError(f"import_memory not supported by {type(self).__name__}")

    @abstractmethod
    def clear_memory(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        """清空用户记忆文档。"""

    def create_fact(
        self,
        content: str,
        category: str = "context",
        confidence: float = 0.5,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        """手动新增事实（默认不支持，PrismMem 覆盖）。"""
        raise NotImplementedError(f"create_fact not supported by {type(self).__name__}")

    def delete_fact(
        self,
        fact_id: str,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        """删除事实（默认不支持，PrismMem 覆盖）。"""
        raise NotImplementedError(f"delete_fact not supported by {type(self).__name__}")

    def update_fact(
        self,
        fact_id: str,
        content: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        """更新事实（默认不支持，PrismMem 覆盖）。"""
        raise NotImplementedError(f"update_fact not supported by {type(self).__name__}")

    def shutdown_flush(self, timeout: float) -> bool:
        """优雅关闭时排空待处理更新（默认无缓冲直接 True，PrismMem 覆盖）。"""
        return True

    def close(self) -> None:
        """释放资源（默认无操作）。"""
