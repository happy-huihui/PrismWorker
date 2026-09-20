from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

"""事件模型（types）

    职责：定义一次 agent run 的思考链统一消息格式 RunEvent。
    背景：工具调用、进度、任务清单、产物、思考/答复文本、结束/错误都抽象成
         RunEvent，经事件总线发布，由 SSE 推送端等订阅；事件本身不落盘，
         持久化交给 runs 表。

    对外暴露：
        - RunEventType           事件类型枚举（值即 SSE 的 event 字段）
        - RunEvent               一条事件（type/run_id/thread_id/ts/seq/payload）
        - TERMINAL_EVENT_TYPES   终端事件集合（run_finished / run_error，必达不丢）
        - make_event             构造一个不带 seq 的事件（seq 由总线覆盖）

    输出数据示例（各事件真实 payload）：
        - run_started                 {"run_id": "…", "thread_id": "…"}
        - run_meta                    {"model_name": "deepseek-v4-pro"}
        - tool_start                  {"tool": "web_search", "tool_call_id": "…", "args_preview": "…", "ts": 1710000000.0}
        - tool_end                    {"tool": "web_search", "tool_call_id": "…", "duration_seconds": 1.2, "ts": 1710000001.2}
        - thinking_chunk / message_chunk  {"text": "…"}
        - prints                      {"prints": ["…", "…"]}          # 增量进度行
        - todos                       {"todos": […]}                  # 全量任务清单
        - artifacts                   {"artifacts": ["outputs/a.md"]} # 增量产物路径
        - run_finished                {"status": "finished", "message_count": 3, "artifacts": […], "finished_at": 1710000009.0}
        - run_error                   {"status": "error", "message_count": 0, "artifacts": [], "error": "…"}
"""

# 终端事件：总线保证必达（队列满也不丢），消费端据此收尾
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset({
    "run_finished",
    "run_error",
})


class RunEventType(str, Enum):
    """事件类型枚举：类型名即 SSE 的 event 字段值。"""

    RUN_STARTED = "run_started"
    RUN_META = "run_meta"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    THINKING_CHUNK = "thinking_chunk"
    PRINTS = "prints"
    TODOS = "todos"
    ARTIFACTS = "artifacts"
    MESSAGE_CHUNK = "message_chunk"
    TOOL_MESSAGE = "tool_message"
    RUN_FINISHED = "run_finished"
    RUN_ERROR = "run_error"


@dataclass(frozen=True)
class RunEvent:
    """一条 run 事件。

    seq 由 EventBus 按 run 内全局递增分配（同一 run 的多个订阅者看到相同
    seq，保证确定性）；ts 为事件产生时刻；payload 为 JSON 友好数据。
    """

    type: RunEventType
    run_id: str
    thread_id: str
    ts: float = field(default_factory=time.time)
    seq: int = 0
    payload: dict[str, Any] = field(default_factory=dict)


def make_event(
    type_: RunEventType,
    *,
    run_id: str,
    thread_id: str,
    payload: dict[str, Any] | None = None,
) -> RunEvent:
    """构造一个不带 seq 的 RunEvent（seq 交给 EventBus 覆盖）。

    参数：
        type_: 事件类型
        run_id: 归属的 run
        thread_id: 归属的线程
        payload: JSON 友好的事件数据，缺省空字典

    返回：
        一个 seq=0 的 RunEvent（发布时由总线改写 seq）
    """
    # payload 缺省兜底为空字典
    data = payload or {}
    return RunEvent(
        type=type_,
        run_id=run_id,
        thread_id=thread_id,
        payload=data,
    )
