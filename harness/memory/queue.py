"""记忆更新防抖队列（异步 + 防抖 + 合并 + 背压）。

职责（对应参考实现 queue 模块）：
    - ``add``：把一轮对话入队，重置防抖计时器；防抖窗口内同
      (thread_id, user_id) 的多次入队**合并**为一次提取（新消息覆盖旧
      消息，信号并集保留）；
    - ``add_nowait``：紧急冲刷（摘要压缩前调用），立即后台处理且
      bypass_watermark（见 updater），并与普通更新共存互不覆盖；
    - 背压：队列深度达到 ``queue_max_depth`` 时拒绝**非信号**普通更新
      （抛 QueueFull → 记日志丢弃，下轮重喂）；信号更新永远准入，
      重要记忆不因拥堵被丢弃；
    - ``_process_queue`` 在 Timer 线程逐个调用 ``updater.update_memory``
      （同步 LLM 调用，不占用主事件循环；项间 0.5s 间隔防限流）；
    - ``flush_sync``：优雅关闭时在守护线程排空队列（硬超时兜底），
      避免重启丢失仍在防抖缓冲里的更新。

队列入队时快照 user_id（Timer 线程不继承 ContextVar）；所有共享状态
访问走锁。进程内纯内存队列：非优雅退出丢尾属可接受（best-effort）。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from harness.memory.config import PrismMemConfig

if TYPE_CHECKING:
    from harness.memory.updater import MemoryUpdater

logger = logging.getLogger(__name__)


class QueueFull(Exception):
    """背压满时拒绝非信号更新（信号更新永远准入）。"""


def queue_key(
    thread_id: str,
    user_id: str | None,
) -> tuple[str, str | None]:
    """防抖合并的身份键（本项目单 Agent，不需要 agent 维度）。"""
    return (thread_id, user_id)


@dataclass
class ConversationContext:
    """一份待处理的对话（入队时快照，跨 Timer 线程安全）。"""

    thread_id: str
    messages: list[Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    user_id: str | None = None
    trace_id: str | None = None
    signals: frozenset[str] = field(default_factory=frozenset)
    # 紧急冲刷（摘要压缩前）：绕过水位线、与普通更新共存
    bypass_watermark: bool = False


class MemoryUpdateQueue:
    """记忆更新防抖队列。"""

    def __init__(self, config: PrismMemConfig, updater: "MemoryUpdater"):
        """注入配置与更新器。"""
        self._config = config
        self._updater = updater
        self._items: list[ConversationContext] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._processing = False
        self._processing_thread: threading.Thread | None = None
        self._reprocess_pending = False

    # ── 入队 ────────────────────────────────────────────────────────────
    def add(
        self,
        thread_id: str,
        messages: list[Any],
        *,
        agent_name: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        signals: frozenset[str] | None = None,
    ) -> None:
        """入队并重置防抖计时器（agent_name 保留兼容签名，单 Agent 忽略）。"""
        with self._lock:
            self._enqueue_locked(
                thread_id=thread_id,
                messages=messages,
                user_id=user_id,
                trace_id=trace_id,
                signals=frozenset(signals) if signals else frozenset(),
                bypass_watermark=False,
            )
            self._schedule_timer(self._config.debounce_seconds)
        logger.info("记忆更新已入队（thread=%s），队列深度 %d", thread_id, len(self._items))

    def add_nowait(
        self,
        thread_id: str,
        messages: list[Any],
        *,
        agent_name: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        signals: frozenset[str] | None = None,
    ) -> None:
        """紧急入队并立即后台处理（bypass_watermark=True，摘要冲刷用）。"""
        with self._lock:
            self._enqueue_locked(
                thread_id=thread_id,
                messages=messages,
                user_id=user_id,
                trace_id=trace_id,
                signals=frozenset(signals) if signals else frozenset(),
                bypass_watermark=True,
            )
            self._schedule_timer(0)
        logger.info("记忆紧急冲刷已入队（thread=%s），队列深度 %d", thread_id, len(self._items))

    def _enqueue_locked(
        self,
        *,
        thread_id: str,
        messages: list[Any],
        user_id: str | None,
        trace_id: str | None,
        signals: frozenset[str],
        bypass_watermark: bool = False,
    ) -> None:
        """加锁入队：同 (thread, user, 普通/紧急) 合并；背压拒绝非信号新项。"""
        key = queue_key(thread_id, user_id)
        # 紧急与普通更新按 bypass 区分共存：紧急冲刷不能覆盖普通更新的未提取尾部
        existing = next(
            (
                c
                for c in self._items
                if queue_key(c.thread_id, c.user_id) == key
                and c.bypass_watermark == bypass_watermark
            ),
            None,
        )
        max_depth = self._config.queue_max_depth
        if (
            max_depth > 0
            and not bypass_watermark
            and not signals
            and existing is None
            and len(self._items) >= max_depth
        ):
            raise QueueFull(
                f"记忆更新队列满（深度 {len(self._items)} >= {max_depth}）；"
                f"thread={thread_id} 的非信号更新被拒绝（下轮自动重喂）"
            )
        merged_signals = signals | (existing.signals if existing is not None else frozenset())
        context = ConversationContext(
            thread_id=thread_id,
            messages=messages,
            user_id=user_id,
            trace_id=trace_id,
            signals=merged_signals,
            bypass_watermark=bypass_watermark,
        )
        if existing is not None:
            self._items = [
                c
                for c in self._items
                if not (
                    queue_key(c.thread_id, c.user_id) == key
                    and c.bypass_watermark == bypass_watermark
                )
            ]
        self._items.append(context)

    # ── 计时器 ──────────────────────────────────────────────────────────
    def _schedule_timer(self, delay_seconds: float) -> None:
        """（调用方必须持锁）取消旧计时器并安排新计时器。"""
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(delay_seconds, self._process_queue)
        self._timer.daemon = True
        self._timer.start()

    # ── 处理 ────────────────────────────────────────────────────────────
    def _process_queue(self, *, skip_inter_item_delay: bool = False) -> None:
        """取出全部待处理项并逐个更新（Timer 线程 / flush 线程执行）。"""
        with self._lock:
            if self._processing:
                self._reprocess_pending = True
                return
            if not self._items:
                return
            self._processing = True
            self._processing_thread = threading.current_thread()
            contexts_to_process = self._items
            self._items = []
            self._timer = None

        logger.info("开始处理 %d 项记忆更新", len(contexts_to_process))
        succeeded = 0
        failed = 0
        try:
            for context in contexts_to_process:
                try:
                    ok = self._updater.update_memory(
                        messages=context.messages,
                        thread_id=context.thread_id,
                        user_id=context.user_id,
                        signals=context.signals,
                        bypass_watermark=context.bypass_watermark,
                    )
                    if ok:
                        succeeded += 1
                    else:
                        failed += 1
                        logger.warning("记忆更新未成功（thread=%s）", context.thread_id)
                except Exception as exc:  # noqa: BLE001 —— 单项失败不阻断批次
                    failed += 1
                    logger.error("记忆更新异常（thread=%s）: %s", context.thread_id, exc)
                if not skip_inter_item_delay and len(contexts_to_process) > 1:
                    time.sleep(0.5)  # 项间防限流
        finally:
            if succeeded or failed:
                logger.info("记忆更新批次完成：成功 %d，失败 %d", succeeded, failed)
            with self._lock:
                self._processing = False
                self._processing_thread = None
                if self._reprocess_pending:
                    self._reprocess_pending = False
                    if self._items:
                        self._schedule_timer(0)

    def flush(self, *, skip_inter_item_delay: bool = False) -> None:
        """立即处理队列（测试 / 关闭前调用）。"""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._process_queue(skip_inter_item_delay=skip_inter_item_delay)

    def flush_nowait(self) -> None:
        """后台立即开始处理（不等待）。"""
        with self._lock:
            self._schedule_timer(0)

    def flush_sync(self, timeout: float) -> bool:
        """有界同步排空（优雅关闭用）：先 join 在飞 worker，再排空队列。

        Returns:
            True 仅在超时内真正排空（无在飞 worker、无异常）。
        """
        deadline = time.monotonic() + timeout

        # 1. 等待在飞 worker（它已取出但尚未处理完的项只有 join 才保得住）
        with self._lock:
            in_flight = self._processing_thread
        if in_flight is not None:
            in_flight.join(timeout=max(0.0, deadline - time.monotonic()))

        # 2. 已空且无 worker → 完成
        if self.pending_count == 0 and not self.is_processing:
            return True

        # 3. 守护线程排空，Event 等待作为硬超时（同步 LLM 调用不可中断）
        success = False
        done = threading.Event()

        def _run() -> None:
            nonlocal success
            try:
                self.flush(skip_inter_item_delay=True)
                success = True
            except Exception:  # noqa: BLE001
                logger.exception("记忆队列关闭排空失败")
            finally:
                done.set()

        worker = threading.Thread(target=_run, name="memory-shutdown-flush", daemon=True)
        worker.start()
        finished = done.wait(timeout=max(0.0, deadline - time.monotonic()))
        if not finished:
            return False
        return bool(success) and not self.is_processing

    def clear(self) -> None:
        """清空队列（不计费处理；测试用）。"""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._items = []
            self._processing = False
            self._processing_thread = None
            self._reprocess_pending = False

    # ── 状态 ────────────────────────────────────────────────────────────
    @property
    def pending_count(self) -> int:
        """待处理项数量。"""
        with self._lock:
            return len(self._items)

    @property
    def is_processing(self) -> bool:
        """是否正在处理。"""
        with self._lock:
            return self._processing