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
    SystemMessageCoalescingMiddleware,
)
from harness.agents.middlewares.model_output_sanitizer import ModelOutputSanitizerMiddleware
from harness.agents.middlewares.input_sanitization_middleware import (
    InputSanitizationMiddleware,
)
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
from harness.agents.middlewares.trace import SpanMiddleware
from harness.agents.middlewares.token_usage import TokenBudgetMiddleware, TokenUsageMiddleware
from harness.agents.middlewares.tool_arg_coercion import ToolArgCoercionMiddleware
from harness.agents.middlewares.tool_progress import ToolProgressMiddleware
from harness.agents.middlewares.tool_result_handling import (
    ToolOutputBudgetMiddleware,
    ToolResultSanitizationMiddleware,
)
from harness.agents.middlewares.uploads import UploadsMiddleware
from harness.agents.middlewares.view_image import ViewImageMiddleware
from harness.config.app_config import AppConfig


"""中间件组装器

    职责：读配置，把全部启用的中间件按固定顺序装配成列表返回
        - wrapper 型是洋葱包裹，先注册的在外层 → 净化 / 上下文 / 注入在前，护栏 / 审计 / 收尾在后
        - before/after 型按顺序先后执行，顺序即依赖顺序

    对外暴露：
        - build_middlewares(app_config, *, event_sink=..., skills_dir=...)
"""


def build_middlewares(
    app_config: AppConfig,
    *,
    event_sink: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    skills_dir: str | None = None,
    sandbox: Any | None = None,
) -> list[AgentMiddleware]:
    """按配置与固定顺序组装中间件列表。

    app_config  应用总配置（模型/记忆/子代理/工具等子配置的来源）
    event_sink  tool_progress 的事件回调（None → 事件收集到中间件实例）
    skills_dir  技能目录（None → 默认 {paths.base_dir}/skills）
    sandbox     已装配的沙箱实例（None → 无沙箱；「写前读」护栏用它探测文件是否存在）
    """
    memory = app_config.memory
    subagents = app_config.subagents
    active_name = app_config.active_model_config().name
    # 「写前读」的存在性探测：目标不存在即视为新建、直接放行（沙箱缺该方法时不探测）
    exists_probe = getattr(sandbox, "file_exists", None) if sandbox is not None else None

    middlewares: list[AgentMiddleware] = [
        # 边界净化必须排首位（洋葱最外层）：入向历史先于一切中间件修复，
        # 出向响应最后过净化，保证下游读到的是干净 tool_calls 与正文。
        ModelOutputSanitizerMiddleware(),
        # span 采集紧随净化之后（净化后响应更干净、用量提取更稳）：只观测不改变结果
        SpanMiddleware(),
        InputSanitizationMiddleware(),
        ThreadContextMiddleware(),
        DynamicContextMiddleware(),
        UploadsMiddleware(),
    ]
    # 记忆中间件：注入长期记忆 + （middleware 模式）每轮自动提取入队。
    # tool 模式只注入不自动提取，写入由模型记忆工具负责（行为与旧版一致）。
    if memory.enabled:
        from harness.memory.integration import MemoryMiddleware, memory_flush_hook

        middlewares.append(
            MemoryMiddleware(
                agent_name=app_config.agent_name,
                auto_extract=(memory.mode == "middleware"),
            )
        )
    # 自定义指令中间件：把用户 agent.md 追加到系统消息末尾（排在记忆之后注册，
    # 洋葱内层后执行，保证 <agent_md> 块位于所有注入内容的最末尾）。
    from harness.agents.middlewares.agent_md import AgentMdMiddleware

    middlewares.append(AgentMdMiddleware())
    middlewares += [
        SkillActivationMiddleware(skills_dir=skills_dir),
        SkillToolPolicyMiddleware(),
        ViewImageMiddleware(),
        TitleMiddleware(model_name=active_name),
        TodoMiddleware(),
    ]
    # 摘要中间件：超长压缩；压缩前记忆冲刷钩子由记忆子系统注入（无记忆时不冲刷）。
    if memory.enabled:
        from harness.memory.integration import memory_flush_hook

        summarization = SummarizationMiddleware(
            model_name=active_name,
            flush_hook=memory_flush_hook,
            agent_name=app_config.agent_name,
        )
    else:
        summarization = SummarizationMiddleware(model_name=active_name)
    middlewares += [
        ClarificationMiddleware(),
        TokenBudgetMiddleware(),
        summarization,
        DeferredToolFilterMiddleware(),
        # 参数容错必须排在 tool_progress 之前（更外层）：这样 tool_start 事件带出去的
        # args 已经是修好的形态，前端拿到的是干净数组而不是 JSON 字符串。
        ToolArgCoercionMiddleware(),
        ToolProgressMiddleware(event_sink=event_sink),
        LoopDetectionMiddleware(),
        DanglingToolCallMiddleware(),
        SubagentLimitMiddleware(max_total_per_run=subagents.max_total_per_run),
        DelegationLedgerMiddleware(),
    ]
    middlewares += [
        ReadBeforeWriteMiddleware(exists_probe=exists_probe),
        SandboxAuditMiddleware(),
        ToolErrorMiddleware(),
        LLMErrorMiddleware(),
    ]
    middlewares += [
        ToolOutputBudgetMiddleware(),
        ToolResultSanitizationMiddleware(),
        SystemMessageCoalescingMiddleware(),
        TokenUsageMiddleware(),
    ]
    return middlewares