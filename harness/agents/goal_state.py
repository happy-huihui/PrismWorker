from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


"""
    会话目标状态（GoalState）——记录一个会话正在追求的长期目标。

    只做类型定义，不含逻辑。整个 thread_state 里 `goal` 字段、以及将来做
    goal 评估的模块，都用这里的三组结构：
      - GoalBlocker：当前目标被什么原因卡住的枚举
      - GoalEvaluation：某次评估的结论（是否满足 + 阻塞原因 + 理由）
      - GoalState：会话目标本身的全部字段
"""

GoalBlocker = Literal[
    "none",
    "missing_evidence",
    "needs_user_input",
    "run_failed",
    "external_wait",
    "goal_not_met_yet",
]


class GoalEvaluation(TypedDict):
    """一次目标评估的结构化结论。

    satisfied        目标是否已达成
    blocker          若未达成，是什么原因卡住
    reason           评估理由（人读）
    evidence_summary 可选的证据摘要
    """
    satisfied: bool
    blocker: GoalBlocker
    reason: str
    evidence_summary: NotRequired[str]


class GoalState(TypedDict):
    """会话目标的状态字段集。

    objective                       要达成目标的具体描述
    status                         	固定 active 表示目标处于推进中
    created_at / updated_at         创建/更新时间
    continuation_count              已经跑了几轮（续跑）
    max_continuations               最多跑几轮
    no_progress_count               连续无进展的轮次
    max_no_progress_continuations   连续无进展几次就刹车
    last_evaluation                 最近评估结论，最近一次评估是否达成/卡住、卡在哪（可选 & GoalEvaluation）
    """
    objective: str
    status: Literal["active"]
    created_at: str
    updated_at: str
    continuation_count: int
    max_continuations: int
    no_progress_count: int
    max_no_progress_continuations: int
    last_evaluation: NotRequired[dict[str, Any]]