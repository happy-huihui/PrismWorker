from __future__ import annotations

from typing import Any

"""思考链（reasoning_content）通用处理

    职责：放「思考链在多轮请求里怎么保住」的通用代码，与具体 provider 无关。
    背景：DeepSeek / MiMo 这类推理模型多轮对话时，官方都建议把上一轮的
         reasoning_content 一并回传；而 LangChain 各家适配器在序列化
         assistant 消息时会把这个字段丢掉，导致多轮推理质量下降。

    对外暴露：
        - restore_reasoning_content  把思考链塞回请求体里的 assistant 消息
        - extract_message_text       从消息 content 中提取纯文本（内部匹配键用）
"""

# 思考内容在多轮请求体里的字段名（各家 OpenAI 兼容接口目前一致）
REASONING_FIELD = "reasoning_content"


def extract_message_text(content: Any) -> str:
    """从消息 content 中提取纯文本（兼容 str 与多模态块列表）。

    参数：
        content: 消息内容，可能是字符串或多模态块列表

    返回：
        拼接后的纯文本（无法识别时为空串）
    """
    # 字符串直接返回
    if isinstance(content, str):
        return content

    # 列表则挑出所有 text 块拼接
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)

    # 其他类型视为无文本
    return ""


def restore_reasoning_content(
    payload_messages: list[dict],
    original_messages: list,
) -> list[dict]:
    """把 assistant 消息 additional_kwargs 里的 reasoning_content 还原进请求体。

    推理模型多轮对话时，每条 assistant 消息都要带 reasoning_content。
    LangChain 适配器序列化时把它留在 additional_kwargs 但没写回 content
    数组，这里补上（实测 DeepSeek / MiMo 两种形状都接受）。

    参数：
        payload_messages: 父类生成的请求体消息列表
        original_messages: 转换前的 LangChain 消息列表（含 additional_kwargs）

    返回：
        还原后的请求体消息列表（原地复用，长度一致）
    """
    # 按纯文本内容建立 reasoning 索引（原始消息 → 思考链）
    by_content: dict[str, dict] = {}
    for msg in original_messages:
        extra = getattr(msg, "additional_kwargs", None) or {}
        reasoning = extra.get(REASONING_FIELD) if isinstance(extra, dict) else None
        if not reasoning:
            continue
        by_content[extract_message_text(msg.content)] = {REASONING_FIELD: reasoning}

    # 遍历请求体消息，把命中的思考链写回 assistant 文本块
    for pm in payload_messages:
        # 只处理 assistant 消息
        if pm.get("role") != "assistant":
            continue
        # 用文本内容作为匹配键
        key = extract_message_text(pm.get("content"))
        if not key:
            continue
        # 没有对应思考链则跳过
        hit = by_content.get(key)
        if hit is None:
            continue
        # 把 reasoning_content 注入 content 列表首个文本块
        content = pm.get("content")
        if isinstance(content, list) and content:
            first = content[0] if isinstance(content[0], dict) else {}
            if first.get("type") == "text":
                content[0] = {**first, REASONING_FIELD: hit[REASONING_FIELD]}

    return payload_messages
