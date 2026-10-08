from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

"""沙箱热池

    职责：热池的保活复用与闲置治理，只增删查条目、不执行任何 docker 命令
        - park / reclaim / peek / remove 条目流转
        - idle_expired 收集闲置条目（0 = 永不超时）
        - evict_oldest 容量闸门逐出最旧
        - 内部 RLock，支持主线程 / run 收尾线程 / idle checker 并发访问

    对外暴露：
        - Parkable   可入池沙箱的协议（id / base_url）
        - WarmEntry  一条热池记录（实例 + 入池时间）
        - WarmPool   热池本体
"""

logger = logging.getLogger(__name__)


class Parkable(Protocol):
    """可入池的沙箱（SandboxManager 持有的是 AioSandbox 实例）。"""

    id: str
    base_url: str


@dataclass
class WarmEntry:
    """一条热池记录：保活的实例 + 入池时间（idle 计时基准）。"""

    instance: Any
    parked_at: float


class WarmPool:
    """沙箱热池：park → 保活 → reclaim / idle / evict。"""

    def __init__(self) -> None:
        """初始化空热池（线程安全）。"""
        # 实例 id → 条目；用 RLock 支持同一线程重入（快照内再调其它方法）
        self._entries: dict[str, WarmEntry] = {}
        self._lock = threading.RLock()

    # ── 基础访问 ────────────────────────────────────────────────────────
    def __len__(self) -> int:
        """热池内条目数量。"""
        with self._lock:
            return len(self._entries)

    def contains(self, sandbox_id: str) -> bool:
        """热池中是否存在指定沙箱。"""
        with self._lock:
            return sandbox_id in self._entries

    def snapshot(self) -> list[dict[str, Any]]:
        """返回热池快照（id / base_url / 入池时间），供管理与日志。"""
        with self._lock:
            return [
                {
                    "id": entry.instance.id,
                    "base_url": entry.instance.base_url,
                    "parked_at": entry.parked_at,
                }
                for entry in self._entries.values()
            ]

    def snapshot_sandbox_ids(self) -> set[str]:
        """返回热池内全部沙箱 id（孤儿扫描的跟踪集合用）。"""
        with self._lock:
            return set(self._entries.keys())

    def instances(self) -> list[Any]:
        """返回全部热池实例（销毁/清理用）。"""
        with self._lock:
            return [entry.instance for entry in self._entries.values()]

    # ── 协议操作 ────────────────────────────────────────────────────────
    def park(self, instance: Any) -> None:
        """把沙箱放入热池（幂等：已存在则刷新入池时间）。

        调用方负责保证容器运行中且实例未被 close（实例复用语义）。
        """
        # 没有 id 就无法做键，直接拒绝（避免产生取不回的孤儿条目）
        if instance is None or not getattr(instance, "id", None):
            raise ValueError("park 需要带 id 的沙箱实例")
        with self._lock:
            # 已存在则覆盖：幂等，且刷新 parked_at = 重置 idle 计时
            self._entries[instance.id] = WarmEntry(instance=instance, parked_at=time.time())

    def reclaim(self, sandbox_id: str) -> Any | None:
        # pop 即「取出并移除」：提升回活跃后就不该再留在热池
        """从热池提升一个沙箱回活跃（取出即删除）；不存在返回 None。"""
        with self._lock:
            entry = self._entries.pop(sandbox_id, None)
            return entry.instance if entry is not None else None

    def peek(self, sandbox_id: str) -> Any | None:
        # get 不删：只探测健康，真正取用由 reclaim 完成
        """只看不取（健康检查前探测用）；不存在返回 None。"""
        with self._lock:
            entry = self._entries.get(sandbox_id)
            return entry.instance if entry is not None else None

    def remove(self, sandbox_id: str) -> Any | None:
        """从热池摘除指定沙箱（销毁时调用）；不存在返回 None。"""
        with self._lock:
            entry = self._entries.pop(sandbox_id, None)
            return entry.instance if entry is not None else None

    def idle_expired(
        self,
        idle_timeout: float,
        *,
        now: float | None = None,
    ) -> list[Any]:
        """收集闲置超过 idle_timeout 的实例（0=永不超时）；不删除。

        调用方拿到列表后自行销毁容器并 ``remove`` 每一条。
        """
        # idle_timeout<=0 视为关闭闲置回收（永不超时）
        if idle_timeout <= 0:
            return []
        current = now if now is not None else time.time()
        with self._lock:
            return [
                entry.instance
                for entry in self._entries.values()
                if current - entry.parked_at >= idle_timeout
            ]

    def evict_oldest(self) -> Any | None:
        """逐出最旧的热池条目（容量闸门用）；取出即删除，空池返回 None。"""
        with self._lock:
            if not self._entries:
                return None
            # 取 parked_at 最小者（入池最久）逐出
            oldest_id = min(self._entries, key=lambda sid: self._entries[sid].parked_at)
            entry = self._entries.pop(oldest_id)
            return entry.instance

    def clear(self) -> list[Any]:
        """清空热池，返回全部实例（调用方负责销毁）。"""
        with self._lock:
            instances = [entry.instance for entry in self._entries.values()]
            self._entries.clear()
            return instances
