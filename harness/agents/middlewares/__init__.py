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

"""实时 Agent 中间件包

    职责：把模型调用 / 工具调用前后的横切逻辑收敛成一组可插拔中间件
        - 输入与上下文：InputSanitization / ThreadContext / DynamicContext / Uploads / Memory
        - 技能与视界：SkillActivation / SkillToolPolicy / ViewImage
        - 规划与护栏：Title / Todo / Clarification / TokenBudget / Summarization /
          DeferredToolFilter / ToolProgress / LoopDetection / DanglingToolCall /
          SubagentLimit / DelegationLedger
        - 沙箱与错误：ReadBeforeWrite / SandboxAudit / ToolError / LLMError
        - 结果与收尾：ToolOutputBudget / ToolResultSanitization / SystemMessageCoalescing / TokenUsage

    对外暴露：
        - build_middlewares        统一组装入口（按固定顺序返回中间件列表）
        - 各中间件类与净化工具函数   完整清单见 __all__
"""

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
