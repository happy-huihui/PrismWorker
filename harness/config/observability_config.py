from __future__ import annotations

from pydantic import BaseModel, Field

"""观测配置

    职责：管理观测子系统的配置——各模型每百万 token 的价格表（成本计算用）。
        价格在 config.yaml 的 `observability.pricing` 段维护，模型标识按子串匹配。

    对外暴露：
        - PricingConfig        单个模型的输入/输出单价
        - ObservabilityConfig  观测配置聚合（pricing 价格表）
"""


class PricingConfig(BaseModel):
    """单个模型每百万 token 的单价（美元）。"""

    input: float = Field(default=0.0, description="输入每百万 token 美元价")

    output: float = Field(default=0.0, description="输出每百万 token 美元价")


class ObservabilityConfig(BaseModel):
    """观测子系统配置。"""

    # 模型标识（子串匹配）→ 单价；缺省空表，cost.py 回退内置默认价
    pricing: dict[str, PricingConfig] = Field(
        default_factory=dict, description="模型价格表：模型标识子串 → 单价"
    )

    # 管理员 user_id 列表：观测台入口仅对这些人显示，且能看到全量用户数据
    admin_user_ids: list[str] = Field(
        default_factory=list, description="管理员 user_id 列表（观测台仅对这些人开放）"
    )
