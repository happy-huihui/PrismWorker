from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware

from harness.memory.manager import get_memory_manager
from harness.runtime.user_context import resolve_runtime_user_id

if TYPE_CHECKING:
    from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)

"""记忆中间件与摘要联动钩子（integration.middleware）

    职责：把长期记忆接进 Agent 运行链——注入 + 每轮自动入队提取，并在摘要
         压缩前做紧急冲刷。记忆永远 best-effort：异常只记日志，绝不阻断主链路。
    两个入口：
        - MemoryMiddleware  ① awrap_model_call 注入 <memory> 块；
                            ② after_agent / aafter_agent 入队提取（auto_extract 控制）；
        - memory_flush_hook 被 SummarizationMiddleware 在「压缩掉消息之前」调用，
                            用 add_nowait 紧急冲刷，保证先入库再压缩。
    模式：middleware 模式自动提取；tool 模式只注入、写入交给模型工具。
"""

# 系统提示里已含该标记则视为已注入，避免重复包裹
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

        参数：
            agent_name: 记忆归属 agent（单 Agent 项目主要用于调用链语义）
            auto_extract: 是否在每轮对话后自动入队提取（middleware 开 / tool 关）
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
        """包装模型调用：把用户的长期记忆注入系统消息（无记忆则静默放行）。"""
        existing = request.system_message
        # 1.已注入过（含 memory 标记）就不重复注入，直接放行
        if existing is not None and _MEMORY_MARKER in existing.text:
            return await handler(request)
        # 2.解析当前用户
        user_id = resolve_runtime_user_id(request.runtime)
        # 3.取注入文本，读失败静默跳过
        try:
            memory_text = get_memory_manager().get_context(
                user_id,
                agent_name=self._agent_name,
            )
        except Exception as exc:  # noqa: BLE001 —— 记忆异常不阻断对话
            logger.warning("记忆注入失败，本次跳过: %s", exc)
            return await handler(request)
        # 4.无可注入内容直接放行
        if not memory_text.strip():
            return await handler(request)

        # 5.包成 <memory> 块，拼到系统消息最前（原系统消息非空则续在其后）
        block = f"{_MEMORY_BLOCK_START}\n{memory_text}\n{_MEMORY_BLOCK_END}"
        if existing is None:
            from langchain_core.messages import SystemMessage

            system_message: SystemMessage = SystemMessage(content=block)
        else:
            text = existing.text
            text = f"{block}\n\n{text}" if text else block
            from langchain_core.messages import SystemMessage

            system_message = SystemMessage(content=text)
        # 6.用改写后的系统消息继续走 handler
        return await handler(request.override(system_message=system_message))

    # ── 提取（middleware 模式）──────────────────────────────────────────
    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Agent 本轮结束：把完整对话入队（异步防抖提取）。"""
        # tool 模式不自动提取
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
        # 1.先从 runtime.context 取 thread_id
        if runtime is not None:
            runtime_context = getattr(runtime, "context", None) or {}
        else:
            runtime_context = {}
        thread_id = runtime_context.get("thread_id") if isinstance(runtime_context, dict) else None
        # 2.取不到回退图全局 config
        if thread_id is None:
            try:
                from langgraph.config import get_config

                config_data = get_config()
                thread_id = (config_data.get("configurable") or {}).get("thread_id")
            except RuntimeError:
                thread_id = None
        # 3.无 thread_id 无法归属，跳过
        if not thread_id:
            logger.debug("无 thread_id，跳过记忆更新")
            return None
        # 4.无消息跳过
        messages = (state or {}).get("messages") or []
        if not messages:
            logger.debug("无消息，跳过记忆更新")
            return None
        # 5.解析 user_id（缺上下文时降级为 None）
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
    """摘要压缩前的记忆紧急冲刷（薄封装：enabled 门控 + user 解析 + add_nowait）。

    参数：
        thread_id: 目标线程
        messages_to_summarize: 即将被压缩掉的消息
        agent_name / user_id: 归属
        runtime: 用于缺 user_id 时解析
    """
    # 1.记忆未启用或无 thread 直接跳过（配置读失败按启用处理，不阻断冲刷）
    try:
        from harness.config.memory_config import get_memory_config

        if not get_memory_config().enabled or not thread_id:
            return
    except Exception:  # noqa: BLE001 —— 配置读取失败按启用处理
        pass
    # 2.缺 user_id 时从 runtime 解析
    if user_id is None and runtime is not None:
        try:
            user_id = resolve_runtime_user_id(runtime)
        except Exception:  # noqa: BLE001
            user_id = None
    # 3.紧急入队（bypass 水位线），失败不阻断压缩
    try:
        get_memory_manager().add_nowait(
            str(thread_id),
            list(messages_to_summarize),
            agent_name=agent_name,
            user_id=user_id,
        )
    except Exception as exc:  # noqa: BLE001 —— 冲刷失败不阻断压缩
        logger.warning("记忆紧急冲刷失败（thread=%s）: %s", thread_id, exc)
