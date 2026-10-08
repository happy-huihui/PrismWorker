from harness.subagents.builtins.general_purpose import GENERAL_PURPOSE_CONFIG
from harness.subagents.config import SubagentConfig

"""内置子代理注册表

    职责：维护内置子代理配置表并提供查询入口
        - 新增子代理类型只需在 BUILTIN_SUBAGENTS 加一条，不需要额外文件

    对外暴露：
        - BUILTIN_SUBAGENTS / GENERAL_PURPOSE_CONFIG
        - get_subagent_config / get_available_subagent_names
"""

BUILTIN_SUBAGENTS: dict[str, SubagentConfig] = {
    "general_purpose": GENERAL_PURPOSE_CONFIG,
}

__all__ = [
    "BUILTIN_SUBAGENTS",
    "GENERAL_PURPOSE_CONFIG",
    "get_subagent_config",
    "get_available_subagent_names",
]


def get_subagent_config(name: str) -> SubagentConfig | None:
    """按类型名查子代理配置，未注册的类型返回 None。"""
    return BUILTIN_SUBAGENTS.get(name)


def get_available_subagent_names() -> list[str]:
    """返回所有已注册的子代理类型名（供 task 工具报错时列候选）。"""
    return list(BUILTIN_SUBAGENTS.keys())
