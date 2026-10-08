from __future__ import annotations

from typing import Literal


"""子代理状态契约

    职责：定义子代理运行结束后的状态枚举与取值集合
        - thread_state.delegations 用它判断委托是否已进入终止态
        - 另含护栏提前终止的原因（token / 轮次 / 循环上限）

    对外暴露：
        - SubagentStatusValue / SUBAGENT_STATUS_VALUES
        - SubagentStopReasonValue / SUBAGENT_STOP_REASON_VALUES
"""

SubagentStatusValue = Literal[
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "polling_timed_out",
]

SUBAGENT_STATUS_VALUES: tuple[SubagentStatusValue, ...] = (
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "polling_timed_out",
)

SubagentStopReasonValue = Literal["token_capped", "turn_capped", "loop_capped"]

SUBAGENT_STOP_REASON_VALUES: tuple[SubagentStopReasonValue, ...] = (
    "token_capped",
    "turn_capped",
    "loop_capped",
)