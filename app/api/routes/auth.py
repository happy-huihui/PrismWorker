"""认证路由（auth）——登录 / 身份自查两个免鉴权端点。

职责：把 app.core.auth 的能力（验密 + 签发/校验 token）暴露成 HTTP 接口；
     真正的「每个请求都要带 token」由 deps.get_user_id 在各业务路由里执行，
     本模块只负责发放通行证。

端点：
  POST /auth/login  {username, password} → {token, user_id}
  GET  /auth/me     Authorization: Bearer <token> → {user_id}（前端启动时验缓存）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_bearer_token
from app.core.auth import authenticate, issue_token, verify_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    """登录请求体：用户名 + 密码（trim 由 auth 层统一处理）。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LoginOut(BaseModel):
    """登录响应：token 供前端持久化，user_id 供界面展示身份。"""

    token: str
    user_id: str


@router.post("/login", response_model=LoginOut)
async def login(body: LoginIn) -> LoginOut:
    """验密换 token。失败统一 401（不透露「用户存在但密码错」这类信息）。"""
    user_id = authenticate(body.username, body.password)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    return LoginOut(token=issue_token(user_id), user_id=user_id)


@router.get("/me")
async def me(token: str | None = Depends(get_bearer_token)) -> dict[str, str]:
    """自查接口：token 有效则回 user_id；无效回 401（前端据此清理本地缓存）。"""
    user_id = verify_token(token or "")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    return {"user_id": user_id}
