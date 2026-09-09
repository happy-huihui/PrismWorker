"""FastAPI 网关入口（main）——app 层 HTTP 薄层。

create_app() 工厂模式：构建 FastAPI 实例、挂载路由、注册 lifespan
（启动时初始化 run service + 事件总线，关闭时回收）。依赖默认走进程级
单例；测试可注入隔离实例（thread_store / run_service / event_bus）做无
副作用冒烟。

启动方式（开发）：
    uvicorn app.api.main:app --reload
"""

from __future__ import annotations

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
            from app.core.run_service import get_run_service

            svc = get_run_service()
        await svc.start()
        yield
        await svc.close()

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