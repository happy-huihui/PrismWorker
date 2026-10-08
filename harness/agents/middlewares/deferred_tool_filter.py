from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import SystemMessage

from harness.prompt import render_text

ModelRequest = types.ModelRequest


"""延迟工具过滤中间件

    职责：模型调用前按白名单过滤工具（stub 最小实现）
        - 白名单内的工具直接放行，为空表示全部放行
        - 非白名单工具移除，并提示模型改用其它工具
        - 不做 defer-and-promote 两阶段机制

    对外暴露：
        - DeferredToolFilterMiddleware
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
        # 1.白名单为空 = 全部放行（等价于未启用本中间件）
        if not self._whitelist:
            return await handler(request)
        # 2.本轮没有工具 → 无可过滤
        tools = request.tools
        if not tools:
            return await handler(request)

        # 3.计算被白名单拦下的工具名
        existing_names = {_tool_name(tool) for tool in tools}
        blocked = sorted(name for name in existing_names if name not in self._whitelist)
        # 3.1 没有被拦的 → 直接放行
        if not blocked:
            return await handler(request)
        # 3.2 保留白名单内的工具
        kept = [tool for tool in tools if _tool_name(tool) in self._whitelist]

        # 4.注入提示：告知模型哪些工具暂不可用（文案集中在 harness.prompt）
        system_message = request.system_message
        text = system_message.text if system_message is not None else ""
        notice = render_text("deferred_tools/unavailable", {"blocked": ", ".join(blocked)})
        if system_message is None:
            new_system = SystemMessage(content=notice)
        else:
            text = f"{notice}\n\n{text}" if text else notice
            new_system = system_message.__class__(content=text)

        # 5.用过滤后的工具 + 新系统消息放行
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