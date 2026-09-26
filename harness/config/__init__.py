from __future__ import annotations

from harness.config.app_config import (
    AppConfig,
    get_app_config,
    reset_app_config,
    set_app_config,
)
from harness.config.database_config import DatabaseConfig
from harness.config.memory_config import MemoryConfig
from harness.config.model_config import ModelConfig, ModelProvider
from harness.config.paths import (
    PROJECT_ROOT,
    SKILLS_CONTAINER_PREFIX,
    VIRTUAL_PATH_PREFIX,
    Paths,
    get_paths,
)
from harness.config.sandbox_config import SandboxConfig, SandboxMount
from harness.config.skills import SkillsConfig
from harness.config.subagents_config import SubagentConfig, SubagentsAppConfig
from harness.config.tool_config import (
    ToolConfig,
    ToolsConfig,
    WebFetchConfig,
    WebSearchConfig,
)

"""配置模块统一出口

    职责：把各子配置类型与全局单例访问函数汇总到一处，外部只需 from harness.config import X。

    对外暴露：
        - 总配置：AppConfig / get_app_config / set_app_config / reset_app_config
        - 模型：ModelConfig / ModelProvider
        - 路由：随 AppConfig.routing 使用（类型见 harness.config.routing_config）
        - 基础设施：DatabaseConfig / SandboxConfig / SandboxMount / MemoryConfig
        - 工具：ToolsConfig / ToolConfig / WebSearchConfig / WebFetchConfig
        - 子代理：SubagentsAppConfig / SubagentConfig
        - 技能：SkillsConfig
        - 路径：Paths / get_paths / PROJECT_ROOT / VIRTUAL_PATH_PREFIX / SKILLS_CONTAINER_PREFIX
"""

__all__ = [
    "AppConfig",
    "get_app_config",
    "set_app_config",
    "reset_app_config",
    "ModelConfig",
    "ModelProvider",
    "SandboxConfig",
    "SandboxMount",
    "MemoryConfig",
    "DatabaseConfig",
    "ToolsConfig",
    "ToolConfig",
    "WebSearchConfig",
    "WebFetchConfig",
    "SubagentsAppConfig",
    "SubagentConfig",
    "SkillsConfig",
    "Paths",
    "VIRTUAL_PATH_PREFIX",
    "SKILLS_CONTAINER_PREFIX",
    "PROJECT_ROOT",
    "get_paths",
]
