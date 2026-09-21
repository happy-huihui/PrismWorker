from __future__ import annotations

from harness.memory.processing.injection import format_memory_for_injection
from harness.memory.processing.messages import (
    SIGNAL_NAMES,
    detect_signals,
    extract_message_text,
    filter_messages_for_memory,
    filter_trivial,
    format_conversation_for_update,
    load_patterns,
)

"""消息处理包（processing）

    职责：长期记忆的无状态纯变换层。
    内容：
        - messages：清洗消息 / 剔除应声 / 检测信号 / 抽取文本 / 渲染对话 / 载入模式
        - injection：把记忆文档渲染成注入文本
    说明：提示词「模板加载」已上收到 harness.prompt；本包不再有 load_prompt* 。
"""

__all__ = [
    "SIGNAL_NAMES",
    "detect_signals",
    "extract_message_text",
    "filter_messages_for_memory",
    "filter_trivial",
    "format_conversation_for_update",
    "load_patterns",
    "format_memory_for_injection",
]
