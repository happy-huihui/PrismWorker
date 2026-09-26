from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any, Self

import yaml

from harness.config.database_config import DatabaseConfig
from harness.config.memory_config import MemoryConfig
from harness.config.model_config import ModelConfig
from harness.config.routing_config import RoutingConfig
from harness.config.sandbox_config import SandboxConfig
from harness.config.subagents_config import SubagentsAppConfig
from harness.config.tool_config import ToolConfig, ToolsConfig

"""应用总配置（AppConfig）

    职责：聚合全部子配置（模型 / 路由 / 数据库 / 沙箱 / 记忆 / 工具 / 子代理），
         并提供加载与查询能力。

    加载链：
        1. 显式传入的配置文件路径（存在才读）
        2. ./config.yaml（存在才读）
        3. 都没有 → 从环境变量兜底造一个模型（密钥缺失时留到调用期再报错）

    对外暴露：
        - AppConfig          总配置对象（含 get_model_config / active_model_config 等查询）
        - get_app_config     进程级单例（懒加载，线程安全）
        - set_app_config     手动注入配置（测试 / 程序化启动）
        - reset_app_config   重置单例，下次访问重新加载
"""


def _resolve_env_variables(value: Any) -> Any:
    """递归展开配置里的 $VAR / ${VAR} / ${VAR:default} 为环境变量值。

    参数：
        value: 任意配置节点（str / dict / list 均可）

    返回：
        展开后的同构结构；字符串保留前后缀，只替换变量部分
    """
    # 字典：逐值递归
    if isinstance(value, dict):
        return {k: _resolve_env_variables(v) for k, v in value.items()}

    # 列表：逐项递归
    if isinstance(value, list):
        return [_resolve_env_variables(v) for v in value]

    # 字符串：按三种写法做正则替换
    if isinstance(value, str):

        def _replace(match: re.Match) -> str:
            # 三种写法分别落在不同捕获组，取到变量名与默认值
            var = match.group(1) or match.group(3) or match.group(4)
            default = match.group(2)
            # 从环境变量取值；未定义时用默认值，无默认值则空串
            return os.getenv(var, default if default is not None else "")

        # ${VAR:default} / ${VAR} / $VAR 三选一匹配
        return re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*):([^}]*)\}"
            r"|\$\{([A-Za-z_][A-Za-z0-9_]*)\}"
            r"|\$([A-Za-z_][A-Za-z0-9_]*)",
            lambda m: _replace(m),
            value,
        )

    # 其余类型原样返回
    return value


