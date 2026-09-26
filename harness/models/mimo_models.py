from __future__ import annotations

import logging
from typing import Any

from langchain_openai import ChatOpenAI

from harness.models.reasoning import (
    REASONING_FIELD,
    extract_message_text,
    restore_reasoning_content,
)

"""MiMo（小米大模型）模型实现类

    职责：放 MiMo 到底用哪几个模型类，以及多轮思考链的保活代码。
    背景：MiMo 走 OpenAI 兼容协议（https://api.xiaomimimo.com/v1），
         所以直接继承 ChatOpenAI；但它是推理模型——默认就返回
         reasoning_content（无需任何开关参数），多轮时把上一轮思考链
         一并回传效果最好，而 LangChain 序列化时会把它丢掉，这里补回来。

    实测结论（2026-09-25，非照抄文档）：
        - /v1/models 当前 9 个模型，pro 档最新为 mimo-v2.6-pro
          （mimo-v2.5-pro / mimo-v2.5 官方公告 2026-10-21 下线，禁用）
        - 思考链：不传任何参数即返回 reasoning_content；额外传
          thinking={"type":"enabled"} / enable_thinking / reasoning_effort
          反而让输出退化（实测完成 token 从 36 掉到 5）→ 一律不下发
        - 流式：delta 同时下发 reasoning_content（103 块）与 content（48 块）
          → langchain_openai 会把前者收进 additional_kwargs，前端思考链零改动可用
        - 工具调用：单轮/多轮 tool_calls 均正常；带 tool_calls 的 assistant 消息
          回传后追问 HTTP 200
        - 多轮回传：assistant 消息带 / 不带 reasoning_content 都是 200，
          带的时候推理更充分 → 用 restore_reasoning_content 注入

    对外暴露：
        - MIMO_BASE_URL                默认接口地址（配置未给 base_url 时用）
        - MIMO_PRO_MODEL_ID            最新 pro 档（mimo-v2.6-pro）
        - MIMO_FLASH_MODEL_ID          快档（mimo-v2.6-flash）
        - MiMoProChatModel             Pro 档实现类（带思考链保活）
        - MiMoFlashChatModel           Flash 档实现类
        - is_mimo_pro_model            精确判断是不是 pro 档
        - is_mimo_flash_model          精确判断是不是 flash 档
"""

logger = logging.getLogger(__name__)

# 官方 OpenAI 兼容接口地址
MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"

# 当前可用的对话模型标识（v2.6 系列；v2.5 系列即将下线，不在支持列表）
MIMO_PRO_MODEL_ID = "mimo-v2.6-pro"
MIMO_FLASH_MODEL_ID = "mimo-v2.6-flash"
MIMO_PRO_ULTRASPEED_MODEL_ID = "mimo-v2.6-pro-ultraspeed"

# 允许配置的模型标识（策略层按此表校验）
MIMO_SUPPORTED_MODEL_IDS = (
    MIMO_PRO_MODEL_ID,
    MIMO_FLASH_MODEL_ID,
    MIMO_PRO_ULTRASPEED_MODEL_ID,
)


def is_mimo_pro_model(model_id: str | None) -> bool:
    """精确判断是不是 pro 档（含 ultraspeed 变体）。

    参数：
        model_id: 模型标识

    返回：
        等于 pro / pro-ultraspeed 标识返回 True，否则 False
    """
    # 空标识不算 pro
    if not model_id:
        return False

    # 去空格、转小写后与 pro 档标识比对
    normalized = model_id.strip().lower()
    return normalized in (MIMO_PRO_MODEL_ID, MIMO_PRO_ULTRASPEED_MODEL_ID)


def is_mimo_flash_model(model_id: str | None) -> bool:
    """精确判断是不是 flash 档。

    参数：
        model_id: 模型标识

    返回：
        等于 flash 标识返回 True，否则 False
    """
    # 空标识不算 flash
    if not model_id:
        return False

    # 去空格、转小写后与 flash 标识精确比对
    return model_id.strip().lower() == MIMO_FLASH_MODEL_ID


def _ensure_text_blocks(
    payload_messages: list[dict],
    original_messages: list,
) -> list[dict]:
    """把「有思考链、但 content 是纯字符串」的 assistant 消息转成文本块数组。

    为什么需要这一步：ChatOpenAI 序列化纯文本 assistant 消息时给的是
    ``content: "..."`` 字符串，而思考链只能挂在 ``[{"type":"text",...}]``
    块里；restore_reasoning_content 只认块数组，直接调会漏掉这类消息。
    实测 MiMo 接受 ``content`` 为单元素文本块数组（HTTP 200）。

    只转换「确实带思考链」的消息，其余保持字符串原样，避免改动无关请求体。

    参数：
        payload_messages: 父类生成的请求体消息列表
        original_messages: 转换前的 LangChain 消息列表（含 additional_kwargs）

    返回：
        归一化后的请求体消息列表（原地复用）
    """
    # 建立「文本 → 思考链」索引，只为带思考链的消息建键
    with_reasoning: set[str] = set()
    for msg in original_messages:
        extra = getattr(msg, "additional_kwargs", None) or {}
        reasoning = extra.get(REASONING_FIELD) if isinstance(extra, dict) else None
        if not reasoning:
            continue
        with_reasoning.add(extract_message_text(msg.content))

    # 命中索引的纯文本 assistant 消息 → 转成单元素文本块数组
    for pm in payload_messages:
        if pm.get("role") != "assistant":
            continue
        content = pm.get("content")
        if not isinstance(content, str):
            continue
        if content not in with_reasoning:
            continue
        pm["content"] = [{"type": "text", "text": content}]

    return payload_messages


