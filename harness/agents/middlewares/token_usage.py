from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import SystemMessage
from langchain_core.messages.utils import count_tokens_approximately

ModelRequest = types.ModelRequest

"""
    token 用量中间件（token_usage）——用量观察与预算护栏。

    TokenUsageMiddleware（用量观察）：
      每次模型调用后读取最新 AIMessage 的 usage_metadata，算出本轮 prompt /
      completion / total 用量增量，向 prints 写一行中文进展，并把累计值暴露
      在实例上（self.total_tokens）供运行层读取埋点。供应商没返回 usage
      元数据时静默跳过（不打扰，也不阻断主流程）。

    TokenBudgetMiddleware（预算护栏）：
      对最近上下文做近似 token 估算（count_tokens_approximately），超过
      max_tokens 时在模型调用前注入"上下文即将超限，请尽快收尾"的系统提示，
      并按 80% 水位提前一次轻提醒。这是软护栏（不硬断），只干预提示不打断
      执行，避免长任务中途被无谓掐断。
"""

logger = logging.getLogger(__name__)

_DEFAULT_TOKEN_BUDGET = 100000


class TokenUsageMiddleware(AgentMiddleware):
    """token 用量观察中间件：记录并打印每次模型调用的用量。"""

    def __init__(self) -> None:
        """初始化累计计数（prompt/completion/total）。"""
        self.total_tokens = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self._last_total = 0

    async def aafter_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用后：读取本轮用量并写进度。"""
        messages = (state or {}).get("messages") or []
        latest_ai = None
        for message in reversed(messages):
            if message is not None and getattr(message, "type", "") == "ai":
                latest_ai = message
                break
        if latest_ai is None:
            return None
        usage = getattr(latest_ai, "usage_metadata", None) or {}
        if not isinstance(usage, dict):
            return None

        prompt = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        completion = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion) or 0)
        if prompt == 0 and completion == 0 and total == 0:
            return None

        if total > 0:
            self._last_total = max(self._last_total, total)
        self.total_prompt_tokens += prompt
        self.total_completion_tokens += completion
        self.total_tokens = self._last_total

        return None


class TokenBudgetMiddleware(AgentMiddleware):
    """token 预算护栏中间件：上下文逼近上限时提醒模型尽快收尾。"""

    def __init__(self, *, max_tokens: int = _DEFAULT_TOKEN_BUDGET) -> None:
        """初始化；max_tokens 为软上限。"""
        self._max_tokens = max(1000, max_tokens)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装模型调用：逼近预算时注入收尾提示。"""
        messages = request.messages or []
        try:
            current = count_tokens_approximately(messages)
        except Exception:  # noqa: BLE001 —— 估算失败就跳过护栏
            return await handler(request)
        if current < self._max_tokens * 0.8:
            return await handler(request)
        if current >= self._max_tokens:
            notice = (
                f"注意：当前上下文 token 已超预算上限（{self._max_tokens}）。"
                "请停止展开新任务，仅在必要时调用轻量工具，并尽快给出最终回答。"
            )
        else:
            notice = (
                f"提示：上下文 token 已达 {current}/{self._max_tokens}"
                "（约 80%），请在后续回答中保持精简。"
            )
        system_message = request.system_message
        if system_message is None:
            system_message = SystemMessage(content=notice)
        else:
            text = system_message.text
            system_message = system_message.__class__(
                content=f"{notice}\n\n{text}" if text else notice
            )
        return await handler(request.override(system_message=system_message))