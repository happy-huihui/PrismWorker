from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


"""会话目标状态

    职责：定义会话长期目标的结构，只做类型定义不含逻辑
        - GoalBlocker     目标被卡住的原因
        - GoalEvaluation  某次评估的结论
        - GoalState       会话目标全部字段

    对外暴露：
        - GoalBlocker / GoalEvaluation / GoalState
"""

# 目标阻塞原因：区分「只是还没做完」和「确实卡住了、卡在哪」，供续跑决策使用
GoalBlocker = Literal[
    "none",              # 未被阻塞
    "missing_evidence",  # 证据不足，需要继续取证
    "needs_user_input",  # 需要用户补充信息（走澄清路径）
    "run_failed",        # 本轮运行失败（模型/工具报错）
    "external_wait",     # 依赖外部系统，只能等待
    "goal_not_met_yet",  # 目标尚未达成，但不属于真阻塞
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