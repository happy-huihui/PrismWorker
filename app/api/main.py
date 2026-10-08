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

"""FastAPI 网关入口

    职责：用工厂模式装配应用——挂路由、注册 lifespan、暴露健康检查
        - lifespan：启动 run service + 后台预热模型；关闭时回收并排空记忆队列
        - 依赖默认走进程级单例，测试可注入隔离实例

    对外暴露：
        - create_app   应用工厂（可注入 thread_store / run_service / bus / paths）
        - app          create_app() 的默认实例（uvicorn 目标）
"""

logger = logging.getLogger(__name__)


def _warm_active_model() -> None:
    """构造一次激活模型并落入工厂缓存（预热用，失败只记日志）。"""
    try:
        from harness.models.factory import create_chat_model

        # 造一次即落入工厂缓存；失败只记日志，首 run 自己再建
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
        # 1.启动 run service（内部会拉起 store / bus 等依赖）
        svc = run_service
        if svc is None:
            from harness.runtime.assembly import get_run_service

            svc = get_run_service()
        await svc.start()
        # 后台预热模型客户端：构造要几秒，不能卡住启动，也不能让首条会话等
        # 2.后台预热模型：构造要几秒，不能卡住启动，也不能让首条会话等
        warm_task = asyncio.create_task(asyncio.to_thread(_warm_active_model))
        yield
        # 3.关闭：先取消预热任务，再关 run service
        warm_task.cancel()
        await svc.close()
        # 观测收尾：停落库后台线程并 flush 剩余日志（避免重启丢失尾部事件）
        try:
            from harness.observability.db_sink import stop_db_sink

            stop_db_sink()
        except Exception:
            pass
        # 优雅关闭：排空记忆防抖队列（避免重启丢失防抖缓冲中的待提取更新）
        try:
            from harness.config.memory_config import get_memory_config

            # 4.优雅关闭：排空记忆防抖队列，避免重启丢失待提取更新
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

    # 全局依赖：所有路由都要过内部 token 校验
    from fastapi import Depends

    from app.api.deps import verify_internal_token

    app = FastAPI(
        title="PrismWorker API",
        version="0.1.0",
        lifespan=_lifespan,
        dependencies=[Depends(verify_internal_token)],
    )

    # 观测：每个请求生成/透传 trace_id（响应回写 X-Trace-Id），贯穿整条观测链路
    # 观测中间件：每请求生成 / 透传 trace_id（响应回写 X-Trace-Id）
    from app.api.trace_middleware import trace_middleware

    app.middleware("http")(trace_middleware)

    # 测试注入：用 dependency_overrides 覆盖进程级单例
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

    # 挂路由：auth 是唯一免 token 的业务入口
    from app.api.routes import runs as runs_router
    from app.api.routes import threads as threads_router
    from app.api.routes import models as models_router
    from app.api.routes import messages as messages_router
    from app.api.routes import uploads as uploads_router
    from app.api.routes import artifacts as artifacts_router
    from app.api.routes import auth as auth_router
    from app.api.routes import agent_md as agent_md_router
    from app.api.routes import skills as skills_router
    from app.api.routes import observability as observability_router

    app.include_router(auth_router.router)  # 登录/自查：唯一免 token 的业务入口
    app.include_router(agent_md_router.router)  # 用户自定义指令（agent.md）读写
    app.include_router(skills_router.router)  # 技能清单与用户黑名单
    app.include_router(observability_router.router)  # 观测中台（运行列表/详情/span/日志）
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
