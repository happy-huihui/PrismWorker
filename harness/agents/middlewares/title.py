from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from harness.prompt import render_text


"""
    会话标题中间件（title）——为新会话生成一句话标题（oneshot）。

    只在「标题尚未生成」且「对话已进行到至少 3 条消息」时触发一次，
    用主模型生成一句简洁标题写入 state.title。生成失败不阻塞对话，
    但会写一条 prints 说明（避免模型反复尝试）。
    为避免额外请求开销，生成标题的模型调用尽量轻量（temperature 低、
    提示词要求一句话）。
"""

logger = logging.getLogger(__name__)

_MIN_MESSAGES_FOR_TITLE = 3

_TITLE_MAX_CHARS = 60


class TitleMiddleware(AgentMiddleware):
    """会话标题中间件：新会话达到触发条件时生成一句话标题。"""

    def __init__(self, *, model: Any | None = None, model_name: str | None = None) -> None:
        """初始化；model 为可复用的主模型实例，model_name 为其配置名（二选一）。"""
        self._model = model
        self._model_name = model_name

    async def abefore_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用前：标题为空且消息足够时，尝试生成标题。"""
        if (state or {}).get("title"):
            return None
        messages = (state or {}).get("messages") or []
        if len(messages) < _MIN_MESSAGES_FOR_TITLE:
            return None

        preview = _build_preview(messages, max_chars=600)

        title = await self._generate_title(preview)
        if not title:
            return None

        title = title.strip().strip("「」\"'").rstrip("。！？")
        if len(title) > _TITLE_MAX_CHARS:
            title = title[:_TITLE_MAX_CHARS] + "…"
        return {
            "title": title,
            "prints": [f"会话标题已生成：{title}"],
        }

    async def _generate_title(self, preview: str) -> str | None:
        """用主模型生成标题（异常时返回 None）。"""
        model = self._model
        if model is None:
            try:
                from harness.models.factory import create_chat_model

                model = create_chat_model(name=self._model_name, thinking_enabled=False)
            except Exception as exc:  # noqa: BLE001 —— 模型不可用不该拖垮会话
                logger.warning("标题中间件创建模型失败: %s", exc)
                return None
        try:
            # 标题模板集中在 harness/prompt，这里只传预览变量
            response = await model.ainvoke(render_text("titler/title", {"preview": preview}))
            text = getattr(response, "content", None)
            if isinstance(text, str):
                return text.strip()
            if isinstance(text, list):
                parts = [
                    str(block.get("text", ""))
                    for block in text
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                return "".join(parts).strip()
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("标题生成失败: %s", exc)
            return None


def _build_preview(messages: list[Any], *, max_chars: int) -> str:
    """把前几条消息拼成简短预览文本（供标题模型概括）。"""
    parts: list[str] = []
    for message in messages[:_MIN_MESSAGES_FOR_TITLE + 1]:
        role = getattr(message, "type", "unknown")
        content = getattr(message, "content", "")
        text = ""
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text = "".join(
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
        if text:
            parts.append(f"{role}: {text[:200]}")
    joined = "\n".join(parts)
    return joined[:max_chars] if len(joined) > max_chars else joined