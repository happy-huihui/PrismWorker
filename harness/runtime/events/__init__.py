from __future__ import annotations

from harness.runtime.events.routing import (
    DispatchEventSink,
    get_dispatch_sink,
    run_context,
)
from harness.runtime.events.types import (
    TERMINAL_EVENT_TYPES,
    RunEvent,
    RunEventType,
    make_event,
)

"""事件包（events）

    职责：run 思考链的统一事件契约。
    内容：types 定义 RunEvent/类型/工厂函数；routing 把 harness
         工具事件路由到当前 run 的总线。
"""

__all__ = [
    "RunEvent",
    "RunEventType",
    "TERMINAL_EVENT_TYPES",
    "make_event",
    "run_context",
    "DispatchEventSink",
    "get_dispatch_sink",
]
