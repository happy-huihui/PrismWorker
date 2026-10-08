from __future__ import annotations

import logging
import threading
from typing import Any

from harness.config.app_config import get_app_config
from harness.runtime.graph import get_agent_registry
from harness.runtime.runs import RunManager, RunStore
from harness.runtime.sandbox import get_app_sandbox
from harness.runtime.sse_stream import get_event_bus
from harness.runtime.threads_data import get_thread_store

"""运行组装根

    职责：把散在各 runtime 包的组件按配置拼成一个可运行的 RunManager，是「谁来 new 依赖」的唯一落点
        - 各包不自举、不反向 import app；依赖组装集中在这里，由 app 的 lifespan 调用
        - 进程级单例懒建，供启动时复用

    对外暴露：
        - build_run_service   组装一个新 RunManager（每次调用新建）
        - get_run_service     进程级单例（懒建，线程安全）
        - reset_run_service   重置单例（测试隔离）
"""

logger = logging.getLogger(__name__)

_service: RunManager | None = None
_service_lock = threading.Lock()


def build_run_service(
    *,
    app_config: Any | None = None,
    db_path: Any | None = None,
    registry: Any | None = None,
    threads: Any | None = None,
    bus: Any | None = None,
) -> RunManager:
    """组装一个 RunManager（注入 graph 注册表 / 线程仓库 / 事件总线 / runs 仓库）。

    参数：
        app_config: 应用配置；None 用全局单例
        db_path: runs 库路径；None 由 RunStore 按配置解析
        registry: 显式 Agent 注册表；None 按 app_config + 沙箱 provider 构造
        threads: 显式 ThreadStore；None 用进程级单例
        bus: 显式 EventBus；None 用进程级单例

    返回：
        已注入依赖、可直接 start() 的 RunManager
    """
    cfg = app_config if app_config is not None else get_app_config()
    # 缺省注册表：绑定当前配置 + 按线程懒启动沙箱的 provider
    agent_registry = registry or get_agent_registry(
        app_config=cfg,
        sandbox_provider=get_app_sandbox,
    )
    # 组装唯一落点：四个依赖全部构造注入，runtime 各包不自己 new
    return RunManager(
        registry=agent_registry,
        threads=threads if threads is not None else get_thread_store(),
        bus=bus if bus is not None else get_event_bus(),
        store=RunStore(db_path=db_path),
    )


def get_run_service() -> RunManager:
    # 快路径：已建好直接返回，不进锁
    """返回进程级 RunManager 单例（懒构造，线程安全）。"""
    global _service
    if _service is not None:
        return _service
    # 慢路径：加锁双检，避免并发 lifespan 各建一个
    with _service_lock:
        if _service is None:
            _service = build_run_service()
        return _service


def reset_run_service() -> None:
    # 置空即可，下次 get_run_service 会重新组装
    """清空单例（测试隔离用：下一次 get_run_service 重新组装）。"""
    global _service
    with _service_lock:
        _service = None
