from __future__ import annotations

import re
from typing import Any

"""序列化与文本抽取（serialization）

    职责：把 LangChain / LangGraph 的对象与 state 通道值，转成运行编排
         需要的朴素 Python 结构（列表 / 纯文本 / 预览 / 角色名），并剥离
         输入防注入中间件加的用户文本包裹标记。
    背景：run 编排（解析 astream 帧）与会话历史读取都要「从消息里抠文本、
         判角色、去安全标记」；把这些纯函数收敛到一处，避免各处重复实现。

    对外暴露：
        - MAX_INPUT_PREVIEW / MAX_MESSAGE_PREVIEW   预览截断长度
        - as_list                 state 通道值 → 列表（兼容 None/列表/元组/字符串）
        - extract_text_content    消息 content → 纯文本（兼容 str 与内容块列表）
        - to_role                 LangChain 消息 type → API 角色名
        - strip_user_input_wrapper 去除防注入包裹标记，还原纯用户文本
        - messages_preview        取首条用户消息文本作为输入预览（截断）
        - convert_messages        dict 形式消息 → BaseMessage

    输出数据示例：
        - extract_text_content("hi")                    -> "hi"
        - extract_text_content([{type:"text",text:"a"},{type:"text",text:"b"}]) -> "ab"
        - to_role("human") / to_role("ai")              -> "user" / "assistant"
        - messages_preview([{type:"human",content:"帮我查天气…"}]) -> "帮我查天气…"(<=160 字)
"""

# 预览截断长度：输入取首条用户消息、输出取模型最终文本
MAX_INPUT_PREVIEW = 160
MAX_MESSAGE_PREVIEW = 120

# LangChain 消息 type → API 角色名（未知类型归为 message）
_ROLE_MAP = {
    "human": "user",
    "ai": "assistant",
    "tool": "tool",
    "system": "system",
    "function": "message",
}

# harness 输入防注入中间件会给 user 消息包上边界标记，
# 该标记属于内部安全机制、不是对话内容，读取时需剥离，避免 UI 展示内部噪音。
# 兼容两类残留：外层真实 BEGIN/END 包裹，与 neutralize 后遗留的惰性标记行。
_USER_INPUT_WRAP_RE = re.compile(
    r"^\s*---\s*BEGIN USER INPUT\s*---[\r\n]+(.*?)[\r\n]+\s*---\s*END USER INPUT\s*---\s*$",
    re.DOTALL,
)
_NEUTRALIZED_BOUNDARY_LINE_RE = re.compile(
    r"^\s*\[(?:BEGIN|END) USER INPUT\]\s*$", re.MULTILINE
)


def as_list(value: Any) -> list[Any]:
    """state 通道值 → 列表（兼容 None / 列表 / 元组 / 字符串）。

    参数：
        value: 待归一的通道值

    返回：
        列表形式（None/标量非字符串一律归为空列表，字符串归为单元素列表）
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        return [value]
    return []


def extract_text_content(content: Any) -> str:
    """消息 content → 纯文本（兼容 str 与 LangChain 内容块列表）。

    参数：
        content: 消息内容，可能是字符串或多模态块列表

    返回：
        拼接后的纯文本（无法识别时为空串）
    """
    if not content:
        return ""
    # 字符串直接返回
    if isinstance(content, str):
        return content
    # 列表：挑出所有 text 块拼接
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def to_role(msg_type: str) -> str:
    """LangChain 消息 type → API 角色名（未知类型归为 message）。

    参数：
        msg_type: LangChain 消息类型（human/ai/tool/system/function）

    返回：
        对应的 API 角色名
    """
    return _ROLE_MAP.get(msg_type, "message")


def strip_user_input_wrapper(text: str) -> str:
    """去除 input_sanitization 的会话包裹标记，还原纯用户文本。

    参数：
        text: 可能被防注入标记包裹的用户文本

    返回：
        剥离标记、折叠多余空行后的纯文本

    说明：先剥外层真实 BEGIN/END 包裹（可能多层嵌套）；再移除 neutralize
         遗留的惰性标记行（[BEGIN/END USER INPUT]）。
    """
    # 反复剥外层真实包裹
    while True:
        stripped = text.strip()
        m = _USER_INPUT_WRAP_RE.match(stripped)
        if not m:
            break
        text = m.group(1)
    # 移除惰性标记行并折叠多余空行
    cleaned = _NEUTRALIZED_BOUNDARY_LINE_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def messages_preview(messages: Any) -> str:
    """取首条用户消息文本作为输入预览（截断到 MAX_INPUT_PREVIEW）。

    参数：
        messages: 消息序列（dict 或 BaseMessage 混合）

    返回：
        首个非空文本的截断预览（无则空串）
    """
    for message in messages:
        # 兼容 dict 与对象两种消息形态
        if isinstance(message, dict):
            content = message.get("content") or ""
        else:
            content = getattr(message, "content", "") or ""
        if isinstance(content, list):
            content = extract_text_content(content)
        text = str(content).strip()
        if text:
            return text[:MAX_INPUT_PREVIEW]
    return ""


def convert_messages(messages: Any) -> list[Any]:
    """把 dict 形式的消息统一转成 BaseMessage（无 langchain 时原样返回）。

    参数：
        messages: 消息序列

    返回：
        BaseMessage 列表；若传入已是消息对象或转换失败，原样返回
    """
    # 已是消息对象则直接透传
    if messages and all(hasattr(m, "type") for m in messages):
        return list(messages)
    try:
        from langchain_core.messages import convert_to_messages

        return convert_to_messages(messages)
    except Exception:  # noqa: BLE001 —— 转换失败原样传递
        return list(messages)
