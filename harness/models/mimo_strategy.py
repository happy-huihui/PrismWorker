from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.language_models import BaseChatModel

from harness.config.model_config import ModelConfig
from harness.models.mimo_models import (
    MIMO_BASE_URL,
    MIMO_SUPPORTED_MODEL_IDS,
    MiMoFlashChatModel,
    MiMoProChatModel,
    is_mimo_flash_model,
    is_mimo_pro_model,
)
from harness.models.strategy import ChatModelStrategy

"""MiMo 模型创建策略

    职责：负责 mimo 这一家的模型怎么造。
    流程：补全 base_url 与 api_key（配置没给就用官方默认 / MIMO_API_KEY），
         再按模型标识分流 Pro / Flash 两个实现类。
    思考：MiMo **默认就输出 reasoning_content**，不需要也不应该下发
         reasoning_effort / thinking 之类参数（实测传了会让输出退化：
         完成 token 从 36 掉到 5）。所以 thinking_enabled 在这里是 no-op，
         开关只影响前端是否展开思考链，不影响请求体。
"""

logger = logging.getLogger(__name__)


class MiMoChatModelStrategy(ChatModelStrategy):
    """MiMo provider 的模型创建策略（按模型标识选 Pro / Flash）。"""

    # 本策略负责的 provider 标识
    provider = "mimo"

    def build(
        self,
        model_config: ModelConfig,
        settings: dict[str, Any],
        thinking_enabled: bool,
        **kwargs: Any,
    ) -> BaseChatModel:
        """按模型标识选择 MiMo 实现类并实例化。

        参数：
            model_config: 模型配置（提供 model 标识与 api_key 兜底）
            settings: 合并好的配置 + 覆盖的构造器参数
            thinking_enabled: 是否开启思考模式（本策略不下发思考参数，仅记录）
            kwargs: 透传给构造器的额外参数

        返回：
            MiMoProChatModel 或 MiMoFlashChatModel 实例

        异常：
            模型标识不在支持列表内时抛 ValueError
        """
        # 接口地址：配置没给就用官方 OpenAI 兼容地址（空串也按没给处理）
        if not settings.get("base_url"):
            settings["base_url"] = MIMO_BASE_URL

        # 密钥：配置为空时回落到 MIMO_API_KEY 环境变量
        if not settings.get("api_key"):
            settings["api_key"] = model_config.resolve_api_key() or os.getenv(
                "MIMO_API_KEY"
            )

        # 思考开关：MiMo 默认就产出思考链，额外下发参数反而退化，故只记日志
        if thinking_enabled:
            logger.debug(
                "MiMo 默认输出 reasoning_content，thinking_enabled 不下发额外请求参数"
            )

        # 取模型标识，统一转成字符串再精确匹配
        model_id = settings.get("model")
        model_id_str = str(model_id) if model_id is not None else None

        # Pro 档（mimo-v2.6-pro / -ultraspeed）
        if is_mimo_pro_model(model_id_str):
            return MiMoProChatModel(**settings, **kwargs)

        # Flash 档（mimo-v2.6-flash）
        if is_mimo_flash_model(model_id_str):
            return MiMoFlashChatModel(**settings, **kwargs)

        # 只认列表内的几款，其余直接报错（提示里带上可用清单）
        raise ValueError(
            f"不支持的 MiMo 模型: {model_id!r}"
            f"（仅支持 {' / '.join(MIMO_SUPPORTED_MODEL_IDS)}）"
        ) from None
