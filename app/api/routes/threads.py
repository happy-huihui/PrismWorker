"""线程路由（threads）——线程会话的 CRUD 网关。

全部委托 app.core.thread_store.ThreadStore：本层只做参数解析 / 异常映射 /
响应序列化，不含业务逻辑。用户身份来自 X-User-Id 请求头（deps.get_user_id）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_thread_store, get_user_id
from app.api.schemas import (
    ThreadCreate,
    ThreadOut,
    ThreadRename,
    thread_out_from_meta,
)

router = APIRouter(prefix="/threads", tags=["threads"])


@router.post("", response_model=ThreadOut, status_code=status.HTTP_201_CREATED)
async def create_thread(
    body: ThreadCreate,
    user_id: str = Depends(get_user_id),
    store: Any = Depends(get_thread_store),
) -> ThreadOut:
    """创建线程（只建元数据；磁盘目录留待首次 run 时创建）。"""
    meta = store.create(user_id=user_id, title=body.title)
    return thread_out_from_meta(meta)


@router.get("", response_model=list[ThreadOut])
async def list_threads(
    user_id: str = Depends(get_user_id),
    store: Any = Depends(get_thread_store),
) -> list[ThreadOut]:
    """列出用户全部线程（按最后更新时间倒序）。"""
    metas = store.list(user_id)
    return [thread_out_from_meta(m) for m in metas]


@router.get("/{thread_id}", response_model=ThreadOut)
async def get_thread(
    thread_id: str,
    user_id: str = Depends(get_user_id),
    store: Any = Depends(get_thread_store),
) -> ThreadOut:
    """查询单个线程。"""
    meta = store.get(user_id=user_id, thread_id=thread_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"线程不存在: {thread_id}")
    return thread_out_from_meta(meta)


@router.patch("/{thread_id}", response_model=ThreadOut)
async def rename_thread(
    thread_id: str,
    body: ThreadRename,
    user_id: str = Depends(get_user_id),
    store: Any = Depends(get_thread_store),
) -> ThreadOut:
    """重命名线程（标题非空）。"""
    try:
        meta = store.rename(user_id=user_id, thread_id=thread_id, new_title=body.title)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"线程不存在: {thread_id}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return thread_out_from_meta(meta)


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: str,
    user_id: str = Depends(get_user_id),
    store: Any = Depends(get_thread_store),
) -> None:
    """删除线程：异步删除磁盘目录成功后删除元数据。

    目录删除失败（OSError）→ 中止并保留元数据，返回 409。
    """
    try:
        deleted = await store.delete(user_id=user_id, thread_id=thread_id)
    except OSError as exc:
        raise HTTPException(status_code=409, detail=f"线程目录删除失败: {exc}")
    if not deleted:
        raise HTTPException(status_code=404, detail=f"线程不存在: {thread_id}")
    return None