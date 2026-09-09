from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

"""
    事件模型（events）——app 层思考链的统一消息格式。

    一次 agent run 进行中产生的全部可观测事件（工具调用、进度、任务清单、
    产物登记、AI 增量文本、结束/错误）都抽象成 RunEvent，经 EventBus 发布，
    由 SSE 推送端 / 未来的 IM 渠道 / 日志模块订阅。设计原则：
      1. payload 必须 JSON 友好（可直接序列化为 SSE data 帧）；
      2. 事件只有瞬态语义——不做持久化（持久化是 SQLite runs 表的职责）；
      3. 终端事件（run_finished / run_error）由 EventBus 保证必达（不丢弃）。
"""

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
    """构造一个不带 seq 的 RunEvent（seq 交给 EventBus 覆盖）。"""
    data = payload or {}
    return RunEvent(
        type=type_,
        run_id=run_id,
        thread_id=thread_id,
        payload=data,
    )