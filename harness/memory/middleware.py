"""记忆中间件与摘要联动钩子。

职责：
    - ``MemoryMiddleware``：挂在 Agent 中间件链上，做两件事——
      ① ``awrap_model_call`` 注入长期记忆（<memory> 块，get_context）；
      ② ``after_agent`` / ``aafter_agent`` 把本轮完整对话交给
      ``manager.add`` 入队（自动提取仅在 middleware 模式开启；
      tool 模式只注入不提取，写入由模型工具负责）；
    - ``memory_flush_hook``：被 SummarizationMiddleware 在「把消息压缩掉
      之前」调用，用 ``manager.add_nowait`` 紧急冲刷，保证被压缩的
      对话先进长期记忆再被移除（摘要与记忆的联动点）。

所有记忆操作均为 best-effort：异常只记日志，绝不阻断主对话链路。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from langchain.agents.middleware import AgentMiddleware

from harness.memory.manager import get_memory_manager
from harness.runtime.user_content import resolve_runtime_user_id

if TYPE_CHECKING:
    from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)

_MEMORY_MARKER = "memory"
# 注入块上下界标签（与系统提示词中的占位约定一致）
_MEMORY_BLOCK_START = "<memory>"
_MEMORY_BLOCK_END = "</memory>"


class MemoryMiddleware(AgentMiddleware):
    """记忆中间件：注入长期记忆 + 每轮对话自动入队提取（按 mode 分流）。"""

    def __init__(
        self,
        *,
        agent_name: str | None = None,
        auto_extract: bool = True,
    ) -> None:
        """初始化。

        Args:
            agent_name: 记忆归属 agent（单 Agent 项目主要用于调用链语义）。
            auto_extract: 是否在每轮对话后自动入队提取（middleware 模式开，
                tool 模式关——tool 模式由模型工具自主写入）。
        """
        super().__init__()
        self._agent_name = agent_name
        self._auto_extract = auto_extract

    # ── 注入 ────────────────────────────────────────────────────────────
    async def awrap_model_call(
        self,
        request: Any,
        handler: Any,
    ) -> Any:
        """包装模型调用：把用户的长期记忆注入系统消息（无记忆则静默）。"""
        existing = request.system_message
        if existing is not None and _MEMORY_MARKER in existing.text:
            return await handler(request)
        user_id = resolve_runtime_user_id(request.runtime)
        try:
            memory_text = get_memory_manager().get_context(
                user_id,
                agent_name=self._agent_name,
            )
        except Exception as exc:  # noqa: BLE001 —— 记忆异常不阻断对话
            logger.warning("记忆注入失败，本次跳过: %s", exc)
            return await handler(request)
        if not memory_text.strip():
            return await handler(request)

        block = f"{_MEMORY_BLOCK_START}\n{memory_text}\n{_MEMORY_BLOCK_END}"
        if existing is None:
            from langchain_core.messages import SystemMessage

            system_message: SystemMessage = SystemMessage(content=block)
        else:
            text = existing.text
            text = f"{block}\n\n{text}" if text else block
            from langchain_core.messages import SystemMessage

            system_message = SystemMessage(content=text)
        return await handler(request.override(system_message=system_message))

    # ── 提取（middleware 模式）──────────────────────────────────────────
    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Agent 本轮结束：把完整对话入队（异步防抖提取）。"""
        if not self._auto_extract:
            return None
        add_args = self._resolve_add_args(state, runtime)
        if add_args is None:
            return None
        thread_id, messages, user_id = add_args
        try:
            get_memory_manager().add(
                thread_id,
                messages,
                agent_name=self._agent_name,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001 —— 记忆异常不阻断对话
            logger.warning("记忆入队失败（thread=%s）: %s", thread_id, exc)
        return None

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """异步路径同 after_agent。"""
        if not self._auto_extract:
            return None
        add_args = self._resolve_add_args(state, runtime)
        if add_args is None:
            return None
        thread_id, messages, user_id = add_args
        try:
            manager = get_memory_manager()
            manager.add(
                thread_id,
                messages,
                agent_name=self._agent_name,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆入队失败（thread=%s）: %s", thread_id, exc)
        return None

    def _resolve_add_args(
        self,
        state: Any,
        runtime: Any,
    ) -> tuple[str, list[Any], str | None] | None:
        """解析 (thread_id, messages, user_id)；缺 thread_id 或消息时跳过。"""
        if runtime is not None:
            runtime_context = getattr(runtime, "context", None) or {}
        else:
            runtime_context = {}
        thread_id = runtime_context.get("thread_id") if isinstance(runtime_context, dict) else None
        if thread_id is None:
            try:
                from langgraph.config import get_config

                config_data = get_config()
                thread_id = (config_data.get("configurable") or {}).get("thread_id")
            except RuntimeError:
                thread_id = None
        if not thread_id:
            logger.debug("无 thread_id，跳过记忆更新")
            return None
        messages = (state or {}).get("messages") or []
        if not messages:
            logger.debug("无消息，跳过记忆更新")
            return None
        try:
            user_id = resolve_runtime_user_id(runtime)
        except Exception:  # noqa: BLE001 —— runtime 缺上下文时降级
            user_id = None
        return str(thread_id), list(messages), user_id


def memory_flush_hook(
    *,
    thread_id: str,
    messages_to_summarize: list[Any],
    agent_name: str | None = None,
    user_id: str | None = None,
    runtime: Any = None,
) -> None:
    """摘要压缩前的记忆紧急冲刷（薄封装：enabled 门控 + user 解析 + add_nowait）。"""
    try:
        from harness.config.memory_config import get_memory_config

        if not get_memory_config().enabled or not thread_id:
            return
    except Exception:  # noqa: BLE001 —— 配置读取失败按启用处理（不阻断冲刷）
        pass
    if user_id is None and runtime is not None:
        try:
            user_id = resolve_runtime_user_id(runtime)
        except Exception:  # noqa: BLE001
            user_id = None
    try:
        get_memory_manager().add_nowait(
            str(thread_id),
            list(messages_to_summarize),
            agent_name=agent_name,
            user_id=user_id,
        )
    except Exception as exc:  # noqa: BLE001 —— 冲刷失败不阻断压缩
        logger.warning("记忆紧急冲刷失败（thread=%s）: %s", thread_id, exc)