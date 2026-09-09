from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ContextT

from harness.config.paths import get_paths
from harness.runtime.user_content import resolve_runtime_user_id


"""
    会话上下文中间件（thread_context）——把线程/用户上下文写入 state。

    这个中间件负责「状态初始化」：Lead Agent 首次运行时，把本次会话的身份
    （thread_id / user_id）与三个文件目录（workspace / uploads / outputs）
    写进 ThreadState.thread_data，并确保目录在宿主机上真实存在。
    后续工具（read_file、present_file、list_uploaded_files 等）靠这些字段
    知道去哪里读写文件，因此只在 thread_data 缺失时补写一次（幂等）。
    同一时间只处理首次初始化，不重复覆盖调用方（Gateway）已注入的值。
"""

logger = logging.getLogger(__name__)


def _resolve_thread_id(runtime: Any) -> str | None:
    """从 runtime 上下文中解析线程 ID（context → config → 图全局配置三阶梯回退）。"""
    context = getattr(runtime, "context", None)
    if isinstance(context, Mapping):
        thread_id = context.get("thread_id")
        if thread_id:
            return str(thread_id)
    config = getattr(runtime, "config", None)
    if isinstance(config, Mapping):
        configurable = config.get("configurable")
        if isinstance(configurable, Mapping):
            thread_id = configurable.get("thread_id")
            if thread_id:
                return str(thread_id)
    try:
        from langgraph.config import get_config as _lg_get_config

        configurable = _lg_get_config().get("configurable") or {}
        thread_id = configurable.get("thread_id")
        if thread_id:
            return str(thread_id)
    except RuntimeError:
        pass
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
        if state.get("thread_data"):
            return None

        thread_id = _resolve_thread_id(runtime)
        if not thread_id:
            return None
        user_id = resolve_runtime_user_id(runtime)

        paths = get_paths()
        thread_data = {
            "workspace_path": str(paths.sandbox_work_dir(thread_id, user_id=user_id)),
            "uploads_path": str(paths.sandbox_uploads_dir(thread_id, user_id=user_id)),
            "outputs_path": str(paths.sandbox_outputs_dir(thread_id, user_id=user_id)),
        }

        if self._auto_ensure_dirs:
            try:
                paths.ensure_thread_dirs(thread_id, user_id=user_id)
            except OSError as exc:
                logger.warning("创建线程目录失败 %s: %s", thread_id, exc)

        return {
            "thread_data": thread_data,
            "prints": [
                f"会话上下文就绪（线程 {thread_id}，用户 {user_id}）",
            ],
        }