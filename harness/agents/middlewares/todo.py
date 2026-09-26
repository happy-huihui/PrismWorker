from __future__ import annotations

import json
import re
import time
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

    状态收敛（2026-09-26 新增，「保证状态正确」的系统侧闸门）：
      模型不总记得写规范的状态枚举（实测会出现 done / 成功 / 进行中 等写法）。
      解析成功后统一做两件事：
        1. 状态归一化 → 只承认 pending / in_progress / completed / cancelled 四值；
        2. 字段规范化 → 每条补齐 {id, content, status}（缺 id 用下标生成，
           纯字符串条目包成对象），前端不再需要兼容一堆别名。
      空数组（[]）也是合法产出：语义 = 「本轮没有计划」→ 收尾时清空 todos。
"""

_TODO_BLOCK_RE = re.compile(r"<todo>(.*?)</todo>", re.DOTALL | re.IGNORECASE)

_JSON_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

# 唯一承认的四个状态值（与前端 TodosPanel / 收尾逻辑三方对齐）
_TODO_STATUS_COMPLETED = "completed"
_TODO_STATUS_IN_PROGRESS = "in_progress"
_TODO_STATUS_CANCELLED = "cancelled"
_TODO_STATUS_PENDING = "pending"

# 模型可能写出的状态同义词 → 规范值（小写匹配；含常见中文写法）
_STATUS_ALIASES: dict[str, str] = {
    # completed
    "completed": _TODO_STATUS_COMPLETED,
    "complete": _TODO_STATUS_COMPLETED,
    "done": _TODO_STATUS_COMPLETED,
    "success": _TODO_STATUS_COMPLETED,
    "finished": _TODO_STATUS_COMPLETED,
    "完成": _TODO_STATUS_COMPLETED,
    "已完成": _TODO_STATUS_COMPLETED,
    "✓": _TODO_STATUS_COMPLETED,
    # in_progress
    "in_progress": _TODO_STATUS_IN_PROGRESS,
    "in-progress": _TODO_STATUS_IN_PROGRESS,
    "inprogress": _TODO_STATUS_IN_PROGRESS,
    "running": _TODO_STATUS_IN_PROGRESS,
    "working": _TODO_STATUS_IN_PROGRESS,
    "processing": _TODO_STATUS_IN_PROGRESS,
    "进行中": _TODO_STATUS_IN_PROGRESS,
    "执行中": _TODO_STATUS_IN_PROGRESS,
    # cancelled
    "cancelled": _TODO_STATUS_CANCELLED,
    "canceled": _TODO_STATUS_CANCELLED,
    "skipped": _TODO_STATUS_CANCELLED,
    "abandoned": _TODO_STATUS_CANCELLED,
    "已取消": _TODO_STATUS_CANCELLED,
    "取消": _TODO_STATUS_CANCELLED,
    "已放弃": _TODO_STATUS_CANCELLED,
    # pending
    "pending": _TODO_STATUS_PENDING,
    "todo": _TODO_STATUS_PENDING,
    "queued": _TODO_STATUS_PENDING,
    "not_started": _TODO_STATUS_PENDING,
    "planned": _TODO_STATUS_PENDING,
    "待办": _TODO_STATUS_PENDING,
    "待处理": _TODO_STATUS_PENDING,
}

# 文本字段候选（模型可能把内容塞进 label / title / task 等字段）
_TEXT_KEYS = ("content", "label", "title", "task", "text", "description")


def normalize_todo_status(raw: Any) -> str:
    """把模型写的状态值归一到四值白名单；认不出的一律按 pending 处理。"""
    if not isinstance(raw, str):
        return _TODO_STATUS_PENDING
    return _STATUS_ALIASES.get(raw.strip().lower(), _TODO_STATUS_PENDING)


def normalize_todo_items(items: list[Any]) -> list[dict[str, Any]]:
    """把模型输出的待办数组规整成 {id, content, status} 形状。

    参数：
        items: 模型输出的原始数组（元素可能是 dict / str / 数字等）

    返回：
        规整后的列表（保证每项都有 id / content / status；
        无法提取内容的条目丢弃，避免前端渲染出空气）
    """
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        # 纯字符串条目：内容即文本
        if isinstance(item, str):
            text = item.strip()
            if text:
                normalized.append(
                    {"id": f"todo-{index + 1}", "content": text, "status": _TODO_STATUS_PENDING}
                )
            continue
        if not isinstance(item, dict):
            continue
        # 内容：按候选键找第一个非空值
        content = ""
        for key in _TEXT_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                content = value.strip()
                break
        if not content:
            continue
        normalized.append(
            {
                "id": str(item.get("id") or f"todo-{index + 1}"),
                "content": content,
                "status": normalize_todo_status(item.get("status")),
            }
        )
    return normalized


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

        # 状态收敛 + 触达标记：todos_touched_at 供 run 收尾判断「本轮模型碰没碰过
        # todo」（没碰过 → 清空；碰过 → 按 completed/cancelled 收尾）。
        normalized = normalize_todo_items(todos)
        return {
            "todos": normalized,
            "todos_touched_at": time.time(),
            "prints": [f"已更新任务计划：{len(normalized)} 项待办"],
        }