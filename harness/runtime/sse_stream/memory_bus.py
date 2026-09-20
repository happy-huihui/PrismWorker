from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from harness.runtime.events.types import RunEvent, TERMINAL_EVENT_TYPES

"""事件总线（memory_bus）——run 事件的进程内发布/订阅枢纽。

    设计对齐「生产侧不阻塞」原则：订阅者队列有界（默认 1000 条），队列满时
    普通事件直接丢弃并计数（思考链可丢、agent 执行不可停）；终端事件
    （run_finished / run_error）与 END 哨兵走高优先级必达通道（阻塞入队，
    保证消费端一定能收到收尾信号）。订阅者彼此独立，互不拖累。

    典型消费流程：
        async for event in bus.subscribe(run_id):
            ...  # event.seq 连续，可直接作 SSE 的 id
    订阅在迭代结束或 run 结束时自动摘除。

    对外暴露：
        - EventBus           总线本体（publish / publish_end / subscribe / cancel）
        - get_event_bus      进程级单例
        - reset_event_bus    重置单例（测试隔离）

    说明：本包只做进程内 pub/sub，不涉及 HTTP；SSE 帧格式化与
         EventSourceResponse 仍在 app 路由层。
"""

logger = logging.getLogger(__name__)

# 每个订阅者队列容量上限（满则丢普通事件）
_DEFAULT_QUEUE_MAXSIZE = 1000


@dataclass(frozen=True)
class _EndMarker:
    """END 哨兵：订阅迭代的终止信号（携带原因）。"""

    cause: str


class EventBus:
    """进程内事件总线：按 run_id 路由，多订阅者 fan-out。"""

    def __init__(self, *, queue_maxsize: int = _DEFAULT_QUEUE_MAXSIZE) -> None:
        """初始化；queue_maxsize 为每个订阅者队列的容量上限。

        参数：
            queue_maxsize: 单订阅者队列容量（满时普通事件丢弃并计数）
        """
        # run_id -> 订阅者队列列表；另记录已结束 run、递增序号、丢弃计数
        self._subscribers: dict[str, list[asyncio.Queue[Any]]] = {}
        self._ended: set[str] = set()
        self._seq: dict[str, int] = {}
        self._dropped: dict[str, int] = {}
        self._unwatched_events: int = 0
        self._queue_maxsize = queue_maxsize
        self._lock = threading.Lock()


    async def publish(self, event: RunEvent) -> RunEvent:
        """发布一条事件：分配 seq 并广播给该 run 的所有订阅者。

        参数：
            event: 待发布事件（不带 seq）

        返回：
            带 seq 的最终事件（供生产方记录）；终端事件必达
        """
        # 为该 run 分配递增序号，并复制成带 seq 的事件
        seq = self._next_seq(event.run_id)
        event = _with_seq(event, seq)
        subs = self._subscribers.get(event.run_id)
        # 无订阅者：计入 unwatched，直接返回
        if not subs:
            self._unwatched_events += 1
            return event
        is_terminal = event.type.value in TERMINAL_EVENT_TYPES
        for queue in list(subs):
            # 终端事件走高优先级必达通道（await 阻塞入队）
            if is_terminal:
                await queue.put(event)
            else:
                # 普通事件尽力而为：队列满则丢弃并计数
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    self._dropped[event.run_id] = self._dropped.get(event.run_id, 0) + 1
                    logger.debug("run %s 有订阅者队列已满，丢弃事件 %s", event.run_id, event.type.value)
        return event

    async def publish_end(self, run_id: str, *, cause: str = "completed") -> None:
        """发布 END 哨兵：所有订阅者迭代自然结束并摘除。

        参数：
            run_id: 目标 run
            cause: 结束原因（默认 completed）
        """
        # 标记已结束，向所有订阅队列投递 END 哨兵
        self._ended.add(run_id)
        subs = self._subscribers.pop(run_id, [])
        for queue in subs:
            await queue.put(_EndMarker(cause))


    async def subscribe(self, run_id: str) -> AsyncIterator[RunEvent]:
        """订阅一个 run 的事件流，直到 END 哨兵到达。

        参数：
            run_id: 目标 run

        返回：
            异步迭代器，逐个产出该 run 的事件；run 已结束则立即结束
        """
        # 每个订阅者拥有独立队列；已结束的 run 不再产生事件
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._queue_maxsize)
        if run_id in self._ended:
            return
        self._subscribers.setdefault(run_id, []).append(queue)
        try:
            while True:
                item = await queue.get()
                # 收到 END 哨兵即收尾
                if isinstance(item, _EndMarker):
                    return
                yield item  # type: ignore[misc]
        finally:
            # 迭代结束（含异常）自动摘除订阅
            self._detach(run_id, queue)


    def cancel(self, run_id: str) -> None:
        """取消订阅：以 cancelled 哨兵尽快结束订阅流（保留已发布事件）。

        参数：
            run_id: 目标 run
        """
        # run 被中断时消费端仍能看到取消前已发布的事件；队列满时挤出最旧
        # 事件腾出槽位，保证 END 哨兵必达。
        subs = self._subscribers.pop(run_id, [])
        self._ended.add(run_id)
        for queue in subs:
            marker = _EndMarker("cancelled")
            for _attempt in range(2):
                try:
                    queue.put_nowait(marker)
                    break
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

    def dropped_count(self, run_id: str | None = None) -> int:
        """查询丢弃统计；run_id 缺省返回全部（含无订阅者丢弃）。

        参数：
            run_id: 指定 run；None 汇总全部

        返回：
            丢弃事件计数
        """
        if run_id is not None:
            return self._dropped.get(run_id, 0)
        return sum(self._dropped.values()) + self._unwatched_events

    def run_count(self) -> int:
        """当前有活跃订阅的 run 数（调试/健康检查用）。"""
        return len(self._subscribers)

    async def close(self) -> None:
        """关闭总线：以 cancelled 终止所有遗留订阅并清空内部表。"""
        for run_id in list(self._subscribers.keys()):
            self.cancel(run_id)
        self._seq.clear()
        self._dropped.clear()
        self._ended.clear()


    def _next_seq(self, run_id: str) -> int:
        """为该 run 分配下一个递增序号。

        参数：
            run_id: 目标 run

        返回：
            自增前的当前序号
        """
        seq = self._seq.get(run_id, 0)
        self._seq[run_id] = seq + 1
        return seq

    def _detach(self, run_id: str, queue: asyncio.Queue[Any]) -> None:
        """从订阅者表中摘除一个队列（列表空时删除 run 键）。

        参数：
            run_id: 目标 run
            queue: 待摘除的订阅者队列
        """
        subs = self._subscribers.get(run_id)
        if not subs:
            return
        try:
            subs.remove(queue)
        except ValueError:
            return
        if not subs:
            self._subscribers.pop(run_id, None)


def _with_seq(event: RunEvent, seq: int) -> RunEvent:
    """用指定 seq 复制事件（dataclass frozen，只能重建）。

    参数：
        event: 原事件
        seq: 新序号

    返回：
        带新 seq 的事件副本
    """
    from dataclasses import replace

    return replace(event, seq=seq)



_event_bus: EventBus | None = None
_event_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """返回进程级 EventBus 单例（懒构造，线程安全）。"""
    global _event_bus
    if _event_bus is not None:
        return _event_bus
    with _event_bus_lock:
        if _event_bus is None:
            _event_bus = EventBus()
        return _event_bus


def reset_event_bus() -> None:
    """清空单例（测试隔离用：下一个 get_event_bus 重新构造）。"""
    global _event_bus
    with _event_bus_lock:
        _event_bus = None
