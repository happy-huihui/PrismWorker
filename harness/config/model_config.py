from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

"""单个模型的配置

    职责：管理单个模型条目的连接参数与能力元数据
        - 构造参数：model / base_url / api_key / temperature / max_tokens（透传给模型类）
        - 元数据：name / provider / supports_vision / supports_thinking / context_window（供工厂决策与前端展示）

    对外暴露：
        - ModelProvider      provider 取值字面量
        - ModelConfig        单模型配置
"""

ModelProvider = Literal["openai", "deepseek", "mimo"]


class ModelConfig(BaseModel):
    """单个模型的配置。"""

    name: str = Field(..., description="模型唯一名称")

    provider: ModelProvider = Field(
        default="openai", description="模型提供方（openai / deepseek / mimo）"
    )

    model: str = Field(..., description="模型标识")

    base_url: str | None = Field(default=None, description="OpenAI 兼容接口地址")

    api_key: str | None = Field(default=None, description="API 密钥")

    supports_vision: bool = Field(default=False, description="是否支持视觉输入")

    supports_thinking: bool = Field(default=False, description="是否支持思考模式")

    context_window: int | None = Field(default=None, description="上下文窗口大小(token)")

    max_tokens: int | None = Field(default=None, description="单次输出 token 上限")

    temperature: float | None = Field(default=None, description="采样温度")

    # 允许配置里写厂商私有字段，原样透传给构造器
    model_config = ConfigDict(extra="allow")

    def resolve_api_key(self) -> str | None:
        """解析真实密钥：先取配置值，再按 provider 读对应环境变量。

        返回：
            可用的密钥字符串；都取不到时返回 None
        """
        # 1.配置里显式写了就直接用（优先级最高）
        if self.api_key:
            return self.api_key

        # 2.否则按 provider 选环境变量候选名
        if self.provider == "deepseek":
            env_names = ("DEEPSEEK_API_KEY",)
        elif self.provider == "mimo":
            env_names = ("MIMO_API_KEY",)
        else:
            env_names = ("OPENAI_API_KEY", "PRISM_WORKER_API_KEY")

        # 3.取第一个有值的候选
        for env_name in env_names:
            value = os.getenv(env_name)
            if value:
                return value
        # 4.都没有 → None
        return None
