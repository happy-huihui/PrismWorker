from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_bearer_token
from app.core.auth import authenticate, issue_token, verify_token

"""认证路由

    职责：把 app.core.auth 的能力暴露成两个免鉴权端点（发放通行证）
        - POST /auth/login 验密换 token
        - GET  /auth/me    验缓存 token 换 user_id

    对外暴露：
        - router
"""

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    """登录请求体：用户名 + 密码（trim 由 auth 层统一处理）。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LoginOut(BaseModel):
    """登录响应：token 供前端持久化，user_id 供界面展示身份，is_admin 供显隐观测台入口。"""

    token: str
    user_id: str
    is_admin: bool = Field(default=False, description="是否观测台管理员")


@router.post("/login", response_model=LoginOut)
async def login(body: LoginIn) -> LoginOut:
    """验密换 token。失败统一 401（不透露「用户存在但密码错」这类信息）。"""
    # 验密失败统一 401，不透露「用户存在但密码错」
    user_id = authenticate(body.username, body.password)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    from app.api.deps import is_admin_user

    return LoginOut(token=issue_token(user_id), user_id=user_id, is_admin=is_admin_user(user_id))


@router.get("/me")
async def me(token: str | None = Depends(get_bearer_token)) -> dict[str, Any]:
    """自查接口：token 有效则回 user_id + is_admin；无效回 401（前端据此清理本地缓存）。"""
    # token 失效回 401，前端据此清本地缓存
    user_id = verify_token(token or "")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    # is_admin 供前端显隐「观测台」入口（观测台仅管理员可用）
    from app.api.deps import is_admin_user

    return {"user_id": user_id, "is_admin": is_admin_user(user_id)}
