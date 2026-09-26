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
        - run_meta                    {"model_name": "deepseek-v4-pro",
                                       "thinking_enabled": true, "thinking_degraded": false}
        - tool_start                  {"tool": "read_file", "tool_call_id": "…",
                                       "args": {"path": "/mnt/…/a.html", "description": "读取页面模板"},
                                       "description": "读取页面模板", "args_preview": "{…}", "ts": 1710000000.0}
        - tool_end                    {"tool": "web_search", "tool_call_id": "…", "duration_seconds": 1.2, "ok": true, "error": null, "ts": 1710000001.2}
        - reasoning_chunk             {"message_id": "lc_…", "text": "…"}   # 模型真实思考（逐 token）
        - message_chunk               {"message_id": "lc_…", "text": "…"}   # 本轮文本（先按答复流式）
        - message_retract             {"message_id": "lc_…"}                # 同轮出现工具 → 文本降级为叙述
        - thinking_chunk              {"text": "…"}                         # 旧版整轮叙述（仅历史兼容）
        - prints                      {"prints": ["…", "…"]}          # 增量进度行
        - todos                       {"todos": […]}                  # 全量任务清单
        - artifacts                   {"artifacts": ["outputs/a.md"]} # 增量产物路径
        - run_finished                {"status": "finished", "message_count": 3, "artifacts": […], "finished_at": 1710000009.0}
        - run_error                   {"status": "error", "message_count": 0, "artifacts": [], "error": "…"}

    文本事件为何带 message_id：模型一轮输出是一个固定 id 的流，前端按它归并才能
    做到「逐 token + 事后定性」（见到工具就把该条文本从答复气泡降级进思考步骤）。
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
    # 模型真实思考（reasoning_content）逐 token 下发
    REASONING_CHUNK = "reasoning_chunk"
    # 本轮文本逐 token 下发（先当答复展示，出现工具则被 message_retract 降级）
    MESSAGE_CHUNK = "message_chunk"
    # 同一条模型消息里出现了工具调用：此前文本属于思考叙述，前端搬进思考链
    MESSAGE_RETRACT = "message_retract"
    # 旧版「整轮叙述」；实时链路不再产生，仅历史落库数据回放时兼容读取
    THINKING_CHUNK = "thinking_chunk"
    PRINTS = "prints"
    TODOS = "todos"
    ARTIFACTS = "artifacts"
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
