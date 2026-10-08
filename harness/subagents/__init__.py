from harness.subagents.builtins import (
    get_available_subagent_names,
    get_subagent_config,
)
from harness.subagents.config import SubagentConfig, resolve_subagent_model_name

"""子代理子系统出口

    职责：聚合子代理的配置、注册表与执行器，供上层一处导入
        - 配置与模型名解析
        - 内置注册表查询
        - 执行器（走 __getattr__ 延迟导出，避开重依赖）

    对外暴露：
        - SubagentConfig / resolve_subagent_model_name
        - get_subagent_config / get_available_subagent_names
        - SubagentExecutor / SubagentResult（延迟导出）
"""

__all__ = [
    "SubagentConfig",
    "resolve_subagent_model_name",
    "get_subagent_config",
    "get_available_subagent_names",
]


def __getattr__(name: str):
    """延迟导出 executor 里的重对象（避免导入 harness.subagents 就拉起重依赖）。"""
    # 只在首次取用时导入 executor（它拉起 create_agent 等重依赖）
    if name in {"SubagentExecutor", "SubagentResult"}:
        from harness.subagents.executor import SubagentExecutor, SubagentResult

        exports = {
            "SubagentExecutor": SubagentExecutor,
            "SubagentResult": SubagentResult,
        }
        globals().update(exports)
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
