from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ContextT

from harness.config.paths import get_paths
from harness.runtime.user_context import resolve_runtime_user_id


"""会话上下文中间件

    职责：会话首次运行时初始化身份与文件目录（幂等，只在缺失时补写）
        - 写入 thread_id / user_id 与 workspace / uploads / outputs 三个目录
        - 后续工具靠这些字段知道去哪读写文件

    对外暴露：
        - ThreadContextMiddleware
"""

logger = logging.getLogger(__name__)


def _resolve_thread_id(runtime: Any) -> str | None:
    """从 runtime 上下文中解析线程 ID（context → config → 图全局配置三阶梯回退）。"""
    # 1.第一阶梯：runtime.context 直接给 thread_id
    context = getattr(runtime, "context", None)
    if isinstance(context, Mapping):
        thread_id = context.get("thread_id")
        if thread_id:
            return str(thread_id)
    # 2.第二阶梯：runtime.config.configurable 里找
    config = getattr(runtime, "config", None)
    if isinstance(config, Mapping):
        configurable = config.get("configurable")
        if isinstance(configurable, Mapping):
            thread_id = configurable.get("thread_id")
            if thread_id:
                return str(thread_id)
    # 3.第三阶梯：LangGraph 图级全局配置兜底
    try:
        from langgraph.config import get_config as _lg_get_config

        configurable = _lg_get_config().get("configurable") or {}
        thread_id = configurable.get("thread_id")
        if thread_id:
            return str(thread_id)
    except RuntimeError:
        pass
    # 4.三处都取不到 → None
    return None


class ThreadContextMiddleware(AgentMiddleware):
    """会话上下文中间件：首次运行时把身份与文件路径写入 state.thread_data。"""

    def __init__(self, *, auto_ensure_dirs: bool = True) -> None:
        """初始化；auto_ensure_dirs=True 时初始化会创建三个目录。"""
        self._auto_ensure_dirs = auto_ensure_dirs

    async def abefore_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用前：thread_data 缺失时补写路径并确保目录存在。"""
        # 1.thread_data 已存在 → 不重复初始化
        if state.get("thread_data"):
            return None

        # 2.解析线程与用户身份；拿不到线程 ID → 放弃本次初始化
        thread_id = _resolve_thread_id(runtime)
        if not thread_id:
            return None
        user_id = resolve_runtime_user_id(runtime)

        # 3.拼出三个文件目录（workspace / uploads / outputs）
        paths = get_paths()
        thread_data = {
            "workspace_path": str(paths.sandbox_work_dir(thread_id, user_id=user_id)),
            "uploads_path": str(paths.sandbox_uploads_dir(thread_id, user_id=user_id)),
            "outputs_path": str(paths.sandbox_outputs_dir(thread_id, user_id=user_id)),
        }

        # 4.按需在宿主机创建目录（失败只记警告，不阻断）
        if self._auto_ensure_dirs:
            try:
                paths.ensure_thread_dirs(thread_id, user_id=user_id)
            except OSError as exc:
                logger.warning("创建线程目录失败 %s: %s", thread_id, exc)

        # 5.只回写 thread_data：不再往 prints 记「会话上下文就绪」——
        #    内部准备信息不是思考内容，进前端只会污染思考链
        return {"thread_data": thread_data}