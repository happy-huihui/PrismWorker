from __future__ import annotations

from harness.runtime.sse_stream.memory_bus import (
    EventBus,
    get_event_bus,
    reset_event_bus,
)

"""流式总线路包（sse_stream）

    职责：提供 run 事件的进程内发布/订阅枢纽（无 HTTP）。
    边界：只做 pub/sub；SSE 帧格式化与 EventSourceResponse 留在 app 路由层。
"""

__all__ = [
    "EventBus",
    "get_event_bus",
    "reset_event_bus",
]
