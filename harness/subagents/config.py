"""子代理配置：SubagentConfig 数据类 + 模型名解析。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.config.app_config import AppConfig


@dataclass
class SubagentConfig:
    """子代理的静态配置（一处定义，executor 与注册表共用）。

    字段说明：
        name:            子代理类型名（注册表 key）
        description:     什么场景下值得派给这个子代理
        system_prompt:   引导子代理行为的系统提示词
        tools:           工具白名单；None 表示继承全部工具
        disallowed_tools:工具黑名单（命中即排除，防特殊情况）
        model:           'inherit' 表示继承父级模型；其它值用指定模型
        max_turns:       单次执行的最大对话轮次（超了按 turn_capped 收尾）
        timeout_seconds: 单次执行超时上限（秒），超了按 timed_out 收尾
    """

    name: str
    description: str
    system_prompt: str | None = None
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = field(default_factory=lambda: ["task"])
    model: str = "inherit"
    max_turns: int = 50
    timeout_seconds: int = 600


def resolve_subagent_model_name(
    config: SubagentConfig,
    parent_model: str | None,
    *,
    app_config: AppConfig | None = None,
) -> str:
    """解析子代理执行时实际使用的模型名。

    规则：
        1. config.model 不是 'inherit' → 用子代理自己指定的模型
        2. 父级模型名存在 → 继承父级
        3. 都没有 → 落到全局配置的默认（激活）模型

    Args:
        config: 子代理配置。
        parent_model: 父代理的模型名（可为空）。
        app_config: 显式应用配置；缺省用全局单例。

    Returns:
        解析出的模型名。
    """
    if config.model != "inherit":
        return config.model
    if parent_model:
        return parent_model
    if app_config is None:
        from harness.config.app_config import get_app_config

        app_config = get_app_config()
    return app_config.active_model_config().name