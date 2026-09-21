from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import (
    AnyMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from harness.prompt import render_text


"""
    对话摘要中间件（summarization）——对话超长时把早期消息压成摘要。

    触发条件：消息条数超过 max_messages 或 token 数超过 max_tokens（默认
    40 条 / 8000 token）。满足其一即触发一次压缩。
    压缩方式：调用主模型（用户确认：摘要走模型），把「最旧的、需要压缩的
    那部分消息」交给模型生成一段中文摘要；被压缩的消息用 RemoveMessage
    清空，保留最近 keep_messages（默认 20）条；摘要正文同时写入
    state.summary_text（拼接式，保留历史摘要链），并写一条进度消息。

    记忆联动：压缩前通过 flush_hook（见 harness/memory/integration/middleware.py 的
    memory_flush_hook）把将被压缩的消息紧急冲刷进长期记忆队列，保证
    「先入记忆、再被压缩」，压缩不丢信息。flush_hook 缺省为 None，
    未注入时行为与改造前完全一致。

    安全边界：
      - 保留段不拆散 AI/Tool 消息对（ToolMessage 必须与发起它的 AI 消息一起保留）；
      - 摘要生成失败时回退为规则式截断（仍可压缩，不阻断对话）。
"""

logger = logging.getLogger(__name__)

_DEFAULT_MAX_MESSAGES = 40
_DEFAULT_MAX_TOKENS = 8000
_DEFAULT_KEEP_MESSAGES = 20


def _find_safe_cutoff(messages: list[AnyMessage], messages_to_keep: int) -> int:
    """找安全切割点：保留最近 N 条，且不把 AI/Tool 消息对拆散。

    目标切割点 = len - keep。若该位置是 ToolMessage，向前回退到包含
    其对应 AI tool_calls 的位置（把整对一起压缩），保证保留段自洽。
    """
    if len(messages) <= messages_to_keep:
        return 0
    target = len(messages) - messages_to_keep
    while target > 0 and isinstance(messages[target], ToolMessage):
        target -= 1
    return target


class SummarizationMiddleware(AgentMiddleware):
    """对话摘要中间件：超长时调模型压缩早期消息并记录摘要。"""

    def __init__(
        self,
        *,
        model: Any | None = None,
        model_name: str | None = None,
        max_messages: int = _DEFAULT_MAX_MESSAGES,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        keep_messages: int = _DEFAULT_KEEP_MESSAGES,
        flush_hook: Callable[..., None] | None = None,
        agent_name: str | None = None,
    ) -> None:
        """初始化；model 为可复用的主模型实例（model_name 二选一）。

        Args:
            model: 复用主模型实例；None 时按 model_name 懒加载。
            model_name: 摘要模型名（与主模型同一配置表）。
            max_messages / max_tokens / keep_messages: 压缩触发与保留参数。
            flush_hook: 压缩前的记忆冲刷回调（thread_id / messages_to_summarize /
                agent_name / runtime 关键字传参）；None 不冲刷（原行为）。
            agent_name: 冲刷时携带的 agent 归属（记忆桶语义）。
        """
        self._model = model
        self._model_name = model_name
        self._max_messages = max_messages
        self._max_tokens = max_tokens
        self._keep_messages = keep_messages
        self._flush_hook = flush_hook
        self._agent_name = agent_name

    async def abefore_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用前：检查是否超长，超长则压缩并返回摘要更新。"""
        messages = (state or {}).get("messages") or []
        if len(messages) < 4:
            return None

        current_tokens = 0
        try:
            current_tokens = count_tokens_approximately(messages)
        except Exception:  # noqa: BLE001 —— 估算失败继续走条数判断
            current_tokens = 0
        over_message_threshold = len(messages) > self._max_messages
        over_token_threshold = current_tokens > self._max_tokens
        if not (over_message_threshold or over_token_threshold):
            return None

        target = _find_safe_cutoff(messages, self._keep_messages)
        if target <= 0:
            return None

        to_summarize = messages[:target]
        preserved = messages[target:]

        # 记忆联动：压缩前先把将被移除的消息冲刷进长期记忆队列
        #（紧急路径 add_nowait，立即后台提取；失败仅告警不阻断压缩）
        self._fire_flush_hook(to_summarize, runtime)

        summary = await self._summarize(to_summarize)
        combined_summary = _append_summary((state or {}).get("summary_text"), summary)

        new_summary_msg = SystemMessage(content=f"对话摘要（压缩后）：\n{summary}")
        new_summary_msg.id = str(uuid.uuid4())
        new_messages = [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            new_summary_msg,
            *preserved,
        ]

        return {
            "messages": new_messages,
            "summary_text": combined_summary,
            "prints": [
                f"对话超长，已压缩早期 {len(to_summarize)} 条消息为摘要"
                f"（保留最近 {len(preserved)} 条）",
            ],
        }


    def _fire_flush_hook(self, messages_to_summarize: list[AnyMessage], runtime: Any) -> None:
        """触发记忆紧急冲刷（无 hook 或缺 thread_id 时静默跳过）。"""
        if self._flush_hook is None:
            return
        thread_id = self._resolve_thread_id(runtime)
        if not thread_id:
            return
        try:
            self._flush_hook(
                thread_id=thread_id,
                messages_to_summarize=messages_to_summarize,
                agent_name=self._agent_name,
                runtime=runtime,
            )
        except Exception as exc:  # noqa: BLE001 —— 冲刷失败绝不能阻断压缩
            logger.warning("记忆冲刷钩子执行失败（thread=%s）: %s", thread_id, exc)

    @staticmethod
    def _resolve_thread_id(runtime: Any) -> str | None:
        """从 runtime 上下文取 thread_id（与记忆中间件的解析方式一致）。"""
        runtime_context = getattr(runtime, "context", None) or {}
        thread_id = runtime_context.get("thread_id") if isinstance(runtime_context, dict) else None
        if thread_id is None:
            try:
                from langgraph.config import get_config

                config_data = get_config()
                thread_id = (config_data.get("configurable") or {}).get("thread_id")
            except RuntimeError:
                thread_id = None
        return str(thread_id) if thread_id else None

    async def _summarize(self, messages: list[AnyMessage]) -> str:
        """把待压缩消息交给模型生成摘要；失败时回退为规则式截断。"""
        try:
            from langchain_core.messages.utils import get_buffer_string

            rendered = get_buffer_string(messages)
        except Exception:  # noqa: BLE001
            rendered = "\n".join(str(m.content) for m in messages)[:20000]
        # 摘要模板集中在 harness/prompt，这里只做变量装配（截断到 30000 字）
        prompt = render_text("summarizer/summary", {"messages": rendered[:30000]})

        try:
            model = self._load_model()
            response = await model.ainvoke(prompt)
            text = _extract_text(response)
            if text.strip():
                return text.strip()
        except Exception as exc:  # noqa: BLE001 —— 模型失败不阻断压缩流程
            logger.warning("摘要生成失败，改用规则式压缩: %s", exc)

        lines: list[str] = []
        for message in messages:
            text = _extract_text(message)
            if text:
                lines.append(text[:120])
        return "；\n".join(lines)[:4000] or "（无可压缩内容）"

    def _load_model(self) -> Any:
        """懒加载模型实例。"""
        if self._model is not None:
            return self._model
        from harness.models.factory import create_chat_model

        self._model = create_chat_model(name=self._model_name, thinking_enabled=False)
        return self._model


def _append_summary(existing: Any, new_summary: str) -> str:
    """把新摘要拼接到旧摘要之后（保留历史摘要链）。"""
    old = (existing or "").strip()
    if not old:
        return new_summary
    return f"{old[-8000:]}\n\n（以下为后续对话摘要）\n{new_summary}"


def _extract_text(message_or_response: Any) -> str:
    """从消息或模型响应里提取纯文本（兼容 str 与多模态块列表）。"""
    if hasattr(message_or_response, "content"):
        content = message_or_response.content
    else:
        content = message_or_response
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content) if content else ""