from __future__ import annotations

import logging
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from harness.memory.manager import MemoryEntry, get_memory_store
from harness.runtime.user_content import resolve_runtime_user_id


"""
    记忆注入中间件（memory_injection）——把长期记忆带进模型上下文。

    与 memory 工具（save/search/delete_memory）互补：
      工具 = 模型主动读写（mode="tool" 时注册）；
      本中间件 = 自动注入 + 保守自动保存。

    awrap_model_call：
      从 SQLite 长期记忆库读取该用户最近的记忆（kind 不限，按更新时间倒序），
      拼成 <memory_recent> 结构块注入系统提示，让模型在开口前就「想起来」；
      无记忆时静默（不打扰）。

    aafter_model（仅 auto_save=True 时）：
      保守自动记忆——只保存「非常明确、值得跨会话保留」的句子（触发词
      命中 + 消息长度限制），key 用内容指纹保证同一句不重复入库。
"""

logger = logging.getLogger(__name__)

_MEMORY_MARKER = "memory_recent"

_DEFAULT_AUTO_SAVE = False

_STRONG_SIGNAL_RE = re.compile(
    r"(请记住|记住了|别忘了|偏好|不喜欢|喜欢|习惯|今后|以后都|永远是|"
    r"一定(要|得)|必须(记|提醒)|约定|决定.*就一直)",
    re.IGNORECASE,
)

_AUTO_SAVE_MAX_CHARS = 200


class MemoryInjectionMiddleware(AgentMiddleware):
    """记忆注入中间件：自动注入最近记忆 + 保守自动保存新偏好。"""

    def __init__(self, *, auto_save: bool = _DEFAULT_AUTO_SAVE, max_results: int = 5) -> None:
        """初始化；auto_save 决定是否在模型调用后做保守自动保存。"""
        self._auto_save = auto_save
        self._max_results = max_results

    async def awrap_model_call(
        self,
        request: Any,
        handler: Any,
    ) -> Any:
        """包装模型调用：把该用户最近的长期记忆注入系统消息。"""
        existing = request.system_message
        if existing is not None and _MEMORY_MARKER in existing.text:
            return await handler(request)
        user_id = resolve_runtime_user_id(request.runtime)
        try:
            entries = get_memory_store().search(
                user_id, None, kind=None, limit=self._max_results
            )
        except Exception as exc:  # noqa: BLE001 —— 记忆库异常不阻断对话
            logger.warning("记忆注入检索失败: %s", exc)
            return await handler(request)
        if not entries:
            return await handler(request)
        lines = [_format_memory(entry) for entry in entries]
        block = _format_block(lines)
        if existing is None:
            system_message = SystemMessage(content=block)
        else:
            text = existing.text
            text = f"{block}\n\n{text}" if text else block
            system_message = SystemMessage(content=text)
        return await handler(request.override(system_message=system_message))

    async def aafter_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用后：保守提取值得记住的句子写入长期记忆。"""
        if not self._auto_save:
            return None

        messages = (state or {}).get("messages") or []
        last_user_text = ""
        for message in reversed(messages):
            if message is not None and getattr(message, "type", "") == "human":
                text = _extract_text(message)
                if text:
                    last_user_text = text
                    break
        sentence = extract_memorable_sentence(last_user_text)
        if sentence is None:
            return None

        user_id = resolve_runtime_user_id(runtime)
        key = _memory_key_from_text(sentence)
        try:
            created = get_memory_store().save(
                user_id, key, sentence, kind="preference"
            )
        except Exception as exc:  # noqa: BLE001 —— 记忆库异常不阻断对话
            logger.warning("自动记忆保存失败: %s", exc)
            return None
        if not created:
            return None
        return {"prints": [f"已记住新偏好：{sentence[:40]}…"]}


def _format_memory(entry: MemoryEntry) -> str:
    """把一条记忆格式化为注入行（标签 + 正文）。"""
    kind_part = f"[{entry.kind}] " if entry.kind != "general" else ""
    return f"{kind_part}{entry.content}"


def _format_block(lines: list[str]) -> str:
    """把记忆行拼成 <memory_recent> 结构块。"""
    return f"<{_MEMORY_MARKER}>\n" + "\n".join(lines) + f"\n</{_MEMORY_MARKER}>"


def _extract_text(message_or_response: Any) -> str:
    """从消息/响应提取纯文本（兼容 str 与多模态块列表）。"""
    content = getattr(message_or_response, "content", message_or_response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""



def extract_memorable_sentence(text: str) -> str | None:
    """从用户文本里提取「值得记住」的单句；无则返回 None。

    规则（保守）：
      - 命中强信号词（偏好/习惯/喜好/记忆类表达）；
      - 整句长度不超过 _AUTO_SAVE_MAX_CHARS；
      - 不是纯语气词/过短（< 6 字）。
    """
    if not isinstance(text, str) or not text.strip():
        return None
    sentence = text.strip()
    if len(sentence) < 6 or len(sentence) > _AUTO_SAVE_MAX_CHARS:
        return None
    match = _STRONG_SIGNAL_RE.search(sentence)
    if match is None:
        return None
    return sentence


def _memory_key_from_text(text: str) -> str:
    """按内容指纹生成记忆 key（同一句不会重复入库）。"""
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"auto.memory.{digest}"