class AppConfig:
    """应用总配置：聚合所有子配置并提供加载/查询。"""

    agent_name: str = "LeadAgent"

    log_level: str = "INFO"

    models: list[ModelConfig]

    active_model: str | None = None

    database: DatabaseConfig
    sandbox: SandboxConfig
    memory: MemoryConfig
    tools: ToolsConfig
    subagents: SubagentsAppConfig
    routing: RoutingConfig

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
        routing: RoutingConfig | None = None,
        agent_name: str = "LeadAgent",
        log_level: str = "INFO",
        tool_overrides: dict[str, bool] | None = None,
    ) -> None:
        """用显式对象构造 AppConfig（子配置缺省时各自取默认值）。"""
        self.models = list(models) if models else []
        self.active_model = active_model
        # 数据库连接串兜底读 DATABASE_URL
        self.database = database or DatabaseConfig(postgres_url=os.getenv("DATABASE_URL", ""))
        self.sandbox = sandbox or SandboxConfig()
        self.memory = memory or MemoryConfig()
        self.tools = tools or ToolsConfig()
        self.subagents = subagents or SubagentsAppConfig()
        self.routing = routing or RoutingConfig()
        self.agent_name = agent_name
        self.log_level = log_level
        self.tool_overrides = tool_overrides or {}

    def get_model_config(self, name: str) -> ModelConfig | None:
        """按名称查找模型配置，找不到返回 None。"""
        # 线性比对 name（模型条目通常个位数，无需索引）
        for m in self.models:
            if m.name == name:
                return m
        return None

    def active_model_config(self) -> ModelConfig:
        """返回当前激活的模型配置。

        优先 active_model 指定的模型；未指定则取列表第一个。

        异常：
            一个模型都没配置时抛出清晰的 ValueError
        """
        # 先按 active_model 精确匹配
        if self.active_model:
            found = self.get_model_config(self.active_model)
            if found is not None:
                return found
        # 其次取列表首个
        if self.models:
            return self.models[0]
        # 都没有则报错，提示两条补救路径
        raise ValueError(
            "No models configured. Add a model via config.yaml "
            "(see config.example.yaml) or set OPENAI_API_KEY / "
            "OPENAI_BASE_URL / OPENAI_MODEL environment variables."
        )

    def get_tool_config(self, name: str) -> ToolConfig | None:
        """按名称查找工具开关配置（tools.enabled_tools 内），找不到返回 None。"""
        # 白名单项可能是字符串或 ToolConfig，统一取出名字比对
        for t in self.tools.enabled_tools:
            tool_name = t.name if isinstance(t, ToolConfig) else str(t)
            if tool_name == name:
                return t if isinstance(t, ToolConfig) else ToolConfig(name=name)
        return None

    def is_tool_enabled(self, name: str) -> bool:
        """判断某个工具是否启用。

        规则（先具体后抽象）：
            1. tool_overrides 显式开关优先
            2. 否则看 enabled_tools 白名单
            3. 白名单为空表示全部启用
        """
        # 显式覆盖优先
        if name in self.tool_overrides:
            return self.tool_overrides[name]
        # 白名单非空则按白名单判定
        if self.tools.enabled_tools:
            return self.get_tool_config(name) is not None
        # 白名单为空 → 全开
        return True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """从字典构造 AppConfig（from_file 的内部实现）。

        参数：
            data: 已解析的配置字典（可含 ${VAR} 占位）

        返回：
            装配完成的 AppConfig
        """
        # 入口统一展开一次环境变量（幂等，重复调用无害）
        data = _resolve_env_variables(data)

        # 模型列表：逐条转 ModelConfig
        models_raw = data.get("models") or []
        models = [ModelConfig(**m) for m in models_raw]

        # 各子配置段原样取出，交给各自的 pydantic 模型校验
        database_raw = data.get("database")
        sandbox_raw = data.get("sandbox")
        memory_raw = data.get("memory")
        tools_raw = data.get("tools")
        subagents_raw = data.get("subagents")
        routing_raw = data.get("routing")

        # 段缺失时传 None，由 __init__ 落回各自默认值
        return cls(
            models=models,
            active_model=data.get("active_model"),
            database=DatabaseConfig(**database_raw) if database_raw else None,
            sandbox=SandboxConfig(**sandbox_raw) if sandbox_raw else None,
            memory=MemoryConfig(**memory_raw) if memory_raw else None,
            tools=ToolsConfig(**tools_raw) if tools_raw else None,
            subagents=SubagentsAppConfig(**subagents_raw) if subagents_raw else None,
            routing=RoutingConfig(**routing_raw) if routing_raw else None,
            agent_name=data.get("agent_name", "LeadAgent"),
            log_level=data.get("log_level", "INFO"),
        )

    @classmethod
    def from_defaults(cls) -> Self:
        """从环境变量构造默认配置（无 config.yaml 时的回退）。"""
        # 只造一个模型，active_model 留空 → 由 active_model_config 取首个
        return cls(
            models=[_default_model_from_env()],
            active_model=None,
        )

    @classmethod
    def from_file(cls, config_path: str | Path | None = None) -> Self:
        """从 YAML 配置文件加载 AppConfig。

        参数：
            config_path: 显式配置路径；None 时探测 ./config.yaml

        返回：
            装配完成的 AppConfig（路径都不存在时走 from_defaults）
        """
        path: Path | None = None
        # 显式路径优先
        if config_path is not None:
            path = Path(config_path)
        else:
            # 否则探测当前工作目录下的 config.yaml
            candidate = Path("config.yaml")
            if candidate.exists():
                path = candidate

        # 没有可用文件 → 环境变量兜底
        if path is None or not path.exists():
            return cls.from_defaults()

        # 读 YAML → 展开环境变量 → 装配
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        data = _resolve_env_variables(raw)

        return cls.from_dict(data)


def _default_model_from_env() -> ModelConfig:
    """从环境变量构造一个默认模型（无 config.yaml 时的兜底）。

    优先级：MiMo > DeepSeek > OpenAI，谁有密钥就用谁。

    返回：
        单条 ModelConfig；三家密钥都没有时返回一个无密钥的 OpenAI 条目，
        实际调用时由模型工厂抛出清晰错误
    """
    # MiMo：当前默认档（用户 2026-09-25 指定）
    if os.getenv("MIMO_API_KEY"):
        return ModelConfig(
            name="default",
            provider="mimo",
            model=os.getenv("MIMO_PRO_MODEL", "mimo-v2.6-pro"),
            base_url=os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"),
            api_key=os.getenv("MIMO_API_KEY"),
            supports_vision=False,
            supports_thinking=True,
            context_window=131072,
        )

    # DeepSeek：备选
    if os.getenv("DEEPSEEK_API_KEY"):
        return ModelConfig(
            name="default",
            provider="deepseek",
            model=os.getenv("DEEPSEEK_FLASH_MODEL", "deepseek-v4-flash"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            supports_vision=False,
            supports_thinking=False,
            context_window=65536,
        )

    # OpenAI：最后兜底（gpt-6-astra，105 万上下文，支持视觉与推理）
    return ModelConfig(
        name="default",
        provider="openai",
        model=os.getenv("OPENAI_MODEL", "gpt-6-astra"),
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        api_key=os.getenv("OPENAI_API_KEY") or None,
        supports_vision=True,
        supports_thinking=True,
        context_window=1050000,
    )


_app_config: AppConfig | None = None
_lock = threading.Lock()


def get_app_config() -> AppConfig:
    """返回 AppConfig 全局唯一实例（懒加载，双重检查加锁）。"""
    global _app_config
    # 快路径：已初始化直接返回，不加锁
    if _app_config is None:
        with _lock:
            # 慢路径：拿到锁后二次确认，避免并发重复加载
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
