"""模型模块统一出口。

对外暴露：create_chat_model（工厂入口）、supports_vision（视觉能力探测）、
supports_thinking（思考能力探测）、PatchedChatDeepSeek（DeepSeek 思考修复实现）。
"""

from __future__ import annotations

from harness.models.factory import (
    PatchedChatDeepSeek,
    create_chat_model,
    supports_thinking,
    supports_vision,
)

__all__ = [
    "create_chat_model",
    "supports_vision",
    "supports_thinking",
    "PatchedChatDeepSeek",
]