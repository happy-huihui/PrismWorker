"""网关依赖（deps）——FastAPI 依赖注入的统一出口。

提供四个依赖：
  - get_bearer_token：从 Authorization: Bearer 头取原始 token（无则 None）；
  - get_user_id：验 token 换出 user_id（缺失/篡改/过期 → 401，全部业务路由据此拦截）；
  - verify_internal_token：X-Internal-Token 与 PRISM_INTERNAL_TOKEN 环境变量
    比对（未配置环境变量 → 本地开发模式放行）；
  - process_request_body：把请求体转成 core 层认识的 dict（类型/字段兜底）。

鉴权演进说明：旧版从 X-User-Id 头直接取身份（可伪造），现已废弃；
身份唯一来源 = 登录后签发的 HMAC token（能力在 app.core.auth）。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, Header, HTTPException

_INTERNAL_TOKEN_HEADER = "X-Internal-Token"
_AUTHORIZATION_HEADER = "Authorization"
_BEARER_PREFIX = "Bearer "


def get_bearer_token(
    authorization: str | None = Header(default=None, alias=_AUTHORIZATION_HEADER),
) -> str | None:
    """从 Authorization 头剥出 Bearer token；格式不对一律视为未登录（None）。"""
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        return None
    token = authorization[len(_BEARER_PREFIX):].strip()
    return token or None


def get_user_id(token: str | None = Depends(get_bearer_token)) -> str:
    """业务路由的身份闸门：token → user_id，任何不合法一律 401。

    前端收到 401 会清本地 token 并弹登录框（core/api/client.ts 统一拦截）。
    """
    from app.core.auth import verify_token  # 延迟导入：避免模块循环依赖

    user_id = verify_token(token or "")
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录或登录已失效")
    return user_id


def verify_internal_token(
    x_internal_token: str | None = Header(default=None, alias=_INTERNAL_TOKEN_HEADER),
) -> None:
    """校验内部 token：未配置环境变量 → 本地开发模式放行。"""
    expected = os.getenv("PRISM_INTERNAL_TOKEN", "").strip()
    if not expected:
        return
    if (x_internal_token or "") != expected:
        raise HTTPException(status_code=401, detail="内部 token 无效")



def get_thread_store() -> Any:
    """线程元数据仓库单例。"""
    from harness.runtime.threads_data import get_thread_store as _get

    return _get()


def get_run_service() -> Any:
    """run 编排服务单例。"""
    from harness.runtime.assembly import get_run_service as _get

    return _get()


def get_event_bus() -> Any:
    """事件总线单例。"""
    from harness.runtime.sse_stream import get_event_bus as _get

    return _get()


def get_app_config() -> Any:
    """app 层全局配置（薄委托 harness 配置单例）。"""
    from app.core.config import get_app_config as _get

    return _get()


def get_paths() -> Any:
    """数据路径管理器（测试可用 dependency_overrides 注入临时 base_dir）。"""
    from harness.config.paths import get_paths as _get

    return _get()


def get_checkpoint_db_path() -> Any:
    """checkpoint 库路径（None = 按全局配置解析；测试可注入临时库）。"""
    return None