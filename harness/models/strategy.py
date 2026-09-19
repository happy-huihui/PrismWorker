from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from langchain_core.language_models import BaseChatModel

from harness.config.model_config import ModelConfig

"""模型创建策略接口

    职责：定义模型接口，strategy.build(...) 拿策略实现

    对外暴露：
        - ChatModelStrategy   策略接口，子类要实现 provider 标识和 build() 方法
"""


class ChatModelStrategy(ABC):
    """模型创建策略接口：一个 provider 对应一个实现类。"""

    # 该策略负责的 provider 标识（子类必须覆盖，如 "openai" / "deepseek"）
    provider: ClassVar[str]

    @abstractmethod
    def build(
        self,
        model_config: ModelConfig,
        settings: dict[str, Any],
        thinking_enabled: bool,
        **kwargs: Any,
    ) -> BaseChatModel:
        """按本 provider 的规则实例化聊天模型。

        参数：
            model_config: 模型配置（含 provider / model / 能力开关等元数据）
            settings: 已合并好的构造器参数（配置值 + 调用方覆盖）
            thinking_enabled: 是否开启思考模式
            kwargs: 透传给模型构造器的额外参数

        返回：
            一个 BaseChatModel 实例
        """
        raise NotImplementedError
