from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from harness.config.model_config import ModelConfig
from harness.models.deepseek_models import (
    DEEPSEEK_FLASH_MODEL_ID,
    DEEPSEEK_PRO_MODEL_ID,
    DeepSeekFlashChatModel,
    DeepSeekProChatModel,
    is_deepseek_flash_model,
    is_deepseek_pro_model,
)
from harness.models.strategy import ChatModelStrategy

"""DeepSeek 模型创建策略

    职责：负责 deepseek 这一家的模型怎么造。
    流程：先去掉 base_url（DeepSeek 自定义模型类不认这个参数），
         再按模型标识精确对应——只认 deepseek-v4-pro / deepseek-v4-flash 两款。
"""


class DeepSeekChatModelStrategy(ChatModelStrategy):
    """DeepSeek provider 的模型创建策略（按模型标识选 Pro / Flash）。"""

    # 本策略负责的 provider 标识
    provider = "deepseek"

    def build(
        self,
        model_config: ModelConfig,
        settings: dict[str, Any],
        thinking_enabled: bool,
        **kwargs: Any,
    ) -> BaseChatModel:
        """按模型标识选择 DeepSeek 实现类并实例化。

        参数：
            model_config: 模型配置（此策略仅需 model 标识做分流）
            settings: 合并好的配置 + 覆盖的构造器参数
            thinking_enabled: 是否开启思考模式（校验已在工厂完成）
            kwargs: 透传给构造器的额外参数

        返回：
            DeepSeekProChatModel 或 DeepSeekFlashChatModel 实例

        异常：
            模型标识不在支持的两款之内时抛 ValueError
        """
        # 移除 base_url，避免传给 DeepSeek 自定义模型类
        settings.pop("base_url", None)

        # 取模型标识，统一转成字符串再精确匹配
        model_id = settings.get("model")
        model_id_str = str(model_id) if model_id is not None else None

        # Pro（deepseek-v4-pro）：走带思考链修复的实现类
        if is_deepseek_pro_model(model_id_str):
            return DeepSeekProChatModel(**settings, **kwargs)

        # Flash（deepseek-v4-flash）：走普通实现类
        if is_deepseek_flash_model(model_id_str):
            return DeepSeekFlashChatModel(**settings, **kwargs)

        # 只认这两款，其余直接报错
        raise ValueError(
            f"不支持的 DeepSeek 模型: {model_id!r}"
            f"（仅支持 {DEEPSEEK_FLASH_MODEL_ID} / {DEEPSEEK_PRO_MODEL_ID}）"
        ) from None
