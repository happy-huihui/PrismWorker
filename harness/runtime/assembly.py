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

"""运行组装根（assembly）

    职责：把散在各 runtime 包的组件，按 harness 配置拼装成一个可运行的
         RunManager，并提供进程级单例——是「谁来 new 依赖」的唯一落点。
    背景：runtime 各包不自举、不反向 import app；依赖组装集中在这里，由
         app 的 FastAPI lifespan 调用（get_run_service），保持分层干净。

    对外暴露：
        - build_run_service   组装一个新 RunManager（每次调用新建）
        - get_run_service     进程级单例（懒建，lifespan 启动/复用）
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
    return RunManager(
        registry=agent_registry,
        threads=threads if threads is not None else get_thread_store(),
        bus=bus if bus is not None else get_event_bus(),
        store=RunStore(db_path=db_path),
    )


def get_run_service() -> RunManager:
    """返回进程级 RunManager 单例（懒构造，线程安全）。"""
    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is None:
            _service = build_run_service()
        return _service


def reset_run_service() -> None:
    """清空单例（测试隔离用：下一次 get_run_service 重新组装）。"""
    global _service
    with _service_lock:
        _service = None
