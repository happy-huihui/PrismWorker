"""run 路由（runs）——run 生命周期网关 + SSE 思考链流。

全部委托 harness.runtime.runs.RunManager / harness.runtime.sse_stream.EventBus。
SSE 桥：GET /runs/{run_id}/stream 订阅 EventBus，把 RunEvent 转成
text/event-stream 帧（id=seq / event=type / data=json(payload)），
run_finished / run_error / END 到达后自动收尾；run 已终结时订阅立即结束。
"""

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
    record = await service.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")

    from sse_starlette.sse import EventSourceResponse

    async def event_generator():
        """把 EventBus 订阅流转成 SSE 帧。"""
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
            async for event in bus.subscribe(run_id):
                frame = {
                    "id": str(event.seq),
                    "event": event.type.value,
                    "data": json.dumps(event.payload, ensure_ascii=False),
                }
                yield frame
        except asyncio.CancelledError:
            raise

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
    )