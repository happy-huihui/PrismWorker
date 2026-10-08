from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import get_paths, get_user_id
from app.core.artifacts import resolve_artifact_path

"""产物下载路由

    职责：按虚拟路径下载线程产物文件
        - 越界 → 400；文件不存在 → 404
        - 实际路径解析委托 app.core.artifacts

    对外暴露：
        - router
"""

router = APIRouter(tags=["artifacts"])


@router.get(
    "/threads/{thread_id}/artifacts/{path:path}",
    summary="下载产物文件",
)
async def download_artifact(
    thread_id: str,
    path: str,
    user_id: str = Depends(get_user_id),
    paths: Any = Depends(get_paths),
) -> FileResponse:
    """下载指定产物（限定 outputs 目录，虚拟路径防穿越）。"""
    # 1.解析真实路径；越界抛 ValueError → 400
    try:
        real = resolve_artifact_path(user_id, thread_id, path, paths=paths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # 2.路径合法但文件不在 → 404
    if real is None:
        raise HTTPException(status_code=404, detail="产物不存在")
    return FileResponse(real, filename=real.name)
