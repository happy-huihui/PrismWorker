"""记忆管理器：后端无关契约 + PrismMem 实现 + 单例工厂。

模块职责（对应参考实现 manager 模块，去掉插件扫描与 tier-3 可选钩子）：
    - ``MemoryManager``：后端无关抽象契约，主链路（中间件 / 工具 /
      注入 / 生命周期）只依赖该接口；
    - ``PrismMemoryManager``：本项目唯一实现，装配 storage / updater /
      queue 三件套，并在写入口统一做「消息过滤 → trivial 过滤 → 双方
      校验 → 信号检测 → 入队」；
    - ``get_memory_manager``：线程安全单例工厂（首次调用时从全局配置
      构建，并顺手执行旧 SQLite 库迁移）；
    - ``resolve_memory_paths``：从 paths.py 转导出，保持旧调用方
      （checkpointer / app 层）import 路径不变。

写入口的异常契约：队列背压（QueueFull）、存储异常全部在内部消化为
日志，绝不向 Agent 主链路扩散（记忆是 best-effort）。
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal

from harness.memory.config import PrismMemConfig
from harness.memory.message_processing import (
    detect_signals,
    filter_messages_for_memory,
    filter_trivial,
    load_patterns,
)
from harness.memory.paths import DEFAULT_AGENT_BUCKET, resolve_memory_paths  # noqa: F401 —— 兼容转导出
from harness.memory.prompt import format_memory_for_injection
from harness.memory.queue import MemoryUpdateQueue, QueueFull
from harness.memory.storage import MemoryStorage, migrate_legacy_sqlite
from harness.memory.updater import MemoryUpdater

logger = logging.getLogger(__name__)

# 单例与构造锁
_manager: MemoryManager | None = None
_manager_lock = threading.Lock()


def _resolve_agent_name(agent_name: str | None) -> str:
    """规范化 agent_name（本项目单 Agent，保留桶语义便于未来扩展）。"""
    return agent_name.lower() if agent_name is not None else DEFAULT_AGENT_BUCKET


class MemoryManager(ABC):
    """记忆管理器契约（tier-1 必须实现，tier-2 带默认实现）。"""

    # 分类标签（保留 ClassVar 语义：后续接入检索后端时声明能力用）
    supports_search: ClassVar[bool] = True
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


class PrismMemoryManager(MemoryManager):
    """项目默认记忆后端：JSON 文档存储 + LLM 防抖提取（PrismMem）。"""

    supports_search: ClassVar[bool] = True

    def __init__(self, mem_config: PrismMemConfig, *, mode: Literal["middleware", "tool"] = "middleware"):
        """装配存储 / 更新器 / 队列（DI，全部实例私有）。"""
        self.mode = mode
        self._config = mem_config
        self._storage = MemoryStorage(mem_config)
        self._updater = MemoryUpdater(mem_config, self._storage)
        self._queue = MemoryUpdateQueue(mem_config, self._updater)
        self._trivial_patterns = load_patterns("trivial", patterns_dir=mem_config.patterns_dir)
        self._patterns_dir = mem_config.patterns_dir

    # ── 写 ──────────────────────────────────────────────────────────────
    def _prepare_update(
        self,
        messages: list[Any],
    ) -> tuple[list[Any], frozenset[str]] | None:
        """过滤 + trivial + 双方校验 + 信号检测；无有效对话返回 None。"""
        filtered = filter_messages_for_memory(messages)
        filtered = filter_trivial(filtered, patterns=self._trivial_patterns)
        user_messages = [m for m in filtered if getattr(m, "type", None) == "human"]
        assistant_messages = [m for m in filtered if getattr(m, "type", None) == "ai"]
        if not user_messages or not assistant_messages:
            return None
        signals = detect_signals(filtered, patterns_dir=self._patterns_dir)
        return filtered, frozenset(signals)

    def add(
        self,
        thread_id: str,
        messages: list[Any],
        *,
        agent_name: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        prepared = self._prepare_update(messages)
        if prepared is None:
            return
        filtered, signals = prepared
        try:
            self._queue.add(
                thread_id=thread_id,
                messages=filtered,
                agent_name=_resolve_agent_name(agent_name),
                user_id=user_id,
                trace_id=trace_id,
                signals=signals,
            )
        except QueueFull as exc:
            logger.warning("记忆更新被背压拒绝（thread=%s）: %s", thread_id, exc)

    def add_nowait(
        self,
        thread_id: str,
        messages: list[Any],
        *,
        agent_name: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        prepared = self._prepare_update(messages)
        if prepared is None:
            return
        filtered, signals = prepared
        try:
            self._queue.add_nowait(
                thread_id=thread_id,
                messages=filtered,
                agent_name=_resolve_agent_name(agent_name),
                user_id=user_id,
                trace_id=trace_id,
                signals=signals,
            )
        except QueueFull as exc:
            logger.warning("记忆紧急冲刷被背压拒绝（thread=%s）: %s", thread_id, exc)

    # ── 读 / 注入 ───────────────────────────────────────────────────────
    def get_context(
        self,
        user_id: str | None,
        *,
        agent_name: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        try:
            memory_data = self._updater.get_memory_data(user_id)
        except Exception as exc:  # noqa: BLE001 —— 记忆异常静默降级，不阻断对话
            logger.warning("记忆读取失败，本次不注入: %s", exc)
            return ""
        return format_memory_for_injection(
            memory_data,
            max_tokens=self._config.max_injection_tokens,
            guaranteed_categories=self._config.guaranteed_categories,
            guaranteed_token_budget=self._config.guaranteed_token_budget,
        )

    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """子串检索（大小写不敏感）+ 置信度降序；空 query 返回空。"""
        if not query or not query.strip() or top_k <= 0:
            return []
        query_lower = query.strip().lower()
        try:
            memory_data = self._updater.get_memory_data(user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆检索读取失败: %s", exc)
            return []
        matched = [
            fact
            for fact in memory_data.get("facts", [])
            if isinstance(fact, dict)
            and (
                (
                    isinstance(fact.get("content"), str)
                    and query_lower in fact["content"].lower()
                )
                or (
                    isinstance(fact.get("key"), str)
                    and query_lower in fact["key"].lower()
                )
            )
            and (category is None or fact.get("category") == category)
        ]
        matched.sort(key=lambda f: _fact_confidence(f), reverse=True)
        return matched[: max(1, int(top_k))]

    # ── 管理 ────────────────────────────────────────────────────────────
    def get_memory(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        return self._updater.get_memory_data(user_id)

    def import_memory(
        self,
        memory_data: dict[str, Any],
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        return self._updater.import_memory_data(memory_data, user_id=user_id)

    def clear_memory(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        return self._updater.clear_memory_data(user_id)

    def create_fact(
        self,
        content: str,
        category: str = "context",
        confidence: float = 1.0,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
        key: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        return self._updater.create_fact(
            content,
            category=category,
            confidence=confidence,
            user_id=user_id,
            key=key,
        )

    def delete_fact(
        self,
        fact_id: str,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        return self._updater.delete_fact(fact_id, user_id=user_id)

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
        return self._updater.update_fact(
            fact_id,
            content=content,
            category=category,
            confidence=confidence,
            user_id=user_id,
        )

    # ── 生命周期 ────────────────────────────────────────────────────────
    def shutdown_flush(self, timeout: float) -> bool:
        """优雅关闭：有界排空防抖队列（超时未完成返回 False 由调用方告警）。"""
        return self._queue.flush_sync(timeout)

    def close(self) -> None:
        """释放存储资源（当前实现无外部连接，保持接口）。"""
        self._storage.close()


def _fact_confidence(fact: dict[str, Any]) -> float:
    """安全取事实置信度（检索排序用）。"""
    raw = fact.get("confidence")
    if raw is None or isinstance(raw, bool):
        return 0.5
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(value, 1.0))


# ── 单例工厂 ─────────────────────────────────────────────────────────────
def get_memory_manager() -> MemoryManager:
    """返回进程级记忆管理器单例（线程安全；首次构建时顺带执行旧库迁移）。

    Returns:
        MemoryManager 实例。记忆未启用（memory.enabled=false）时返回
        ``None``？——不：为保持调用方简单，本实现始终可构建；禁用开关
        由调用方（中间件 / 工具）自行判断。
    """
    global _manager
    if _manager is not None:
        return _manager
    with _manager_lock:
        if _manager is not None:
            return _manager

        from harness.config.memory_config import get_memory_config

        host_config = get_memory_config()
        mem_config = PrismMemConfig.from_backend_config(host_config.backend_config)
        # 旧 SQLite 库一次性迁移（幂等：无旧库 / 已备份则直接跳过）
        try:
            migrated = migrate_legacy_sqlite(mem_config)
            if migrated:
                logger.info("旧长期记忆库迁移完成：%d 个用户文档", migrated)
        except Exception as exc:  # noqa: BLE001 —— 迁移失败不阻断记忆启动
            logger.warning("旧长期记忆库迁移失败（已跳过，不影响新记忆）: %s", exc)

        _manager = PrismMemoryManager(mem_config, mode=host_config.mode)
        logger.info(
            "记忆管理器就绪: mode=%s storage_path=%s",
            host_config.mode,
            mem_config.storage_path or "(默认数据目录)",
        )
        return _manager


def reset_memory_manager() -> None:
    """清空单例（测试 / 运行时重建用）。"""
    global _manager
    with _manager_lock:
        current = _manager
        _manager = None
        if current is not None:
            try:
                current.close()
            except Exception:  # noqa: BLE001
                logger.debug("记忆管理器关闭异常（忽略）", exc_info=True)