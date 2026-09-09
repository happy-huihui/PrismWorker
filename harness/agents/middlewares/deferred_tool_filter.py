from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import SystemMessage

ModelRequest = types.ModelRequest


"""
    延迟工具过滤中间件（deferred_tool_filter）——stub 最小实现。

    背景（用户确认的方案）：完整版里，某些"延迟工具"（deferrable tools，
    如耗时的 Web 搜索 / 子代理派发）不该随请求一次性全量暴露给模型，而应
    按需放行。本项目收敛为最小实现：
      - 保留 whitelist（白名单）概念：白名单内的工具**直接放行**（原样留在
        request.tools，交给后续模型调用）；
      - 白名单为空 = 全部放行（等价于未启用本中间件）；
      - 非白名单工具在模型调用前被移除，并注入一条系统提示说明哪些工具
        暂不可用，引导模型改用其它工具。

    这是一个明确标注的 stub：只做「白名单直接放行」这一档，不做
    defer-and-promote（延迟到后续轮次再提升）的完整两阶段机制。
"""


class DeferredToolFilterMiddleware(AgentMiddleware):
    """工具过滤 stub：白名单直接放行，其余工具移除并提示模型。"""

    def __init__(self, *, whitelist: list[str] | set[str] | None = None) -> None:
        """初始化；whitelist 内工具直接放行，None/空 = 全部放行。"""
        self._whitelist = frozenset(whitelist) if whitelist else frozenset()

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装模型调用：按白名单过滤 request.tools 后交给 handler。"""
        if not self._whitelist:
            return await handler(request)
        tools = request.tools
        if not tools:
            return await handler(request)

        existing_names = {_tool_name(tool) for tool in tools}
        blocked = sorted(name for name in existing_names if name not in self._whitelist)
        if not blocked:
            return await handler(request)
        kept = [tool for tool in tools if _tool_name(tool) in self._whitelist]

        system_message = request.system_message
        text = system_message.text if system_message is not None else ""
        notice = (
            f"提示：以下工具当前不可用，请勿调用：{', '.join(blocked)}。"
            "请改用白名单内的可用工具完成任务。"
        )
        if system_message is None:
            new_system = SystemMessage(content=notice)
        else:
            text = f"{notice}\n\n{text}" if text else notice
            new_system = system_message.__class__(content=text)

        return await handler(
            request.override(
                tools=kept,
                system_message=new_system,
            )
        )


def _tool_name(tool: Any) -> str:
    """从（BaseTool | dict schema）里安全提取工具名。"""
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    name = getattr(tool, "name", None)
    return str(name) if name else ""