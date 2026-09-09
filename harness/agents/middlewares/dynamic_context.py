from __future__ import annotations

from datetime import datetime
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage


"""
    动态上下文中间件（dynamic_context）——在每次模型调用前注入当前时间。

    模型对“现在几点/今天几号”没有概念，而很多任务（日报、时间线、截止日期）
    依赖当前时间。每次调用前在系统消息末尾追加一行当前本地时间，
    供模型在需要时直接读取。插入点为系统消息，不污染对话历史。
"""

_DATETIME_MARKER = "<current_datetime>"


class DynamicContextMiddleware(AgentMiddleware):
    """动态上下文中间件：每次模型调用前追加当前日期时间。"""

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],  # type: ignore[valid-type]
        handler: Any,
    ) -> ModelResponse:
        """包装模型调用：在系统消息末尾注入当前本地时间后放行。"""
        existing = request.system_message
        if existing is not None and _DATETIME_MARKER in existing.text:
            return await handler(request)

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        block = (
            f"<{_DATETIME_MARKER.strip('<>')}>当前日期时间：{now}"
            f"（本地时区）</{_DATETIME_MARKER.strip('<>')}>"
        )

        if existing is None:
            system_message = SystemMessage(content=block)
        else:
            text = existing.text
            if text:
                text = f"{text}\n\n{block}"
            else:
                text = block
            system_message = SystemMessage(content=text)

        return await handler(request.override(system_message=system_message))