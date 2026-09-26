from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

ModelRequest = types.ModelRequest
ToolCallRequest = types.ToolCallRequest

"""
    错误处理中间件（error_handling）——工具异常与模型异常的兜底。

    ToolErrorMiddleware（工具异常）：
      用 awrap_tool_call 包住工具执行：handler 抛异常时不把异常抛到图
      上层让整个 run 崩溃，而是转成一条友好的 ToolMessage（内容 = 统一
      中文错误说明 + 原始异常摘要），让模型看到"工具调用失败了，尝试
      其它方法"。对工具名/参数做净化，避免异常文本里的不可信内容注入。
      可注入 last_n 条最近错误在 prints 中提示。

    LLMErrorMiddleware（模型异常）：
      用 awrap_model_call 包住模型调用：捕获模型 API 异常，返回一条
      AIMessage 说明模型调用失败并引导用户重试/换模型，同时写一条中文
      进度消息。重试一次后仍失败才兜底（一次轻量重试，绝大多数瞬时
      网络抖动都可自愈）。
"""

logger = logging.getLogger(__name__)

_TOOL_ERROR_TEMPLATE = "工具执行出错：{error}\n请检查参数或改用其它方案重试。"
_LLM_ERROR_TEMPLATE = "模型调用失败：{error}\n请稍后重试，或检查模型配置/网络。"



class ToolErrorMiddleware(AgentMiddleware):
    """工具异常中间件：把工具执行异常转成模型可见的错误 ToolMessage。"""

    def __init__(self, *, retry_once: bool = False) -> None:
        """初始化；retry_once=True 时相同工具/参数失败先自动重试一次。"""
        self._retry_once = retry_once

    async def awrap_tool_call(
        self,
        request: ToolCallRequest[Any],
        handler: Callable[[ToolCallRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装工具执行：兜底异常为错误 ToolMessage。"""
        tool_call_id = str(request.tool_call.get("id") or "")
        tool_name = str(request.tool_call.get("name") or "")
        # 空 id 兜底：合成一个合法 id，避免 ToolMessage(tool_call_id="") 入历史后
        # 下一轮请求体出现空 id 引发服务端 400（2026-09-26 线上实锤）
        if not tool_call_id:
            tool_call_id = f"call_sanitized_{uuid.uuid4().hex[:24]}"
        last_error: Exception | None = None
        try:
            return await handler(request)
        except Exception as exc:  # noqa: BLE001 —— 工具异常一律兜底
            last_error = exc
            logger.warning("工具 %s 调用失败: %s", tool_name, exc, exc_info=True)
        if self._retry_once:
            try:
                return await handler(request)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("工具 %s 重试仍失败: %s", tool_name, exc)
        assert last_error is not None
        message = _sanitize(_TOOL_ERROR_TEMPLATE.format(error=_error_brief(last_error)))
        result = ToolMessage(
            content=message,
            tool_call_id=tool_call_id,
            name=tool_name,
        )
        result.id = tool_call_id
        return result



class LLMErrorMiddleware(AgentMiddleware):
    """模型异常中间件：模型调用失败时兜底为可读的 AIMessage。"""

    def __init__(self, *, max_retries: int = 1) -> None:
        """初始化；max_retries 为调用失败后的最大重试次数。"""
        self._max_retries = max(0, max_retries)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装模型调用：捕获异常并重试，仍失败则返回兜底 AIMessage。"""
        last_error: Exception | None = None
        attempts = 0
        while True:
            try:
                return await handler(request)
            except Exception as exc:  # noqa: BLE001 —— 模型异常统一兜底
                last_error = exc
                if attempts < self._max_retries:
                    attempts += 1
                    logger.warning("模型调用失败，第 %s 次重试……", attempts)
                    continue
                logger.error("模型调用最终失败: %s", exc, exc_info=True)
                break
        assert last_error is not None
        message = _sanitize(_LLM_ERROR_TEMPLATE.format(error=_error_brief(last_error)))
        return AIMessage(content=f"（系统提示模型调用异常）{message}")



def _error_brief(exc: Exception) -> str:
    """把异常压缩成一行摘要（截断，防超长刷屏）。"""
    cause = exc.__cause__ or exc
    text = str(cause).strip() or type(cause).__name__
    first_line = text.splitlines()[0] if text else type(exc).__name__
    return first_line[:300]


def _sanitize(text: str) -> str:
    """净化错误信息里的不可信片段（转义伪框架标签）。"""
    return neutralize_tags(text)


def neutralize_tags(text: str) -> str:
    """把常见的伪框架标签 <system>/<memory> 等转义为字面文本。

    独立可测试的公共原语；异常文本可能来自不可信来源（API 错误报文、
    工具输出里回显的外部数据），转义防止它们冒充系统指令。
    """
    return text.replace("<", "&lt;").replace(">", "&gt;")