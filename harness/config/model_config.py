"""模型配置（OpenAI / DeepSeek 双 provider）。

定义单个模型的配置结构。所有 Agent / 子代理使用的模型都从
配置文件中按 name 选择，工厂根据 provider 创建对应的聊天模型实例。
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ModelProvider = Literal["openai", "deepseek"]


class ModelConfig(BaseModel):
    """单个模型的配置。"""

    name: str = Field(..., description="模型唯一名称")

    provider: ModelProvider = Field(
        default="openai", description="模型提供方（openai / deepseek）"
    )

    model: str = Field(..., description="模型标识")

    base_url: str | None = Field(default=None, description="OpenAI 兼容接口地址")

    api_key: str | None = Field(default=None, description="API 密钥")

    supports_vision: bool = Field(default=False, description="是否支持视觉输入")

    supports_thinking: bool = Field(default=False, description="是否支持思考模式")

    context_window: int | None = Field(default=None, description="上下文窗口大小(token)")

    max_tokens: int | None = Field(default=None, description="单次输出 token 上限")

    temperature: float | None = Field(default=None, description="采样温度")

    model_config = ConfigDict(extra="allow")

    def resolve_api_key(self) -> str | None:
        """按优先级解析真实密钥。

        1. 配置值（api_key 字段）
        2. 按 provider 读取对应环境变量：
           openai → OPENAI_API_KEY / PRISM_WORKER_API_KEY
           deepseek → DEEPSEEK_API_KEY
        """
        if self.api_key:
            return self.api_key
        env_names = ("DEEPSEEK_API_KEY",) if self.provider == "deepseek" else (
            "OPENAI_API_KEY",
            "PRISM_WORKER_API_KEY",
        )
        for env_name in env_names:
            value = os.getenv(env_name)
            if value:
                return value
        return None