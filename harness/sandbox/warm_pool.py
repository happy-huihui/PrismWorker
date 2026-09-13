"""沙箱热池（Warm Pool）——保活复用与闲置治理的纯数据结构与策略。

职责（对应参考实现 aio_sandbox_provider 的 warm pool 协议与
WarmPoolLifecycleMixin，去掉跨进程 ownership）：
    - ``park``：把 run 结束的沙箱放回热池（容器保持运行，零冷启动复用）；
    - ``reclaim``：取用时从热池提升回活跃（同线程下轮直接复用）；
    - ``idle_expired``：按 idle_timeout 收集闲置超时的条目（供 idle checker
      销毁容器）——配置字段 idle_timeout 在本模块真正生效；
    - ``evict_oldest``：容量闸门——活跃+热池达到 replicas 上限时逐出最旧
      的热池条目（参考实现的 _evict_oldest_warm）；
    - ``snapshot / clear``：管理接口与优雅关闭。

线程安全：内部 RLock，可被主线程、run 收尾线程与 idle checker 守护线程
并发访问。模块只处理「条目的增删查」，不执行任何 docker 命令——容器销毁
由调用方（SandboxManager）根据返回的条目执行，保持职责分离。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

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
        if instance is None or not getattr(instance, "id", None):
            raise ValueError("park 需要带 id 的沙箱实例")
        with self._lock:
            self._entries[instance.id] = WarmEntry(instance=instance, parked_at=time.time())

    def reclaim(self, sandbox_id: str) -> Any | None:
        """从热池提升一个沙箱回活跃（取出即删除）；不存在返回 None。"""
        with self._lock:
            entry = self._entries.pop(sandbox_id, None)
            return entry.instance if entry is not None else None

    def peek(self, sandbox_id: str) -> Any | None:
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
            oldest_id = min(self._entries, key=lambda sid: self._entries[sid].parked_at)
            entry = self._entries.pop(oldest_id)
            return entry.instance

    def clear(self) -> list[Any]:
        """清空热池，返回全部实例（调用方负责销毁）。"""
        with self._lock:
            instances = [entry.instance for entry in self._entries.values()]
            self._entries.clear()
            return instances