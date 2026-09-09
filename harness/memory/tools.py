"""长期记忆工具：让模型自主保存 / 检索 / 删除跨会话记忆。

三个工具（仅 mode=tool 时注册）：
    - save_memory   存一条记忆（同 key 自动更新）
    - search_memory 按关键词 / 主题标签检索
    - delete_memory 删一条记忆

user_id 统一从 runtime 解析（见 runtime/user_content.py），消息统一中文。
"""

from __future__ import annotations

import logging
from typing import Annotated

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from harness.memory.manager import get_memory_store
from harness.runtime.user_content import resolve_runtime_user_id
from harness.tools.types import Runtime

logger = logging.getLogger(__name__)


def _error_message(tool_call_id: str, exc: Exception) -> Command:
    """把存取异常统一转成中文工具消息。"""
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"记忆操作失败: {type(exc).__name__}: {exc}",
                    tool_call_id=tool_call_id,
                )
            ]
        }
    )


@tool("save_memory", parse_docstring=True)
def save_memory_tool(
    key: str,
    content: str,
    runtime: Runtime,
    tool_call_id: Annotated[str, InjectedToolCallId],
    kind: str = "general",
) -> Command:
    """把一条值得长期记住的信息存入长期记忆库。

    什么时候用 save_memory：
    - 用户明确表达的偏好/习惯（语言、风格、喜欢什么、不喜欢什么）
    - 项目级约定与决定（技术栈、目录规范、命名风格）
    - 跨会话仍然有用的事实（环境配置、踩坑结论、常用命令）

    什么不该存：临时性信息、会话内一次性上下文、对话历史本来就能覆盖的内容。

    建议：
    - key 用层级命名，如 preference.language / project.tech_stack
    - kind 是主题标签（general / preference / project / fact / episodic 等），
      存的时候带上，检索时可以按标签过滤

    Args:
        key: 记忆的唯一键（同 key 重复保存会更新内容）。
        content: 记忆正文（建议一句话讲清楚，超长自动截断）。
        kind: 主题标签，默认 general。
    """
    user_id = resolve_runtime_user_id(runtime)
    try:
        created = get_memory_store().save(user_id, key, content, kind=kind)
    except Exception as exc:  # noqa: BLE001 —— 存储异常转成可读消息
        logger.exception("save_memory 失败")
        return _error_message(tool_call_id, exc)
    action = "已新建" if created else "已更新"
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"{action}记忆 [{key}]（标签: {kind}）",
                    tool_call_id=tool_call_id,
                )
            ]
        }
    )


@tool("search_memory", parse_docstring=True)
def search_memory_tool(
    query: str,
    runtime: Runtime,
    tool_call_id: Annotated[str, InjectedToolCallId],
    kind: str | None = None,
    limit: int = 5,
) -> Command:
    """在长期记忆库中按关键词检索记忆。

    什么时候用 search_memory：
    - 需要回忆用户之前告诉过你的偏好/习惯
    - 任务涉及过去的决定、过往的项目约定
    - 想确认某件事以前有没有记录过

    Args:
        query: 检索关键词（匹配记忆正文或记忆 key）。
        kind: 只找该标签下的记忆；省略则不限标签。
        limit: 最多返回条数（默认 5）。
    """
    user_id = resolve_runtime_user_id(runtime)
    try:
        entries = get_memory_store().search(user_id, query, kind=kind, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.exception("search_memory 失败")
        return _error_message(tool_call_id, exc)
    if not entries:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"没有找到匹配的记忆（关键词: {query}）",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )
    lines = [f"- [{entry.key}]（{entry.kind}）{entry.content}" for entry in entries]
    body = f"找到 {len(entries)} 条记忆：\n" + "\n".join(lines)
    return Command(
        update={
            "messages": [
                ToolMessage(content=body, tool_call_id=tool_call_id)
            ]
        }
    )


@tool("delete_memory", parse_docstring=True)
def delete_memory_tool(
    key: str,
    runtime: Runtime,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """删除一条长期记忆。

    什么时候用 delete_memory：
    - 用户明确说要忘掉/删除某条信息
    - 记忆已过时或错误，留着会误导后续判断

    Args:
        key: 要删除的记忆键（须与保存时一致）。
    """
    user_id = resolve_runtime_user_id(runtime)
    try:
        removed = get_memory_store().delete(user_id, key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("delete_memory 失败")
        return _error_message(tool_call_id, exc)
    if removed:
        content = f"已删除记忆 [{key}]"
    else:
        content = f"没有找到要删除的记忆 [{key}]"
    return Command(
        update={
            "messages": [ToolMessage(content=content, tool_call_id=tool_call_id)]
        }
    )