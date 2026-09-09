from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from harness.agents.middlewares.input_sanitization_middleware import neutralize_untrusted_tags

"""
    工具结果处理中间件（tool_result_handling）——工具输出的安全与预算两道闸。

    ToolResultSanitizationMiddleware（净化）：
      工具/子代理的返回内容属于「不可信」输入（可能来自网络、用户文件、
      外部系统），中间件在每次模型调用后扫描本轮新增的 ToolMessage，
      对其中文本内容做 neutralize_untrusted_tags 转义，防止工具输出里夹带
      的伪 <system> / <memory> 等标签冒充框架上下文实施提示注入。
      实现：返回同 id 的新 ToolMessage（add_messages 按 id 原地覆盖），
      只重写文本块；非文本块与结构化字段原样保留。

    ToolOutputBudgetMiddleware（预算）：
      防止个别工具吐出超长结果挤爆上下文——对每条新增 ToolMessage 做近似
      token 估算，超过 max_output_tokens 时保头截断、附加「已截断」说明，
      并按 80% 水位提前打印一条中文进度提示。
"""

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOKENS = 8000



class ToolResultSanitizationMiddleware(AgentMiddleware):
    """工具结果净化中间件：每次模型调用后净化新增的工具输出文本。"""

    async def aafter_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用后：净化新增的 ToolMessage 文本内容。"""
        messages = (state or {}).get("messages") or []
        changed: list[ToolMessage] = []
        for message in messages:
            if message is None or getattr(message, "type", "") != "tool":
                continue
            sanitized = _sanitize_tool_content(message.content)
            if sanitized != message.content:
                new_message = ToolMessage(
                    content=sanitized,
                    tool_call_id=str(getattr(message, "tool_call_id", "") or ""),
                    name=getattr(message, "name", None),
                )
                new_message.id = message.id
                changed.append(new_message)
        if not changed:
            return None
        return {"messages": changed}



class ToolOutputBudgetMiddleware(AgentMiddleware):
    """工具输出预算中间件：超限的工具结果保头截断，防止撑爆上下文。"""

    def __init__(self, *, max_output_tokens: int = _DEFAULT_MAX_TOKENS) -> None:
        """初始化；max_output_tokens 为单条工具输出的 token 上限。"""
        self._max_tokens = max(1, max_output_tokens)

    async def aafter_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用后：扫描新增工具结果，超限的截断并提示。"""
        messages = (state or {}).get("messages") or []
        prints: list[str] = []
        changed: list[ToolMessage] = []
        for message in messages:
            if message is None or getattr(message, "type", "") != "tool":
                continue
            try:
                tokens = count_tokens_approximately([message])
            except Exception:  # noqa: BLE001 —— 估算失败就不做预算干预
                continue
            if tokens > self._max_tokens * 0.8 and tokens <= self._max_tokens:
                prints.append(
                    f"注意：工具 {getattr(message, 'name', '')} 输出已达"
                    f"{tokens} token（预算 {self._max_tokens}）"
                )
            if tokens > self._max_tokens:
                truncated = _truncate_content(
                    message.content, self._max_tokens
                )
                new_message = ToolMessage(
                    content=truncated,
                    tool_call_id=str(getattr(message, "tool_call_id", "") or ""),
                    name=getattr(message, "name", None),
                )
                new_message.id = message.id
                changed.append(new_message)
                prints.append(
                    f"工具 {getattr(message, 'name', '')} 输出超预算，已截断"
                    f"至 {len(truncated)} 字符"
                )
        updates: dict[str, Any] = {}
        if changed:
            updates["messages"] = changed
        if prints:
            updates["prints"] = prints
        return updates or None



def _sanitize_tool_content(content: Any) -> Any:
    """净化工具结果文本：字符串整体净化；块列表只净化 text 块。"""
    if isinstance(content, str):
        return neutralize_untrusted_tags(content)
    if isinstance(content, list):
        processed: list[Any] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                processed.append({
                    **block,
                    "text": neutralize_untrusted_tags(str(block.get("text", ""))),
                })
            else:
                processed.append(block)
        return processed
    return content


def _truncate_content(content: Any, max_tokens: int) -> Any:
    """保头截断工具结果，并附「已截断」说明。

    估算上 1 token ≈ 4 字符，这里直接按 4*max_tokens 字符截取（保守足够）。
    """
    if isinstance(content, str):
        limit_chars = max(64, max_tokens * 4)
        if len(content) <= limit_chars:
            return content
        return content[:limit_chars] + "\n…（工具输出过长，已截断）"
    if isinstance(content, list):
        text = "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        return _truncate_content(text, max_tokens)
    return content