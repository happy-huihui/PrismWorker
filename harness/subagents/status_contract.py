from __future__ import annotations

from typing import Literal


"""
    子代理状态契约（status_contract）——子代理运行结束后的状态枚举与取值集合。

    thread_state 的 `delegations` 用 `SUBAGENT_STATUS_VALUES` 判断某次委托是否已
    进入终止态（终止态不可被后续的非终止写入覆盖）。这里只保留契约本身：
      - SubagentStatusValue        子代理最终状态的取值类型
      - SUBAGENT_STATUS_VALUES     全部可能状态的元组（不可变）
      - SubagentStopReasonValue / SUBAGENT_STOP_REASON_VALUES
        由护栏（token/轮次/循环上限）提前终止时的原因
    其余 subagent 的运行时组装逻辑不属于本契约，不在本文件展开。
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