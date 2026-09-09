from __future__ import annotations
from collections.abc import Mapping
from typing import Any

"""用户上下文解析。

工具函数必须调用此方法获取 user_id，不要直接从 runtime 里硬编码取值。
"""

DEFAULT_USER_ID: str = "default"

"""从 Runtime 中解析当前用户 ID（context → config → 图全局配置三阶梯回退）。"""
def resolve_runtime_user_id(runtime: Any) -> str:
    context = getattr(runtime, "context", None)
    if isinstance(context, Mapping):
        user_id = context.get("user_id")
        if user_id:
            return str(user_id)

    config = getattr(runtime, "config", None)
    if isinstance(config, Mapping):
        configurable = config.get("configurable")
        if isinstance(configurable, Mapping):
            user_id = configurable.get("user_id")
            if user_id:
                return str(user_id)

    try:
        from langgraph.config import get_config as _lg_get_config
        configurable = _lg_get_config().get("configurable") or {}
        user_id = configurable.get("user_id")
        if user_id:
            return str(user_id)
    except RuntimeError:
        pass

    return DEFAULT_USER_ID