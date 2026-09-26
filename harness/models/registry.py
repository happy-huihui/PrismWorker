from __future__ import annotations

from harness.models.deepseek_strategy import DeepSeekChatModelStrategy
from harness.models.mimo_strategy import MiMoChatModelStrategy
from harness.models.openai_strategy import OpenAIChatModelStrategy
from harness.models.strategy import ChatModelStrategy

"""策略注册表

    职责：记一张表，provider 名字 -> 该用哪个策略，工厂来这张表按名字取策略。
    现状：登记了 openai（及其兼容接口）、deepseek、mimo 三种。
"""

# provider 标识 → 策略单例（策略无状态，进程内复用即可）
_STRATEGIES: dict[str, ChatModelStrategy] = {
    OpenAIChatModelStrategy.provider: OpenAIChatModelStrategy(),
    DeepSeekChatModelStrategy.provider: DeepSeekChatModelStrategy(),
    MiMoChatModelStrategy.provider: MiMoChatModelStrategy(),
}


def get_strategy(provider: str) -> ChatModelStrategy | None:
    """按 provider 标识获取对应策略实例。

    参数：
        provider: 模型提供商标识（如 "openai" / "deepseek"）

    返回：
        对应策略实例；未注册则返回 None
    """
    # 直接查表，未命中返回 None 交由上层决定如何报错
    return _STRATEGIES.get(provider)


def supported_providers() -> tuple[str, ...]:
    """列出当前已注册（受支持）的 provider 标识。

    返回：
        已注册 provider 标识元组，用于错误提示等场景
    """
    # 返回注册表全部键，保持顺序稳定
    return tuple(_STRATEGIES.keys())
