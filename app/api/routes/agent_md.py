from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_user_id
from harness.runtime.agent_md import MAX_AGENT_MD_LENGTH, get_agent_md_store

"""自定义指令路由

    职责：把 harness.runtime.agent_md 的存储能力暴露成 HTTP 接口
        - GET /agent-md 读现状（content / updated_at / max_length）
        - PUT /agent-md 保存（空即清空；超长由 pydantic 拦下）

    对外暴露：
        - router
"""

router = APIRouter(prefix="/agent-md", tags=["agent-md"])


class AgentMdOut(BaseModel):
    """自定义指令现状：content 为空串表示未设置；max_length 供前端表单约束。"""

    content: str = Field(default="", description="当前指令内容，空串表示未设置")
    updated_at: float | None = Field(default=None, description="最后修改时间（Unix 秒）")
    max_length: int = Field(default=MAX_AGENT_MD_LENGTH, description="长度上限（字符数）")


class AgentMdIn(BaseModel):
    """保存请求体：strip 后为空即清空，超上限由 max_length 校验拦下。"""

    content: str = Field(
        default="",
        max_length=MAX_AGENT_MD_LENGTH,
        description="自定义指令内容（Markdown 纯文本）",
    )


def _build_out(user_id: str) -> AgentMdOut:
    """读取当前指令并组装响应（updated_at 取文件 mtime，无文件为 None）。"""
    # 1.读当前指令内容（空串 = 未设置）
    store = get_agent_md_store()
    content = store.load(user_id)
    updated_at: float | None = None
    # 有内容才追 mtime；stat 失败不影响主响应
    if content:
        try:
            md_file = store.file_path(user_id)
            updated_at = md_file.stat().st_mtime
        except OSError:
            updated_at = None
    return AgentMdOut(content=content, updated_at=updated_at)


@router.get("", response_model=AgentMdOut)
async def get_agent_md(user_id: str = Depends(get_user_id)) -> AgentMdOut:
    """查询当前用户自定义指令。"""
    return _build_out(user_id)


@router.put("", response_model=AgentMdOut)
async def save_agent_md(body: AgentMdIn, user_id: str = Depends(get_user_id)) -> AgentMdOut:
    """保存当前用户自定义指令（空内容即清空，返回清空后的现状）。"""
    # 保存后回读现状，保证前端拿到规整后的结果
    get_agent_md_store().save(user_id, body.content)
    return _build_out(user_id)
