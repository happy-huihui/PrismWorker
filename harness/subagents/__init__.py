"""子代理子系统包统一出口。

对外暴露：
    - SubagentConfig / resolve_subagent_model_name  配置与模型名解析
    - SubagentExecutor / SubagentResult             执行器与结果
    - get_subagent_config / get_available_subagent_names  注册表查询
"""

from harness.subagents.builtins import (
    get_available_subagent_names,
    get_subagent_config,
)
from harness.subagents.config import SubagentConfig, resolve_subagent_model_name

__all__ = [
    "SubagentConfig",
    "resolve_subagent_model_name",
    "get_subagent_config",
    "get_available_subagent_names",
]


def __getattr__(name: str):
    """延迟导出 executor 里的重对象（避免导入 harness.subagents 就拉起重依赖）。"""
    if name in {"SubagentExecutor", "SubagentResult"}:
        from harness.subagents.executor import SubagentExecutor, SubagentResult

        exports = {
            "SubagentExecutor": SubagentExecutor,
            "SubagentResult": SubagentResult,
        }
        globals().update(exports)
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")