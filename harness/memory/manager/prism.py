from __future__ import annotations

import logging
from typing import Any, ClassVar, Literal

from harness.memory.config import PrismMemConfig
from harness.memory.extraction import MemoryUpdateQueue, MemoryUpdater, QueueFull
from harness.memory.manager.contract import MemoryManager
from harness.memory.paths import DEFAULT_AGENT_BUCKET
from harness.memory.processing import (
    detect_signals,
    filter_messages_for_memory,
    filter_trivial,
    format_memory_for_injection,
    load_patterns,
)
from harness.memory.storage import MemoryStorage

logger = logging.getLogger(__name__)

"""PrismMem 记忆管理器（manager.prism）

    职责：本项目唯一的 MemoryManager 实现——装配 storage / updater / queue
         三件套，并在写入口统一做「过滤 → trivial 过滤 → 双方校验 → 信号
         检测 → 入队」。
    异常契约：队列背压（QueueFull）、存储异常全在内部消化为日志，绝不向
         Agent 主链路扩散（记忆是 best-effort）。
"""

# 缺省 agent 桶（单 Agent 架构下所有记忆按 user 分桶）
_DEFAULT_BUCKET = DEFAULT_AGENT_BUCKET


def _resolve_agent_name(agent_name: str | None) -> str:
    """规范化 agent_name（本项目单 Agent，保留桶语义便于未来扩展）。"""
    return agent_name.lower() if agent_name is not None else _DEFAULT_BUCKET


def _fact_confidence(fact: dict[str, Any]) -> float:
    """安全取事实置信度（检索排序用；异常/越界归 0.5）。"""
    raw = fact.get("confidence")
    if raw is None or isinstance(raw, bool):
        return 0.5
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(value, 1.0))


class PrismMemoryManager(MemoryManager):
    """项目默认记忆后端：JSON 文档存储 + LLM 防抖提取（PrismMem）。"""

    supports_search: ClassVar[bool] = True

    def __init__(self, mem_config: PrismMemConfig, *, mode: Literal["middleware", "tool"] = "middleware"):
        """装配存储 / 更新器 / 队列（DI，全部实例私有）。

        参数：
            mem_config: 后端私有配置
            mode: middleware（自动提取）/ tool（只注入）
        """
        self.mode = mode
        self._config = mem_config
        # 三件套自下而上装配：storage ← updater ← queue（queue 驱动 updater）
        self._storage = MemoryStorage(mem_config)
        self._updater = MemoryUpdater(mem_config, self._storage)
        self._queue = MemoryUpdateQueue(mem_config, self._updater)
        # trivial 应声模式（写入口用于过滤无信息轮）
        self._trivial_patterns = load_patterns("trivial", patterns_dir=mem_config.patterns_dir)
        self._patterns_dir = mem_config.patterns_dir

    # ── 写 ──────────────────────────────────────────────────────────────
    def _prepare_update(
        self,
        messages: list[Any],
    ) -> tuple[list[Any], frozenset[str]] | None:
        """过滤 + trivial + 双方校验 + 信号检测；无有效对话返回 None。

        参数：
            messages: 本轮完整消息

        返回：
            (清洗后的消息, 命中信号集) 或 None（不值得提取）
        """
        # 1.只留用户/最终助手消息，再剔除纯应声轮
        filtered = filter_messages_for_memory(messages)
        filtered = filter_trivial(filtered, patterns=self._trivial_patterns)
        # 2.必须同时有用户与助手消息才值得提取
        user_messages = [m for m in filtered if getattr(m, "type", None) == "human"]
        assistant_messages = [m for m in filtered if getattr(m, "type", None) == "ai"]
        if not user_messages or not assistant_messages:
            return None
        # 3.信号检测（作为提取 hint）
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
        """把一轮对话防抖入队（内部完成过滤与信号检测）。"""
        # 1.无有效对话直接返回
        prepared = self._prepare_update(messages)
        if prepared is None:
            return
        filtered, signals = prepared
        # 2.入队；背压满只记日志丢弃（下轮重喂），绝不抛给主链路
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
        """紧急入队立即处理（摘要压缩前调用，bypass 水位线）。"""
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
        """返回可直接注入系统提示的记忆文本（读失败或无内容返回空串）。"""
        # 1.读记忆文档，异常静默降级为「不注入」
        try:
            memory_data = self._updater.get_memory_data(user_id)
        except Exception as exc:  # noqa: BLE001 —— 记忆异常静默降级，不阻断对话
            logger.warning("记忆读取失败，本次不注入: %s", exc)
            return ""
        # 2.按 token 预算 + 必保类别渲染成注入文本
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
        """子串检索（大小写不敏感）+ 置信度降序；空 query 返回空。

        参数：
            query: 关键词（匹配 content 或 key）
            top_k: 返回条数上限
            category: 限定类别

        返回：
            命中的事实列表
        """
        # 1.空 query / 非法 top_k 直接空
        if not query or not query.strip() or top_k <= 0:
            return []
        query_lower = query.strip().lower()
        # 2.读文档，失败返回空
        try:
            memory_data = self._updater.get_memory_data(user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆检索读取失败: %s", exc)
            return []
        # 3.content 或 key 子串命中 + 类别过滤
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
        # 4.置信度降序，截 top_k
        matched.sort(key=_fact_confidence, reverse=True)
        return matched[: max(1, int(top_k))]

    # ── 管理 ────────────────────────────────────────────────────────────
    def get_memory(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        """返回用户记忆完整文档。"""
        return self._updater.get_memory_data(user_id)

    def import_memory(
        self,
        memory_data: dict[str, Any],
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        """导入记忆文档（合并语义，交给 updater 落库）。"""
        return self._updater.import_memory_data(memory_data, user_id=user_id)

    def clear_memory(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        """清空用户记忆文档。"""
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
        """手动新增事实（转交 updater，key 稳定语义由 updater 处理）。"""
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
        """删除事实（转交 updater）。"""
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
        """更新事实（转交 updater）。"""
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
