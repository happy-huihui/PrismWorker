"""FastAPI 网关入口（main）——app 层 HTTP 薄层。

create_app() 工厂模式：构建 FastAPI 实例、挂载路由、注册 lifespan
（启动时初始化 run service + 事件总线，关闭时回收）。依赖默认走进程级
单例；测试可注入隔离实例（thread_store / run_service / event_bus）做无
副作用冒烟。

启动预热：lifespan 里后台先造一次激活模型（模型客户端构造实测要几秒，
Windows 上尤慢），把它存进工厂缓存，避免第一条会话替全进程买单。

启动方式（开发）：
    uvicorn app.api.main:app --reload
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI

from app.api.deps import get_event_bus as get_event_bus_dep
from app.api.deps import get_paths as get_paths_dep
from app.api.deps import get_run_service as get_run_service_dep
from app.api.deps import get_thread_store as get_thread_store_dep
from app.api.deps import get_checkpoint_db_path as get_checkpoint_db_path_dep

logger = logging.getLogger(__name__)


def _warm_active_model() -> None:
    """构造一次激活模型并落入工厂缓存（预热用，失败只记日志）。"""
    try:
        from harness.models.factory import create_chat_model

        create_chat_model()
        logger.info("模型预热完成（默认模型客户端已缓存）")
    except Exception as exc:  # noqa: BLE001 —— 预热失败不阻断启动，首 run 自己再建
        logger.warning("模型预热失败（忽略，首次运行时重建）: %s", exc)


def create_app(
    *,
    thread_store: Any | None = None,
    run_service: Any | None = None,
    bus: Any | None = None,
    paths: Any | None = None,
    checkpoint_db_path: Any | None = None,
) -> FastAPI:
    """构建 FastAPI 应用实例。

    参数用于覆盖进程级单例（测试隔离）；paths / checkpoint_db_path 分别
    覆盖数据目录与 checkpoint 库位置；缺省走全局单例。
    """
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        svc = run_service
        if svc is None:
            from harness.runtime.assembly import get_run_service

            svc = get_run_service()
        await svc.start()
        # 后台预热模型客户端：构造要几秒，不能卡住启动，也不能让首条会话等
        warm_task = asyncio.create_task(asyncio.to_thread(_warm_active_model))
        yield
        warm_task.cancel()
        await svc.close()
        # 优雅关闭：排空记忆防抖队列（避免重启丢失防抖缓冲中的待提取更新）
        try:
            from harness.config.memory_config import get_memory_config

            if get_memory_config().enabled:
                from harness.memory.manager import get_memory_manager

                manager = get_memory_manager()
                if not manager.shutdown_flush(timeout=10.0):
                    import logging

                    logging.getLogger(__name__).warning(
                        "记忆队列未在超时内排空，尾部更新可能丢失"
                    )
                manager.close()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("记忆队列关闭排空异常（忽略）")

    from fastapi import Depends

    from app.api.deps import verify_internal_token

    app = FastAPI(
        title="PrismWorker API",
        version="0.1.0",
        lifespan=_lifespan,
        dependencies=[Depends(verify_internal_token)],
    )

    if thread_store is not None:
        app.dependency_overrides[get_thread_store_dep] = lambda: thread_store
    if run_service is not None:
        app.dependency_overrides[get_run_service_dep] = lambda: run_service
    if bus is not None:
        app.dependency_overrides[get_event_bus_dep] = lambda: bus
    if paths is not None:
        app.dependency_overrides[get_paths_dep] = lambda: paths
    if checkpoint_db_path is not None:
        app.dependency_overrides[get_checkpoint_db_path_dep] = lambda: checkpoint_db_path

    from app.api.routes import runs as runs_router
    from app.api.routes import threads as threads_router
    from app.api.routes import models as models_router
    from app.api.routes import messages as messages_router
    from app.api.routes import uploads as uploads_router
    from app.api.routes import artifacts as artifacts_router
    from app.api.routes import auth as auth_router

    app.include_router(auth_router.router)  # 登录/自查：唯一免 token 的业务入口
    app.include_router(threads_router.router)
    app.include_router(runs_router.router)
    app.include_router(models_router.router)
    app.include_router(messages_router.router)
    app.include_router(uploads_router.router)
    app.include_router(artifacts_router.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """健康检查：进程内的服务依赖已就绪即认为健康。"""
        return {"status": "ok"}

    return app


app = create_app()