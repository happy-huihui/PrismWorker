from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, Header, HTTPException

"""网关依赖出口

    职责：FastAPI 依赖注入的统一出口——鉴权 + 各单例取用
        - get_user_id：token → user_id，不合法一律 401
        - verify_internal_token：内部 token 校验（未配置则本地放行）
        - 其余为 thread_store / run_service / event_bus / config / paths 的取用

    对外暴露：
        - get_bearer_token / get_user_id / verify_internal_token
        - get_thread_store / get_run_service / get_event_bus
        - get_app_config / get_paths / get_checkpoint_db_path
"""

_INTERNAL_TOKEN_HEADER = "X-Internal-Token"
_AUTHORIZATION_HEADER = "Authorization"
_BEARER_PREFIX = "Bearer "


def get_bearer_token(
    authorization: str | None = Header(default=None, alias=_AUTHORIZATION_HEADER),
) -> str | None:
    """从 Authorization 头剥出 Bearer token；格式不对一律视为未登录（None）。"""
    # 头缺失或前缀不符一律视为未登录
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        return None
    token = authorization[len(_BEARER_PREFIX):].strip()
    return token or None


def get_user_id(token: str | None = Depends(get_bearer_token)) -> str:
    """业务路由的身份闸门：token → user_id，任何不合法一律 401。

    前端收到 401 会清本地 token 并弹登录框（core/api/client.ts 统一拦截）。
    """
    # 延迟导入 auth：避免 app.api ↔ app.core 的模块循环依赖
    from app.core.auth import verify_token  # 延迟导入：避免模块循环依赖

    user_id = verify_token(token or "")
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录或登录已失效")
    return user_id


def verify_internal_token(
    x_internal_token: str | None = Header(default=None, alias=_INTERNAL_TOKEN_HEADER),
) -> None:
    """校验内部 token：未配置环境变量 → 本地开发模式放行。"""
    # 未配置内部 token = 本地开发模式，直接放行
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


async def get_observability_store() -> Any:
    """观测数据仓库单例（懒连接，供中台查询 spans/logs）。"""
    from harness.observability.store import get_observability_store as _get

    store = _get()
    # 懒连接：首次查询时建连接；已连接则复用
    if not store.connected:
        await store.connect()
    return store


def is_admin_user(user_id: str) -> bool:
    """判断 user_id 是否在观测台管理员列表里（供 /auth/me 与鉴权依赖共用）。"""
    from harness.config import get_app_config

    return user_id in get_app_config().observability.admin_user_ids


async def require_admin(user_id: str = Depends(get_user_id)) -> str:
    """观测台鉴权闸门：非管理员一律 403；通过则返回 user_id。

    管理员可看全部用户数据，故观测台路由统一用本依赖鉴权，查询时不再按 user_id 过滤。
    """
    if not is_admin_user(user_id):
        raise HTTPException(status_code=403, detail="观测台仅管理员可用")
    return user_id


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
    # 默认 None = 让 harness 按全局配置解析；测试用 dependency_overrides 注入
    return None
