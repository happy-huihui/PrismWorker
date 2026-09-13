"""模型工厂（OpenAI / DeepSeek 双实现）。

把配置层的 ModelConfig 变成真正可用的 LangChain 聊天模型实例。
对外暴露：
    - create_chat_model()   按 provider 分流创建模型
    - supports_vision()     查询某模型是否支持视觉（决定 view_image 工具注册）
    - supports_thinking()   查询某模型是否支持思考模式（决定是否开启推理）
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from harness.config.app_config import AppConfig, get_app_config
from harness.config.model_config import ModelConfig

logger = logging.getLogger(__name__)


class DeepSeekFlashChatModel(ChatDeepSeek):
    """DeepSeek Flash 级模型（快速对话，无思考链）。

    对应 deepseek-chat / deepseek-v3 等快速对话模型：低延迟、不输出
    reasoning_content，适合日常问答与轻任务。与 Patched 相比不需要
    reasoning 还原（Flash 模型不产生思考链），构造/序列化行为与父类
    一致，仅统一密钥解析约定（DEEPSEEK_API_KEY）。
    """

    @classmethod
    def is_lc_serializable(cls) -> bool:
        """LangChain 序列化兼容开关（保持与父类一致的默认行为）。"""
        return True

    @property
    def lc_secrets(self) -> dict[str, str]:
        """序列化时把密钥字段统一映射到 DEEPSEEK_API_KEY。"""
        return {"api_key": "DEEPSEEK_API_KEY", "openai_api_key": "DEEPSEEK_API_KEY"}


# DeepSeek 推理型模型标识（Pro 级）；其余标识归 Flash 级
_DEEPSEEK_REASONER_MARKERS = ("reasoner", "deepseek-r1", "r1-")


def is_deepseek_pro_model(model_id: str | None) -> bool:
    """按模型标识判断是否 Pro 级（推理型）DeepSeek 模型。"""
    if not model_id:
        return False
    lowered = model_id.lower()
    return any(marker in lowered for marker in _DEEPSEEK_REASONER_MARKERS)


class PatchedChatDeepSeek(ChatDeepSeek):
    """修复 reasoning_content 多轮丢失问题的 ChatDeepSeek。

    DeepSeek 思考型模型（deepseek-reasoner）在每次回答里会返回
    reasoning_content（思考过程）。官方 langchain_deepseek 在组装多轮
    请求时只保留 content、丢掉 reasoning_content，导致带思考模型的
    多轮对话被 API 拒绝。这里重写 _get_request_payload，把
    additional_kwargs 里的 reasoning_content 还原进请求消息。
    """

    @classmethod
    def is_lc_serializable(cls) -> bool:
        """LangChain 序列化兼容开关（保持与父类一致的默认行为）。"""
        return True

    @property
    def lc_secrets(self) -> dict[str, str]:
        """序列化时把两个密钥字段都映射到 DEEPSEEK_API_KEY。"""
        return {"api_key": "DEEPSEEK_API_KEY", "openai_api_key": "DEEPSEEK_API_KEY"}

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """组装请求体，并还原 assistant 消息里的 reasoning_content。

        步骤：
        1. 先把输入转成消息列表（拿到每个消息的额外信息）
        2. 调用父类拿到标准请求体
        3. 遍历请求体中的消息，把带 reasoning_content 的
           additional_kwargs 内容写回消息的 content 列表里
        """
        original_messages = self._convert_input(input_).to_messages()

        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        restored = restore_reasoning_content(payload.get("messages", []), original_messages)

        payload["messages"] = restored
        return payload


class DeepSeekProChatModel(PatchedChatDeepSeek):
    """DeepSeek Pro 级模型（深度推理，带 reasoning_content 修复）。

    对应 deepseek-reasoner / deepseek-r1 等推理模型：输出思考链
    （reasoning_content），多轮对话必须携带历史思考链——本实现继承
    已修复该问题的 PatchedChatDeepSeek，保证多轮链路稳定。
    """


def restore_reasoning_content(
    payload_messages: list[dict], original_messages: list
) -> list[dict]:
    """把 assistant 消息 additional_kwargs 里的 reasoning_content 还原进请求体。

    DeepSeek API 要求思考模型多轮对话时，每条 assistant 消息都要带
    reasoning_content。langchain_deepseek 序列化时把 reasoning_content
    塞进了 additional_kwargs 但没写回 content 数组，这里补上。

    参数：
        payload_messages: 父类生成的请求体消息列表
        original_messages: 转换前的 LangChain 消息列表（含 additional_kwargs）

    返回：
        还原后的请求体消息列表（原地复用，长度一致）
    """
    by_content: dict[str, dict] = {}
    for msg in original_messages:
        reasoning = getattr(msg, "additional_kwargs", {}).get("reasoning_content")
        if not reasoning:
            continue
        by_content[_extract_text(msg.content)] = {"reasoning_content": reasoning}

    for pm in payload_messages:
        if pm.get("role") != "assistant":
            continue
        key = _extract_text(pm.get("content"))
        if not key:
            continue
        hit = by_content.get(key)
        if hit is None:
            continue
        content = pm.get("content")
        if isinstance(content, list) and content:
            first = content[0] if isinstance(content[0], dict) else {}
            if first.get("type") == "text":
                content[0] = {**first, "reasoning_content": hit["reasoning_content"]}

    return payload_messages


def _extract_text(content: Any) -> str:
    """从消息 content 中提取纯文本（兼容 str 与多模态块列表）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def create_chat_model(
    name: str | None = None,
    thinking_enabled: bool = False,
    *,
    app_config: AppConfig | None = None,
    model_overrides: dict | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """按配置创建聊天模型实例。

    参数：
        name: 模型在配置中的名称；None 用当前激活模型
        thinking_enabled: 是否开启思考模式（仅支持的模型生效）
        app_config: 显式应用配置；缺省用全局单例
        model_overrides: 调用方采样覆盖（temperature / max_tokens 等），
            值为 None 的键忽略，避免覆盖掉配置值
        kwargs: 透传给模型构造器的额外参数

    返回：
        一个 BaseChatModel 实例（ChatOpenAI 或 ChatDeepSeek）
    """
    config = app_config or get_app_config()
    if name is None:
        model_config = config.active_model_config()
    else:
        model_config = config.get_model_config(name)
        if model_config is None:
            raise ValueError(f"模型 {name} 在配置中不存在") from None

    settings = _model_to_settings(model_config)

    if model_overrides:
        settings.update(
            {k: v for k, v in model_overrides.items() if v is not None}
        )

    model = _build_model(model_config, settings, thinking_enabled, **kwargs)

    logger.debug(
        "Created model %s (provider=%s, thinking=%s)",
        model_config.name,
        model_config.provider,
        thinking_enabled,
    )
    return model


def _model_to_settings(model_config: ModelConfig) -> dict[str, Any]:
    """把 ModelConfig 转换为模型构造器可接受的参数字典。

    剔除纯元数据字段（name/provider/supports_* / context_window），
    保留 model / api_key / base_url / temperature / max_tokens 等。
    """
    data = model_config.model_dump(exclude_none=True)
    for meta_key in ("name", "provider", "supports_vision", "supports_thinking", "context_window"):
        data.pop(meta_key, None)
    return data


def _build_model(
    model_config: ModelConfig,
    settings: dict[str, Any],
    thinking_enabled: bool,
    **kwargs: Any,
) -> BaseChatModel:
    """按 provider 选择实现类并实例化。

    参数：
        model_config: 模型配置（判断 provider 与思考支持）
        settings: 已合好配置与覆盖的构造器参数
        thinking_enabled: 是否请求思考模式
        kwargs: 额外透传参数
    """
    provider = model_config.provider

    if thinking_enabled and not model_config.supports_thinking:
        raise ValueError(
            f"模型 {model_config.name} 不支持思考模式（supports_thinking=false）"
        ) from None

    if provider == "openai":
        return ChatOpenAI(**settings, **kwargs)

    if provider == "deepseek":
        settings.pop("base_url", None)
        model_id = settings.get("model")
        if is_deepseek_pro_model(str(model_id) if model_id is not None else None):
            return DeepSeekProChatModel(**settings, **kwargs)
        return DeepSeekFlashChatModel(**settings, **kwargs)

    raise ValueError(f"不支持的模型提供方: {provider!r}（仅支持 openai / deepseek）") from None


def supports_vision(name: str | None = None, *, app_config: AppConfig | None = None) -> bool:
    """查询指定模型是否支持视觉输入。

    用途：view_image 工具只在模型能看图时才注册给 Agent，
    避免给纯文本模型塞一个它用不了的工具。

    参数：
        name: 模型配置名；None 用当前激活模型
        app_config: 显式配置；缺省用全局单例

    返回：
        该模型 supports_vision 是否为 True（找不到模型返回 False）
    """
    config = app_config or get_app_config()
    model_config = _find_model(config, name)
    if model_config is None:
        return False
    return bool(model_config.supports_vision)


def supports_thinking(name: str | None = None, *, app_config: AppConfig | None = None) -> bool:
    """查询指定模型是否支持思考模式。

    参数：
        name: 模型配置名；None 用当前激活模型
        app_config: 显式配置；缺省用全局单例

    返回：
        该模型 supports_thinking 是否为 True（找不到模型返回 False）
    """
    config = app_config or get_app_config()
    model_config = _find_model(config, name)
    if model_config is None:
        return False
    return bool(model_config.supports_thinking)


def _find_model(config: AppConfig, name: str | None) -> ModelConfig | None:
    """按名称或激活配置查找模型，找不到返回 None（不抛错，查询场景用）。"""
    if name is not None:
        return config.get_model_config(name)
    try:
        return config.active_model_config()
    except ValueError:
        return None