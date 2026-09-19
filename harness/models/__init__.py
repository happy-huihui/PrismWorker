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

    职责：外面要用的东西都从这里拿，不用记一堆子文件路径。
    结构：接口在 strategy，openai / deepseek 的实现在各自 strategy，
         DeepSeek 的模型类在 deepseek_models，用哪个策略查 registry，
         能力探测在 capabilities，造模型的入口在 factory。

    对外暴露：
        - create_chat_model               工厂入口，要造模型就找它
        - supports_vision / supports_thinking   查模型支不支持看图 / 思考
        - ChatModelStrategy               策略接口
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
