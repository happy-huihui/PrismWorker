from __future__ import annotations

from typing import Any

from langchain_deepseek import ChatDeepSeek

from harness.models.reasoning import (
    extract_message_text,
    restore_reasoning_content,
)

"""DeepSeek 模型实现类

    职责：放 DeepSeek 到底用哪几个模型类，以及“思考链丢失”问题的修复代码。
    背景：DeepSeek 推理模型多轮对话必须带着上一次的思考内容，
         官方库会把它弄丢导致请求被拒，这里补回来。

    对外暴露：
        - DeepSeekFlashChatModel     快速对话模型，不出思考链
        - DeepSeekProChatModel       推理模型，带思考链修复
        - PatchedChatDeepSeek        Pro 用的修复基类
        - is_deepseek_pro_model      精确判断是不是 Pro（deepseek-v4-pro）
        - is_deepseek_flash_model    精确判断是不是 Flash（deepseek-v4-flash）

    复用：思考链还原属通用能力，已抽到 harness.models.reasoning，
         这里 re-export 保持既有导入路径可用。
"""

# 兼容旧导入路径（内部实现已迁到 reasoning 模块）
_extract_text = extract_message_text
__all__ = [
    "DEEPSEEK_FLASH_MODEL_ID",
    "DEEPSEEK_PRO_MODEL_ID",
    "DeepSeekFlashChatModel",
    "DeepSeekProChatModel",
    "PatchedChatDeepSeek",
    "is_deepseek_pro_model",
    "is_deepseek_flash_model",
    "restore_reasoning_content",
]

# DeepSeek 模型
DEEPSEEK_FLASH_MODEL_ID = "deepseek-v4-flash"
DEEPSEEK_PRO_MODEL_ID = "deepseek-v4-pro"


def is_deepseek_pro_model(model_id: str | None) -> bool:
    """精确判断是不是 Pro（deepseek-v4-pro）。

    参数：
        model_id: 模型标识（只认 deepseek-v4-flash / deepseek-v4-pro）

    返回：
        等于 Pro 标识返回 True，否则 False
    """
    # 空标识不算 Pro
    if not model_id:
        return False

    # 去空格、转小写后与 Pro 标识精确比对
    return model_id.strip().lower() == DEEPSEEK_PRO_MODEL_ID


def is_deepseek_flash_model(model_id: str | None) -> bool:
    """精确判断是不是 Flash（deepseek-v4-flash）。

    参数：
        model_id: 模型标识（只认 deepseek-v4-flash / deepseek-v4-pro）

    返回：
        等于 Flash 标识返回 True，否则 False
    """
    # 空标识不算 Flash
    if not model_id:
        return False

    # 去空格、转小写后与 Flash 标识精确比对
    return model_id.strip().lower() == DEEPSEEK_FLASH_MODEL_ID


class DeepSeekFlashChatModel(ChatDeepSeek):
    """DeepSeek Flash 级模型（快速对话，无思考链）。

    对应 deepseek-v4-flash：低延迟、不输出
    reasoning_content，适合日常问答与轻任务。与 Patched 相比不需要
    reasoning 还原（Flash 模型不产生思考链），构造/序列化行为与父类
    一致，仅统一密钥解析约定（DEEPSEEK_API_KEY）。
    """

    @classmethod
    def is_lc_serializable(cls) -> bool:
        """LangChain 序列化兼容开关（保持与父类一致的默认行为）。"""
        # 允许被 LangChain 序列化
        return True

    @property
    def lc_secrets(self) -> dict[str, str]:
        """序列化时把密钥字段统一映射到 DEEPSEEK_API_KEY。"""
        # 两个密钥字段都走同一个环境变量
        return {"api_key": "DEEPSEEK_API_KEY", "openai_api_key": "DEEPSEEK_API_KEY"}


class PatchedChatDeepSeek(ChatDeepSeek):
    """修复 reasoning_content 多轮丢失问题的 ChatDeepSeek。

    DeepSeek 思考型模型（deepseek-v4-pro）在每次回答里会返回
    reasoning_content（思考过程）。官方 langchain_deepseek 在组装多轮
    请求时只保留 content、丢掉 reasoning_content，导致带思考模型的
    多轮对话被 API 拒绝。这里重写 _get_request_payload，把
    additional_kwargs 里的 reasoning_content 还原进请求消息。
    """

    @classmethod
    def is_lc_serializable(cls) -> bool:
        """LangChain 序列化兼容开关（保持与父类一致的默认行为）。"""
        # 允许被 LangChain 序列化
        return True

    @property
    def lc_secrets(self) -> dict[str, str]:
        """序列化时把两个密钥字段都映射到 DEEPSEEK_API_KEY。"""
        # 统一密钥来源，避免序列化丢失鉴权
        return {"api_key": "DEEPSEEK_API_KEY", "openai_api_key": "DEEPSEEK_API_KEY"}

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """组装请求体，并还原 assistant 消息里的 reasoning_content。

        参数：
            input_: 语言模型输入（消息列表或提示）
            stop: 停止序列
            kwargs: 透传给父类的其他请求参数

        返回：
            补齐思考链后的请求体字典
        """
        # 1. 先把输入转成消息列表（拿到每个消息的额外信息）
        original_messages = self._convert_input(input_).to_messages()

        # 2. 调用父类拿到标准请求体
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        # 3. 把带 reasoning_content 的 additional_kwargs 内容写回请求消息
        restored = restore_reasoning_content(payload.get("messages", []), original_messages)

        # 用还原后的消息替换请求体消息
        payload["messages"] = restored
        return payload


class DeepSeekProChatModel(PatchedChatDeepSeek):
    """DeepSeek Pro 级模型（深度推理，带 reasoning_content 修复）。

    对应 deepseek-v4-pro：输出思考链
    （reasoning_content），多轮对话必须携带历史思考链——本实现继承
    已修复该问题的 PatchedChatDeepSeek，保证多轮链路稳定。
    """
