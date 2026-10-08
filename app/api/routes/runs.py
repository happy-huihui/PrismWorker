from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_event_bus, get_run_service, get_thread_store, get_user_id
from app.api.schemas import ChainOut, RunCreate, RunOut, run_out_from_record
from harness.runtime.runs import (
    RUN_STATUS_CANCELLED,
    RUN_STATUS_ERROR,
    RUN_STATUS_FINISHED,
    RunConflictError,
    RunNotFoundError,
)

"""run 路由

    职责：run 生命周期网关 + SSE 思考链流
        - 创建 / 列出 / 查询 / 取消，全部委托 RunManager
        - GET /runs/{id}/stream 订阅 EventBus，把 RunEvent 转成 SSE 帧
        - 已终结的 run 直接回一帧终态事件，不订阅

    对外暴露：
        - router
"""

router = APIRouter(tags=["runs"])



@router.post(
    "/threads/{thread_id}/runs",
    response_model=RunOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    thread_id: str,
    body: RunCreate,
    user_id: str = Depends(get_user_id),
    service: Any = Depends(get_run_service),
    store: Any = Depends(get_thread_store),
) -> RunOut:
    """创建并启动一个 run（后台异步执行，立即返回句柄信息）。

    线程不存在 → 自动创建元数据（idle）；线程已有未结束 run → 409 冲突。
    """
    # 1.线程不存在就先补一条元数据（前端可能直接带新 id 起 run）
    meta = store.get(user_id=user_id, thread_id=thread_id)
    if meta is None:
        store.create(user_id=user_id, thread_id=thread_id)
    try:
        handle = await service.create_run(
            user_id=user_id,
            thread_id=thread_id,
            messages=body.messages,
            model_name=body.model_name,
            thinking_enabled=body.thinking_enabled,
        )
    # 2.同线程已有未结束 run → 409；线程不存在 → 404
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return run_out_from_record(_handle_to_record(handle))


@router.get("/threads/{thread_id}/runs", response_model=list[RunOut])
async def list_thread_runs(
    thread_id: str,
    limit: int = 50,
    user_id: str = Depends(get_user_id),
    service: Any = Depends(get_run_service),
) -> list[RunOut]:
    """列出某线程的历史 run（倒序）。"""
    records = await service.list_runs(user_id=user_id, thread_id=thread_id, limit=limit)
    return [run_out_from_record(r) for r in records]


@router.get("/threads/{thread_id}/chains", response_model=list[ChainOut])
async def get_thread_chains(
    thread_id: str,
    user_id: str = Depends(get_user_id),
    service: Any = Depends(get_run_service),
) -> list[ChainOut]:
    """返回该线程已落库 run 的思考链事件流（创建时间正序），供前端重开会话时回放。"""
    chains = await service.get_thread_chains(user_id=user_id, thread_id=thread_id)
    return [ChainOut(**c) for c in chains]


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(
    run_id: str,
    service: Any = Depends(get_run_service),
) -> RunOut:
    """查询单个 run 的最新状态。"""
    record = await service.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")
    return run_out_from_record(record)


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
async def cancel_run(
    run_id: str,
    service: Any = Depends(get_run_service),
) -> RunOut:
    """取消一个正在进行的 run（已结束则原样返回）。"""
    try:
        record = await service.cancel_run(run_id)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")
    return run_out_from_record(record)



@router.get("/runs/{run_id}/stream")
async def stream_run_events(
    run_id: str,
    service: Any = Depends(get_run_service),
    bus: Any = Depends(get_event_bus),
):
    """SSE 流：实时推送该 run 的思考链事件，直到 run 终结。

    帧格式：
        id: {seq}
        event: {type}          # tool_start / prints / todos / artifacts / ...
        data: {json(payload)}
    客户端依据 run_finished / run_error 事件收尾；SSE 连接随之关闭。

    回放：订阅前该 run 已发布的事件（run_started / run_meta / 已出的文本块）会先
    补发一遍——前端是拿到 run_id 后才连流，不回放就永远错过首批事件。
    不缓存：响应头显式禁缓 + 禁代理缓冲，否则中间代理会把流式帧攒成一团。
    """
    # 1.先确认 run 存在，避免订阅一个不存在的流
    record = await service.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")

    # 2.延迟导入：SSE 库只在真正建流时才需要
    from sse_starlette.sse import EventSourceResponse

    async def event_generator():
        """把 EventBus 订阅流转成 SSE 帧。"""
        # 3.已终结的 run 不订阅：总线录制已释放，直接补一帧终态事件
        if record.status in (RUN_STATUS_FINISHED, RUN_STATUS_CANCELLED, RUN_STATUS_ERROR):
            # 已终结的 run：总线录制已随 run 释放，历史思考链走 /threads/{id}/chains
            if record.status == RUN_STATUS_ERROR:
                payload = {
                    "run_id": run_id,
                    "thread_id": record.thread_id,
                    "status": "error",
                    "error": record.error,
                }
                event_type = "run_error"
            else:
                payload = {
                    "run_id": run_id,
                    "thread_id": record.thread_id,
                    "status": record.status,
                }
                event_type = "run_finished"
            yield {
                "id": str(record.created_at or 0),
                "event": event_type,
                "data": json.dumps(payload, ensure_ascii=False),
            }
            return
        try:
        # 4.订阅总线：每个 RunEvent 转一帧（id=seq / event=type / data=json）
            async for event in bus.subscribe(run_id):
                frame = {
                    "id": str(event.seq),
                    "event": event.type.value,
                    "data": json.dumps(event.payload, ensure_ascii=False),
                }
                yield frame
        except asyncio.CancelledError:
            raise

    # 5.显式禁缓存与代理缓冲，否则中间代理会把流式帧攒成一团
    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )



def _handle_to_record(handle: Any) -> Any:
    """把 RunHandle 转成伪 RunRecord（仅公开字段，供响应序列化）。

    create_run 返回的是 RunHandle（内存句柄），响应只需要其公开信息；
    用轻量对象聚合这些字段，避免引入 core 内部类型到 API 层。
    """
    # 用轻量对象聚合公开字段，避免把 core 内部类型引到 API 层
    from types import SimpleNamespace

    return SimpleNamespace(
        run_id=handle.run_id,
        thread_id=handle.thread_id,
        user_id=handle.user_id,
        status=handle.status,
        model_name=handle.model_name or "",
        input_preview=handle.input_preview,
        error=None,
        artifacts=[],
        message_count=0,
        created_at=handle.created_at,
        started_at=None,
        finished_at=None,
        # 1.观测三字段必须补齐：run_out_from_record 会读 trace_id/total_tokens/cost，
        #    缺一即 AttributeError（本句柄刚创建，token/成本尚未产生，取零值）。
        #    trace_id 从 RunHandle 继承（create_run 时已由 TraceMiddleware 绑定）。
        trace_id=getattr(handle, "trace_id", "") or "",
        total_tokens=0,
        cost=0.0,
    )
