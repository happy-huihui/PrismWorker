from __future__ import annotations

import threading
from typing import Any

from harness.agents.lead_agent.agent import build_lead_agent
from harness.config.app_config import AppConfig, get_app_config

"""Lead Agent 统一入口工厂

    职责：提供「拿到一个可运行的 Lead Agent」的统一入口，并管理其生命周期
        - 进程级单例 + 懒加载，加锁防并发重复构建
        - rebuild=True 强制重建（换配置 / 换沙箱）
        - 全部委托给 lead_agent.agent.build_lead_agent

    对外暴露：
        - get_lead_agent     单例入口（rebuild=True 强制重建）
        - build_agent        每次新建一个实例（不缓存）
        - reset_lead_agent   清空单例缓存（测试隔离）
"""

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
    # 1.整段加锁：单例创建与重建都可能被并发触发，必须串行（否则重复组装、白烧资源）
    with _lead_agent_lock:
        # 2.只有「没有缓存」或「调用方显式要求重建」才走完整组装，否则直接复用
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
    # 1.取配置：显式传入优先，否则用全局单例
    config = app_config or get_app_config()
    # 2.不走缓存，每次完整组装一个新实例（测试/特殊场景需要互相隔离的实例）
    return build_lead_agent(config, **kwargs)


def reset_lead_agent() -> None:
    """清空进程级单例缓存（下一调用会重新组装）。"""
    global _lead_agent
    # 1.用同一把锁清空：避免与在途组装竞态（清空后下次调用会重新组装）
    with _lead_agent_lock:
        _lead_agent = None
