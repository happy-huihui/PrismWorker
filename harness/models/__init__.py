from __future__ import annotations

from harness.models.capabilities import supports_thinking, supports_vision
from harness.models.deepseek_models import (
    DeepSeekFlashChatModel,
    DeepSeekProChatModel,
    PatchedChatDeepSeek,
)
from harness.models.factory import create_chat_model
from harness.models.registry import get_strategy, supported_providers
from harness.models.strategy import ChatModelStrategy

"""模型包统一出口

    职责：汇总模型子系统的对外能力，外部只需 from harness.models import X
        - 工厂入口与策略接口
        - 能力探测
        - provider 策略注册表

    对外暴露：
        - create_chat_model                     工厂入口，按 provider 造模型
        - supports_vision / supports_thinking   查模型是否支持看图 / 思考
        - ChatModelStrategy                     模型创建策略接口
"""

__all__ = [
    "create_chat_model",
    "supports_vision",
    "supports_thinking",
    "ChatModelStrategy",
    "get_strategy",
    "supported_providers",
    "PatchedChatDeepSeek",
    "DeepSeekFlashChatModel",
    "DeepSeekProChatModel",
]
