from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

"""单个模型的配置（openai / deepseek / mimo 三 provider）

    职责：定义「一个模型条目长什么样」，供 config.yaml 的 models 段声明。
         至于条目怎么造成实例（按 provider 分流）由 harness/models 的工厂负责。

    字段分两类：
        - 构造类：model / base_url / api_key / temperature / max_tokens
          → 直接透传给对应的 LangChain 模型类
        - 元数据类：name / provider / supports_vision / supports_thinking / context_window
          → 只供工厂决策与前端展示，不进构造参数

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
        # 配置里显式写了就直接用
        if self.api_key:
            return self.api_key

        # 否则按 provider 选环境变量候选
        if self.provider == "deepseek":
            env_names = ("DEEPSEEK_API_KEY",)
        elif self.provider == "mimo":
            env_names = ("MIMO_API_KEY",)
        else:
            env_names = ("OPENAI_API_KEY", "PRISM_WORKER_API_KEY")

        # 取第一个有值的候选
        for env_name in env_names:
            value = os.getenv(env_name)
            if value:
                return value
        return None
