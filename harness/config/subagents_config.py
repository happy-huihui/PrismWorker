"""子代理配置。

管理子代理（subagent）的运行时限制：单次执行超时、最大轮次、
每次运行允许的总调用次数、token 预算。子代理是 main agent
通过 task 工具派发出去的独立执行单元。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubagentConfig(BaseModel):
    """单个子代理的配置（按名称覆盖默认值）。"""

    name: str = Field(..., description="子代理名称")

    timeout_seconds: int = Field(default=600, description="子代理执行超时(秒)")

    max_turns: int = Field(default=30, description="子代理最大轮次")

    token_budget: int | None = Field(default=None, description="子代理 token 预算")


class SubagentsAppConfig(BaseModel):
    """子代理运行总配置。"""

    timeout_seconds: int = Field(default=600, description="子代理默认超时(秒)")

    max_turns: int = Field(default=30, description="子代理默认轮次上限")

    max_total_per_run: int = Field(default=10, description="每次运行子代理总数上限")

    agents: dict[str, SubagentConfig] = Field(
        default_factory=dict, description="子代理覆盖配置"
    )

    def effective_max_turns(self) -> int:
        """返回默认轮次上限，供创建子代理时兜底。"""
        return self.max_turns