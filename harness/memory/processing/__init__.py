from __future__ import annotations

from harness.memory.processing.messages import (
    SIGNAL_NAMES,
    detect_signals,
    extract_message_text,
    filter_messages_for_memory,
    filter_trivial,
    format_conversation_for_update,
    load_patterns,
)
from harness.memory.processing.prompts import (
    PromptConfigurationError,
    format_memory_for_injection,
    load_prompt,
    load_prompt_messages,
)

"""消息处理包（processing）

    职责：长期记忆的无状态纯变换层——把消息清洗、检测信号、渲染对话，
         把记忆文档渲染成注入文本。不含 I/O 副作用与主链路依赖。

    对外暴露：
        - messages：filter_messages_for_memory / filter_trivial / detect_signals /
          extract_message_text / format_conversation_for_update / load_patterns / SIGNAL_NAMES
        - prompts：load_prompt / load_prompt_messages / format_memory_for_injection /
          PromptConfigurationError
"""

__all__ = [
    "SIGNAL_NAMES",
    "detect_signals",
    "extract_message_text",
    "filter_messages_for_memory",
    "filter_trivial",
    "format_conversation_for_update",
    "load_patterns",
    "PromptConfigurationError",
    "format_memory_for_injection",
    "load_prompt",
    "load_prompt_messages",
]
