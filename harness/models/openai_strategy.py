from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from harness.config.model_config import ModelConfig
from harness.models.strategy import ChatModelStrategy

"""OpenAI 模型创建策略

    职责：负责 openai 这一家（及任何 OpenAI 兼容端点）的模型怎么造。
    流程：把工厂合并好的参数直接交给 ChatOpenAI；只有「开思考」时多一步——
         下发 reasoning_effort，让推理档（如 gpt-6-astra）真的走深度推理。

    思考：不开思考时不发任何参数，保证普通对话（含指向第三方兼容代理的
         base_url）行为与改造前完全一致，不引入额外风险。
"""

# 开思考时的推理强度（gpt-6-astra 的 reasoning.effort 支持 low/medium/high/xhigh/max）
THINKING_REASONING_EFFORT = "high"


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
            model_config: 模型配置（此策略无需分流，仅作接口对齐）
            settings: 合并好的配置 + 覆盖的构造器参数
            thinking_enabled: 是否开启思考模式（校验已在工厂完成）
            kwargs: 透传给构造器的额外参数

        返回：
            ChatOpenAI 实例
        """
        # 空串视为「没配」：剔掉后由 ChatOpenAI 自行回退环境变量 / 官方地址，
        # 否则空 base_url 会被当成真实地址拼出坏请求
        for key in ("api_key", "base_url"):
            if not settings.get(key):
                settings.pop(key, None)

        # 开思考：下发推理强度；不覆盖调用方显式给的值
        if thinking_enabled:
            settings.setdefault("reasoning_effort", THINKING_REASONING_EFFORT)

        # 使用配置和额外参数实例化 ChatOpenAI
        return ChatOpenAI(**settings, **kwargs)
