from __future__ import annotations

from collections.abc import Mapping
from typing import Any

"""用户上下文解析（user_context）

    职责：从 LangChain/LangGraph 的 runtime 里解析当前 user_id，供工具 / 中间件
         取用（不要从别处硬编码取值）。
    阶梯：context → config.configurable → 图全局 get_config()，逐级回退；
         都取不到则归到 DEFAULT_USER_ID。

    对外暴露：
        - DEFAULT_USER_ID            缺省用户
        - resolve_runtime_user_id    解析当前 user_id
"""

DEFAULT_USER_ID: str = "default"


def resolve_runtime_user_id(runtime: Any) -> str:
    """从 Runtime 中解析当前用户 ID（context → config → 图全局配置三阶梯回退）。

    参数：
        runtime: LangGraph 工具/中间件收到的 runtime 对象

    返回：
        解析到的 user_id；都没有时返回 DEFAULT_USER_ID
    """
    # 1) 优先取 runtime.context
    context = getattr(runtime, "context", None)
    if isinstance(context, Mapping):
        user_id = context.get("user_id")
        if user_id:
            return str(user_id)

    # 2) 退到 runtime.config.configurable
    config = getattr(runtime, "config", None)
    if isinstance(config, Mapping):
        configurable = config.get("configurable")
        if isinstance(configurable, Mapping):
            user_id = configurable.get("user_id")
            if user_id:
                return str(user_id)

    # 3) 退到图执行期的全局 config（可能不在图上下文中，抛错则忽略）
    try:
        from langgraph.config import get_config as _lg_get_config
        configurable = _lg_get_config().get("configurable") or {}
        user_id = configurable.get("user_id")
        if user_id:
            return str(user_id)
    except RuntimeError:
        pass

    # 兜底缺省用户
    return DEFAULT_USER_ID
