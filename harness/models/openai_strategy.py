from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from harness.config.model_config import ModelConfig
from harness.models.strategy import ChatModelStrategy

"""OpenAI 模型创建策略

    职责：负责 openai 这一家的模型怎么造。
    流程：最省事，把工厂合并好的参数直接交给 ChatOpenAI 实例化就行，没有额外分支。
"""


class OpenAIChatModelStrategy(ChatModelStrategy):
    """OpenAI（及兼容接口）provider 的模型创建策略。"""

    # 本策略负责的 provider 标识
    provider = "openai"

    def build(
        self,
        model_config: ModelConfig,
        settings: dict[str, Any],
        thinking_enabled: bool,
        **kwargs: Any,
    ) -> BaseChatModel:
        """实例化 ChatOpenAI。

        参数：
            model_config: 模型配置（此策略无需额外判断，仅作接口对齐）
            settings: 合并好的配置 + 覆盖的构造器参数
            thinking_enabled: 是否开启思考模式（校验已在工厂完成）
            kwargs: 透传给构造器的额外参数

        返回：
            ChatOpenAI 实例
        """
        # 使用配置和额外参数实例化 ChatOpenAI
        return ChatOpenAI(**settings, **kwargs)
