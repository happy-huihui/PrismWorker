from __future__ import annotations

import logging
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

"""
    澄清中间件（clarification）——模型信息不足时把疑问点暴露给前端。

    背景：Agent 在信息不足时（工具搜不到 / 用户意图含糊）需要向人提问。
    完整实现有专门的 clarify 工具 + 消息流；本项目收敛为「标记解析」最小版：
      - 注入系统提示：信息不足时，模型可在回复中用
        <clarify question="具体问题"/> 标记钦点需要澄清的问题；
      - aafter_model：扫描本轮 AI 消息里的 clarify 标记，把它们剥离出
        正文（避免控制标签原样出现在回复里），并把问题列表写入
        state.prints —— 运行层把 prints 流式推给前端，前端可据此
        向用户展示"需要澄清"的环节。无标记时静默。
"""

logger = logging.getLogger(__name__)

_CLARIFY_TAG_RE = re.compile(
    r"<\s*clarify\s+question=[\"']([^\"']+)[\"']\s*/?>"
    r"|<\s*clarify\s+question=[\"']([^\"']+)[\"']\s*>\s*</\s*clarify\s*>",
    re.IGNORECASE,
)

_CLARIFY_HINT = (
    "当信息不足、无法继续任务时，请在回复中单独使用"
    "<clarify question=\"你需要澄清的问题\"/> 标记提出疑问"
    "（每个标记一个简短具体的问题），不要编造缺失的信息。"
)


class ClarificationMiddleware(AgentMiddleware):
    """澄清中间件：解析模型提出的 clarify 标记并输出为进度消息。"""

    async def awrap_model_call(
        self,
        request: Any,
        handler: Any,
    ) -> Any:
        """包装模型调用：注入 clarify 提示（仅当尚未注入过）。"""
        system_message = request.system_message
        if system_message is not None and "clarify" in system_message.text:
            return await handler(request)
        if system_message is None:
            from langchain_core.messages import SystemMessage

            system_message = SystemMessage(content=_CLARIFY_HINT)
        else:
            text = system_message.text
            system_message = system_message.__class__(
                content=f"{_CLARIFY_HINT}\n\n{text}" if text else _CLARIFY_HINT
            )
        return await handler(request.override(system_message=system_message))

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