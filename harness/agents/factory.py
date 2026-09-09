"""Lead Agent 统一入口工厂。

对外暴露：
    - get_lead_agent(app_config=None, *, rebuild=False, **kwargs)
        进程级单例 + 懒加载；首次调用按配置组装并缓存，之后直接返回缓存实例。
        需要换配置/换沙箱时传 rebuild=True 强制重建（或调 reset_lead_agent）。
    - build_agent(app_config, **kwargs)
        每次显式构建一个新实例（不缓存，测试或特殊用途用）。
    - reset_lead_agent()
        清空单例缓存（测试隔离用）。

内部全部委托给 harness.agents.lead_agent.agent.build_lead_agent。
"""

from __future__ import annotations

import threading
from typing import Any

from harness.agents.lead_agent.agent import build_lead_agent
from harness.config.app_config import AppConfig, get_app_config

_lead_agent: Any = None
_lead_agent_lock = threading.Lock()


def get_lead_agent(
    app_config: AppConfig | None = None,
    *,
    rebuild: bool = False,
    **kwargs: Any,
) -> Any:
    """返回进程级单例 Lead Agent（懒加载，线程安全）。

    参数：
        app_config  显式配置；None 用全局 get_app_config()
        rebuild     True 时忽略缓存强制重建（换模型/沙箱/checkpointer 后调用）
        kwargs      build_lead_agent 的其余参数（sandbox / checkpointer /
                    model_name / thinking_enabled / event_sink / skills_dir）
    """
    global _lead_agent
    with _lead_agent_lock:
        if _lead_agent is None or rebuild:
            config = app_config or get_app_config()
            _lead_agent = build_lead_agent(config, **kwargs)
        return _lead_agent


def build_agent(
    app_config: AppConfig | None = None,
    **kwargs: Any,
) -> Any:
    """每次显式构建一个新的 Lead Agent（不缓存）。

    与 get_lead_agent 的区别：这里永远走完整组装流程，适合需要
    独立实例的测试或特殊运行场景。
    """
    config = app_config or get_app_config()
    return build_lead_agent(config, **kwargs)


def reset_lead_agent() -> None:
    """清空进程级单例缓存（下一调用会重新组装）。"""
    global _lead_agent
    with _lead_agent_lock:
        _lead_agent = None