class _MiMoChatModelBase(ChatOpenAI):
    """MiMo 模型公共基类：统一密钥来源 + 多轮思考链保活。

    ChatOpenAI 本身已能跑通 MiMo（OpenAI 兼容），这里只补两件事：
        1. 序列化时密钥统一映射到 MIMO_API_KEY，避免换环境后鉴权丢失；
        2. 重写 _get_request_payload，把 additional_kwargs 里的
           reasoning_content 写回 assistant 消息（实测注入 content 文本块
           内 MiMo 接受，HTTP 200）。
    """

    @classmethod
    def is_lc_serializable(cls) -> bool:
        """LangChain 序列化兼容开关（保持与父类一致的默认行为）。"""
        # 允许被 LangChain 序列化
        return True

    @property
    def lc_secrets(self) -> dict[str, str]:
        """序列化时把密钥字段统一映射到 MIMO_API_KEY。"""
        # 三个可能的密钥字段名都走同一个环境变量
        return {
            "api_key": "MIMO_API_KEY",
            "openai_api_key": "MIMO_API_KEY",
            "openai_api_base": "MIMO_API_KEY",
        }

    def _create_chat_result(
        self,
        response: Any,
        generation_info: dict | None = None,
    ) -> Any:
        """非流式：把响应里的 reasoning_content 补进 additional_kwargs。

        langchain_openai 明确不抽取第三方字段（其类文档原话："Non-standard
        response fields added by third-party providers (e.g., reasoning_content)
        are not extracted"），不补这里，非流式调用就拿不到思考链。

        参数：
            response: 原始响应（dict 或 openai 的 pydantic 对象）
            generation_info: 父类透传的生成信息

        返回：
            补好思考链的 ChatResult（解析失败时原样返回，不影响主流程）
        """
        # 先让父类照常解析
        result = super()._create_chat_result(response, generation_info)

        # 再按 choice 顺序把思考链挂回去（拿不到原始 dict 就跳过，不抛错）
        try:
            raw = (
                response
                if isinstance(response, dict)
                else response.model_dump(warnings=False)
            )
            choices = (raw or {}).get("choices") or []
            for i, gen in enumerate(result.generations):
                if i >= len(choices):
                    break
                reasoning = (choices[i].get("message") or {}).get(REASONING_FIELD)
                if not reasoning:
                    continue
                message = gen.message
                message.additional_kwargs = {
                    **dict(getattr(message, "additional_kwargs", {}) or {}),
                    REASONING_FIELD: reasoning,
                }
        except Exception:  # noqa: BLE001 — 兜底：解析差异不该打断一次调用
            logger.debug("MiMo 非流式响应未取到 reasoning_content", exc_info=True)

        return result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> Any:
        """流式：把 delta.reasoning_content 补进增量块的 additional_kwargs。

        与非流式同理，父类不抽取该字段；这里逐块注入，LangChain 合并
        additional_kwargs 时对字符串做拼接，思考链因此能完整累积。

        参数：
            chunk: 一条 SSE 解析后的响应字典
            default_chunk_class: 父类指定的消息块类型
            base_generation_info: 父类透传的生成信息

        返回：
            补好思考链的 ChatGenerationChunk（父类返回 None 时也返回 None）
        """
        # 先让父类照常解析
        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation is None or generation.message is None:
            return generation

        # 从 delta 里取本块的思考增量
        choices = chunk.get("choices") or []
        if not choices:
            return generation
        delta = choices[0].get("delta") or {}
        reasoning = delta.get(REASONING_FIELD)
        if not reasoning:
            return generation

        # 挂到增量块上，供运行层 extract_reasoning_content 逐块取用
        message = generation.message
        message.additional_kwargs = {
            **dict(getattr(message, "additional_kwargs", {}) or {}),
            REASONING_FIELD: reasoning,
        }
        return generation

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """组装请求体，并还原 assistant 消息里的 reasoning_content。

        注意：这里**不**下发任何思考开关参数——实测传了反而让输出退化，
        MiMo 默认就会产出 reasoning_content。

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

        # 3. 纯文本 assistant 消息先转成文本块数组，好让思考链有地方挂
        normalized = _ensure_text_blocks(payload.get("messages", []), original_messages)

        # 4. 把带 reasoning_content 的 additional_kwargs 内容写回请求消息
        restored = restore_reasoning_content(normalized, original_messages)

        # 用还原后的消息替换请求体消息
        payload["messages"] = restored
        return payload


class MiMoProChatModel(_MiMoChatModelBase):
    """MiMo Pro 档模型（mimo-v2.6-pro，深度推理）。

    当前最新 pro，默认输出 reasoning_content；多轮时会把上一轮思考链
    一起回传（见基类 _get_request_payload），推理更连贯。
    """


class MiMoFlashChatModel(_MiMoChatModelBase):
    """MiMo Flash 档模型（mimo-v2.6-flash，低延迟）。

    同样返回 reasoning_content，只是推理更浅、更快；行为与 Pro 一致，
    单独成类是为了让策略层能按模型标识精确分流。
    """
