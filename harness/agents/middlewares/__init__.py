"""实时 Agent 中间件包。

按职责分层（完整清单见 build_middlewares.py 的组装顺序）：
  - 输入与上下文：InputSanitization / ThreadContext / DynamicContext / Uploads / Memory（记忆中间件）
  - 技能与视界：SkillActivation / SkillToolPolicy / ViewImage
  - 规划与护栏：Title / Todo / Clarification / TokenBudget / Summarization /
    DeferredToolFilter / ToolProgress / LoopDetection / DanglingToolCall /
    SubagentLimit / DelegationLedger
  - 沙箱与错误：ReadBeforeWrite / SandboxAudit / ToolError / LLMError
  - 结果与收尾：ToolOutputBudget / ToolResultSanitization / SystemMessageCoalescing /
    TokenUsage

统一入口 build_middlewares(app_config, *, event_sink=..., skills_dir=...) 返回
按固定顺序排序的 [AgentMiddleware]，供 lead_agent 组装时按序挂载。
"""

from harness.agents.middlewares.build_middlewares import build_middlewares
from harness.agents.middlewares.clarification import ClarificationMiddleware
from harness.agents.middlewares.deferred_tool_filter import DeferredToolFilterMiddleware
from harness.agents.middlewares.dynamic_context import DynamicContextMiddleware
from harness.agents.middlewares.error_handling import LLMErrorMiddleware, ToolErrorMiddleware
from harness.agents.middlewares.flow_control import (
    DanglingToolCallMiddleware,
    LoopDetectionMiddleware,
    SystemMessageCoalescingMiddleware,
)
from harness.agents.middlewares.input_sanitization_middleware import (
    InputSanitizationMiddleware,
    neutralize_untrusted_tags,
)
from harness.agents.middlewares.model_output_sanitizer import (
    ModelOutputSanitizerMiddleware,
    clean_tool_call_shells,
    repair_history,
    sanitize_tool_calls,
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
from harness.agents.middlewares.token_usage import TokenBudgetMiddleware, TokenUsageMiddleware
from harness.agents.middlewares.tool_progress import ToolProgressMiddleware
from harness.agents.middlewares.tool_result_handling import (
    ToolOutputBudgetMiddleware,
    ToolResultSanitizationMiddleware,
)
from harness.agents.middlewares.uploads import UploadsMiddleware
from harness.agents.middlewares.view_image import ViewImageMiddleware

__all__ = [
    "build_middlewares",
    "InputSanitizationMiddleware",
    "neutralize_untrusted_tags",
    "ModelOutputSanitizerMiddleware",
    "sanitize_tool_calls",
    "clean_tool_call_shells",
    "repair_history",
    "ThreadContextMiddleware",
    "DynamicContextMiddleware",
    "UploadsMiddleware",
    "SkillActivationMiddleware",
    "SkillToolPolicyMiddleware",
    "ViewImageMiddleware",
    "TitleMiddleware",
    "TodoMiddleware",
    "ClarificationMiddleware",
    "TokenBudgetMiddleware",
    "SummarizationMiddleware",
    "DeferredToolFilterMiddleware",
    "ToolProgressMiddleware",
    "LoopDetectionMiddleware",
    "DanglingToolCallMiddleware",
    "SubagentLimitMiddleware",
    "DelegationLedgerMiddleware",
    "ReadBeforeWriteMiddleware",
    "SandboxAuditMiddleware",
    "ToolErrorMiddleware",
    "LLMErrorMiddleware",
    "ToolOutputBudgetMiddleware",
    "ToolResultSanitizationMiddleware",
    "SystemMessageCoalescingMiddleware",
    "TokenUsageMiddleware",
]