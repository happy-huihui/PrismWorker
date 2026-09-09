"""上传文件路由（uploads）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import get_paths, get_user_id
from app.api.schemas import UploadOut
from app.core.uploads import list_uploads, save_upload

router = APIRouter(tags=["uploads"])

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@router.post(
    "/threads/{thread_id}/uploads",
    response_model=UploadOut,
    status_code=201,
    summary="上传文件",
)
async def upload_file(
    thread_id: str,
    file: UploadFile = File(..., description="要上传的文件"),
    user_id: str = Depends(get_user_id),
    paths: Any = Depends(get_paths),
) -> UploadOut:
    """上传文件到线程 uploads 目录（目录不存在自动创建）。"""
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 20MB 上限")
    try:
        info = save_upload(
            user_id, thread_id, filename=file.filename, data=data, paths=paths
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return UploadOut(**info)


@router.get(
    "/threads/{thread_id}/uploads",
    response_model=list[UploadOut],
    summary="已上传文件列表",
)
async def list_thread_uploads(
    thread_id: str,
    user_id: str = Depends(get_user_id),
    paths: Any = Depends(get_paths),
) -> list[UploadOut]:
    """列出线程已上传文件（按时间倒序，最新在前）。"""
    items = list_uploads(user_id, thread_id, paths=paths)
    return [UploadOut(**i) for i in items]