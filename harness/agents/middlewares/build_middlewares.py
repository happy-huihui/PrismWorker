from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware

from harness.agents.middlewares.clarification import ClarificationMiddleware
from harness.agents.middlewares.deferred_tool_filter import DeferredToolFilterMiddleware
from harness.agents.middlewares.dynamic_context import DynamicContextMiddleware
from harness.agents.middlewares.error_handling import LLMErrorMiddleware, ToolErrorMiddleware
from harness.agents.middlewares.flow_control import (
    DanglingToolCallMiddleware,
    LoopDetectionMiddleware,
    SafetyFinishReasonMiddleware,
    SystemMessageCoalescingMiddleware,
)
from harness.agents.middlewares.input_sanitization_middleware import (
    InputSanitizationMiddleware,
)
from harness.agents.middlewares.memory_injection import MemoryInjectionMiddleware
from harness.agents.middlewares.sandbox_protection import (
    ReadBeforeWriteMiddleware,
    SandboxAuditMiddleware,
)
from harness.agents.middlewares.skill_middlewares import (
    SkillActivationMiddleware,
    SkillToolPolicyMiddleware,
)
from harness.agents.middlewares.subagent_middlewares import (
    DelegationLedgerMiddleware,
    SubagentLimitMiddleware,
)
from harness.agents.middlewares.summarization import SummarizationMiddleware
from harness.agents.middlewares.thread_context import ThreadContextMiddleware
from harness.agents.middlewares.title import TitleMiddleware
from harness.agents.middlewares.todo import TodoMiddleware
from harness.agents.middlewares.token_usage import TokenBudgetMiddleware, TokenUsageMiddleware
from harness.agents.middlewares.tool_progress import ToolProgressMiddleware
from harness.agents.middlewares.tool_result_handling import (
    ToolOutputBudgetMiddleware,
    ToolResultSanitizationMiddleware,
)
from harness.agents.middlewares.uploads import UploadsMiddleware
from harness.agents.middlewares.view_image import ViewImageMiddleware
from harness.config.app_config import AppConfig


"""
    中间件组装器（build_middlewares）——按固定顺序装配全部启用的中间件。

    挂载策略（与各钩子的执行语义对应）：
      - "wrapper" 型（awrap_model_call / awrap_tool_call）是洋葱包裹：先注册的
        在外层。因此把"最该先兜底/最外层"的净化、上下文、注入类放前面，
        护栏、审计、收尾统计放后面；
      - "before/after" 型按顺序先后执行，顺序即依赖顺序（如：先注入上下文，
        再注入记忆/上传；先工具进度，后委托账本收账）。

    每个中间件从 app_config 读取自身配置；个别可选参数（如 tool_progress
    的 event_sink）通过 kwargs 传入，None 时用默认值。返回按此顺序排列的
    列表，供 lead_agent 组装时按序挂载。
"""


def build_middlewares(
    app_config: AppConfig,
    *,
    event_sink: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    skills_dir: str | None = None,
) -> list[AgentMiddleware]:
    """按配置与固定顺序组装中间件列表。

    app_config  应用总配置（模型/记忆/子代理/工具等子配置的来源）
    event_sink  tool_progress 的事件回调（None → 事件收集到中间件实例）
    skills_dir  技能目录（None → 默认 {paths.base_dir}/skills）
    """
    memory = app_config.memory
    subagents = app_config.subagents
    active_name = app_config.active_model_config().name

    middlewares: list[AgentMiddleware] = [
        InputSanitizationMiddleware(),
        ThreadContextMiddleware(),
        DynamicContextMiddleware(),
        UploadsMiddleware(),
    ]
    if memory.mode == "middleware":
        middlewares.append(
            MemoryInjectionMiddleware(
                auto_save=False,
                max_results=memory.max_results,
            )
        )
    middlewares += [
        SkillActivationMiddleware(skills_dir=skills_dir),
        SkillToolPolicyMiddleware(),
        ViewImageMiddleware(),
        TitleMiddleware(model_name=active_name),
        TodoMiddleware(),
    ]
    middlewares += [
        ClarificationMiddleware(),
        TokenBudgetMiddleware(),
        SummarizationMiddleware(model_name=active_name),
        DeferredToolFilterMiddleware(),
        ToolProgressMiddleware(event_sink=event_sink),
        LoopDetectionMiddleware(),
        DanglingToolCallMiddleware(),
        SubagentLimitMiddleware(max_total_per_run=subagents.max_total_per_run),
        DelegationLedgerMiddleware(),
    ]
    middlewares += [
        ReadBeforeWriteMiddleware(),
        SandboxAuditMiddleware(),
        ToolErrorMiddleware(),
        LLMErrorMiddleware(),
    ]
    middlewares += [
        ToolOutputBudgetMiddleware(),
        ToolResultSanitizationMiddleware(),
        SystemMessageCoalescingMiddleware(),
        SafetyFinishReasonMiddleware(),
        TokenUsageMiddleware(),
    ]
    return middlewares