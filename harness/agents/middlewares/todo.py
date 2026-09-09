from __future__ import annotations

import json
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage


"""
    待办计划中间件（todo）——从模型回复里解析「计划列表」并合并进 state.todos。

    约定（用户确认）：模型只有以 JSON 数组形式输出待办计划时才会被解析，
    其余格式一律跳过。识别入口有两种：
      1. 独立 JSON 数组：消息正文就是一个 JSON 数组（或包含 json 代码块）；
      2. 内嵌标记：正文里出现 `<todo>[...]</todo>` 结构块。
    解析成功则整体替换 state.todos（merge_todos 是全量替换语义），
    并写一条进度消息；解析失败/无计划时静默返回（不打扰对话）。
"""

_TODO_BLOCK_RE = re.compile(r"<todo>(.*?)</todo>", re.DOTALL | re.IGNORECASE)

_JSON_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_todo_json_array(content: str) -> list[Any] | None:
    """从模型回复正文里提取 JSON 数组形式的待办计划；解析不到返回 None。

    优先级：<todo> 结构块 → ```json 代码块 → 整段正文（去掉首尾空白后直接解析）。
    只接受顶层是 JSON 数组的结果，其它类型（dict / 数字 / 字符串）一律拒绝，
    防止模型把非计划内容误当成 todos。
    """
    if not isinstance(content, str) or not content.strip():
        return None

    candidates: list[str] = []

    block_matches = _TODO_BLOCK_RE.findall(content)
    candidates.extend(m.strip() for m in block_matches if m.strip())

    code_matches = _JSON_CODE_BLOCK_RE.findall(content)
    candidates.extend(m.strip() for m in code_matches if m.strip())

    stripped = content.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        candidates.append(stripped)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, list):
            return parsed
    return None


class TodoMiddleware(AgentMiddleware):
    """待办计划中间件：识别模型输出的 JSON 数组计划并合并进 state.todos。"""

    async def aafter_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用后：从最新 AI 消息里解析待办计划。"""
        messages = (state or {}).get("messages") or []
        last_ai: AIMessage | None = None
        for message in reversed(messages):
            if message is not None and getattr(message, "type", "") == "ai":
                last_ai = message
                break
        if last_ai is None:
            return None

        todos = parse_todo_json_array(last_ai.content)
        if todos is None:
            return None

        return {
            "todos": todos,
            "prints": [f"已更新任务计划：{len(todos)} 项待办"],
        }