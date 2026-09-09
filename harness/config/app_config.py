"""应用总配置（AppConfig）。

聚合全部子配置（模型 / 沙箱 / 记忆 / 工具 / 子代理），并提供：
    - from_file()：从 config.yaml 加载，支持 $ENV 环境变量替换
    - get_app_config()：全局单例（懒加载，与 get_paths() 同款风格）
    - 查询方法：get_model_config / active_model_config / get_tool_config

加载优先级：
    1. 显式传入的 config 文件路径
    2. ./config.yaml（存在时）
    3. 都缺失时，从环境变量构造一个 OpenAI 兼容默认模型（含报错兜底）
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Self

import yaml

from harness.config.database_config import DatabaseConfig
from harness.config.memory_config import MemoryConfig
from harness.config.model_config import ModelConfig
from harness.config.sandbox_config import SandboxConfig
from harness.config.subagents_config import SubagentsAppConfig
from harness.config.tool_config import ToolConfig, ToolsConfig


def _resolve_env_variables(value: Any) -> Any:
    """递归替换配置中的 $VAR / ${VAR} / ${VAR:default} 为环境变量值。

    支持 str / dict / list 三种结构。字符串形如 "abc ${VAR} def"
    会保留前后缀只替换变量部分；${VAR:default} 在环境变量未定义时
    使用 default 值；${VAR}（无默认值）未定义时替换为空字符串。
    """
    if isinstance(value, dict):
        return {k: _resolve_env_variables(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_variables(v) for v in value]
    if isinstance(value, str):
        import re

        def _replace(match: re.Match) -> str:
            var = match.group(1) or match.group(3) or match.group(4)
            default = match.group(2)
            return os.getenv(var, default if default is not None else "")

        return re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*):([^}]*)\}"
            r"|\$\{([A-Za-z_][A-Za-z0-9_]*)\}"
            r"|\$([A-Za-z_][A-Za-z0-9_]*)",
            lambda m: _replace(m),
            value,
        )
    return value


class AppConfig:
    """应用总配置：聚合所有子配置并提供加载/查询能力。"""

    agent_name: str = "LeadAgent"

    log_level: str = "INFO"

    models: list[ModelConfig]

    active_model: str | None = None

    database: DatabaseConfig
    sandbox: SandboxConfig
    memory: MemoryConfig
    tools: ToolsConfig
    subagents: SubagentsAppConfig

    tool_overrides: dict[str, bool]

    def __init__(
        self,
        *,
        models: list[ModelConfig] | None = None,
        active_model: str | None = None,
        database: DatabaseConfig | None = None,
        sandbox: SandboxConfig | None = None,
        memory: MemoryConfig | None = None,
        tools: ToolsConfig | None = None,
        subagents: SubagentsAppConfig | None = None,
        agent_name: str = "LeadAgent",
        log_level: str = "INFO",
        tool_overrides: dict[str, bool] | None = None,
    ) -> None:
        """使用显式对象构造 AppConfig（供代码内联使用）。"""
        self.models = list(models) if models else []
        self.active_model = active_model
        self.database = database or DatabaseConfig(postgres_url=os.getenv("DATABASE_URL", ""))
        self.sandbox = sandbox or SandboxConfig()
        self.memory = memory or MemoryConfig()
        self.tools = tools or ToolsConfig()
        self.subagents = subagents or SubagentsAppConfig()
        self.agent_name = agent_name
        self.log_level = log_level
        self.tool_overrides = tool_overrides or {}


    def get_model_config(self, name: str) -> ModelConfig | None:
        """按名称查找模型配置，找不到返回 None。"""
        for m in self.models:
            if m.name == name:
                return m
        return None

    def active_model_config(self) -> ModelConfig:
        """返回当前激活的模型配置。

        优先 active_model 指定的模型；未指定则取列表第一个。
        没有配置任何模型时抛出清晰异常。
        """
        if self.active_model:
            found = self.get_model_config(self.active_model)
            if found is not None:
                return found
        if self.models:
            return self.models[0]
        raise ValueError(
            "No models configured. Add a model via config.yaml "
            "(see config.example.yaml) or set OPENAI_API_KEY / "
            "OPENAI_BASE_URL / OPENAI_MODEL environment variables."
        )

    def get_tool_config(self, name: str) -> ToolConfig | None:
        """按名称查找工具开关配置（tools.enabled_tools 内），找不到返回 None。"""
        for t in self.tools.enabled_tools:
            tool_name = t.name if isinstance(t, ToolConfig) else str(t)
            if tool_name == name:
                return t if isinstance(t, ToolConfig) else ToolConfig(name=name)
        return None

    def is_tool_enabled(self, name: str) -> bool:
        """判断某个工具是否启用。

        规则：
        - 显式工具开关（tool_overrides / enabled_tools）优先
        - 白名单为空表示全部启用
        """
        if name in self.tool_overrides:
            return self.tool_overrides[name]
        if self.tools.enabled_tools:
            return self.get_tool_config(name) is not None
        return True


    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """从字典构造 AppConfig（from_file 的内部实现）。

        入口处统一做一次环境变量展开，保证任何调用方传入的
        ${VAR} / ${VAR:default} 都能被解析（幂等，重复调用无害）。
        """
        data = _resolve_env_variables(data)

        models_raw = data.get("models") or []
        models = [ModelConfig(**m) for m in models_raw]

        database_raw = data.get("database")
        sandbox_raw = data.get("sandbox")
        memory_raw = data.get("memory")
        tools_raw = data.get("tools")
        subagents_raw = data.get("subagents")

        return cls(
            models=models,
            active_model=data.get("active_model"),
            database=DatabaseConfig(**database_raw) if database_raw else None,
            sandbox=SandboxConfig(**sandbox_raw) if sandbox_raw else None,
            memory=MemoryConfig(**memory_raw) if memory_raw else None,
            tools=ToolsConfig(**tools_raw) if tools_raw else None,
            subagents=SubagentsAppConfig(**subagents_raw) if subagents_raw else None,
            agent_name=data.get("agent_name", "LeadAgent"),
            log_level=data.get("log_level", "INFO"),
        )

    @classmethod
    def from_defaults(cls) -> Self:
        """从环境变量构造默认配置（无 config.yaml 时的回退）。"""
        return cls(
            models=[_default_model_from_env()],
            active_model=None,
        )

    @classmethod
    def from_file(cls, config_path: str | Path | None = None) -> Self:
        """从 YAML 配置文件加载 AppConfig。

        加载链：
        1. 显式传入的路径（存在才读）
        2. ./config.yaml（存在才读）
        3. 都没有 → 回退 from_defaults（环境变量模型）
        """
        path: Path | None = None
        if config_path is not None:
            path = Path(config_path)
        else:
            candidate = Path("config.yaml")
            if candidate.exists():
                path = candidate

        if path is None or not path.exists():
            return cls.from_defaults()

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        data = _resolve_env_variables(raw)

        return cls.from_dict(data)



def _default_model_from_env() -> ModelConfig:
    """从环境变量构造一个默认模型。

    优先 DeepSeek（存在 DEEPSEEK_API_KEY 时），否则回退 OpenAI：
    读取 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL。
    若 API 密钥也没配置，构造一个 supports_vision=False 的模型，
    实际调用时由模型工厂抛清晰的错误。
    """
    if os.getenv("DEEPSEEK_API_KEY"):
        return ModelConfig(
            name="default",
            provider="deepseek",
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            supports_vision=False,
            supports_thinking=True,
            context_window=65536,
        )
    return ModelConfig(
        name="default",
        provider="openai",
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        api_key=os.getenv("OPENAI_API_KEY") or None,
        supports_vision=True,
        supports_thinking=False,
        context_window=128000,
    )



_app_config: AppConfig | None = None
_lock = threading.Lock()


def get_app_config() -> AppConfig:
    """返回 AppConfig 全局唯一实例（懒加载，线程安全）。"""
    global _app_config
    if _app_config is None:
        with _lock:
            if _app_config is None:
                _app_config = AppConfig.from_file()
    return _app_config


def set_app_config(config: AppConfig) -> None:
    """手动设置全局配置（测试或程序化启动时用）。"""
    global _app_config
    with _lock:
        _app_config = config


def reset_app_config() -> None:
    """重置全局配置，下次 get_app_config() 重新加载（测试用）。"""
    global _app_config
    with _lock:
        _app_config = None