from __future__ import annotations

import logging
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

"""澄清中间件

    职责：兜底解析模型正文里残留的 <clarify> 标记
        - 澄清已改为「工具优先」，本中间件只做兼容兜底
        - 把标记从正文剥离并写入 state.prints 供前端展示

    对外暴露：
        - ClarificationMiddleware
"""

logger = logging.getLogger(__name__)

_CLARIFY_TAG_RE = re.compile(
    r"<\s*clarify\s+question=[\"']([^\"']+)[\"']\s*/?>"
    r"|<\s*clarify\s+question=[\"']([^\"']+)[\"']\s*>\s*</\s*clarify\s*>",
    re.IGNORECASE,
)


class ClarificationMiddleware(AgentMiddleware):
    """澄清中间件（向后兼容）：解析模型可能残留的 <clarify> 标记。

    说明：澄清已改为「工具优先」——由主提示词的 <clarification_system> 指示模型
    调用 ask_clarification 工具（该工具会中断本轮并等待用户回复）。本中间件不再
    向系统提示注入 <clarify> 用法提示，仅在模型仍吐出遗留 <clarify> 标记时，把
    问题剥离正文并转成进度打印，保证不污染回复。
    """

    async def aafter_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用后：提取 clarify 问题，剥离标记并写进度。"""
        # 1.从后往前找最近一条 AI 消息
        messages = (state or {}).get("messages") or []
        latest_ai = None
        for message in reversed(messages):
            if message is not None and getattr(message, "type", "") == "ai":
                latest_ai = message
                break
        # 2.没有 AI 消息 → 无可处理
        if latest_ai is None:
            return None
        # 3.提取正文与 clarify 问题；无问题 → 放行
        content = _extract_text(latest_ai)
        questions = _extract_clarify_questions(content)
        if not questions:
            return None
        # 4.剥离 <clarify> 标记得到干净正文，重建 AI 消息（保留原 id）
        cleaned = _CLARIFY_TAG_RE.sub("", content).strip()
        new_message = AIMessage(
            content=cleaned,
            name=getattr(latest_ai, "name", None),
        )
        new_message.id = latest_ai.id
        # 5.问题转成进度打印（最多 5 条），供前端展示
        prints = [f"需要向用户澄清：{question}" for question in questions[:5]]
        return {"messages": [new_message], "prints": prints}



def _extract_clarify_questions(text: str) -> list[str]:
    """从文本里提取全部 clarify 问题（去重保序，去掉空问题）。"""
    # 1.空文本无问题
    if not text:
        return []
    # 2.遍历所有标记匹配，去空、去重、保序
    questions: list[str] = []
    for match in _CLARIFY_TAG_RE.finditer(text):
        question = (match.group(1) or match.group(2) or "").strip()
        if question and question not in questions:
            questions.append(question)
    return questions


def _extract_text(message_or_response: Any) -> str:
    """从消息提取纯文本（兼容 str 与多模态块列表）。"""
    # 1.取 content（消息对象或原始值都兼容）
    content = getattr(message_or_response, "content", message_or_response)
    # 2.字符串直接返回
    if isinstance(content, str):
        return content
    # 3.多模态块列表：拼出所有 text 块
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    # 4.其它类型视为无文本
    return ""