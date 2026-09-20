from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from harness.runtime.events.types import RunEventType, make_event
from harness.runtime.sse_stream import get_event_bus

"""事件路由（routing）

    职责：把 harness 注入式 event_sink 收到的工具事件，路由到「当前 run」
         对应的 EventBus 上。
    背景：harness 的 event_sink 是装配期固定的、且工具事件本身不带 run 标识；
         为了让 agent 能被安全缓存、又支持并发多 run 事件各归各位，采用
         「进程级分发 sink + run 上下文变量」：
           1. DispatchEventSink 是进程级单例，作为所有缓存 agent 的 event_sink；
           2. run 执行期间（run_worker 内）设置 _CURRENT_RUN 上下文，工具事件
              据此路由到对应 run 的 EventBus；
           3. 没有 run 上下文时（测试 / 直连场景）事件被忽略，不产生副作用。

    对外暴露：
        - run_context          进入/退出一次 run 的上下文（contextmanager）
        - DispatchEventSink    进程级分发 sink（harness event_sink）
        - get_dispatch_sink    分发 sink 单例
"""

# 当前执行中的 run / thread（contextvar 天然隔离并发 asyncio.Task）
_CURRENT_RUN: ContextVar[str | None] = ContextVar("prism_current_run", default=None)
_CURRENT_THREAD: ContextVar[str | None] = ContextVar("prism_current_thread", default=None)


@contextmanager
def run_context(run_id: str, thread_id: str):
    """进入一次 run 的执行上下文（run_worker 内包裹 astream 用）。

    参数：
        run_id: 本次 run 的 id
        thread_id: 本次 run 的线程 id

    说明：期间 DispatchEventSink 收到的工具事件会路由到该 run；退出时自动
         恢复上一个上下文（支持嵌套/并发，contextvar 隔离）。
    """
    # 进入前记录 token，退出时按 token 复位
    run_token = _CURRENT_RUN.set(run_id)
    thread_token = _CURRENT_THREAD.set(thread_id)
    try:
        yield
    finally:
        _CURRENT_RUN.reset(run_token)
        _CURRENT_THREAD.reset(thread_token)


class DispatchEventSink:
    """进程级事件分发 sink：把 harness 工具事件路由到当前 run 的 EventBus。

    作为所有缓存 agent 的 event_sink 注入；事件归属由 _CURRENT_RUN 决定。
    """

    def __init__(self, *, bus: Any | None = None) -> None:
        """初始化；bus 缺省用进程级 EventBus 单例。

        参数：
            bus: 显式事件总线；None 时用 get_event_bus() 单例
        """
        self._bus = bus

    async def __call__(self, event: dict[str, Any]) -> None:
        """接收 harness 工具事件并路由发布（无 run 上下文则忽略）。

        参数：
            event: harness 工具事件字典（含 event/tool/tool_call_id 等字段）
        """
        # 无 run 上下文（测试/直连）直接忽略，不产生副作用
        run_id = _CURRENT_RUN.get()
        if not run_id:
            return
        kind = event.get("event")
        # 只处理工具开始 / 结束两类事件，转成对应的 RunEvent
        if kind == "tool_start":
            run_event = make_event(
                RunEventType.TOOL_START,
                run_id=run_id,
                thread_id=_CURRENT_THREAD.get() or "",
                payload={
                    "tool": event.get("tool", ""),
                    "tool_call_id": event.get("tool_call_id", ""),
                    "args_preview": event.get("args_preview", ""),
                    "ts": event.get("ts"),
                },
            )
        elif kind == "tool_end":
            run_event = make_event(
                RunEventType.TOOL_END,
                run_id=run_id,
                thread_id=_CURRENT_THREAD.get() or "",
                payload={
                    "tool": event.get("tool", ""),
                    "tool_call_id": event.get("tool_call_id", ""),
                    "duration_seconds": event.get("duration_seconds"),
                    "ts": event.get("ts"),
                },
            )
        else:
            return
        await (self._bus or get_event_bus()).publish(run_event)


_dispatch_sink: DispatchEventSink | None = None
_dispatch_sink_lock = threading.Lock()


def get_dispatch_sink() -> DispatchEventSink:
    """返回进程级事件分发 sink 单例（懒构造，线程安全）。"""
    global _dispatch_sink
    if _dispatch_sink is not None:
        return _dispatch_sink
    with _dispatch_sink_lock:
        if _dispatch_sink is None:
            _dispatch_sink = DispatchEventSink()
        return _dispatch_sink
