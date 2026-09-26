"""模型动态路由包（harness.models.routing）。

把「这一轮该用哪个模型」从人工选择变成按规则自动决策。

对外暴露：
    - ModelRouter        路由决策器
    - get_model_router   进程级单例
    - reset_model_router 重置单例（测试隔离）
    - RoutingDecision    决策结果（模型名 + 原因 + 来源）
    - RoutingSignals     路由输入信号
"""

from __future__ import annotations

from harness.models.routing.decision import RoutingDecision, RoutingSignals
from harness.models.routing.router import (
    ModelRouter,
    get_model_router,
    reset_model_router,
)

__all__ = [
    "ModelRouter",
    "RoutingDecision",
    "RoutingSignals",
    "get_model_router",
    "reset_model_router",
]
