"""网关依赖（deps）——FastAPI 依赖注入的统一出口。

提供三个依赖：
  - get_user_id：从 X-User-Id 请求头解析用户身份（缺省 default）；
  - verify_internal_token：X-Internal-Token 与 PRISM_INTERNAL_TOKEN 环境变量
    比对（未配置环境变量 → 本地开发模式放行）；
  - process_request_body：把请求体转成 core 层认识的 dict（类型/字段兜底）。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import Header, HTTPException

_INTERNAL_TOKEN_HEADER = "X-Internal-Token"
_USER_ID_HEADER = "X-User-Id"

_USER_ID_RE = None


def _validate_user_id(user_id: str) -> str:
    """校验用户 id 合法（1-64 位字母数字下划线连字符），非法抛 400。"""
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user_id):
        raise HTTPException(status_code=400, detail=f"用户 id 非法: {user_id!r}")
    return user_id


def get_user_id(
    x_user_id: str | None = Header(default=None, alias=_USER_ID_HEADER),
) -> str:
    """从请求头取用户身份，缺省 'default' 并做合法性校验。"""
    user_id = (x_user_id or "").strip() or "default"
    return _validate_user_id(user_id)


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
    from app.core.thread_store import get_thread_store as _get

    return _get()


def get_run_service() -> Any:
    """run 编排服务单例。"""
    from app.core.run_service import get_run_service as _get

    return _get()


def get_event_bus() -> Any:
    """事件总线单例。"""
    from app.core.event_bus import get_event_bus as _get

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