from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_checkpoint_db_path, get_user_id
from app.api.schemas import MessageOut
from harness.runtime.history import get_message_history

"""会话历史路由

    职责：从 checkpoint 读出线程会话历史，转成精简的 {role, content}

    对外暴露：
        - router
"""

router = APIRouter(tags=["messages"])


@router.get(
    "/threads/{thread_id}/messages",
    response_model=list[MessageOut],
    summary="会话历史",
)
async def list_thread_messages(
    thread_id: str,
    user_id: str = Depends(get_user_id),
    db_path: Any = Depends(get_checkpoint_db_path),
) -> list[MessageOut]:
    """读取线程会话历史（精简 {role, content}），无历史返回空数组。"""
    # 读 checkpoint 里的对话原文；无历史返回空数组
    history = await get_message_history(user_id, thread_id, db_path=db_path)
    return [MessageOut(role=m["role"], content=m["content"]) for m in history]
