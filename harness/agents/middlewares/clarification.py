from __future__ import annotations

import logging
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

"""
    澄清中间件（clarification）——向后兼容地处理模型可能残留的 <clarify> 标记。

    现状：澄清已改为「工具优先」。主提示词的 <clarification_system> 指示模型在
    信息不足/有歧义/需确认时调用 ask_clarification 工具（return_direct，会中断本轮
    等待用户回复），不再依赖系统提示里的 <clarify> 用法说明。
    本中间件因此只保留兜底解析：aafter_model 扫描本轮 AI 消息里遗留的 <clarify> 标记，
    把它们从正文剥离（避免控制标签外泄）并把问题写入 state.prints，供前端展示；无标记则静默。
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
        messages = (state or {}).get("messages") or []
        latest_ai = None
        for message in reversed(messages):
            if message is not None and getattr(message, "type", "") == "ai":
                latest_ai = message
                break
        if latest_ai is None:
            return None
        content = _extract_text(latest_ai)
        questions = _extract_clarify_questions(content)
        if not questions:
            return None
        cleaned = _CLARIFY_TAG_RE.sub("", content).strip()
        new_message = AIMessage(
            content=cleaned,
            name=getattr(latest_ai, "name", None),
        )
        new_message.id = latest_ai.id
        prints = [f"需要向用户澄清：{question}" for question in questions[:5]]
        return {"messages": [new_message], "prints": prints}



def _extract_clarify_questions(text: str) -> list[str]:
    """从文本里提取全部 clarify 问题（去重保序，去掉空问题）。"""
    if not text:
        return []
    questions: list[str] = []
    for match in _CLARIFY_TAG_RE.finditer(text):
        question = (match.group(1) or match.group(2) or "").strip()
        if question and question not in questions:
            questions.append(question)
    return questions


def _extract_text(message_or_response: Any) -> str:
    """从消息提取纯文本（兼容 str 与多模态块列表）。"""
    content = getattr(message_or_response, "content", message_or_response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""