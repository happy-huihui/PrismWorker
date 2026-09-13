"""会话历史读取（history）——app/core 文件操作之一。

从 checkpoints.db（与 runs 表同库）读出指定线程「最新一轮」checkpoint
保存的对话原文，转成前端可直接渲染的精简结构 {role, content}。
线程从没跑过 run（无 checkpoint）→ 返回空数组。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

_ROLE_MAP = {
    "human": "user",
    "ai": "assistant",
    "tool": "tool",
    "system": "system",
    "function": "message",
}

# harness 输入防注入中间件会给 user 消息包上边界标记，
# 该标记属于内部安全机制、不是对话内容，历史读取时剥离，避免 UI 展示内部噪音。
# 兼容两类残留：外层真实 BEGIN/END 包裹，与 neutralize 后遗留的惰性标记行。
_USER_INPUT_WRAP_RE = re.compile(
    r"^\s*---\s*BEGIN USER INPUT\s*---[\r\n]+(.*?)[\r\n]+\s*---\s*END USER INPUT\s*---\s*$",
    re.DOTALL,
)
_NEUTRALIZED_BOUNDARY_LINE_RE = re.compile(
    r"^\s*\[(?:BEGIN|END) USER INPUT\]\s*$", re.MULTILINE
)


def _strip_user_input_wrapper(text: str) -> str:
    """去除 input_sanitization 的会话包裹标记，还原纯用户文本。

    先剥外层真实 BEGIN/END 包裹（可能多层嵌套）；再移除 neutralize
    遗留的惰性标记行（[BEGIN/END USER INPUT]），并折叠多余空行。
    """
    while True:
        stripped = text.strip()
        m = _USER_INPUT_WRAP_RE.match(stripped)
        if not m:
            break
        text = m.group(1)
    cleaned = _NEUTRALIZED_BOUNDARY_LINE_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


async def get_message_history(
    user_id: str,
    thread_id: str,
    *,
    limit: int = 200,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """读取线程会话历史（精简结构），无历史返回空列表。

    实现要点：
    - 与 run 共用同一个 checkpoints.db（resolve_memory_paths 解析路径）
    - 只读连接 + AsyncSqliteSaver.aget_tuple 取最新 checkpoint
    - 库文件不存在 / 无 checkpoint → 空列表（不建库、不报错）
    - db_path 缺省按全局配置解析（测试可注入临时库）
    """
    limit = max(1, min(int(limit), 500))
    if db_path is None:
        from harness.memory.manager import resolve_memory_paths

        _, db_path = resolve_memory_paths(None)

    db_path = Path(db_path)
    if not db_path.exists():
        return []

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    conn = await aiosqlite.connect(str(db_path))
    try:
        saver = AsyncSqliteSaver(conn)
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id,
            }
        }
        tup = await saver.aget_tuple(config)
        if tup is None:
            return []
        channel_values = tup.checkpoint.get("channel_values", {}) or {}
        messages = channel_values.get("messages", []) or []
        result: list[dict[str, Any]] = []
        seq = messages[-limit:]
        for i, msg in enumerate(seq):
            mtype = _ROLE_MAP.get(getattr(msg, "type", ""), "message")
            if mtype == "tool":
                continue
            if mtype == "assistant":
                has_tool_calls = bool(getattr(msg, "tool_calls", None))
                nxt = seq[i + 1] if i + 1 < len(seq) else None
                nxt_type = _ROLE_MAP.get(getattr(nxt, "type", ""), "") if nxt is not None else ""
                if has_tool_calls or nxt_type == "tool":
                    continue
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                texts: list[str] = []
                for piece in content:
                    if isinstance(piece, str):
                        texts.append(piece)
                    elif isinstance(piece, dict) and piece.get("type") == "text":
                        texts.append(str(piece.get("text", "")))
                content = "\n".join(t for t in texts if t)
            content = str(content)
            if mtype == "user":
                content = _strip_user_input_wrapper(content)
            result.append({"role": mtype, "content": content})
        return result
    finally:
        await conn.close()


async def count_messages(user_id: str, thread_id: str, *, db_path: str | Path | None = None) -> int:
    """线程当前消息条数（供 UI 角标/调试；无历史返回 0）。"""
    history = await get_message_history(user_id, thread_id, limit=500, db_path=db_path)
    return len(history)



async def _main() -> None:
    """命令行自检：python -m app.core.history。"""
    import sys

    if len(sys.argv) < 3:
        print("用法: python -m app.core.history <user_id> <thread_id>")
        return
    msgs = await get_message_history(sys.argv[1], sys.argv[2])
    print(f"共 {len(msgs)} 条消息:")
    for m in msgs:
        print(f"  [{m['role']}] {m['content'][:80]}")


if __name__ == "__main__":
    asyncio.run(_main())