from __future__ import annotations

from datetime import datetime
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage


"""动态上下文中间件

    职责：每次模型调用前把当前本地时间追加到系统消息末尾
        - 模型对「现在几点 / 今天几号」没有概念，而日报、时间线、截止日期都依赖它

    对外暴露：
        - DynamicContextMiddleware
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
        # 1.已注入过当前时间（含标记）就不重复注入
        existing = request.system_message
        if existing is not None and _DATETIME_MARKER in existing.text:
            return await handler(request)

        # 2.取当前本地时间，拼成带标记的一行块
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        block = (
            f"<{_DATETIME_MARKER.strip('<>')}>当前日期时间：{now}"
            f"（本地时区）</{_DATETIME_MARKER.strip('<>')}>"
        )

        # 3.追加到系统消息末尾（无系统消息则直接作为系统消息）
        if existing is None:
            system_message = SystemMessage(content=block)
        else:
            text = existing.text
            if text:
                text = f"{text}\n\n{block}"
            else:
                text = block
            system_message = SystemMessage(content=text)

        # 4.用新系统消息放行
        return await handler(request.override(system_message=system_message))